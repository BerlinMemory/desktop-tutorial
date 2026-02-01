"""
数据库操作模块
封装 SQLite 数据库的所有操作
"""
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import os


class Database:
    """SQLite 数据库操作类"""

    def __init__(self, db_path: str = "data/zhihu.db"):
        """初始化数据库连接"""
        self.db_path = db_path
        # 确保数据目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # 返回字典格式
        self.init_tables()

    def init_tables(self):
        """初始化数据库表"""
        cursor = self.conn.cursor()

        # 创建问题表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT,
            keyword TEXT,
            answer_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 创建回答表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            author_name TEXT,
            author_id TEXT,
            content TEXT,
            voteup_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            created_time TEXT,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (question_id) REFERENCES questions(id)
        )
        """)

        # 创建评论表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            answer_id TEXT NOT NULL,
            parent_id TEXT,
            is_child INTEGER DEFAULT 0,
            author_name TEXT,
            content TEXT,
            like_count INTEGER DEFAULT 0,
            reply_to TEXT,
            created_time TEXT,
            FOREIGN KEY (answer_id) REFERENCES answers(id)
        )
        """)

        # 创建索引以提升查询性能
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_questions_status
        ON questions(status)
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_answers_question_status
        ON answers(question_id, status)
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_answer
        ON comments(answer_id)
        """)

        self.conn.commit()

    # ==================== 问题相关操作 ====================

    def insert_question(self, question_id: str, title: str, url: str,
                       keyword: str, answer_count: int = 0) -> bool:
        """插入问题，如果已存在则忽略"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO questions
            (id, title, url, keyword, answer_count, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (question_id, title, url, keyword, answer_count,
                  datetime.now().isoformat()))
            self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            print(f"插入问题失败: {e}")
            return False

    def get_pending_questions(self, limit: Optional[int] = None) -> List[Dict]:
        """获取待处理的问题列表"""
        cursor = self.conn.cursor()
        query = "SELECT * FROM questions WHERE status = 'pending' ORDER BY created_at"
        if limit:
            query += f" LIMIT {limit}"
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def update_question_status(self, question_id: str, status: str):
        """更新问题状态"""
        cursor = self.conn.cursor()
        cursor.execute("""
        UPDATE questions SET status = ? WHERE id = ?
        """, (status, question_id))
        self.conn.commit()

    def get_question_stats(self) -> Dict:
        """获取问题统计信息"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM questions
        """)
        row = cursor.fetchone()
        return dict(row) if row else {}

    # ==================== 回答相关操作 ====================

    def insert_answer(self, answer_id: str, question_id: str,
                     author_name: str, author_id: str, content: str,
                     voteup_count: int, comment_count: int,
                     created_time: str) -> bool:
        """插入回答，如果已存在则忽略"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO answers
            (id, question_id, author_name, author_id, content,
             voteup_count, comment_count, created_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (answer_id, question_id, author_name, author_id, content,
                  voteup_count, comment_count, created_time))
            self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            print(f"插入回答失败: {e}")
            return False

    def get_pending_answers(self, limit: Optional[int] = None) -> List[Dict]:
        """获取待处理的回答列表"""
        cursor = self.conn.cursor()
        query = "SELECT * FROM answers WHERE status = 'pending'"
        if limit:
            query += f" LIMIT {limit}"
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def update_answer_status(self, answer_id: str, status: str):
        """更新回答状态"""
        cursor = self.conn.cursor()
        cursor.execute("""
        UPDATE answers SET status = ? WHERE id = ?
        """, (status, answer_id))
        self.conn.commit()

    def get_answer_stats(self) -> Dict:
        """获取回答统计信息"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM answers
        """)
        row = cursor.fetchone()
        return dict(row) if row else {}

    # ==================== 评论相关操作 ====================

    def insert_comment(self, comment_id: str, answer_id: str,
                      parent_id: Optional[str], is_child: int,
                      author_name: str, content: str, like_count: int,
                      reply_to: Optional[str], created_time: str) -> bool:
        """插入评论，如果已存在则忽略"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO comments
            (id, answer_id, parent_id, is_child, author_name,
             content, like_count, reply_to, created_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (comment_id, answer_id, parent_id, is_child, author_name,
                  content, like_count, reply_to, created_time))
            self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            print(f"插入评论失败: {e}")
            return False

    def get_comment_stats(self) -> Dict:
        """获取评论统计信息"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN is_child = 0 THEN 1 ELSE 0 END) as root_comments,
            SUM(CASE WHEN is_child = 1 THEN 1 ELSE 0 END) as child_comments
        FROM comments
        """)
        row = cursor.fetchone()
        return dict(row) if row else {}

    # ==================== 导出相关操作 ====================

    def get_all_data_for_export(self) -> List[Dict]:
        """获取所有数据用于导出（联表查询）"""
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT
            q.id as question_id,
            q.title as question_title,
            q.url as question_url,
            q.keyword as keyword,
            a.id as answer_id,
            a.author_name as answer_author,
            a.content as answer_content,
            a.voteup_count as answer_voteup,
            a.created_time as answer_time,
            c.id as comment_id,
            c.author_name as comment_author,
            c.content as comment_content,
            c.like_count as comment_likes,
            c.is_child as is_child_comment,
            c.reply_to as reply_to,
            c.created_time as comment_time
        FROM questions q
        LEFT JOIN answers a ON q.id = a.question_id
        LEFT JOIN comments c ON a.id = c.answer_id
        ORDER BY q.id, a.id, c.is_child, c.created_time
        """)
        return [dict(row) for row in cursor.fetchall()]

    # ==================== 工具方法 ====================

    def reset_failed_to_pending(self):
        """重置失败状态为待处理（用于重试）"""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE questions SET status = 'pending' WHERE status = 'failed'")
        cursor.execute("UPDATE answers SET status = 'pending' WHERE status = 'failed'")
        self.conn.commit()
        print("已将所有失败项重置为待处理状态")

    def get_overall_stats(self) -> Dict:
        """获取整体统计信息"""
        return {
            'questions': self.get_question_stats(),
            'answers': self.get_answer_stats(),
            'comments': self.get_comment_stats()
        }

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        """支持 with 语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出时自动关闭连接"""
        self.close()


if __name__ == "__main__":
    # 测试代码
    db = Database("data/zhihu.db")
    print("数据库初始化成功")
    print("统计信息:", db.get_overall_stats())
    db.close()
