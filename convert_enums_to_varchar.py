import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

conversions = [
    # (table, column, drop_default, new_type)
    ("api_keys",        "status",      True,  "VARCHAR(20)"),
    ("api_keys",        "environment", True,  "VARCHAR(10)"),
    ("request_logs",    "environment", True,  "VARCHAR(10)"),
    ("support_tickets", "status",      True,  "VARCHAR(20)"),
    ("support_tickets", "priority",    True,  "VARCHAR(20)"),
    ("support_tickets", "category",    True,  "VARCHAR(30)"),
    ("mock_virtual_accounts", "status",     True,  "VARCHAR(20)"),
    ("mock_ledger_entries",   "entry_type", False, "VARCHAR(20)"),
    ("mock_transfers",        "status",     True,  "VARCHAR(20)"),
    ("mock_webhook_outbox",   "status",     True,  "VARCHAR(20)"),
    ("mock_cards",            "status",     True,  "VARCHAR(20)"),
]

for table, col, drop_default, new_type in conversions:
    try:
        if drop_default:
            cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {new_type} USING {col}::text")
        print(f"  ✓ {table}.{col} → {new_type}")
    except Exception as e:
        print(f"  ~ {table}.{col} skipped: {e}")

conn.close()
print("\nDone.")