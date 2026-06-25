import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

# Check current enum values
cur.execute("SELECT enum_range(NULL::keystatus)")
print("keystatus current:", cur.fetchone())

cur.execute("SELECT enum_range(NULL::keyenvironment)")
print("keyenvironment current:", cur.fetchone())

# Fix keystatus: drop default, convert to varchar, lowercase, recreate enum, convert back
print("\nFixing keystatus...")
cur.execute("ALTER TABLE api_keys ALTER COLUMN status DROP DEFAULT")
cur.execute("ALTER TABLE api_keys ALTER COLUMN status TYPE VARCHAR(20)")
cur.execute("UPDATE api_keys SET status = LOWER(status)")
cur.execute("DROP TYPE keystatus")
cur.execute("CREATE TYPE keystatus AS ENUM ('active', 'revoked', 'expired')")
cur.execute("ALTER TABLE api_keys ALTER COLUMN status TYPE keystatus USING status::keystatus")
cur.execute("ALTER TABLE api_keys ALTER COLUMN status SET DEFAULT 'active'::keystatus")
print("  ✓ keystatus fixed")

# Fix keyenvironment on api_keys
print("Fixing keyenvironment on api_keys...")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment DROP DEFAULT")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment TYPE VARCHAR(10)")
cur.execute("UPDATE api_keys SET environment = LOWER(environment)")
cur.execute("DROP TYPE keyenvironment")
cur.execute("CREATE TYPE keyenvironment AS ENUM ('test', 'live')")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment TYPE keyenvironment USING environment::keyenvironment")
cur.execute("ALTER TABLE api_keys ALTER COLUMN environment SET DEFAULT 'test'::keyenvironment")
print("  ✓ keyenvironment on api_keys fixed")

# Fix keyenvironment on request_logs if it exists
cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='request_logs' AND column_name='environment'")
if cur.fetchone():
    print("Fixing keyenvironment on request_logs...")
    cur.execute("ALTER TABLE request_logs ALTER COLUMN environment DROP DEFAULT")
    cur.execute("ALTER TABLE request_logs ALTER COLUMN environment TYPE VARCHAR(10)")
    cur.execute("UPDATE request_logs SET environment = LOWER(environment)")
    cur.execute("ALTER TABLE request_logs ALTER COLUMN environment TYPE keyenvironment USING environment::keyenvironment")
    cur.execute("ALTER TABLE request_logs ALTER COLUMN environment SET DEFAULT 'test'::keyenvironment")
    print("  ✓ keyenvironment on request_logs fixed")

# Fix ticket enums
for enum_name, table, col, default_val, values in [
    ("ticketstatus",   "support_tickets", "status",   "open",   "('open','in_progress','resolved','closed')"),
    ("ticketpriority", "support_tickets", "priority", "medium", "('low','medium','high','critical')"),
    ("ticketcategory", "support_tickets", "category", "other",  "('api_keys','transactions','webhooks','account','billing','bug','other')"),
]:
    cur.execute(f"SELECT 1 FROM information_schema.columns WHERE table_name='{table}' AND column_name='{col}'")
    if cur.fetchone():
        print(f"Fixing {enum_name}...")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE VARCHAR(30)")
        cur.execute(f"UPDATE {table} SET {col} = LOWER({col})")
        cur.execute(f"DROP TYPE IF EXISTS {enum_name}")
        cur.execute(f"CREATE TYPE {enum_name} AS ENUM {values}")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE {enum_name} USING {col}::{enum_name}")
        cur.execute(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT '{default_val}'::{enum_name}")
        print(f"  ✓ {enum_name} fixed")

conn.close()
print("\nAll enum fixes complete.")