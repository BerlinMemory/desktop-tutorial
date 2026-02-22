"""
爬虫核心逻辑模块
实现搜索、回答、评论的爬取
支持多 Cookie 并行爬取
"""
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, List
from datetime import datetime
from database import Database
from http_client import ZhihuAPI, ZhihuHTTPClient


class CrawlWorker:
    """单个 Cookie 的爬取工作器"""

    def __init__(self, worker_id: int, cookie: str, config: Dict):
        """
        初始化工作器
        :param worker_id: 工作器编号
        :param cookie: 知乎 Cookie
        :param config: 配置字典
        """
        self.worker_id = worker_id
        self.cookie = cookie
        rate_config = config.get('rate_limit', {})
        self.http_client = ZhihuHTTPClient(
            cookie=cookie,
            requests_per_second=rate_config.get('requests_per_second', 1.5),
            retry_times=rate_config.get('retry_times', 3),
            retry_backoff=rate_config.get('retry_backoff', 2)
        )
        self.api = ZhihuAPI(self.http_client)

    def close(self):
        """关闭 HTTP 客户端"""
        self.http_client.close()

    @property
    def tag(self) -> str:
        """日志标签"""
        return f"[Worker-{self.worker_id}]"


class ZhihuCrawler:
    """知乎爬虫主类（支持多 Cookie 并行）"""

    def __init__(self, config: Dict):
        """
        初始化爬虫
        :param config: 配置字典
        """
        self.config = config
        self.db = Database(config.get('database_path', 'data/zhihu.db'))
        self._print_lock = threading.Lock()  # 保护 print 输出不混乱

        # 解析 Cookie 列表
        self.cookies = self._parse_cookies(config)

        # 为每个 Cookie 创建独立的工作器
        self.workers: List[CrawlWorker] = []
        for i, cookie in enumerate(self.cookies):
            self.workers.append(CrawlWorker(i + 1, cookie, config))

        # 并行配置
        parallel_config = config.get('parallel', {})
        self.max_workers = min(
            parallel_config.get('max_workers', len(self.cookies)),
            len(self.cookies)
        )

        # 爬取限制配置
        limits = config.get('limits', {})
        self.questions_per_keyword = limits.get('questions_per_keyword', 20)
        self.answers_per_question = limits.get('answers_per_question', None)
        self.comments_per_answer = limits.get('comments_per_answer', None)

    def _parse_cookies(self, config: Dict) -> List[str]:
        """
        解析 Cookie 配置（兼容单 Cookie 和多 Cookie 格式）
        :param config: 配置字典
        :return: Cookie 列表
        """
        # 优先使用 cookies 列表
        cookies = config.get('cookies', [])
        if cookies and isinstance(cookies, list):
            # 过滤掉空的和占位的
            valid = [c for c in cookies if c and c != 'your_cookie_here']
            if valid:
                return valid

        # 降级到单个 cookie
        single = config.get('cookie', '')
        if single and single != 'your_cookie_here':
            return [single]

        return ['']

    def _safe_print(self, *args, **kwargs):
        """线程安全的 print"""
        with self._print_lock:
            print(*args, **kwargs)

    def clean_html(self, text: str) -> str:
        """
        清理 HTML 标签，提取纯文本
        :param text: HTML 文本
        :return: 纯文本
        """
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&amp;', '&').replace('&quot;', '"')
        text = text.replace('&nbsp;', ' ')
        text = ' '.join(text.split())
        return text

    def format_timestamp(self, timestamp: int) -> str:
        """格式化时间戳"""
        try:
            return datetime.fromtimestamp(timestamp).isoformat()
        except:
            return ""

    # ==================== 预览模式 ====================

    def preview_search(self, keywords: List[str]):
        """
        预览搜索结果：只看问题数量和回答数，不爬取具体内容
        :param keywords: 关键词列表
        """
        print("\n" + "="*60)
        print("预览模式 — 搜索结果概览")
        print("="*60)

        worker = self.workers[0]
        all_results = []

        for keyword in keywords:
            print(f"\n>> \u641c\u7d22\u5173\u952e\u8bcd: {keyword}")
            questions = self._preview_keyword(worker, keyword)
            all_results.append((keyword, questions))

        # 打印汇总表（全部结果）
        print("\n" + "="*60)
        print("搜索结果汇总（全部）")
        print("="*60)
        grand_total_q = 0
        grand_total_a = 0

        for keyword, questions in all_results:
            total_q = len(questions)
            total_a = sum(q['answer_count'] for q in questions)
            grand_total_q += total_q
            grand_total_a += total_a
            print(f"\n  {keyword}: {total_q} ge, {total_a} answers (unfiltered)")

        print(f"\n  Total (unfiltered): {grand_total_q} questions, {grand_total_a} answers")

        # 过滤：只保留标题包含关键词的问题
        filtered_results = []
        for keyword, questions in all_results:
            filtered = [q for q in questions if keyword in q['title']]
            filtered_results.append((keyword, filtered))

        # 去重：合并多个关键词命中的同一问题
        dedup_map = {}  # question_id -> {question_data, keywords: set}
        for keyword, questions in filtered_results:
            for q in questions:
                qid = q['id']
                if qid in dedup_map:
                    dedup_map[qid]['keywords'].add(keyword)
                else:
                    dedup_map[qid] = {
                        'id': qid,
                        'title': q['title'],
                        'answer_count': q['answer_count'],
                        'keywords': {keyword}
                    }

        dedup_list = sorted(dedup_map.values(), key=lambda x: x['answer_count'], reverse=True)
        unique_q = len(dedup_list)
        unique_a = sum(q['answer_count'] for q in dedup_list)
        overlap = sum(1 for q in dedup_list if len(q['keywords']) > 1)

        print("\n" + "="*60)
        print("Filtered + Deduplicated")
        print("="*60)
        print(f"\n  Unique questions: {unique_q}")
        print(f"  Total answers:    {unique_a}")
        print(f"  Overlap (both keywords): {overlap}")

        for i, q in enumerate(dedup_list, 1):
            kws = '+'.join(sorted(q['keywords']))
            title = q['title'][:45] + ('...' if len(q['title']) > 45 else '')
            print(f"    {i}. [{q['answer_count']}] [{kws}] {title}")

        # 导出预览结果到 CSV
        import csv
        import os
        os.makedirs('data', exist_ok=True)

        # 全部结果
        csv_all = 'data/preview_all.csv'
        with open(csv_all, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['keyword', 'question_id', 'title', 'answer_count', 'url', 'contains_keyword'])
            for keyword, questions in all_results:
                for q in questions:
                    url = f"https://www.zhihu.com/question/{q['id']}"
                    contains = 'Y' if keyword in q['title'] else 'N'
                    writer.writerow([keyword, q['id'], q['title'], q['answer_count'], url, contains])

        # 过滤+去重后结果
        csv_filtered = 'data/preview_filtered.csv'
        with open(csv_filtered, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['question_id', 'title', 'answer_count', 'keywords', 'url'])
            for q in dedup_list:
                url = f"https://www.zhihu.com/question/{q['id']}"
                kws = '+'.join(sorted(q['keywords']))
                writer.writerow([q['id'], q['title'], q['answer_count'], kws, url])

        print(f"\n  Full results:     {csv_all}")
        print(f"  Filtered+dedup:   {csv_filtered}")

    def _preview_keyword(self, worker: CrawlWorker, keyword: str) -> List[Dict]:
        """按关键词预览搜索结果，返回问题列表"""
        offset = 0
        limit = 20
        collected = 0
        seen_ids = set()
        results = []

        max_q = self.questions_per_keyword if self.questions_per_keyword > 0 else 999999
        while collected < max_q:
            data = worker.api.search_questions(keyword, offset=offset, limit=limit)

            if not data or 'data' not in data:
                break

            items = data['data']
            if not items:
                break

            for item in items:
                item_type = item.get('type', '')

                question_id = None
                title = ''
                answer_count = 0

                if item_type == 'search_result':
                    obj = item.get('object', {})
                    question_info = obj.get('question', {})
                    question_id = str(question_info.get('id', '') or obj.get('question_id', ''))

                    if obj.get('type') == 'question':
                        question_id = str(obj.get('id', ''))
                        title = obj.get('title', '')
                        answer_count = obj.get('answer_count', 0)
                    elif question_id:
                        title = obj.get('title', '') or question_info.get('name', '')
                        title = self.clean_html(title)
                        answer_count = obj.get('answer_count', 0) or question_info.get('answer_count', 0)
                    else:
                        continue

                elif item_type == 'hot_timing':
                    content_items = item.get('object', {}).get('content_items', [])
                    for ci in content_items:
                        ci_obj = ci.get('object', {})
                        if ci_obj.get('type') == 'question':
                            qid = str(ci_obj.get('id', ''))
                            if qid and qid not in seen_ids:
                                seen_ids.add(qid)
                                results.append({
                                    'id': qid,
                                    'title': self.clean_html(ci_obj.get('title', '')),
                                    'answer_count': ci_obj.get('answer_count', 0)
                                })
                                collected += 1
                            if collected >= self.questions_per_keyword:
                                break
                    continue
                else:
                    continue

                if not question_id or question_id in seen_ids:
                    continue

                seen_ids.add(question_id)
                results.append({
                    'id': question_id,
                    'title': title,
                    'answer_count': answer_count
                })
                collected += 1

                if collected >= self.questions_per_keyword:
                    break

            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        return results

    # ==================== 搜索阶段（单线程） ====================

    def crawl_search(self, keywords: List[str]):
        """
        搜索问题并存入数据库（使用第一个 Cookie，单线程）
        只入库标题包含关键词的问题，过滤掉不相关结果
        :param keywords: 关键词列表
        """
        print("\n========== 开始搜索阶段 ==========")
        print("  (已启用标题过滤：只入库标题包含关键词的问题)")
        # 搜索阶段使用第一个 Worker
        worker = self.workers[0]

        for keyword in keywords:
            print(f"\n搜索关键词: {keyword}")
            self._search_questions_by_keyword(worker, keyword)

        stats = self.db.get_question_stats()
        print(f"\n搜索完成，共找到 {stats.get('total', 0)} 个问题")

    def _search_questions_by_keyword(self, worker: CrawlWorker, keyword: str):
        """按关键词搜索问题"""
        offset = 0
        limit = 20
        collected = 0
        seen_question_ids = set()

        skipped = 0
        max_q = self.questions_per_keyword if self.questions_per_keyword > 0 else 999999
        while collected < max_q:
            print(f"  正在获取 offset={offset}")
            data = worker.api.search_questions(keyword, offset=offset, limit=limit)

            if not data or 'data' not in data:
                print(f"  搜索失败或无更多结果")
                break

            items = data['data']
            if not items:
                print(f"  无更多结果")
                break

            for item in items:
                item_type = item.get('type', '')

                if item_type == 'search_result':
                    obj = item.get('object', {})
                    question_info = obj.get('question', {})
                    question_id = str(question_info.get('id', '') or obj.get('question_id', ''))

                    if obj.get('type') == 'question':
                        question_id = str(obj.get('id', ''))
                        title = obj.get('title', '')
                        answer_count = obj.get('answer_count', 0)
                    elif question_id:
                        title = obj.get('title', '') or question_info.get('name', '')
                        title = self.clean_html(title)
                        answer_count = obj.get('answer_count', 0) or question_info.get('answer_count', 0)
                    else:
                        continue

                elif item_type == 'hot_timing':
                    content_items = item.get('object', {}).get('content_items', [])
                    for ci in content_items:
                        ci_obj = ci.get('object', {})
                        if ci_obj.get('type') == 'question':
                            question_id = str(ci_obj.get('id', ''))
                            title = self.clean_html(ci_obj.get('title', ''))
                            answer_count = ci_obj.get('answer_count', 0)

                            if question_id and question_id not in seen_question_ids:
                                seen_question_ids.add(question_id)
                                if keyword not in title:
                                    skipped += 1
                                    continue
                                url = f"https://www.zhihu.com/question/{question_id}"
                                if self.db.insert_question(question_id, title, url, keyword, answer_count):
                                    collected += 1
                                    print(f"    + {title[:50]}... [{answer_count}]")
                                if collected >= max_q:
                                    break
                    continue
                else:
                    continue

                if not question_id or question_id in seen_question_ids:
                    continue

                seen_question_ids.add(question_id)

                # 标题过滤：跳过不包含关键词的问题
                if keyword not in title:
                    skipped += 1
                    continue

                url = f"https://www.zhihu.com/question/{question_id}"

                if self.db.insert_question(question_id, title, url, keyword, answer_count):
                    collected += 1
                    print(f"    + {title[:50]}... [{answer_count}]")

                if collected >= max_q:
                    break

            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        print(f"  关键词 '{keyword}': 入库 {collected} 个, 过滤 {skipped} 个不相关问题")

    # ==================== 回答阶段（多线程并行） ====================

    def crawl_answers(self):
        """爬取所有待处理问题的回答（多 Cookie 并行）"""
        print("\n========== 开始回答阶段 ==========")
        pending_count = len(self.db.get_pending_questions())
        print(f"待处理问题数: {pending_count}")

        if pending_count == 0:
            print("没有待处理的问题")
            return

        if len(self.workers) == 1:
            # 单 Cookie，走简单路径
            self._safe_print(f"单 Cookie 模式，顺序爬取")
            self._answer_worker_loop(self.workers[0])
        else:
            # 多 Cookie 并行
            self._safe_print(f"多 Cookie 并行模式，使用 {self.max_workers} 个线程")
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for worker in self.workers[:self.max_workers]:
                    future = executor.submit(self._answer_worker_loop, worker)
                    futures.append(future)

                # 等待所有线程完成
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self._safe_print(f"Worker 异常: {e}")

        stats = self.db.get_answer_stats()
        print(f"\n回答阶段完成，共收集 {stats.get('total', 0)} 条回答")

    def _answer_worker_loop(self, worker: CrawlWorker):
        """
        单个 Worker 的回答爬取循环
        不断从数据库领取待处理问题，直到没有更多任务
        """
        processed = 0
        while True:
            # 原子性地领取一个问题
            question = self.db.claim_pending_question()
            if not question:
                self._safe_print(f"  {worker.tag} 没有更多待处理问题，退出")
                break

            question_id = question['id']
            title = question['title']
            self._safe_print(f"  {worker.tag} 处理问题: {title[:50]}...")

            try:
                self._crawl_answers_for_question(worker, question_id)
                self.db.update_question_status(question_id, 'done')
                processed += 1
                self._safe_print(f"  {worker.tag} [OK] 问题处理完成")
            except Exception as e:
                self._safe_print(f"  {worker.tag} [FAIL] 问题处理失败: {e}")
                self.db.update_question_status(question_id, 'failed')

        self._safe_print(f"  {worker.tag} 共处理 {processed} 个问题")

    def _crawl_answers_for_question(self, worker: CrawlWorker, question_id: str):
        """
        爬取指定问题的所有回答
        :param worker: 工作器
        :param question_id: 问题 ID
        """
        offset = 0
        limit = 20
        collected = 0

        while True:
            if self.answers_per_question and collected >= self.answers_per_question:
                self._safe_print(f"  {worker.tag} 达到回答数限制 ({self.answers_per_question})")
                break

            data = worker.api.get_question_answers(question_id, offset=offset, limit=limit)

            if not data or 'data' not in data:
                break

            answers = data['data']
            if not answers:
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
                    self._safe_print(
                        f"    {worker.tag} [OK] 回答 by {author_name} "
                        f"(赞:{voteup_count}, 评论:{comment_count})"
                    )

            paging = data.get('paging', {})
            if not paging.get('is_end', True):
                offset += limit
            else:
                break

        self._safe_print(f"  {worker.tag} 共收集 {collected} 条回答")

    # ==================== 评论阶段（多线程并行） ====================

    def crawl_comments(self):
        """爬取所有待处理回答的评论（多 Cookie 并行）"""
        print("\n========== 开始评论阶段 ==========")
        pending_count = len(self.db.get_pending_answers())
        print(f"待处理回答数: {pending_count}")

        if pending_count == 0:
            print("没有待处理的回答")
            return

        if len(self.workers) == 1:
            self._safe_print(f"单 Cookie 模式，顺序爬取")
            self._comment_worker_loop(self.workers[0])
        else:
            self._safe_print(f"多 Cookie 并行模式，使用 {self.max_workers} 个线程")
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for worker in self.workers[:self.max_workers]:
                    future = executor.submit(self._comment_worker_loop, worker)
                    futures.append(future)

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self._safe_print(f"Worker 异常: {e}")

        stats = self.db.get_comment_stats()
        print(f"\n评论阶段完成，共收集 {stats.get('total', 0)} 条评论")

    def _comment_worker_loop(self, worker: CrawlWorker):
        """
        单个 Worker 的评论爬取循环
        不断从数据库领取待处理回答，直到没有更多任务
        """
        processed = 0
        while True:
            answer = self.db.claim_pending_answer()
            if not answer:
                self._safe_print(f"  {worker.tag} 没有更多待处理回答，退出")
                break

            answer_id = answer['id']
            author_name = answer['author_name']
            self._safe_print(
                f"  {worker.tag} 处理回答: {author_name} 的回答 (ID: {answer_id})"
            )

            try:
                self._crawl_comments_for_answer(worker, answer_id)
                self.db.update_answer_status(answer_id, 'done')
                processed += 1
                self._safe_print(f"  {worker.tag} [OK] 回答处理完成")
            except Exception as e:
                self._safe_print(f"  {worker.tag} [FAIL] 回答处理失败: {e}")
                self.db.update_answer_status(answer_id, 'failed')

        self._safe_print(f"  {worker.tag} 共处理 {processed} 条回答")

    def _crawl_comments_for_answer(self, worker: CrawlWorker, answer_id: str):
        """
        爬取指定回答的所有评论（含子评论）
        使用双排序策略突破知乎评论翻页限制：
        - 第一轮：order=normal（时间顺序，最多200-300条）
        - 第二轮：order=score（热度顺序，最多200-300条）
        - 数据库自动去重，理论覆盖率可达 ~400-600 条
        """
        total_collected = 0

        # 第一轮：按时间顺序爬取
        self._safe_print(f"  {worker.tag} [第1轮] order=normal (时间顺序)")
        round1_new = self._crawl_comments_one_pass(worker, answer_id, 'normal', total_collected)
        total_collected += round1_new

        # 第二轮：按热度顺序爬取
        self._safe_print(f"  {worker.tag} [第2轮] order=score (热度顺序)")
        round2_new = self._crawl_comments_one_pass(worker, answer_id, 'score', total_collected)
        total_collected += round2_new

        self._safe_print(
            f"  {worker.tag} 双排序完成：第1轮 {round1_new} 条，第2轮 {round2_new} 条，"
            f"总计 {total_collected} 条（去重后）"
        )

    def _crawl_comments_one_pass(self, worker: CrawlWorker, answer_id: str,
                                  order: str, already_collected: int) -> int:
        """
        单排序策略爬取评论（一轮）
        :param worker: 工作器
        :param answer_id: 回答ID
        :param order: 排序方式 (normal/score)
        :param already_collected: 已收集的评论数（用于判断是否达到限制）
        :return: 本轮新增的评论数
        """
        offset = 0
        limit = 20
        new_count = 0

        try:
            while True:
                # 检查是否达到评论数限制
                if self.comments_per_answer and (already_collected + new_count) >= self.comments_per_answer:
                    self._safe_print(f"  {worker.tag} 达到评论数限制 ({self.comments_per_answer})")
                    break

                data = worker.api.get_answer_root_comments(answer_id, offset=offset, limit=limit, order=order)

                if data is None:  # Request failed
                    raise Exception(f"API请求失败(RootComments, order={order})")

                if 'data' not in data:
                    break

                comments = data['data']
                if not comments:
                    break

                for comment in comments:
                    comment_id = str(comment.get('id', ''))
                    author = comment.get('author', {})
                    author_name = author.get('member', {}).get('name', '匿名用户')
                    content = comment.get('content', '')
                    like_count = comment.get('like_count', 0)
                    created_time = self.format_timestamp(comment.get('created_time', 0))
                    child_count = comment.get('child_comment_count', 0)

                    # INSERT OR IGNORE：如果评论ID已存在（第1轮已爬过），则跳过
                    is_new = self.db.insert_comment(
                        comment_id, answer_id, None, 0,
                        author_name, content, like_count, None, created_time
                    )

                    if is_new:
                        new_count += 1
                        # 只为新评论爬取子评论，避免重复爬取
                        if child_count > 0:
                            self._crawl_child_comments(worker, answer_id, comment_id, child_count)

                paging = data.get('paging', {})
                if not paging.get('is_end', True):
                    offset += limit
                else:
                    break

            return new_count
        except Exception as e:
            self._safe_print(f"  {worker.tag} 抓取评论异常 (order={order}, ID={answer_id}): {e}")
            raise e

    def _crawl_child_comments(self, worker: CrawlWorker, answer_id: str,
                              parent_comment_id: str, child_count: int):
        """爬取子评论（楼中楼）"""
        offset = 0
        limit = 20
        collected = 0

        try:
            while collected < child_count:
                data = worker.api.get_comment_child_comments(
                    parent_comment_id, offset=offset, limit=limit
                )

                if data is None:
                    raise Exception("API请求失败(ChildComments)")

                if 'data' not in data:
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
                        # self._safe_print(
                        #     f"      {worker.tag} [OK] 子评论 by {author_name} -> {reply_to}"
                        # )

                paging = data.get('paging', {})
                if not paging.get('is_end', True):
                    offset += limit
                else:
                    break
        except Exception as e:
            self._safe_print(f"  {worker.tag} 抓取子评论异常 Parent {parent_comment_id}: {e}")
            raise e

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

        # 断点续爬：重置上次中断/失败的任务
        self.db.reset_failed_to_pending()

        print(f"配置的关键词: {keywords}")
        print(f"Cookie 数量: {len(self.cookies)}")
        if len(self.cookies) > 1:
            print(f"并行线程数: {self.max_workers}")
        rate = self.config.get('rate_limit', {}).get('requests_per_second', 1.5)
        print(f"限速设置: {rate} req/s (每个 Cookie 独立限速)")

        # 阶段 1: 搜索问题（单线程）
        self.crawl_search(keywords)

        # 阶段 2: 爬取回答（多线程并行）
        self.crawl_answers()

        # 阶段 3: 爬取评论（多线程并行）
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
        """关闭所有资源"""
        for worker in self.workers:
            worker.close()
        self.db.close()


if __name__ == "__main__":
    print("爬虫模块已加载")
    print("请通过 main.py 运行爬虫")
