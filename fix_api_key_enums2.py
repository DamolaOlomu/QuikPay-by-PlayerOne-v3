import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

# Fix keyenvironment — must convert ALL dependent columns before dropping
print("Fixing keyenvironment...")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment DROP DEFAULT")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment TYPE VARCHAR(10)")
cur.execute("UPDATE api_keys SET environment = LOWER(environment)")

cur.execute("ALTER TABLE request_logs ALTER COLUMN environment DROP DEFAULT")
cur.execute("ALTER TABLE request_logs ALTER COLUMN environment TYPE VARCHAR(10)")
cur.execute("UPDATE request_logs SET environment = LOWER(environment)")

cur.execute("DROP TYPE keyenvironment")
cur.execute("CREATE TYPE keyenvironment AS ENUM ('test', 'live')")

cur.execute("ALTER TABLE api_keys ALTER COLUMN environment TYPE keyenvironment USING environment::keyenvironment")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment SET DEFAULT 'test'::keyenvironment")

cur.execute("ALTER TABLE request_logs ALTER COLUMN environment TYPE keyenvironment USING environment::keyenvironment")
cur.execute("ALTER TABLE request_logs ALTER COLUMN environment SET DEFAULT 'test'::keyenvironment")
print("  ✓ keyenvironment fixed")

# Fix ticket enums
for enum_name, table, col, default_val, values in [
    ("ticketstatus",   "support_tickets", "status",   "open",   "('open','in_progress','resolved','closed')"),
    ("ticketpriority", "support_tickets", "priority", "medium", "('low','medium','high','critical')"),
    ("ticketcategory", "support_tickets", "category", "other",  "('api_keys','transactions','webhooks','account','billing','bug','other')"),
]:
    print(f"Fixing {enum_name}...")
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE VARCHAR(30)")
    cur.execute(f"UPDATE {table} SET {col} = LOWER({col})")
    cur.execute(f"DROP TYPE IF EXISTS {enum_name}")
    cur.execute(f"CREATE TYPE {enum_name} AS ENUM {values}")
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {enum_name} USING {col}::{enum_name}")
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT '{default_val}'::{enum_name}")
    print(f"  ✓ {enum_name} fixed")

# Also fix mock table enums while we're at it
for enum_name, table, col, default_val, values in [
    ("mockaccountstatus", "mock_virtual_accounts", "status", "active", "('active','frozen','closed')"),
    ("mockentrytype",     "mock_ledger_entries",   "entry_type", None,  "('credit','debit')"),
    ("mocktransferstatus","mock_transfers",         "status", "pending","('pending','success','failed')"),
    ("mockwebhookstatus", "mock_webhook_outbox",    "status", "pending","('pending','delivered','failed')"),
    ("mockcardstatus",    "mock_cards",             "status", "active", "('active','expired','revoked')"),
]:
    print(f"Fixing {enum_name}...")
    try:
        if default_val:
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE VARCHAR(20)")
        cur.execute(f"UPDATE {table} SET {col} = LOWER({col})")
        cur.execute(f"DROP TYPE IF EXISTS {enum_name}")
        cur.execute(f"CREATE TYPE {enum_name} AS ENUM {values}")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {enum_name} USING {col}::{enum_name}")
        if default_val:
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT '{default_val}'::{enum_name}")
        print(f"  ✓ {enum_name} fixed")
    except Exception as e:
        print(f"  ~ {enum_name} skipped: {e}")

conn.close()
print("\nAll enum fixes complete.")