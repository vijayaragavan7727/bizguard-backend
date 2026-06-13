-- ============================================================
-- BizGuard — PostgreSQL DDL Schema
-- FILE: schema.sql
-- Run this script once in Supabase SQL Editor to initialize
-- all tables with correct constraints and indexes.
-- ============================================================

-- Enable UUID generation extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────────────────────
-- TABLE 1: users
-- Core merchant profile. One row per registered shopkeeper.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number    VARCHAR(15)  NOT NULL UNIQUE,
    shop_name       VARCHAR(255) NOT NULL,
    location        VARCHAR(255),
    gstin           VARCHAR(20),                    -- GST Identification Number
    owner_name      VARCHAR(255),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Index for fast phone-number lookups during OTP login
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number);

-- ─────────────────────────────────────────────────────────────
-- TABLE 2: transactions
-- Individual billing records. transaction_id is the
-- idempotency key — if a duplicate sync arrives, ON CONFLICT
-- DO NOTHING silently skips it. Prevents double-counting.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id  TEXT         PRIMARY KEY,        -- Client-generated idempotency key (e.g. UPI ref, POS ID)
    user_id         UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    amount          NUMERIC(12, 2) NOT NULL,          -- 12 digits total, 2 decimal precision
    payment_mode    VARCHAR(20)  NOT NULL CHECK (payment_mode IN ('UPI', 'Cash', 'Card', 'NetBanking', 'Other')),
    category        VARCHAR(50)  NOT NULL CHECK (category IN ('Income', 'Expense', 'Utility', 'Inventory', 'Compliance', 'Salary')),
    description     TEXT,
    vendor_name     VARCHAR(255),
    timestamp       TIMESTAMPTZ  NOT NULL,            -- Original transaction time from client
    synced_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(), -- When this record hit our server
    is_flagged      BOOLEAN      NOT NULL DEFAULT FALSE
);

-- Composite index for user + time range queries (dashboard loads)
CREATE INDEX IF NOT EXISTS idx_transactions_user_time
    ON transactions(user_id, timestamp DESC);

-- Index for category-based filtering (expense breakdown)
CREATE INDEX IF NOT EXISTS idx_transactions_category
    ON transactions(user_id, category);

-- ─────────────────────────────────────────────────────────────
-- TABLE 3: expense_anomalies
-- AI-detected financial anomalies. Supports Tamil text in
-- description field (PostgreSQL is UTF-8 by default).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS expense_anomalies (
    anomaly_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    transaction_id  TEXT         REFERENCES transactions(transaction_id) ON DELETE SET NULL,
    title           VARCHAR(255) NOT NULL,            -- English title: "TNEB Bill Spike"
    title_tamil     TEXT,                             -- Tamil: "மின்சார கட்டண அதிகரிப்பு"
    description     TEXT         NOT NULL,            -- Detailed English description
    description_tamil TEXT,                           -- Full Tamil explanation for shopkeeper
    severity        VARCHAR(20)  NOT NULL CHECK (severity IN ('Low', 'Medium', 'High', 'Critical')),
    category        VARCHAR(50),                      -- Which expense category triggered this
    current_amount  NUMERIC(12, 2),                   -- The anomalous amount
    baseline_amount NUMERIC(12, 2),                   -- The 3-month average baseline
    is_resolved     BOOLEAN      NOT NULL DEFAULT FALSE,
    detected_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

-- Index for fetching active alerts per user
CREATE INDEX IF NOT EXISTS idx_anomalies_user_active
    ON expense_anomalies(user_id, is_resolved, detected_at DESC);

-- ─────────────────────────────────────────────────────────────
-- TABLE 4: otp_sessions
-- Temporary OTP storage. Expires after 10 minutes.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS otp_sessions (
    session_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone_number    VARCHAR(15)  NOT NULL,
    otp_hash        TEXT         NOT NULL,            -- Bcrypt hash of 6-digit OTP
    expires_at      TIMESTAMPTZ  NOT NULL,            -- NOW() + 10 minutes
    is_used         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Auto-cleanup index for expired OTPs
CREATE INDEX IF NOT EXISTS idx_otp_phone_expiry
    ON otp_sessions(phone_number, expires_at);

-- ─────────────────────────────────────────────────────────────
-- TABLE 5: sync_logs
-- Audit trail for every bulk sync operation.
-- Useful for debugging and replay protection.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_logs (
    log_id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id             UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    sync_started_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sync_completed_at   TIMESTAMPTZ,
    total_received      INTEGER      NOT NULL DEFAULT 0,  -- Records sent by client
    total_inserted      INTEGER      NOT NULL DEFAULT 0,  -- New records written to DB
    total_skipped       INTEGER      NOT NULL DEFAULT 0,  -- Duplicates silently ignored
    total_rejected      INTEGER      NOT NULL DEFAULT 0,  -- Failed validation
    status              VARCHAR(20)  NOT NULL DEFAULT 'processing'
                            CHECK (status IN ('processing', 'completed', 'failed')),
    error_message       TEXT
);

-- ─────────────────────────────────────────────────────────────
-- HELPER: Auto-update updated_at timestamp on users table
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ─────────────────────────────────────────────────────────────
-- SAMPLE DATA: One test merchant for development
-- ─────────────────────────────────────────────────────────────
INSERT INTO users (user_id, phone_number, shop_name, location, owner_name, gstin)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    '9876543210',
    'Kannan Supermarket',
    'Tirunelveli, Tamil Nadu',
    'Kannan',
    '33AABCK1234F1ZK'
) ON CONFLICT (phone_number) DO NOTHING;