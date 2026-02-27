# -*- coding: utf-8 -*-
import sqlite3, os, sys, datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

db = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'zhihu_crawler', 'data', 'zhihu.db'))

if not os.path.exists(db):
    print(f"ERROR: DB not found at {db}")
    sys.exit(1)

conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
c = conn.cursor()

print("=" * 60)
print("  ZHIHU BROWSER CRAWLER STATUS REPORT")
print(f"  Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# 1. Progress summary
print("\n--- Progress (browser_crawl_progress) ---")
try:
    rows = c.execute('SELECT status, COUNT(*), COALESCE(SUM(comments_found),0) FROM browser_crawl_progress GROUP BY status').fetchall()
    total_answers = 0
    total_comments = 0
    for r in rows:
        print(f"  {r[0]:10s}: {r[1]:4d} answers, {r[2]:6d} comments found")
        total_answers += r[1]
        total_comments += r[2]
    print(f"  {'TOTAL':10s}: {total_answers:4d} answers, {total_comments:6d} comments found")
except Exception as e:
    print(f"  Error: {e}")

# 2. Currently crawling
print("\n--- Currently Crawling ---")
try:
    crawling = c.execute("SELECT answer_id, started_at FROM browser_crawl_progress WHERE status='crawling'").fetchall()
    if crawling:
        for cr in crawling:
            print(f"  answer_id={cr[0]}, started_at={cr[1]}")
    else:
        print("  (none)")
except Exception as e:
    print(f"  Error: {e}")

# 3. Recent activity (last 10)
print("\n--- Recent Activity (last 10) ---")
try:
    recent = c.execute("SELECT answer_id, status, comments_found, started_at, finished_at FROM browser_crawl_progress ORDER BY rowid DESC LIMIT 10").fetchall()
    for r in recent:
        print(f"  {r[0]} | {r[1]:8s} | {r[2]:4d} comments | {r[3]} -> {r[4]}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Scroll bottom log
print("\n--- Scroll Bottom Log (world's end) ---")
try:
    bl = c.execute('SELECT COUNT(*) FROM scroll_bottom_log').fetchone()[0]
    print(f"  Total entries: {bl}")
except Exception as e:
    print(f"  Error: {e}")

# 5. Thread tracking summary
print("\n--- Thread Tracking ---")
try:
    tt = c.execute('SELECT thread_type, COUNT(*), SUM(expected_replies), SUM(actual_replies) FROM thread_tracking GROUP BY thread_type').fetchall()
    for t in tt:
        diff = t[2] - t[3]
        print(f"  {t[0]}: {t[1]} threads, expected={t[2]}, actual={t[3]}, gap={diff}")
except Exception as e:
    print(f"  Error: {e}")

# 6. Gap summary
print("\n--- Comment Gap Summary ---")
try:
    gap = c.execute('''
        SELECT COUNT(*),
               SUM(CASE WHEN a.comment_count > COALESCE(cc.cnt,0) AND (a.comment_count - COALESCE(cc.cnt,0)) >= 1 THEN 1 ELSE 0 END),
               SUM(a.comment_count), SUM(COALESCE(cc.cnt,0))
        FROM answers a
        LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc ON a.id = cc.answer_id
    ''').fetchone()
    missing = gap[2] - gap[3]
    print(f"  Total answers: {gap[0]}")
    print(f"  Answers with gap>=1: {gap[1]}")
    print(f"  Expected comments: {gap[2]}")
    print(f"  Actual comments: {gap[3]}")
    print(f"  Total missing: {missing}")
    if gap[2] > 0:
        coverage = gap[3] / gap[2] * 100
        print(f"  Coverage: {coverage:.1f}%")
except Exception as e:
    print(f"  Error: {e}")

# 7. Pending (not started)
print("\n--- Pending (not started, gap>=1) ---")
try:
    pending = c.execute('''
        SELECT COUNT(*)
        FROM answers a
        LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc ON a.id = cc.answer_id
        LEFT JOIN browser_crawl_progress bp ON a.id = bp.answer_id
        WHERE a.comment_count > COALESCE(cc.cnt,0)
          AND (a.comment_count - COALESCE(cc.cnt,0)) >= 1
          AND bp.answer_id IS NULL
    ''').fetchone()
    print(f"  Pending answers: {pending[0]}")
except Exception as e:
    print(f"  Error: {e}")

# 8. Browser vs API comment counts
print("\n--- Comments by Source ---")
try:
    src = c.execute("SELECT COALESCE(source,'api'), COUNT(*) FROM comments GROUP BY COALESCE(source,'api')").fetchall()
    for s in src:
        print(f"  {s[0]:10s}: {s[1]:6d}")
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "=" * 60)
conn.close()
