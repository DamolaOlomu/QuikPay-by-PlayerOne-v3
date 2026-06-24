"""
migrate.py — runs all 5 migrations directly via psycopg2.
Safe to run multiple times (uses IF NOT EXISTS / checkfirst logic).
"""
import psycopg2
import sys

DB_URL = "postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"

def run(conn, sql, label=""):
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        if label:
            print(f"  ✓ {label}")
    except psycopg2.errors.DuplicateTable:
        conn.rollback()
        print(f"  ~ {label} (already exists, skipped)")
    except psycopg2.errors.DuplicateObject:
        conn.rollback()
        print(f"  ~ {label} (already exists, skipped)")
    except psycopg2.errors.UndefinedColumn:
        conn.rollback()
        print(f"  ~ {label} (column missing, skipped)")
    except Exception as e:
        conn.rollback()
        print(f"  ✗ {label}: {e}")

def col_exists(conn, table, col):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name=%s AND column_name=%s
        """, (table, col))
        return cur.fetchone() is not None

def table_exists(conn, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name=%s
        """, (table,))
        return cur.fetchone() is not None

def index_exists(conn, index):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname=%s", (index,))
        return cur.fetchone() is not None

def enum_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_type WHERE typname=%s", (name,))
        return cur.fetchone() is not None

