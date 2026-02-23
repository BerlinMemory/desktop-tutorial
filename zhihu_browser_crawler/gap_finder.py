"""
缺口查询模块
查询数据库，找出 API 评论爬取不完整的回答
"""
import sqlite3
from typing import List, Dict, Optional


class GapFinder:
    """查询评论缺口"""

    def __init__(self, db_path: str):
        """
        初始化
        :param db_path: SQLite 数据库路径
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def find_gaps(self, min_gap: int = 50, limit: Optional[int] = None) -> List[Dict]:
        """
        查找评论缺口大于 min_gap 的回答
        :param min_gap: 最小缺口阈值
        :param limit: 最多返回多少条（None=不限）
        :return: [{answer_id, question_id, expected, actual, gap}, ...]
        """
        sql = '''
            SELECT
                a.id          AS answer_id,
                a.question_id AS question_id,
                a.comment_count AS expected,
                COALESCE(cnt.actual, 0) AS actual,
                a.comment_count - COALESCE(cnt.actual, 0) AS gap
            FROM answers a
            LEFT JOIN (
                SELECT answer_id, COUNT(*) AS actual
                FROM comments
                GROUP BY answer_id
            ) cnt ON a.id = cnt.answer_id
            WHERE a.comment_count - COALESCE(cnt.actual, 0) > ?
            ORDER BY gap DESC
        '''
        if limit:
            sql += f' LIMIT {int(limit)}'

        cursor = self.conn.cursor()
        cursor.execute(sql, (min_gap,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def find_gap_for_answer(self, answer_id: str) -> Optional[Dict]:
        """
        查看单个回答的缺口情况
        :param answer_id: 回答 ID
        :return: {answer_id, question_id, expected, actual, gap} 或 None
        """
        sql = '''
            SELECT
                a.id          AS answer_id,
                a.question_id AS question_id,
                a.comment_count AS expected,
                COALESCE(cnt.actual, 0) AS actual,
                a.comment_count - COALESCE(cnt.actual, 0) AS gap
            FROM answers a
            LEFT JOIN (
                SELECT answer_id, COUNT(*) AS actual
                FROM comments
                GROUP BY answer_id
            ) cnt ON a.id = cnt.answer_id
            WHERE a.id = ?
        '''
        cursor = self.conn.cursor()
        cursor.execute(sql, (answer_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_summary(self) -> Dict:
        """获取缺口统计摘要"""
        cursor = self.conn.cursor()

        # 总体统计
        cursor.execute('SELECT COUNT(*) FROM answers')
        total_answers = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM comments')
        total_comments = cursor.fetchone()[0]

        cursor.execute('SELECT SUM(comment_count) FROM answers')
        expected_comments = cursor.fetchone()[0] or 0

        # 各档位的缺口分布
        cursor.execute('''
            SELECT
                CASE
                    WHEN gap <= 10  THEN '1-10'
                    WHEN gap <= 50  THEN '11-50'
                    WHEN gap <= 100 THEN '51-100'
                    WHEN gap <= 500 THEN '101-500'
                    ELSE '500+'
                END AS bucket,
                COUNT(*)  AS answer_count,
                SUM(gap)  AS total_gap
            FROM (
                SELECT a.comment_count - COALESCE(cnt.actual, 0) AS gap
                FROM answers a
                LEFT JOIN (
                    SELECT answer_id, COUNT(*) AS actual
                    FROM comments GROUP BY answer_id
                ) cnt ON a.id = cnt.answer_id
                WHERE a.comment_count - COALESCE(cnt.actual, 0) > 0
            )
            GROUP BY bucket
            ORDER BY MIN(gap)
        ''')
        distribution = []
        for row in cursor.fetchall():
            distribution.append({
                'range': row[0],
                'answer_count': row[1],
                'total_gap': row[2],
            })

        return {
            'total_answers': total_answers,
            'total_comments_collected': total_comments,
            'total_comments_expected': expected_comments,
            'missing': expected_comments - total_comments,
            'distribution': distribution,
        }

    def close(self):
        """关闭连接"""
        self.conn.close()


if __name__ == '__main__':
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else '../zhihu_crawler/data/zhihu.db'
    gf = GapFinder(db)
    summary = gf.get_summary()
    print(f"总回答: {summary['total_answers']}")
    print(f"已采集评论: {summary['total_comments_collected']}")
    print(f"期望评论: {summary['total_comments_expected']}")
    print(f"缺失: {summary['missing']}")
    print("\n分布:")
    for d in summary['distribution']:
        print(f"  {d['range']:<10} {d['answer_count']:>5} 个回答, 共缺 {d['total_gap']:>8} 条")
    gf.close()
