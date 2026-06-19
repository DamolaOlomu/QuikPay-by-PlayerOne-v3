import sqlite3

db_path = r"C:\Users\damol\Downloads\QuikPay by PlayerOne v3\QuikPay-by-PlayerOne-main\playeronepay_dev.db"

conn = sqlite3.connect(db_path)

tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", tables)

rows = conn.execute("SELECT * FROM users").fetchall()
print("Users:", rows)

conn.close()