def alembic_version_exists(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name='alembic_version'
        """)
        return cur.fetchone() is not None

def get_current_revision(conn):
    if not alembic_version_exists(conn):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        return row[0] if row else None

def set_revision(conn, rev):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alembic_version")
        cur.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (rev,))
    conn.commit()
    print(f"  ✓ alembic_version → {rev}")

def ensure_alembic_table(conn):
    run(conn, """
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
    """, "alembic_version table")

# ── migrations ────────────────────────────────────────────────────────────────

def migration_0001(conn):
    print("\n[0001] initial schema")

    run(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(26) PRIMARY KEY,
            phone_number VARCHAR(20) NOT NULL,
            email VARCHAR(255),
            fullname VARCHAR(255) NOT NULL,
            hashed_password VARCHAR(255) NOT NULL,
            pin_hash VARCHAR(255),
            role VARCHAR(20) NOT NULL DEFAULT 'customer',
            status VARCHAR(30) NOT NULL DEFAULT 'pending_verification',
            api_key_hash VARCHAR(255),
            wallet_id VARCHAR(32) NOT NULL DEFAULT '',
            balance NUMERIC(18,4) NOT NULL DEFAULT 0,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            last_idempotency_key VARCHAR(64),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "users table")

    for idx, unique, cols in [
        ("ix_users_phone_number", True,  "phone_number"),
        ("ix_users_email",        True,  "email"),
        ("ix_users_wallet_id",    True,  "wallet_id"),
        ("ix_users_is_deleted",   False, "is_deleted"),
        ("ix_users_api_key_hash", False, "api_key_hash"),
    ]:
        if not index_exists(conn, idx):
            u = "UNIQUE" if unique else ""
            run(conn, f"CREATE {u} INDEX {idx} ON users ({cols})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS kyc (
            id VARCHAR(26) PRIMARY KEY,
            user_id VARCHAR(26) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier VARCHAR(10) NOT NULL DEFAULT 'tier_0',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            bvn VARCHAR(11), nin VARCHAR(11),
            id_type VARCHAR(50), id_number VARCHAR(50), id_expiry VARCHAR(20),
            id_front_ref VARCHAR(255), id_back_ref VARCHAR(255), selfie_ref VARCHAR(255),
            rejection_reason TEXT, reviewer_id VARCHAR(26),
            daily_limit NUMERIC(18,2) NOT NULL DEFAULT 50000,
            monthly_limit NUMERIC(18,2) NOT NULL DEFAULT 200000,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "kyc table")
    if not index_exists(conn, "ix_kyc_user_id"):
        run(conn, "CREATE UNIQUE INDEX ix_kyc_user_id ON kyc (user_id)", "index ix_kyc_user_id")

    run(conn, """
        CREATE TABLE IF NOT EXISTS transactions (
            id VARCHAR(26) PRIMARY KEY,
            reference VARCHAR(64) NOT NULL UNIQUE,
            idempotency_key VARCHAR(64) UNIQUE,
            external_reference VARCHAR(128),
            amount NUMERIC(18,4) NOT NULL,
            fee NUMERIC(18,4) NOT NULL DEFAULT 0,
            balance_before NUMERIC(18,4) NOT NULL,
            balance_after NUMERIC(18,4) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            transaction_type VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'initiated',
            origin VARCHAR(20) NOT NULL,
            channel VARCHAR(20) NOT NULL,
            description TEXT, metadata_json TEXT,
            user_id VARCHAR(26) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            counterparty_id VARCHAR(26), counterparty_name VARCHAR(255),
            counterparty_account VARCHAR(50),
            reversed_by_id VARCHAR(26) REFERENCES transactions(id),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "transactions table")
    for idx, cols in [
        ("ix_transactions_user_id",  "user_id"),
        ("ix_transactions_reference","reference"),
        ("ix_transactions_status",   "status"),
        ("ix_transactions_type",     "transaction_type"),
    ]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON transactions ({cols})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS transaction_events (
            id VARCHAR(26) PRIMARY KEY,
            transaction_id VARCHAR(26) NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
            from_status VARCHAR(20), to_status VARCHAR(20) NOT NULL,
            actor VARCHAR(64) NOT NULL, note TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "transaction_events table")
    if not index_exists(conn, "ix_transaction_events_transaction_id"):
        run(conn, "CREATE INDEX ix_transaction_events_transaction_id ON transaction_events (transaction_id)", "index ix_transaction_events_transaction_id")

    run(conn, """
        CREATE TABLE IF NOT EXISTS agents (
            id VARCHAR(26) PRIMARY KEY,
            user_id VARCHAR(26) NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
            kyc_id VARCHAR(26) REFERENCES kyc(id),
            status VARCHAR(20) NOT NULL DEFAULT 'inactive',
            address TEXT NOT NULL,
            latitude FLOAT, longitude FLOAT,
            lga VARCHAR(100), state VARCHAR(100),
            float_balance NUMERIC(18,2) NOT NULL DEFAULT 0,
            daily_transaction_limit NUMERIC(18,2) NOT NULL DEFAULT 500000,
            commission_rate FLOAT NOT NULL DEFAULT 0.005,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "agents table")

    run(conn, """
        CREATE TABLE IF NOT EXISTS atms (
            id VARCHAR(26) PRIMARY KEY,
            terminal_id VARCHAR(50) NOT NULL UNIQUE,
            bank_name VARCHAR(100), bank_code VARCHAR(10),
            status VARCHAR(20) NOT NULL DEFAULT 'offline',
            address TEXT NOT NULL,
            latitude FLOAT, longitude FLOAT, state VARCHAR(100),
            cash_level NUMERIC(18,2) NOT NULL DEFAULT 0,
            max_cash_capacity NUMERIC(18,2) NOT NULL DEFAULT 5000000,
            last_serviced_by VARCHAR(26),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "atms table")

    run(conn, """
        CREATE TABLE IF NOT EXISTS payment_links (
            id VARCHAR(26) PRIMARY KEY,
            creator_id VARCHAR(26) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            slug VARCHAR(64) NOT NULL UNIQUE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            amount NUMERIC(18,4),
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            collect_phone BOOLEAN NOT NULL DEFAULT TRUE,
            collect_name BOOLEAN NOT NULL DEFAULT TRUE,
            one_time_use BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            expires_at TIMESTAMPTZ,
            times_paid INTEGER NOT NULL DEFAULT 0,
            total_collected NUMERIC(18,4) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "payment_links table")
    if not index_exists(conn, "ix_payment_links_creator_id"):
        run(conn, "CREATE INDEX ix_payment_links_creator_id ON payment_links (creator_id)", "index ix_payment_links_creator_id")


def migration_0002(conn):
    print("\n[0002] add wallet_id")
    if not col_exists(conn, "users", "wallet_id"):
        run(conn, "ALTER TABLE users ADD COLUMN wallet_id VARCHAR(32)", "add wallet_id column")
    else:
        print("  ~ wallet_id column already exists")


def migration_0003(conn):
    print("\n[0003] mock bank tables")
    for name in ("mockaccountstatus", "mockentrytype", "mocktransferstatus", "mockwebhookstatus"):
        if not enum_exists(conn, name):
            values = {
                "mockaccountstatus": "('active','frozen','closed')",
                "mockentrytype":     "('credit','debit')",
                "mocktransferstatus":"('pending','success','failed')",
                "mockwebhookstatus": "('pending','delivered','failed')",
            }[name]
            run(conn, f"CREATE TYPE {name} AS ENUM {values}", f"enum {name}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS mock_virtual_accounts (
            id VARCHAR(26) PRIMARY KEY,
            account_number VARCHAR(10) NOT NULL UNIQUE,
            bank_code VARCHAR(10) NOT NULL DEFAULT '999',
            bank_name VARCHAR(64) NOT NULL DEFAULT 'MockBank MFB',
            account_name VARCHAR(128) NOT NULL,
            customer_ref VARCHAR(64) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            balance NUMERIC(18,4) NOT NULL DEFAULT 0,
            status mockaccountstatus NOT NULL DEFAULT 'active',
            metadata_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """, "mock_virtual_accounts table")
    for idx, col in [("ix_mock_va_account_number","account_number"),("ix_mock_va_customer_ref","customer_ref")]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON mock_virtual_accounts ({col})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS mock_ledger_entries (
            id VARCHAR(26) PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            account_id VARCHAR(26) NOT NULL REFERENCES mock_virtual_accounts(id) ON DELETE RESTRICT,
            entry_type mockentrytype NOT NULL,
            amount NUMERIC(18,4) NOT NULL,
            balance_before NUMERIC(18,4) NOT NULL,
            balance_after NUMERIC(18,4) NOT NULL,
            reference VARCHAR(64) NOT NULL,
            description TEXT
        )
    """, "mock_ledger_entries table")
    for idx, col in [("ix_mock_ledger_account_id","account_id"),("ix_mock_ledger_reference","reference")]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON mock_ledger_entries ({col})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS mock_transfers (
            id VARCHAR(26) PRIMARY KEY,
            reference VARCHAR(64) NOT NULL UNIQUE,
            amount NUMERIC(18,4) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            dest_bank_code VARCHAR(10) NOT NULL,
            dest_account_number VARCHAR(20) NOT NULL,
            dest_account_name VARCHAR(128),
            status mocktransferstatus NOT NULL DEFAULT 'pending',
            failure_reason TEXT,
            provider_ref VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """, "mock_transfers table")
    if not index_exists(conn, "ix_mock_transfer_reference"):
        run(conn, "CREATE INDEX ix_mock_transfer_reference ON mock_transfers (reference)", "index ix_mock_transfer_reference")

    run(conn, """
        CREATE TABLE IF NOT EXISTS mock_webhook_outbox (
            id VARCHAR(26) PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            event_type VARCHAR(64) NOT NULL,
            payload_json TEXT NOT NULL,
            target_url TEXT,
            status mockwebhookstatus NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            error TEXT
        )
    """, "mock_webhook_outbox table")
    if not index_exists(conn, "ix_mock_outbox_status"):
        run(conn, "CREATE INDEX ix_mock_outbox_status ON mock_webhook_outbox (status)", "index ix_mock_outbox_status")


def migration_0004(conn):
    print("\n[0004] mock cards")
    if not enum_exists(conn, "mockcardstatus"):
        run(conn, "CREATE TYPE mockcardstatus AS ENUM ('active','expired','revoked')", "enum mockcardstatus")
    run(conn, """
        CREATE TABLE IF NOT EXISTS mock_cards (
            id VARCHAR(26) PRIMARY KEY,
            token VARCHAR(64) NOT NULL UNIQUE,
            customer_ref VARCHAR(64) NOT NULL,
            card_number_masked VARCHAR(19) NOT NULL,
            card_type VARCHAR(16) NOT NULL DEFAULT 'visa',
            expiry_month VARCHAR(2) NOT NULL,
            expiry_year VARCHAR(4) NOT NULL,
            cardholder_name VARCHAR(128) NOT NULL,
            bank_name VARCHAR(64),
            status mockcardstatus NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """, "mock_cards table")
    for idx, col in [("ix_mock_card_token","token"),("ix_mock_card_customer_ref","customer_ref")]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON mock_cards ({col})", f"index {idx}")


def migration_0005(conn):
    print("\n[0005] developer dashboard")
    for name, vals in [
        ("keyenvironment",  "('test','live')"),
        ("keystatus",       "('active','revoked','expired')"),
        ("ticketstatus",    "('open','in_progress','resolved','closed')"),
        ("ticketpriority",  "('low','medium','high','critical')"),
        ("ticketcategory",  "('api_keys','transactions','webhooks','account','billing','bug','other')"),
    ]:
        if not enum_exists(conn, name):
            run(conn, f"CREATE TYPE {name} AS ENUM {vals}", f"enum {name}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS api_keys (
            id VARCHAR(26) PRIMARY KEY,
            user_id VARCHAR(26) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            prefix VARCHAR(8) NOT NULL,
            key_hash VARCHAR(64) NOT NULL UNIQUE,
            environment keyenvironment NOT NULL DEFAULT 'test',
            status keystatus NOT NULL DEFAULT 'active',
            revoked_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            request_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "api_keys table")
    for idx, unique, cols in [
        ("ix_api_keys_user_id",  False, "user_id"),
        ("ix_api_keys_key_hash", True,  "key_hash"),
        ("ix_api_keys_prefix",   False, "prefix"),
    ]:
        if not index_exists(conn, idx):
            u = "UNIQUE" if unique else ""
            run(conn, f"CREATE {u} INDEX {idx} ON api_keys ({cols})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS request_logs (
            id VARCHAR(26) PRIMARY KEY,
            user_id VARCHAR(26) REFERENCES users(id) ON DELETE SET NULL,
            api_key_id VARCHAR(26) REFERENCES api_keys(id) ON DELETE SET NULL,
            environment keyenvironment NOT NULL DEFAULT 'test',
            method VARCHAR(10) NOT NULL,
            path VARCHAR(500) NOT NULL,
            request_id VARCHAR(26),
            status_code INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            success BOOLEAN NOT NULL,
            error_code VARCHAR(64),
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "request_logs table")
    for idx, cols in [
        ("ix_request_logs_api_key_created", "api_key_id, created_at"),
        ("ix_request_logs_user_created",    "user_id, created_at"),
        ("ix_request_logs_created_at",      "created_at"),
        ("ix_request_logs_request_id",      "request_id"),
    ]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON request_logs ({cols})", f"index {idx}")

    run(conn, """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id VARCHAR(26) PRIMARY KEY,
            user_id VARCHAR(26) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            subject VARCHAR(255) NOT NULL,
            body TEXT NOT NULL,
            category ticketcategory NOT NULL DEFAULT 'other',
            priority ticketpriority NOT NULL DEFAULT 'medium',
            status ticketstatus NOT NULL DEFAULT 'open',
            assigned_to VARCHAR(26),
            resolution_note TEXT,
            related_resource_type VARCHAR(64),
            related_resource_id VARCHAR(26),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """, "support_tickets table")
    for idx, col in [("ix_support_tickets_user_id","user_id"),("ix_support_tickets_status","status")]:
        if not index_exists(conn, idx):
            run(conn, f"CREATE INDEX {idx} ON support_tickets ({col})", f"index {idx}")

    # rename api_key_hash -> _legacy_api_key_hash if not done yet
    if col_exists(conn, "users", "api_key_hash") and not col_exists(conn, "users", "_legacy_api_key_hash"):
        run(conn, 'ALTER TABLE users RENAME COLUMN api_key_hash TO _legacy_api_key_hash', "rename api_key_hash")
    else:
        print("  ~ api_key_hash rename already done or column missing")

    # also ensure the users table has the fullname/status columns the app expects
    # (the model uses userrole/userstatus enums but migration 0001 used VARCHAR — keep as VARCHAR)


def main():
    print("Connecting to Supabase via psycopg2...")
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
        print("Connected.")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    ensure_alembic_table(conn)
    current = get_current_revision(conn)
    print(f"Current revision: {current or '(none)'}")

    revisions = ["0001_initial", "0002_add_wallet_id", "0003_mock_bank", "0004_mock_cards", "0005"]
    migrations = [migration_0001, migration_0002, migration_0003, migration_0004, migration_0005]

    already_done = revisions.index(current) + 1 if current in revisions else 0

    if already_done == len(revisions):
        print("\nAll migrations already applied.")
        conn.close()
        return

    for i in range(already_done, len(revisions)):
        migrations[i](conn)
        set_revision(conn, revisions[i])

    print("\n✓ All migrations complete.")
    conn.close()

if __name__ == "__main__":
    main()