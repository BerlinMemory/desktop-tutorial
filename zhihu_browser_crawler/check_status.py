# -*- coding: utf-8 -*-
import sqlite3, sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

db = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'zhihu_crawler', 'data', 'zhihu.db'))
conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=10)
c = conn.cursor()

# Progress summary
rows = c.execute('SELECT status, COUNT(*), COALESCE(SUM(comments_found),0) FROM browser_crawl_progress GROUP BY status').fetchall()
print('=== PROGRESS ===')
for r in rows:
    print(f'  {r[0]}: {r[1]} answers, {r[2]} comments')

# Recent activity
recent = c.execute("SELECT answer_id, status, comments_found, started_at, finished_at FROM browser_crawl_progress ORDER BY rowid DESC LIMIT 5").fetchall()
print('\nRecent:')
for r in recent:
    print(f'  {r[0]} | {r[1]} | {r[2]} comments | {r[3]} -> {r[4]}')

# Currently crawling
crawling = c.execute("SELECT answer_id, started_at FROM browser_crawl_progress WHERE status='crawling'").fetchall()
if crawling:
    print(f'\nCurrently crawling: {crawling[0][0]} since {crawling[0][1]}')

# Scroll bottom log count
bl = c.execute('SELECT COUNT(*) FROM scroll_bottom_log').fetchone()[0]
print(f'\nScroll bottom log: {bl} entries')

# Gap summary
gap = c.execute('''
    SELECT COUNT(*),
           SUM(CASE WHEN a.comment_count > COALESCE(cc.cnt,0) AND (a.comment_count - COALESCE(cc.cnt,0)) >= 1 THEN 1 ELSE 0 END),
           SUM(a.comment_count), SUM(COALESCE(cc.cnt,0))
    FROM answers a
    LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc ON a.id = cc.answer_id
''').fetchone()
print(f'\nTotal answers: {gap[0]}, With gap>=1: {gap[1]}, Expected: {gap[2]}, Actual: {gap[3]}, Missing: {gap[2]-gap[3]}')

# Pending (not in progress table, but has gap)
pending = c.execute('''
    SELECT COUNT(*)
    FROM answers a
    LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc ON a.id = cc.answer_id
    LEFT JOIN browser_crawl_progress bp ON a.id = bp.answer_id
    WHERE a.comment_count > COALESCE(cc.cnt,0)
      AND (a.comment_count - COALESCE(cc.cnt,0)) >= 1
      AND bp.answer_id IS NULL
''').fetchone()
print(f'Pending (not started): {pending[0]}')

conn.close()
