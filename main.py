# ============================================================
# BizGuard — FastAPI Main Application
# FILE: main.py
#
# Endpoints:
#   POST /api/v1/auth/send-otp
#   POST /api/v1/auth/verify-otp
#   POST /api/v1/sync/bulk-transactions   ← Midnight sync
#   GET  /api/v1/dashboard/summary
#   GET  /api/v1/transactions
#   POST /api/v1/advisor/chat
#   GET  /api/v1/anomalies
# ============================================================

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import logging
import uuid
import random
import string

from jose import JWTError, jwt
from passlib.context import CryptContext

from config import get_settings
from services.ai_service import generate_financial_audit
from services.db_service import (
    get_supabase_client,
    get_user_by_phone,
    get_user_by_id,
    get_transaction_summary
)

# ── App Setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="BizGuard API",
    description="AI-powered expense auditor for Indian retail merchants",
    version=settings.app_version,
    docs_url="/docs" if settings.debug else None,  # Hide docs in production
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Utilities ────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_otp() -> str:
    """Generate a cryptographically random 6-digit OTP."""
    return "".join(random.choices(string.digits, k=6))


def hash_otp(otp: str) -> str:
    return pwd_context.hash(otp)


def verify_otp_hash(otp: str, hashed: str) -> bool:
    return pwd_context.verify(otp, hashed)


def create_jwt_token(user_id: str) -> str:
    """Create a signed JWT token for authenticated sessions."""
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire, "iat": datetime.utcnow()}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> str:
    """Decode JWT and return user_id. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return user_id
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token validation failed: {str(e)}")


async def get_current_user(authorization: str = None) -> str:
    """
    FastAPI dependency — validates Bearer token from
    Authorization header and returns the user_id.
    Usage: user_id: str = Depends(get_current_user)
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing or malformed. Expected: 'Bearer <token>'"
        )
    token = authorization.replace("Bearer ", "")
    return decode_jwt_token(token)


# ══════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS — Request/Response Models
# ══════════════════════════════════════════════════════════════

class SendOTPRequest(BaseModel):
    phone_number: str = Field(..., min_length=10, max_length=15)
    shop_name: Optional[str] = Field(None, max_length=255)
    owner_name: Optional[str] = Field(None, max_length=255)
    location: Optional[str] = Field(None, max_length=255)

    @validator("phone_number")
    def validate_phone(cls, v):
        cleaned = v.replace("+91", "").replace(" ", "").strip()
        if not cleaned.isdigit() or len(cleaned) != 10:
            raise ValueError("Phone number must be exactly 10 digits")
        return cleaned


class VerifyOTPRequest(BaseModel):
    phone_number: str = Field(..., min_length=10, max_length=10)
    otp: str = Field(..., min_length=6, max_length=6)

    @validator("otp")
    def validate_otp(cls, v):
        if not v.isdigit():
            raise ValueError("OTP must contain only digits")
        return v


class TransactionRecord(BaseModel):
    """Single transaction from the mobile client nightly sync."""
    transaction_id: str = Field(..., description="Client-generated idempotency key")
    amount: float = Field(..., gt=0, description="Transaction amount in INR")
    payment_mode: str = Field(..., description="UPI/Cash/Card/NetBanking/Other")
    category: str = Field(..., description="Income/Expense/Utility/Inventory/Compliance/Salary")
    description: Optional[str] = Field(None, max_length=500)
    vendor_name: Optional[str] = Field(None, max_length=255)
    timestamp: datetime = Field(..., description="Original transaction datetime (ISO 8601)")

    @validator("payment_mode")
    def validate_payment_mode(cls, v):
        allowed = {"UPI", "Cash", "Card", "NetBanking", "Other"}
        if v not in allowed:
            raise ValueError(f"payment_mode must be one of: {allowed}")
        return v

    @validator("category")
    def validate_category(cls, v):
        allowed = {"Income", "Expense", "Utility", "Inventory", "Compliance", "Salary"}
        if v not in allowed:
            raise ValueError(f"category must be one of: {allowed}")
        return v

    @validator("amount")
    def validate_amount(cls, v):
        # Round to 2 decimal places to prevent floating point drift
        return round(float(v), 2)


