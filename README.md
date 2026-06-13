# BizGuard Backend API

FastAPI + Supabase + Gemini AI backend for the BizGuard expense auditor.

## Setup

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your Supabase and Gemini credentials

# 4. Run database schema
# Copy schema.sql contents → paste in Supabase SQL Editor → Run

# 5. Start server
python main.py
# API runs at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/send-otp` | Send OTP to phone |
| POST | `/api/v1/auth/verify-otp` | Verify OTP → get JWT |
| POST | `/api/v1/sync/bulk-transactions` | Nightly bulk sync |
| GET | `/api/v1/dashboard/summary` | Dashboard data |
| GET | `/api/v1/transactions` | Transaction list |
| POST | `/api/v1/advisor/chat` | AI financial advisor |
| GET | `/api/v1/anomalies` | Expense anomalies |

## File Structure

```
bizguard-backend/
├── main.py              ← FastAPI app + all endpoints
├── config.py            ← Environment settings
├── schema.sql           ← PostgreSQL DDL (run in Supabase)
├── requirements.txt     ← Python dependencies
├── .env.example         ← Environment template
├── .gitignore
└── services/
    ├── ai_service.py    ← Gemini AI integration
    └── db_service.py    ← Supabase DB operations
```