import psycopg2

conn = psycopg2.connect('postgresql://postgres.njdjhtonlhyqozngnbtm:L8ET5IkbSMz03wYO@aws-0-eu-west-1.pooler.supabase.com:6543/postgres')
conn.autocommit = True
cur = conn.cursor()

# Drop defaults first
cur.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
cur.execute("ALTER TABLE users ALTER COLUMN status DROP DEFAULT")
print('defaults dropped')

# Convert columns
cur.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::userrole")
cur.execute("ALTER TABLE users ALTER COLUMN status TYPE userstatus USING status::userstatus")
print('columns converted')

# Restore defaults
cur.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'CUSTOMER'::userrole")
cur.execute("ALTER TABLE users ALTER COLUMN status SET DEFAULT 'PENDING_VERIFICATION'::userstatus")
print('defaults restored')

conn.close()
print('done')