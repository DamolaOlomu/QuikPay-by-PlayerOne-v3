import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

# ── Fix userrole enum values (CUSTOMER → customer etc.) ──────────────────────
print("Fixing userrole...")
cur.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
cur.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(20)")
cur.execute("UPDATE users SET role = LOWER(role)")
cur.execute("DROP TYPE userrole")
cur.execute("CREATE TYPE userrole AS ENUM ('customer','agent','admin','super_admin')")
cur.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::userrole")
cur.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'customer'::userrole")
print("  ✓ userrole fixed")

# ── Fix userstatus enum values ────────────────────────────────────────────────
print("Fixing userstatus...")
cur.execute("ALTER TABLE users ALTER COLUMN status DROP DEFAULT")
cur.execute("ALTER TABLE users ALTER COLUMN status TYPE VARCHAR(30)")
cur.execute("UPDATE users SET status = LOWER(status)")
cur.execute("DROP TYPE userstatus")
cur.execute("CREATE TYPE userstatus AS ENUM ('active','inactive','suspended','pending_verification','banned','closed')")
cur.execute("ALTER TABLE users ALTER COLUMN status TYPE userstatus USING status::userstatus")
cur.execute("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'pending_verification'::userstatus")
print("  ✓ userstatus fixed")

conn.close()
print("\nDone. All enum values are now lowercase.")