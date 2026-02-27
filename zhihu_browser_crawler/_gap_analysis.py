# -*- coding: utf-8 -*-
"""Gap analysis: expected vs actual comments, with distribution"""
import sqlite3, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

db = os.path.join(os.path.dirname(__file__), '..', 'zhihu_crawler', 'data', 'zhihu.db')
c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)

# Overall stats
total_answers = c.execute('SELECT COUNT(*) FROM answers').fetchone()[0]
total_expected = c.execute('SELECT SUM(comment_count) FROM answers').fetchone()[0]
total_actual = c.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
api_cnt = c.execute("SELECT COUNT(*) FROM comments WHERE COALESCE(source,'api')='api'").fetchone()[0]
br_cnt = c.execute("SELECT COUNT(*) FROM comments WHERE source='browser'").fetchone()[0]

print("=" * 60)
print("  COMMENT GAP ANALYSIS")
print("=" * 60)
print(f"\n  Total answers:    {total_answers}")
print(f"  Expected total:   {total_expected}")
print(f"  Actual total:     {total_actual}")
print(f"  Overall gap:      {total_expected - total_actual}")
print(f"  Coverage:         {total_actual/total_expected*100:.1f}%")
print(f"  API comments:     {api_cnt}")
print(f"  Browser comments: {br_cnt}")

# Per-answer gap distribution
rows = c.execute('''
    SELECT a.id, a.comment_count as expected, COALESCE(cc.cnt,0) as actual,
           a.comment_count - COALESCE(cc.cnt,0) as gap
    FROM answers a
    LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc
        ON a.id = cc.answer_id
    ORDER BY gap DESC
''').fetchall()

# Categorize gaps
perfect = []      # gap = 0
small = []        # gap 1-5
medium = []       # gap 6-50
large = []        # gap 51-200
huge = []         # gap > 200
negative = []     # gap < 0 (actual > expected)

for r in rows:
    gap = r[3]
    if gap < 0:
        negative.append(r)
    elif gap == 0:
        perfect.append(r)
    elif gap <= 5:
        small.append(r)
    elif gap <= 50:
        medium.append(r)
    elif gap <= 200:
        large.append(r)
    else:
        huge.append(r)

print(f"\n--- Gap Distribution ({total_answers} answers) ---")
print(f"  {'Category':<25} {'Count':>6} {'% of total':>10}")
print(f"  {'-'*45}")
print(f"  {'gap = 0 (perfect)' :<25} {len(perfect):>6} {len(perfect)/total_answers*100:>9.1f}%")
print(f"  {'gap 1~5 (minor)' :<25} {len(small):>6} {len(small)/total_answers*100:>9.1f}%")
print(f"  {'gap 6~50 (moderate)' :<25} {len(medium):>6} {len(medium)/total_answers*100:>9.1f}%")
print(f"  {'gap 51~200 (large)' :<25} {len(large):>6} {len(large)/total_answers*100:>9.1f}%")
print(f"  {'gap > 200 (huge)' :<25} {len(huge):>6} {len(huge)/total_answers*100:>9.1f}%")
print(f"  {'gap < 0 (over-collected)':<25} {len(negative):>6} {len(negative)/total_answers*100:>9.1f}%")

# Browser crawl status breakdown
print(f"\n--- Browser Crawl Status ---")
statuses = c.execute('SELECT status, COUNT(*) FROM browser_crawl_progress GROUP BY status').fetchall()
for s in statuses:
    print(f"  {s[0]:<18}: {s[1]}")
not_crawled = total_answers - sum(s[1] for s in statuses)
print(f"  {'not_crawled':<18}: {not_crawled}")

# comments_closed answers gap contribution
closed_gap = c.execute('''
    SELECT SUM(a.comment_count - COALESCE(cc.cnt,0))
    FROM browser_crawl_progress bp
    JOIN answers a ON bp.answer_id = a.id
    LEFT JOIN (SELECT answer_id, COUNT(*) as cnt FROM comments GROUP BY answer_id) cc
        ON a.id = cc.answer_id
    WHERE bp.status = 'comments_closed'
''').fetchone()[0] or 0
print(f"\n  Gap from comments_closed answers: {closed_gap}")
print(f"  Gap from other causes:            {total_expected - total_actual - closed_gap}")

# Top 15 biggest gaps
print(f"\n--- Top 15 Biggest Gaps ---")
print(f"  {'answer_id':<20} {'expected':>8} {'actual':>8} {'gap':>6} {'coverage':>8}")
for r in rows[:15]:
    cov = f"{r[2]/r[1]*100:.0f}%" if r[1] > 0 else "N/A"
    print(f"  {r[0]:<20} {r[1]:>8} {r[2]:>8} {r[3]:>6} {cov:>8}")

# Gap by expected comment count ranges
print(f"\n--- Gap by Expected Comment Count ---")
ranges = [(0, 10), (11, 50), (51, 200), (201, 1000), (1001, 99999)]
labels = ['0~10', '11~50', '51~200', '201~1000', '1000+']
print(f"  {'Range':<12} {'Answers':>8} {'Expected':>10} {'Actual':>10} {'Gap':>8} {'Coverage':>8}")
for (lo, hi), label in zip(ranges, labels):
    subset = [r for r in rows if lo <= r[1] <= hi]
    if not subset:
        continue
    s_exp = sum(r[1] for r in subset)
    s_act = sum(r[2] for r in subset)
    s_gap = s_exp - s_act
    cov = f"{s_act/s_exp*100:.1f}%" if s_exp > 0 else "N/A"
    print(f"  {label:<12} {len(subset):>8} {s_exp:>10} {s_act:>10} {s_gap:>8} {cov:>8}")

c.close()
