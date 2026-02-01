"""
爬虫核心逻辑模块
实现搜索、回答、评论的爬取
"""
import re
import time
from typing import Dict, Optional, List
from datetime import datetime
from database import Database
from http_client import ZhihuAPI, ZhihuHTTPClient


class ZhihuCrawler:
    """知乎爬虫主类"""

    def __init__(self, config: Dict):
        """
        初始化爬虫
        :param config: 配置字典
        """
        self.config = config
        self.db = Database(config.get('database_path', 'data/zhihu.db'))

        # 初始化 HTTP 客户端
        rate_config = config.get('rate_limit', {})
        self.http_client = ZhihuHTTPClient(
            cookie=config.get('cookie', ''),
            requests_per_second=rate_config.get('requests_per_second', 1.5),
            retry_times=rate_config.get('retry_times', 3),
            retry_backoff=rate_config.get('retry_backoff', 2)
        )
        self.api = ZhihuAPI(self.http_client)

        # 爬取限制配置
        limits = config.get('limits', {})
        self.questions_per_keyword = limits.get('questions_per_keyword', 20)
        self.answers_per_question = limits.get('answers_per_question', None)
        self.comments_per_answer = limits.get('comments_per_answer', None)

    def clean_html(self, text: str) -> str:
        """
        清理 HTML 标签，提取纯文本
        :param text: HTML 文本
        :return: 纯文本
        """
        if not text:
            return ""
        # 移除 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)
        # 转换 HTML 实体
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&amp;', '&').replace('&quot;', '"')
        text = text.replace('&nbsp;', ' ')
        # 移除多余空白
        text = ' '.join(text.split())
        return text

    def format_timestamp(self, timestamp: int) -> str:
        """
        格式化时间戳
        :param timestamp: Unix 时间戳
        :return: ISO 格式时间字符串
        """
        try:
            return datetime.fromtimestamp(timestamp).isoformat()
        except:
            return ""

    # ==================== 搜索阶段 ====================

    def crawl_search(self, keywords: List[str]):
        """
        搜索问题并存入数据库
        :param keywords: 关键词列表
        """
        print("\n========== 开始搜索阶段 ==========")
        for keyword in keywords:
            print(f"\n搜索关键词: {keyword}")
            self._search_questions_by_keyword(keyword)

        stats = self.db.get_question_stats()
        print(f"\n搜索完成，共找到 {stats.get('total', 0)} 个问题")

    def _search_questions_by_keyword(self, keyword: str):
        """
        按关键词搜索问题
        :param keyword: 搜索关键词
        """
        offset = 0
        limit = 20
        collected = 0

        while collected < self.questions_per_keyword:
            print(f"  正在获取 offset={offset}")
            data = self.api.search_questions(keyword, offset=offset, limit=limit)

            if not data or 'data' not in data:
                print(f"  搜索失败或无更多结果")
                break

            items = data['data']
            if not items:
                print(f"  无更多结果")
                break

            for item in items:
                # 只处理问题类型
                if item.get('type') != 'search_result':
                    continue

                obj = item.get('object', {})
                if obj.get('type') != 'question':
                    continue

                question_id = str(obj.get('id', ''))
                title = obj.get('title', '')
                url = f"https://www.zhihu.com/question/{question_id}"
                answer_count = obj.get('answer_count', 0)

                if self.db.insert_question(question_id, title, url, keyword, answer_count):
                    collected += 1
                    print(f"    ✓ 问题: {title[:50]}... (ID: {question_id})")

                if collected >= self.questions_per_keyword:
                    break

            # 检查是否还有下一页
            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        print(f"  关键词 '{keyword}' 共收集 {collected} 个问题")

    # ==================== 回答阶段 ====================

    def crawl_answers(self):
        """爬取所有待处理问题的回答"""
        print("\n========== 开始回答阶段 ==========")
        pending_questions = self.db.get_pending_questions()
        total = len(pending_questions)

        print(f"待处理问题数: {total}")

        for idx, question in enumerate(pending_questions, 1):
            question_id = question['id']
            title = question['title']
            print(f"\n[{idx}/{total}] 处理问题: {title[:50]}...")

            try:
                self._crawl_answers_for_question(question_id)
                self.db.update_question_status(question_id, 'done')
                print(f"  ✓ 问题处理完成")
            except Exception as e:
                print(f"  ✗ 问题处理失败: {e}")
                self.db.update_question_status(question_id, 'failed')

        stats = self.db.get_answer_stats()
        print(f"\n回答阶段完成，共收集 {stats.get('total', 0)} 条回答")

    def _crawl_answers_for_question(self, question_id: str):
        """
        爬取指定问题的所有回答
        :param question_id: 问题 ID
        """
        offset = 0
        limit = 20
        collected = 0

        while True:
            # 检查是否达到限制
            if self.answers_per_question and collected >= self.answers_per_question:
                print(f"  达到回答数限制 ({self.answers_per_question})")
                break

            print(f"  获取回答 offset={offset}")
            data = self.api.get_question_answers(question_id, offset=offset, limit=limit)

            if not data or 'data' not in data:
                print(f"  获取回答失败")
                break

            answers = data['data']
            if not answers:
                print(f"  无更多回答")
                break

            for answer in answers:
                answer_id = str(answer.get('id', ''))
                author = answer.get('author', {})
                author_name = author.get('name', '匿名用户')
                author_id = author.get('id', '')
                content = self.clean_html(answer.get('content', ''))
                voteup_count = answer.get('voteup_count', 0)
                comment_count = answer.get('comment_count', 0)
                created_time = self.format_timestamp(answer.get('created_time', 0))

                if self.db.insert_answer(
                    answer_id, question_id, author_name, author_id,
                    content, voteup_count, comment_count, created_time
                ):
                    collected += 1
                    print(f"    ✓ 回答 by {author_name} (赞:{voteup_count}, 评论:{comment_count})")

            # 检查是否还有下一页
            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        print(f"  共收集 {collected} 条回答")

    # ==================== 评论阶段 ====================

    def crawl_comments(self):
        """爬取所有待处理回答的评论"""
        print("\n========== 开始评论阶段 ==========")
        pending_answers = self.db.get_pending_answers()
        total = len(pending_answers)

        print(f"待处理回答数: {total}")

        for idx, answer in enumerate(pending_answers, 1):
            answer_id = answer['id']
            author_name = answer['author_name']
            print(f"\n[{idx}/{total}] 处理回答: {author_name} 的回答 (ID: {answer_id})")

            try:
                self._crawl_comments_for_answer(answer_id)
                self.db.update_answer_status(answer_id, 'done')
                print(f"  ✓ 回答处理完成")
            except Exception as e:
                print(f"  ✗ 回答处理失败: {e}")
                self.db.update_answer_status(answer_id, 'failed')

        stats = self.db.get_comment_stats()
        print(f"\n评论阶段完成，共收集 {stats.get('total', 0)} 条评论")

    def _crawl_comments_for_answer(self, answer_id: str):
        """
        爬取指定回答的所有评论（含子评论）
        :param answer_id: 回答 ID
        """
        offset = 0
        limit = 20
        root_collected = 0

        while True:
            # 检查是否达到限制
            if self.comments_per_answer and root_collected >= self.comments_per_answer:
                print(f"  达到评论数限制 ({self.comments_per_answer})")
                break

            print(f"  获取主评论 offset={offset}")
            data = self.api.get_answer_root_comments(answer_id, offset=offset, limit=limit)

            if not data or 'data' not in data:
                print(f"  获取评论失败")
                break

            comments = data['data']
            if not comments:
                print(f"  无更多主评论")
                break

            for comment in comments:
                comment_id = str(comment.get('id', ''))
                author = comment.get('author', {})
                author_name = author.get('member', {}).get('name', '匿名用户')
                content = comment.get('content', '')
                like_count = comment.get('like_count', 0)
                created_time = self.format_timestamp(comment.get('created_time', 0))
                child_count = comment.get('child_comment_count', 0)

                # 插入主评论
                if self.db.insert_comment(
                    comment_id, answer_id, None, 0,
                    author_name, content, like_count, None, created_time
                ):
                    root_collected += 1
                    print(f"    ✓ 主评论 by {author_name} (赞:{like_count}, 子评论:{child_count})")

                # 爬取子评论（楼中楼）
                if child_count > 0:
                    self._crawl_child_comments(answer_id, comment_id, child_count)

            # 检查是否还有下一页
            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        print(f"  共收集 {root_collected} 条主评论")

    def _crawl_child_comments(self, answer_id: str, parent_comment_id: str, child_count: int):
        """
        爬取子评论（楼中楼）
        :param answer_id: 回答 ID
        :param parent_comment_id: 父评论 ID
        :param child_count: 子评论数量
        """
        offset = 0
        limit = 20
        collected = 0

        while collected < child_count:
            data = self.api.get_comment_child_comments(
                parent_comment_id, offset=offset, limit=limit
            )

            if not data or 'data' not in data:
                break

            child_comments = data['data']
            if not child_comments:
                break

            for child in child_comments:
                child_id = str(child.get('id', ''))
                author = child.get('author', {})
                author_name = author.get('member', {}).get('name', '匿名用户')
                content = child.get('content', '')
                like_count = child.get('like_count', 0)
                reply_to_author = child.get('reply_to_author', {})
                reply_to = reply_to_author.get('member', {}).get('name', '')
                created_time = self.format_timestamp(child.get('created_time', 0))

                if self.db.insert_comment(
                    child_id, answer_id, parent_comment_id, 1,
                    author_name, content, like_count, reply_to, created_time
                ):
                    collected += 1
                    print(f"      ✓ 子评论 by {author_name} -> {reply_to}")

            # 检查是否还有下一页
            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

    # ==================== 完整爬取流程 ====================

    def run_full_crawl(self):
        """执行完整的爬取流程"""
        print("\n" + "="*60)
        print("知乎爬虫启动")
        print("="*60)

        # 获取关键词列表
        keywords = self.config.get('keywords', [])
        if not keywords:
            print("错误：未配置搜索关键词")
            return

        print(f"配置的关键词: {keywords}")
        print(f"限速设置: {self.config.get('rate_limit', {}).get('requests_per_second', 1.5)} req/s")

        # 阶段 1: 搜索问题
        self.crawl_search(keywords)

        # 阶段 2: 爬取回答
        self.crawl_answers()

        # 阶段 3: 爬取评论
        self.crawl_comments()

        # 打印最终统计
        print("\n" + "="*60)
        print("爬取完成！统计信息：")
        print("="*60)
        stats = self.db.get_overall_stats()
        print(f"问题: {stats['questions']}")
        print(f"回答: {stats['answers']}")
        print(f"评论: {stats['comments']}")

    def close(self):
        """关闭资源"""
        self.db.close()
        self.http_client.close()


if __name__ == "__main__":
    print("爬虫模块已加载")
    print("请通过 main.py 运行爬虫")
