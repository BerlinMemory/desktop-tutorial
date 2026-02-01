"""
数据导出模块
将数据库中的数据导出为 CSV 格式
"""
import csv
import os
from datetime import datetime
from typing import List, Dict
from database import Database


class DataExporter:
    """数据导出类"""

    def __init__(self, db: Database, output_dir: str = "data/exports"):
        """
        初始化导出器
        :param db: 数据库实例
        :param output_dir: 输出目录
        """
        self.db = db
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def export_questions(self, filename: str = None) -> str:
        """
        导出问题数据到 CSV
        :param filename: 输出文件名，默认自动生成
        :return: 输出文件路径
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"questions_{timestamp}.csv"

        filepath = os.path.join(self.output_dir, filename)

        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM questions ORDER BY created_at")
        questions = [dict(row) for row in cursor.fetchall()]

        if not questions:
            print("无问题数据可导出")
            return ""

        # 定义 CSV 列
        fieldnames = ['id', 'title', 'url', 'keyword', 'answer_count', 'status', 'created_at']

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(questions)

        print(f"✓ 问题数据已导出: {filepath} ({len(questions)} 条)")
        return filepath

    def export_answers(self, filename: str = None) -> str:
        """
        导出回答数据到 CSV
        :param filename: 输出文件名，默认自动生成
        :return: 输出文件路径
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"answers_{timestamp}.csv"

        filepath = os.path.join(self.output_dir, filename)

        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM answers ORDER BY question_id, created_time")
        answers = [dict(row) for row in cursor.fetchall()]

        if not answers:
            print("无回答数据可导出")
            return ""

        fieldnames = ['id', 'question_id', 'author_name', 'author_id',
                     'content', 'voteup_count', 'comment_count', 'created_time', 'status']

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(answers)

        print(f"✓ 回答数据已导出: {filepath} ({len(answers)} 条)")
        return filepath

    def export_comments(self, filename: str = None) -> str:
        """
        导出评论数据到 CSV
        :param filename: 输出文件名，默认自动生成
        :return: 输出文件路径
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"comments_{timestamp}.csv"

        filepath = os.path.join(self.output_dir, filename)

        cursor = self.db.conn.cursor()
        cursor.execute("""
        SELECT * FROM comments
        ORDER BY answer_id, is_child, created_time
        """)
        comments = [dict(row) for row in cursor.fetchall()]

        if not comments:
            print("无评论数据可导出")
            return ""

        fieldnames = ['id', 'answer_id', 'parent_id', 'is_child',
                     'author_name', 'content', 'like_count', 'reply_to', 'created_time']

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(comments)

        print(f"✓ 评论数据已导出: {filepath} ({len(comments)} 条)")
        return filepath

    def export_full_data(self, filename: str = None) -> str:
        """
        导出完整数据（问题-回答-评论联表）到 CSV
        :param filename: 输出文件名，默认自动生成
        :return: 输出文件路径
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"zhihu_full_{timestamp}.csv"

        filepath = os.path.join(self.output_dir, filename)

        data = self.db.get_all_data_for_export()

        if not data:
            print("无数据可导出")
            return ""

        fieldnames = [
            'question_id', 'question_title', 'question_url', 'keyword',
            'answer_id', 'answer_author', 'answer_content', 'answer_voteup', 'answer_time',
            'comment_id', 'comment_author', 'comment_content', 'comment_likes',
            'is_child_comment', 'reply_to', 'comment_time'
        ]

        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        print(f"✓ 完整数据已导出: {filepath} ({len(data)} 条记录)")
        return filepath

    def export_all(self):
        """一次性导出所有数据到不同的 CSV 文件"""
        print("\n========== 开始导出数据 ==========")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.export_questions(f"questions_{timestamp}.csv")
        self.export_answers(f"answers_{timestamp}.csv")
        self.export_comments(f"comments_{timestamp}.csv")
        self.export_full_data(f"zhihu_full_{timestamp}.csv")

        print("\n所有数据导出完成！")


def main():
    """主函数：直接运行时执行导出"""
    import argparse

    parser = argparse.ArgumentParser(description='导出知乎爬虫数据到 CSV')
    parser.add_argument('--type', choices=['questions', 'answers', 'comments', 'full', 'all'],
                       default='all', help='导出类型')
    parser.add_argument('--output', '-o', help='输出文件名（可选）')
    parser.add_argument('--db', default='data/zhihu.db', help='数据库路径')
    parser.add_argument('--dir', default='data/exports', help='输出目录')

    args = parser.parse_args()

    # 检查数据库是否存在
    if not os.path.exists(args.db):
        print(f"错误：数据库文件不存在: {args.db}")
        print("请先运行爬虫程序")
        return

    with Database(args.db) as db:
        exporter = DataExporter(db, args.dir)

        if args.type == 'questions':
            exporter.export_questions(args.output)
        elif args.type == 'answers':
            exporter.export_answers(args.output)
        elif args.type == 'comments':
            exporter.export_comments(args.output)
        elif args.type == 'full':
            exporter.export_full_data(args.output)
        elif args.type == 'all':
            exporter.export_all()


if __name__ == "__main__":
    main()
