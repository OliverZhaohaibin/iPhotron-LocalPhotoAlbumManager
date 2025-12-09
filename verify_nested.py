import sqlite3
import os

db_path = "test_nested.db"
if os.path.exists(db_path):
    os.remove(db_path)

conn = sqlite3.connect(db_path)
conn.execute("CREATE TABLE t (id INTEGER)")

print("Starting outer")
try:
    with conn:
        print("Inside outer")
        conn.execute("INSERT INTO t VALUES (1)")
        print("Starting inner")
        with conn:
            print("Inside inner")
            conn.execute("INSERT INTO t VALUES (2)")
        print("Finished inner")
    print("Finished outer")
except Exception as e:
    print(f"Caught exception: {type(e).__name__}: {e}")

conn.close()
