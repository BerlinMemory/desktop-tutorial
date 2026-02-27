"""清理 answer 131529927 的重复评论，然后重新测试"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect('../zhihu_crawler/data/zhihu.db')
c = conn.cursor()

answer_id = '131529927'

# 1. 统计当前状态
c.execute('SELECT COUNT(*) FROM comments WHERE answer_id=?', (answer_id,))
before = c.fetchone()[0]
print(f'清理前: {before} 条')

# 2. 删除该回答的所有浏览器爬取的评论 (id 以 'b_' 开头)
c.execute("DELETE FROM comments WHERE answer_id=? AND id LIKE 'b_%'", (answer_id,))
deleted = c.rowcount
print(f'删除了 {deleted} 条 browser 评论')

# 3. 保留的 API 爬取评论
c.execute('SELECT COUNT(*) FROM comments WHERE answer_id=?', (answer_id,))
after = c.fetchone()[0]
print(f'清理后: {after} 条 (API 爬取的)')

# 4. 重置进度
c.execute("DELETE FROM browser_crawl_progress WHERE answer_id=?", (answer_id,))
print(f'进度已重置')

conn.commit()
conn.close()
print('Done')
