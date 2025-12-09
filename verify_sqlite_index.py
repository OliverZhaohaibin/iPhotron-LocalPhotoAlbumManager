import sqlite3

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE t (id INTEGER, dt TEXT)")
conn.execute("INSERT INTO t VALUES (1, '2023-01-01')")
conn.execute("INSERT INTO t VALUES (2, NULL)")
conn.execute("INSERT INTO t VALUES (3, '2024-01-01')")

conn.execute("CREATE INDEX idx_dt_desc ON t (dt DESC)")

print("--- EXPLAIN QUERY PLAN (ORDER BY dt DESC) ---")
for row in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM t ORDER BY dt DESC"):
    print(row)

print("\n--- EXPLAIN QUERY PLAN (ORDER BY dt DESC NULLS LAST) ---")
for row in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM t ORDER BY dt DESC NULLS LAST"):
    print(row)

conn.close()