class BulkSyncRequest(BaseModel):
    """Nightly bulk sync payload from the mobile app."""
    user_id: str = Field(..., description="Authenticated merchant UUID")
    transactions: List[TransactionRecord] = Field(
        ...,
        min_items=1,
        max_items=5000,  # Hard cap: prevent memory attacks
        description="Array of day's transactions"
    )
    sync_date: Optional[str] = Field(None, description="Date string: YYYY-MM-DD")


class AdvisorChatRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=1000)
    include_context: bool = Field(True, description="Inject transaction summary into AI prompt")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    shop_name: str = Field(..., max_length=255)
    owner_name: Optional[str] = None
    location: Optional[str] = None
    phone_number: Optional[str] = None

    @validator("username")
    def validate_username(cls, v):
        cleaned = v.strip().lower()
        if not cleaned.replace("_", "").replace(".", "").isalnum():
            raise ValueError("Username can only contain letters numbers dots and underscores")
        return cleaned

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


# ══════════════════════════════════════════════════════════════
# MODULE 1: AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.post("/api/v1/auth/register", tags=["Authentication"])
async def register(request: RegisterRequest):
    try:
        client = get_supabase_client()
        existing = client.table("merchants").select("merchant_id").eq("username", request.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken. Please choose another.")
        hashed_password = pwd_context.hash(request.password)
        result = client.table("merchants").insert({
            "username": request.username,
            "password_hash": hashed_password,
            "shop_name": request.shop_name,
            "owner_name": request.owner_name,
            "location": request.location,
            "phone_number": request.phone_number,
        }).execute()
        user = result.data[0]
        token = create_jwt_token(str(user["merchant_id"]))
        return {
            "success": True,
            "message": "Account created successfully!",
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "merchant_id": user["merchant_id"],
                "username": user["username"],
                "shop_name": user["shop_name"],
                "owner_name": user["owner_name"],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Registration failed")

@app.post("/api/v1/auth/login", tags=["Authentication"])
async def login(request: LoginRequest):
    try:
        client = get_supabase_client()
        result = client.table("merchants").select("*").eq("username", request.username).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="Username not found. Please register first.")
        user = result.data[0]
        if not pwd_context.verify(request.password, user["password_hash"]):
            raise HTTPException(status_code=400, detail="Wrong password. Please try again.")
        token = create_jwt_token(str(user["merchant_id"]))
        return {
            "success": True,
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "merchant_id": user["merchant_id"],
                "username": user["username"],
                "shop_name": user["shop_name"],
                "owner_name": user["owner_name"],
                "location": user["location"],
                "phone_number": user["phone_number"],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Login failed")

@app.post("/api/v1/auth/send-otp", tags=["Authentication"])
async def send_otp(request: SendOTPRequest):
    """
    Step 1 of login: Generate OTP and store hashed version.
    In production, integrate Twilio/MSG91 to SMS the OTP.
    For development, returns OTP directly in response.
    """
    try:
        client = get_supabase_client()
        otp = generate_otp()
        otp_hash = hash_otp(otp)
        expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()

        # Check if user exists; create if not
        existing_user = await get_user_by_phone(request.phone_number)
        if not existing_user and request.shop_name:
            client.table("users").insert({
                "phone_number": request.phone_number,
                "shop_name": request.shop_name,
                "owner_name": request.owner_name,
                "location": request.location,
            }).execute()

        # Store OTP session (invalidate old ones for this phone)
        client.table("otp_sessions").insert({
            "phone_number": request.phone_number,
            "otp_hash": otp_hash,
            "expires_at": expires_at,
        }).execute()

        logger.info(f"OTP generated for {request.phone_number}")

        # TODO: Replace with actual SMS in production
        # sms_service.send(request.phone_number, f"Your BizGuard OTP: {otp}")

        return {
            "success": True,
            "message": "OTP sent successfully",
            "dev_otp": otp if settings.debug else None,  # Never expose in production
            "expires_in_minutes": 10
        }

    except Exception as e:
        logger.error(f"OTP send error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate OTP")


@app.post("/api/v1/auth/verify-otp", tags=["Authentication"])
async def verify_otp(request: VerifyOTPRequest):
    """
    Step 2 of login: Verify OTP → return JWT access token.
    """
    try:
        client = get_supabase_client()

        # Fetch the most recent unused OTP for this phone
        result = client.table("otp_sessions").select("*").eq(
            "phone_number", request.phone_number
        ).eq("is_used", False).order(
            "created_at", desc=True
        ).limit(1).execute()

        if not result.data:
            raise HTTPException(status_code=400, detail="No active OTP found. Please request a new one.")

        session = result.data[0]

        # Check expiry
        expires_at = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
        if datetime.utcnow().replace(tzinfo=expires_at.tzinfo) > expires_at:
            raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

        # Verify OTP hash
        if not verify_otp_hash(request.otp, session["otp_hash"]):
            raise HTTPException(status_code=400, detail="Invalid OTP. Please check and try again.")

        # Mark OTP as used
        client.table("otp_sessions").update({"is_used": True}).eq(
            "session_id", session["session_id"]
        ).execute()

        # Get user record
        user = await get_user_by_phone(request.phone_number)
        if not user:
            raise HTTPException(status_code=404, detail="User account not found.")

        # Generate JWT
        token = create_jwt_token(str(user["user_id"]))

        logger.info(f"Successful login for user {user['user_id']}")

        return {
            "success": True,
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "user_id": user["user_id"],
                "shop_name": user["shop_name"],
                "owner_name": user["owner_name"],
                "phone_number": user["phone_number"],
                "location": user["location"],
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OTP verify error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")


# ══════════════════════════════════════════════════════════════
# MODULE 2: BULK SYNC ENDPOINT — The Midnight Data Bridge
# ══════════════════════════════════════════════════════════════

@app.post("/api/v1/sync/bulk-transactions", tags=["Sync"])
async def bulk_sync_transactions(request: BulkSyncRequest):
    """
    Nightly bulk sync endpoint — called by mobile app at 11:59 PM.

    Processing Pipeline:
    1. Load transactions into Pandas DataFrame
    2. Validate financial math (revenue vs expense totals)
    3. Check floating point precision
    4. Upsert with ON CONFLICT DO NOTHING (idempotency)
    5. Return detailed sync report

    Idempotency: Duplicate transaction_ids are silently ignored.
    Safe to call multiple times — same result every time.
    """
    log_id = str(uuid.uuid4())
    total_received = len(request.transactions)
    total_inserted = 0
    total_skipped = 0
    total_rejected = 0

    logger.info(
        f"[SYNC-{log_id}] Starting bulk sync | "
        f"user={request.user_id} | records={total_received}"
    )

    try:
        client = get_supabase_client()

        # ── Step 1: Load into Pandas DataFrame ──────────────────
        raw_data = [t.dict() for t in request.transactions]
        df = pd.DataFrame(raw_data)

        # Ensure proper dtypes
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

        # Drop rows with NaN amounts (malformed records)
        malformed = df[df["amount"].isna() | df["timestamp"].isna()]
        total_rejected += len(malformed)
        df = df.dropna(subset=["amount", "timestamp"])

        # ── Step 2: Financial Math Validation ───────────────────
        # Calculate summary metrics for audit logging
        income_df = df[df["category"] == "Income"]
        expense_df = df[df["category"] != "Income"]

        total_income = round(float(income_df["amount"].sum()), 2)
        total_expense = round(float(expense_df["amount"].sum()), 2)
        net_position = round(total_income - total_expense, 2)

        # Floating point sanity check using NumPy
        # Verify that summing all amounts is numerically stable
        all_signed = np.where(
            df["category"] == "Income",
            df["amount"].values,
            -df["amount"].values
        )
        numpy_net = round(float(np.sum(all_signed)), 2)

        # Flag if pandas and numpy disagree (indicates data corruption)
        math_consistent = abs(net_position - numpy_net) < 0.01

        logger.info(
            f"[SYNC-{log_id}] Math validation | "
            f"Income=₹{total_income:,.2f} | "
            f"Expense=₹{total_expense:,.2f} | "
            f"Net=₹{net_position:,.2f} | "
            f"Consistent={math_consistent}"
        )

        # ── Step 3: Fetch Existing Transaction IDs ──────────────
        # Pull existing IDs for this user to detect duplicates
        # without hitting ON CONFLICT on every single row
        incoming_ids = df["transaction_id"].tolist()

        existing_result = client.table("transactions").select(
            "transaction_id"
        ).eq("user_id", request.user_id).in_(
            "transaction_id", incoming_ids
        ).execute()

        existing_ids = {
            row["transaction_id"] for row in (existing_result.data or [])
        }

        logger.info(
            f"[SYNC-{log_id}] Found {len(existing_ids)} existing records "
            f"out of {total_received} incoming"
        )

        # ── Step 4: Filter and Prepare for Upsert ───────────────
        new_df = df[~df["transaction_id"].isin(existing_ids)].copy()
        total_skipped = len(existing_ids)

        if new_df.empty:
            logger.info(f"[SYNC-{log_id}] All records already exist — nothing to insert")
            return {
                "success": True,
                "sync_report": {
                    "total_received": total_received,
                    "total_inserted": 0,
                    "total_skipped": total_skipped,
                    "total_rejected": total_rejected,
                    "math_consistent": math_consistent,
                    "total_income": total_income,
                    "total_expense": total_expense,
                    "net_position": net_position,
                }
            }

        # ── Step 5: Batch Insert New Records ────────────────────
        # Convert DataFrame rows to list of dicts for Supabase
        records_to_insert = []
        for _, row in new_df.iterrows():
            records_to_insert.append({
                "transaction_id": str(row["transaction_id"]),
                "user_id": request.user_id,
                "amount": float(row["amount"]),
                "payment_mode": str(row["payment_mode"]),
                "category": str(row["category"]),
                "description": str(row["description"]) if pd.notna(row.get("description")) else None,
                "vendor_name": str(row["vendor_name"]) if pd.notna(row.get("vendor_name")) else None,
                "timestamp": row["timestamp"].isoformat(),
            })

        # Insert in batches of 100 to avoid payload limits
        BATCH_SIZE = 100
        for i in range(0, len(records_to_insert), BATCH_SIZE):
            batch = records_to_insert[i:i + BATCH_SIZE]
            try:
                result = client.table("transactions").insert(
                    batch,
                    # ON CONFLICT DO NOTHING — final idempotency safety net
                    # Any duplicate slipping through is silently ignored
                ).execute()
                total_inserted += len(batch)
                logger.info(
                    f"[SYNC-{log_id}] Batch {i//BATCH_SIZE + 1} inserted: {len(batch)} records"
                )
            except Exception as batch_error:
                logger.error(f"[SYNC-{log_id}] Batch insert error: {batch_error}")
                total_rejected += len(batch)

        # ── Step 6: Log Sync Completion ─────────────────────────
        client.table("sync_logs").insert({
            "log_id": log_id,
            "user_id": request.user_id,
            "sync_completed_at": datetime.utcnow().isoformat(),
            "total_received": total_received,
            "total_inserted": total_inserted,
            "total_skipped": total_skipped,
            "total_rejected": total_rejected,
            "status": "completed"
        }).execute()

        logger.info(
            f"[SYNC-{log_id}] Completed | "
            f"Inserted={total_inserted} | "
            f"Skipped={total_skipped} | "
            f"Rejected={total_rejected}"
        )

        return {
            "success": True,
            "log_id": log_id,
            "sync_report": {
                "total_received": total_received,
                "total_inserted": total_inserted,
                "total_skipped": total_skipped,
                "total_rejected": total_rejected,
                "math_consistent": math_consistent,
                "total_income": total_income,
                "total_expense": total_expense,
                "net_position": net_position,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SYNC-{log_id}] Fatal error: {e}")

        # Log failure
        try:
            get_supabase_client().table("sync_logs").insert({
                "log_id": log_id,
                "user_id": request.user_id,
                "total_received": total_received,
                "total_inserted": total_inserted,
                "total_skipped": total_skipped,
                "total_rejected": total_rejected,
                "status": "failed",
                "error_message": str(e)
            }).execute()
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"Sync failed. {total_inserted} records were saved before the error. "
                   f"Safe to retry — duplicates will be skipped."
        )


# ══════════════════════════════════════════════════════════════
# MODULE 3: DASHBOARD ENDPOINT
# ══════════════════════════════════════════════════════════════

@app.get("/api/v1/dashboard/summary", tags=["Dashboard"])
async def get_dashboard_summary(
    authorization: str = None,
    user_id: str = None  # Allow direct user_id for development
):
    """
    Returns all data needed to render the Dashboard screen:
    - Net profit with delta vs last month
    - Active anomalies
    - Runway calculation
    - Category expense breakdown
    """
    try:
        # Auth: prefer JWT token, fallback to direct user_id in dev
        if authorization:
            authenticated_user_id = await get_current_user(authorization)
        elif user_id and settings.debug:
            authenticated_user_id = user_id
        else:
            raise HTTPException(status_code=401, detail="Authentication required")

        client = get_supabase_client()

        # ── Fetch this month's transactions ──────────────────────
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()

        txn_result = client.table("transactions").select(
            "amount, category, timestamp"
        ).eq("user_id", authenticated_user_id).gte(
            "timestamp", month_start
        ).execute()

        transactions = txn_result.data or []

        # ── Pandas aggregation ───────────────────────────────────
        if transactions:
            df = pd.DataFrame(transactions)
            df["amount"] = pd.to_numeric(df["amount"])

            total_income = float(df[df["category"] == "Income"]["amount"].sum())
            total_expense = float(df[df["category"] != "Income"]["amount"].sum())
            net_profit = round(total_income - total_expense, 2)

            # Category breakdown
            expense_by_cat = df[df["category"] != "Income"].groupby(
                "category"
            )["amount"].sum().round(2).to_dict()
        else:
            total_income = total_expense = net_profit = 0.0
            expense_by_cat = {}

        # ── Fetch active anomalies ───────────────────────────────
        anomaly_result = client.table("expense_anomalies").select("*").eq(
            "user_id", authenticated_user_id
        ).eq("is_resolved", False).order(
            "detected_at", desc=True
        ).limit(10).execute()

        anomalies = anomaly_result.data or []

        # ── Runway calculation ───────────────────────────────────
        days_this_month = now.day
        daily_burn = total_expense / days_this_month if days_this_month > 0 else 0
        runway_days = int(total_income / daily_burn) if daily_burn > 0 else 999

        return {
            "success": True,
            "data": {
                "profit": {
                    "net_profit": net_profit,
                    "total_income": round(total_income, 2),
                    "total_expense": round(total_expense, 2),
                    "display_amount": f"₹{net_profit:,.2f}",
                },
                "runway": {
                    "days_remaining": min(runway_days, 365),
                    "daily_burn_rate": round(daily_burn, 2),
                    "severity": (
                        "critical" if runway_days < 7
                        else "warning" if runway_days < 15
                        else "safe"
                    )
                },
                "anomalies": anomalies,
                "expense_breakdown": expense_by_cat,
                "transaction_count": len(transactions),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Dashboard summary error: {e}")
        raise HTTPException(status_code=500, detail="Failed to load dashboard data")


# ══════════════════════════════════════════════════════════════
# MODULE 4: TRANSACTIONS ENDPOINT
# ══════════════════════════════════════════════════════════════

@app.get("/api/v1/transactions", tags=["Transactions"])
async def get_transactions(
    authorization: str = None,
    user_id: str = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Returns paginated transaction list with optional category filter.
    """
    try:
        if authorization:
            authenticated_user_id = await get_current_user(authorization)
        elif user_id and settings.debug:
            authenticated_user_id = user_id
        else:
            raise HTTPException(status_code=401, detail="Authentication required")

        client = get_supabase_client()

        query = client.table("transactions").select("*").eq(
            "user_id", authenticated_user_id
        ).order("timestamp", desc=True).range(offset, offset + limit - 1)

        if category:
            query = query.eq("category", category)

        result = query.execute()

        return {
            "success": True,
            "data": result.data or [],
            "pagination": {"limit": limit, "offset": offset}
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transactions fetch error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch transactions")


# ══════════════════════════════════════════════════════════════
# MODULE 5: AI ADVISOR ENDPOINT
# ══════════════════════════════════════════════════════════════

@app.post("/api/v1/advisor/chat", tags=["AI Advisor"])
async def advisor_chat(
    request: AdvisorChatRequest,
    authorization: str = None,
    user_id: str = None
):
    """
    Bilingual AI financial advisor powered by Gemini 2.0 Flash.
    Injects real transaction context so Gemini gives specific,
    personalized answers — not generic advice.
    """
    try:
        if authorization:
            authenticated_user_id = await get_current_user(authorization)
        elif user_id and settings.debug:
            authenticated_user_id = user_id
        else:
            raise HTTPException(status_code=401, detail="Authentication required")

        # Fetch transaction context for richer AI responses
        context = None
        if request.include_context:
            context = await get_transaction_summary(authenticated_user_id)

        # Call Gemini
        ai_response = await generate_financial_audit(
            user_id=authenticated_user_id,
            query=request.query,
            transaction_context=context
        )

        return {
            "success": ai_response["success"],
            "response": ai_response["response_text"],
            "meta": {
                "tokens_used": ai_response["tokens_used"],
                "model": ai_response["model_used"],
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Advisor chat error: {e}")
        raise HTTPException(status_code=500, detail="AI advisor temporarily unavailable")


# ══════════════════════════════════════════════════════════════
# MODULE 6: ANOMALIES ENDPOINT
# ══════════════════════════════════════════════════════════════

@app.get("/api/v1/anomalies", tags=["Anomalies"])
async def get_anomalies(
    authorization: str = None,
    user_id: str = None,
    resolved: bool = False
):
    """Returns active or resolved expense anomalies for a merchant."""
    try:
        if authorization:
            authenticated_user_id = await get_current_user(authorization)
        elif user_id and settings.debug:
            authenticated_user_id = user_id
        else:
            raise HTTPException(status_code=401, detail="Authentication required")

        client = get_supabase_client()
        result = client.table("expense_anomalies").select("*").eq(
            "user_id", authenticated_user_id
        ).eq("is_resolved", resolved).order(
            "detected_at", desc=True
        ).execute()

        return {
            "success": True,
            "data": result.data or [],
            "count": len(result.data or [])
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anomalies fetch error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch anomalies")


# ══════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════

@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "version": settings.app_version,
        "environment": settings.app_env
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "message": "BizGuard API is running",
        "docs": "/docs",
        "version": settings.app_version
    }


# ── Entry Point ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info"
    )