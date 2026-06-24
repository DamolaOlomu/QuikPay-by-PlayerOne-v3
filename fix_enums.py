import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT 1 FROM pg_type WHERE typname='userrole'")
if not cur.fetchone():
    cur.execute("CREATE TYPE userrole AS ENUM ('CUSTOMER','AGENT','ADMIN','SUPER_ADMIN')")
    print('created userrole')
else:
    print('userrole already exists')

cur.execute("SELECT 1 FROM pg_type WHERE typname='userstatus'")
if not cur.fetchone():
    cur.execute("CREATE TYPE userstatus AS ENUM ('ACTIVE','INACTIVE','SUSPENDED','PENDING_VERIFICATION','BANNED')")
    print('created userstatus')
else:
    print('userstatus already exists')

cur.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::userrole")
cur.execute("ALTER TABLE users ALTER COLUMN status TYPE userstatus USING status::userstatus")
print('columns converted successfully')

conn.close()
print('done')