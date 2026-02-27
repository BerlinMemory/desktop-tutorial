"""清理数据库中的重复评论：
1. 删除所有 b_ 开头的 browser 测试评论
2. 删除 API 评论中的内容重复
"""
import sqlite3, os, sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'zhihu_crawler', 'data', 'zhihu.db'))
print('DB:', db_path)

conn = sqlite3.connect(db_path)

# 清理前统计
total_before = conn.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
browser_before = conn.execute("SELECT COUNT(*) FROM comments WHERE id LIKE 'b!_%' ESCAPE '!'").fetchone()[0]
print(f'清理前: 总计 {total_before} 条, 其中 browser {browser_before} 条')

# 1. 删除所有 browser 测试评论 (b_ 开头)
conn.execute("DELETE FROM comments WHERE id LIKE 'b!_%' ESCAPE '!'")
deleted_browser = conn.total_changes
print(f'删除了 {browser_before} 条 browser 测试评论')

# 2. 删除 API 评论中的内容重复（保留 ROWID 最小的那条）
conn.execute("""
    DELETE FROM comments WHERE rowid NOT IN (
        SELECT MIN(rowid) FROM comments
        GROUP BY answer_id, author_name, content
    )
""")
# 计算删了多少
total_after = conn.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
deleted_api_dups = total_before - browser_before - total_after
print(f'删除了 {deleted_api_dups} 条 API 内容重复评论')

conn.commit()

# 验证
remaining_dups = conn.execute("""
    SELECT COALESCE(SUM(c-1), 0) FROM (
        SELECT COUNT(*) as c FROM comments 
        GROUP BY answer_id, author_name, content 
        HAVING c > 1
    )
""").fetchone()[0]
print(f'\n清理后: 总计 {total_after} 条, 剩余重复 {remaining_dups} 条')

# 同时清理 crawl_progress 中的 browser 标记
conn.execute("DELETE FROM crawl_progress WHERE status LIKE '%browser%'")
conn.commit()

conn.close()
print('Done')
