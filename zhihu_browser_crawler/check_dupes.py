import sqlite3

conn = sqlite3.connect(r'C:\Users\55412\.gemini\antigravity\scratch\desktop-tutorial\zhihu_crawler\data\zhihu.db')
c = conn.cursor()

# 1. Check for b_ prefix IDs (browser-generated hash IDs)
c.execute("SELECT id, answer_id, content FROM comments WHERE id LIKE 'b_%' LIMIT 20")
rows = c.fetchall()
print(f"=== b_ prefix IDs: {len(rows)} ===")
for r in rows:
    content = (r[2] or '')[:60]
    print(f"  id={r[0]}, answer={r[1]}, content={content}")

# 2. Non-numeric IDs (any non-standard ID)
c.execute("SELECT id FROM comments WHERE id GLOB '*[^0-9]*' LIMIT 20")
non_num = c.fetchall()
print(f"\n=== Non-numeric IDs: {len(non_num)} ===")
for r in non_num:
    print(f"  id={r[0]}")

# 3. Same content + answer appearing multiple times (real duplicates)
c.execute("""
    SELECT content, answer_id, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
    FROM comments
    WHERE content IS NOT NULL AND content != ''
    GROUP BY content, answer_id
    HAVING cnt > 1
    ORDER BY cnt DESC
    LIMIT 15
""")
dupes = c.fetchall()
print(f"\n=== Duplicate content (same answer): {len(dupes)} pairs ===")
for d in dupes:
    ids = d[3][:80] if d[3] else ''
    print(f"  answer={d[1]}, count={d[2]}, ids=[{ids}]")
    print(f"    content: {d[0][:80]}")

# 4. Total
c.execute("SELECT COUNT(*) FROM comments")
print(f"\nTotal comments: {c.fetchone()[0]}")

conn.close()
