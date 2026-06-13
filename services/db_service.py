# ============================================================
# BizGuard — Database Service
# FILE: services/db_service.py
# All Supabase/PostgreSQL operations centralized here.
# ============================================================

from supabase import create_client, Client
from functools import lru_cache
from config import get_settings
import logging

logger = logging.getLogger(__name__)


@lru_cache()
def get_supabase_client() -> Client:
    """
    Returns a cached Supabase client instance.
    Uses service role key for full DB access (backend only).
    """
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_service_key
    )


async def get_user_by_phone(phone_number: str) -> dict | None:
    """Fetch a user record by phone number."""
    try:
        client = get_supabase_client()
        result = client.table("users").select("*").eq(
            "phone_number", phone_number
        ).single().execute()
        return result.data
    except Exception as e:
        logger.error(f"DB error fetching user by phone: {e}")
        return None


async def get_user_by_id(user_id: str) -> dict | None:
    """Fetch a user record by UUID."""
    try:
        client = get_supabase_client()
        result = client.table("users").select("*").eq(
            "user_id", user_id
        ).single().execute()
        return result.data
    except Exception as e:
        logger.error(f"DB error fetching user by id: {e}")
        return None


async def get_transaction_summary(user_id: str, months: int = 1) -> dict:
    """
    Fetch aggregated transaction summary for AI context injection.
    Returns income/expense totals and category breakdown.
    """
    try:
        client = get_supabase_client()

        # Fetch recent transactions
        result = client.table("transactions").select(
            "amount, category, payment_mode, timestamp"
        ).eq("user_id", user_id).order(
            "timestamp", desc=True
        ).limit(500).execute()

        transactions = result.data or []

        if not transactions:
            return {}

        # Basic aggregation
        income = sum(t["amount"] for t in transactions if t["category"] == "Income")
        expense = sum(t["amount"] for t in transactions if t["category"] != "Income")
        net_profit = income - expense
        profit_margin = (net_profit / income * 100) if income > 0 else 0

        # Category breakdown
        categories = {}
        for t in transactions:
            cat = t["category"]
            if cat != "Income":
                categories[cat] = categories.get(cat, 0) + t["amount"]

        category_str = "\n".join(
            f"  - {cat}: ₹{amt:,.2f}"
            for cat, amt in sorted(categories.items(), key=lambda x: x[1], reverse=True)
        )

        # Fetch active anomalies
        anomaly_result = client.table("expense_anomalies").select(
            "title, severity, current_amount, baseline_amount"
        ).eq("user_id", user_id).eq("is_resolved", False).limit(5).execute()

        anomaly_str = "\n".join(
            f"  - [{a['severity']}] {a['title']}: "
            f"₹{a['current_amount']:,.2f} vs avg ₹{a['baseline_amount']:,.2f}"
            for a in (anomaly_result.data or [])
        ) or "None"

        return {
            "total_income": income,
            "total_expense": expense,
            "net_profit": net_profit,
            "profit_margin": profit_margin,
            "category_breakdown": category_str,
            "top_anomalies": anomaly_str
        }

    except Exception as e:
        logger.error(f"DB error fetching transaction summary: {e}")
        return {}