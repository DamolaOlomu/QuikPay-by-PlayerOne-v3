import psycopg2

phone = "+2349034907321"

conn = psycopg2.connect(
    dbname="quikpay",
    user="postgres",
    password="playerone123",
    host="localhost",
    port="5432"
)

cur = conn.cursor()
cur.execute("UPDATE users SET role = 'admin', status = 'active' WHERE phone_number = %s", (phone,))
conn.commit()

cur.execute("SELECT id, fullname, role, status FROM users WHERE phone_number = %s", (phone,))
row = cur.fetchone()
print("Done:", row)

cur.close()
conn.close()