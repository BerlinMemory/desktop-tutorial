"""
Playwright 浏览器评论爬取核心模块
使用浏览器自动化补爬 API 无法获取的评论

DOM 结构参考（2026-02 知乎版本）：
- 评论按钮: button.ContentItem-action（回答级），text 含 "XX 条评论"
- 评论面板: [role="dialog"] 弹窗
- 评论内容: .CommentContent（唯一稳定 class）
- 评论包装: CommentContent 的 parentElement
- 作者: wrapper 内 a[href*="/people/"]
- 时间: wrapper 内 span 匹配 YYYY-MM-DD 或 X小时前等
- 点赞: wrapper 内 SVG class 含 Heart 的 button
- 根评论: css-jp43l4 类
- 子评论: css-1kwt8l8 类
"""
import asyncio
import os
import random
import sqlite3
import sys
import time
from typing import Dict, List, Optional

# Windows 控制台 UTF-8 兼容
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
# 尝试设置控制台代码页 (仅 Windows)
try:
    if sys.platform == 'win32':
        os.system('chcp 65001 >nul 2>&1')
except Exception:
    pass


def safe_str(text: str) -> str:
    """将字符串中的不可打印 / 零宽字符替换掉，避免 GBK 编码报错"""
    return text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


class BrowserCrawler:
    """Playwright 浏览器评论爬虫"""

    def __init__(self, config: dict):
        self.config = config
        self.db_path = config.get('database_path', '../zhihu_crawler/data/zhihu.db')
        self.headless = config.get('headless', False)
        self.delay_range = config.get('delay_range', [3, 8])
        self.scroll_wait = config.get('scroll_wait', 1.5)
        self.max_stale_rounds = config.get('max_stale_rounds', 10)

        self.cookie_str = self._get_cookie(config)

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.db_conn = None

        self.stats = {
            'answers_processed': 0,
            'comments_inserted': 0,
            'comments_skipped': 0,
            'errors': 0,
        }

    def _get_cookie(self, config: dict) -> str:
        cookies_list = config.get('cookies', [])
        for cookie in cookies_list:
            if cookie and cookie != 'your_cookie_here':
                return cookie
        return ''

    async def setup(self):
        """启动浏览器和页面"""
        from playwright.async_api import async_playwright

        # stealth v2 API
        stealth_obj = None
        try:
            from playwright_stealth import Stealth
            stealth_obj = Stealth(
                navigator_webdriver=True,
                navigator_user_agent=True,
                navigator_plugins=True,
                navigator_vendor=True,
                navigator_languages=True,
                webgl_vendor=True,
                chrome_runtime=False,
            )
            print("[OK] playwright-stealth v2 已加载")
        except ImportError:
            print("[提示] playwright-stealth 未安装，跳过反检测")

        self.playwright = await async_playwright().start()
        # headless 模式使用 Chromium 新无头参数，更难被反爬检测
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ]
        if self.headless:
            launch_args.append('--headless=new')
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=launch_args
        )

        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )

        # stealth v2: 在已有 context 上注入反检测脚本
        if stealth_obj:
            await stealth_obj.apply_stealth_async(self.context)

        # 设置知乎 Cookie
        if self.cookie_str:
            cookies = self._parse_cookie_string(self.cookie_str)
            await self.context.add_cookies(cookies)
            print(f"[OK] 已设置 {len(cookies)} 个 Cookie 项")

        self.page = await self.context.new_page()

        # 预热：先访问知乎首页，建立 session（避免反爬拦截）
        try:
            await self.page.goto('https://www.zhihu.com', wait_until='domcontentloaded', timeout=15000)
            await self.page.wait_for_timeout(2000)
            print("[OK] 预热访问完成")
        except Exception as e:
            print(f"[提示] 预热访问超时，继续: {e}")

        # 初始化数据库连接
        self.db_conn = sqlite3.connect(self.db_path)
        self.db_conn.execute('PRAGMA journal_mode=WAL')
        self._init_progress_table()
        print(f"[OK] 数据库已连接: {self.db_path}")

    def _parse_cookie_string(self, cookie_str: str) -> list:
        cookies = []
        for part in cookie_str.split(';'):
            part = part.strip()
            if '=' in part:
                name, value = part.split('=', 1)
                cookies.append({
                    'name': name.strip(),
                    'value': value.strip(),
                    'domain': '.zhihu.com',
                    'path': '/',
                })
        return cookies

    # ================================================================
    # 断点续爬 — 进度跟踪
    # ================================================================

    def _init_progress_table(self):
        """自动创建进度跟踪表（如不存在），并确保 comments 表有 source 列"""
        self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS browser_crawl_progress (
                answer_id   TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'pending',
                comments_found INTEGER DEFAULT 0,
                started_at  TEXT,
                finished_at TEXT
            )
        ''')
        # 楼中楼追溯表
        self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS thread_tracking (
                answer_id        TEXT NOT NULL,
                root_comment_id  TEXT NOT NULL,
                thread_type      TEXT NOT NULL,
                expected_replies INTEGER NOT NULL,
                actual_replies   INTEGER DEFAULT 0,
                crawled_at       TEXT,
                PRIMARY KEY (answer_id, root_comment_id)
            )
        ''')
        # 滚动触底日志表
        self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS scroll_bottom_log (
                answer_id        TEXT PRIMARY KEY,
                total_visible    INTEGER,
                last_comment_id  TEXT,
                last_author      TEXT,
                last_content     TEXT,
                last_time        TEXT,
                last_likes       INTEGER DEFAULT 0,
                last_is_child    INTEGER DEFAULT 0,
                scroll_rounds    INTEGER DEFAULT 0,
                crawled_at       TEXT
            )
        ''')
        # 给 comments 表添加 source 和 inserted_at 列
        for col_sql, col_name in [
            ("ALTER TABLE comments ADD COLUMN source TEXT DEFAULT 'api'", 'source'),
            ("ALTER TABLE comments ADD COLUMN inserted_at TEXT DEFAULT NULL", 'inserted_at'),
        ]:
            try:
                self.db_conn.execute(col_sql)
                self.db_conn.commit()
                print(f"[OK] 已添加 {col_name} 列到 comments 表")
            except Exception:
                pass  # 列已存在，忽略
        self.db_conn.commit()

    def _is_answer_done(self, answer_id: str) -> bool:
        """检查该回答是否已完成爬取（done 或 comments_closed 都跳过）"""
        row = self.db_conn.execute(
            'SELECT status FROM browser_crawl_progress WHERE answer_id = ?',
            (answer_id,)
        ).fetchone()
        return row is not None and row[0] in ('done', 'comments_closed')

    def _mark_answer_started(self, answer_id: str):
        """标记回答开始爬取"""
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.db_conn.execute('''
            INSERT INTO browser_crawl_progress (answer_id, status, started_at)
            VALUES (?, 'crawling', ?)
            ON CONFLICT(answer_id) DO UPDATE SET status='crawling', started_at=?
        ''', (answer_id, now, now))
        self.db_conn.commit()

    def _mark_answer_done(self, answer_id: str, comments_found: int):
        """标记回答爬取完成"""
        from datetime import datetime
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.db_conn.execute('''
            UPDATE browser_crawl_progress
            SET status='done', comments_found=?, finished_at=?
            WHERE answer_id=?
        ''', (comments_found, now, answer_id))
        self.db_conn.commit()

    def _mark_answer_failed(self, answer_id: str):
        """标记回答爬取失败（下次重试）"""
        self.db_conn.execute('''
            UPDATE browser_crawl_progress SET status='failed'
            WHERE answer_id=?
        ''', (answer_id,))
        self.db_conn.commit()

    def _mark_answer_comments_closed(self, answer_id: str):
        """标记回答评论区已关闭（不再重试）"""
        self.db_conn.execute('''
            UPDATE browser_crawl_progress SET status='comments_closed',
            finished_at=datetime('now','localtime')
            WHERE answer_id=?
        ''', (answer_id,))
        self.db_conn.commit()

    # ================================================================
    # 核心爬取流程
    # ================================================================

    async def crawl_answer_comments(self, answer_id: str, question_id: str) -> int:
        """爬取单个回答的评论，返回新增评论数"""
        url = f'https://www.zhihu.com/question/{question_id}/answer/{answer_id}'
        print(f"\n{'='*60}")
        print(f"[爬取] Answer {answer_id} (Question {question_id})")
        print(f"  URL: {url}")

        # 断点续爬：标记开始
        self._mark_answer_started(answer_id)

        try:
            # 1. 导航（networkidle 确保 JS 渲染完成）
            try:
                await self.page.goto(url, wait_until='networkidle', timeout=45000)
            except Exception:
                await self.page.goto(url, wait_until='load', timeout=30000)
            await self.page.wait_for_timeout(3000)

            # 检测评论区是否已关闭
            comments_closed = await self.page.evaluate(
                '() => document.body.innerText.includes("评论区已关闭")'
            )
            if comments_closed:
                print("  [跳过] 评论区已关闭")
                self._mark_answer_comments_closed(answer_id)
                return 0

            # 先滚到回答区域，确保评论按钮可见
            await self.page.evaluate('''() => {
                const actions = document.querySelector('.ContentItem-actions');
                if (actions) actions.scrollIntoView({behavior: 'smooth', block: 'center'});
            }''')
            await self.page.wait_for_timeout(1000)

            # 2. 点击目标回答的评论按钮（带重试）
            opened = False
            for attempt in range(3):
                opened = await self._trigger_comment_section(answer_id)
                if opened:
                    break
                print(f"  [重试] 第 {attempt+1} 次未找到评论按钮，等 3s 重试...")
                await self.page.wait_for_timeout(3000)

            if not opened:
                # 兜底：再检测一次评论区是否关闭
                comments_closed = await self.page.evaluate(
                    '() => document.body.innerText.includes("评论区已关闭")'
                )
                if comments_closed:
                    print("  [跳过] 评论区已关闭")
                    self._mark_answer_comments_closed(answer_id)
                    return 0
                print("  [警告] 多次尝试未能打开评论区，跳过")
                self._mark_answer_failed(answer_id)
                return 0

            # 等待评论区加载（headless 模式下可能较慢）
            loaded = await self._wait_for_comments_loaded()
            if not loaded:
                # 评论区加载失败，检测是否是"评论区已关闭"
                comments_closed = await self.page.evaluate(
                    '() => document.body.innerText.includes("评论区已关闭")'
                )
                if comments_closed:
                    print("  [跳过] 评论区已关闭")
                    self._mark_answer_comments_closed(answer_id)
                    return 0
                print("  [警告] 评论区加载超时，跳过")
                self._mark_answer_failed(answer_id)
                return 0

            # 2.5 滚动找到并点击"点击查看全部评论"进入完整评论区
            await self._enter_full_comment_page()

            # 3. 边滚动边提取边存储（增量保存）
            new_count = await self._scroll_and_save_comments(answer_id)

            # 断点续爬：标记完成
            total_in_db = self.db_conn.execute(
                'SELECT COUNT(*) FROM comments WHERE answer_id = ?',
                (answer_id,)
            ).fetchone()[0]
            self._mark_answer_done(answer_id, total_in_db)

            self.stats['answers_processed'] += 1
            self.stats['comments_inserted'] += new_count
            print(f"  [完成] 本回答新增 {new_count} 条评论 (DB总计 {total_in_db})")
            return new_count

        except Exception as e:
            print(f"  [错误] 爬取失败: {e}")
            self._mark_answer_failed(answer_id)
            self.stats['errors'] += 1
            return 0

    async def _trigger_comment_section(self, answer_id: str = '') -> bool:
        """
        点击目标回答的评论按钮
        优先匹配 ContentItem-action 类（回答级别），避免点到问题级别评论
        """
        result = await self.page.evaluate(r'''(answerId) => {
            const strip = t => (t||'').replace(/[\u200b\u200c\u200d\ufeff\u00a0]/g, '').trim();
            const btns = [...document.querySelectorAll('button')];

            // 方法 1: ContentItem-action 类的评论按钮（回答级别）
            const answerBtns = btns.filter(b => {
                const cls = b.className.toString();
                const text = strip(b.textContent);
                return cls.includes('ContentItem-action') && text.includes('评论');
            });
            if (answerBtns.length > 0) {
                answerBtns[0].click();
                return 'answer_click: ' + strip(answerBtns[0].textContent);
            }

            // 方法 2: 任何含评论文字的按钮
            const commentBtn = btns.find(b => {
                const text = strip(b.textContent);
                return /^\d+\s*条?\s*评论$/.test(text) || text === '添加评论';
            });
            if (commentBtn) {
                commentBtn.click();
                return 'text_click: ' + strip(commentBtn.textContent);
            }
            return 'not_found';
        }''', answer_id)

        if 'not_found' not in result:
            print(f"  [OK] 评论区已触发 ({result})")
            return True

        # 诊断信息
        diag = await self.page.evaluate(r'''() => {
            const bodyLen = document.body ? document.body.innerText.length : 0;
            const btns = [...document.querySelectorAll('button')];
            const commentBtns = btns.filter(b => b.textContent.includes('评论'));
            return {
                bodyLen,
                totalBtns: btns.length,
                commentBtns: commentBtns.map(b => ({
                    text: (b.textContent||'').trim().slice(0, 50),
                    cls: b.className.toString().slice(0, 80)
                }))
            };
        }''')
        print(f"  [失败] 未找到评论按钮 (body={diag['bodyLen']}, "
              f"buttons={diag['totalBtns']}, 含评论={len(diag['commentBtns'])})")
        for b in diag.get('commentBtns', []):
            print(f"    btn: text='{b['text']}' class='{b['cls']}'")
        return False

    async def _enter_full_comment_page(self):
        """
        在内联评论区内滚动，找到并点击"点击查看全部评论"按钮，
        进入完整评论区（全量评论列表）。
        """
        print("  [步骤] 寻找'点击查看全部评论'按钮...")

        for attempt in range(5):
            # 向下滚动页面，使评论区底部可见
            await self.page.evaluate(r'''() => {
                window.scrollBy(0, 600);
            }''')
            await self.page.wait_for_timeout(1500)

            # 搜索"点击查看全部评论"
            clicked = await self.page.evaluate(r'''() => {
                const allEls = document.querySelectorAll('div, button, a, span');
                for (const el of allEls) {
                    const text = (el.textContent || '').trim();
                    // 精确匹配，避免误点
                    if (text === '点击查看全部评论' || text === '查看全部评论') {
                        el.scrollIntoView({behavior: 'smooth', block: 'center'});
                        el.click();
                        return text;
                    }
                }
                return null;
            }''')

            if clicked:
                print(f"  [OK] 已点击: '{clicked}'")
                # 等待完整评论区加载
                await self.page.wait_for_timeout(3000)

                # 等待更多评论内容出现
                try:
                    await self.page.wait_for_selector('.CommentContent', timeout=10000)
                except Exception:
                    pass

                new_count = await self.page.evaluate(
                    '() => document.querySelectorAll(".CommentContent").length'
                )
                print(f"  [OK] 完整评论区已加载，可见 {new_count} 条评论")
                return

        print("  [警告] 未找到'点击查看全部评论'按钮，继续使用内联评论区")

    async def _wait_for_comments_loaded(self) -> bool:
        """
        等待评论内容加载出来 (.CommentContent)
        知乎评论可能是弹窗(dialog)或内嵌展开，都用 .CommentContent 判断
        """
        # 直接等 CommentContent 出现（最长 15 秒）
        try:
            await self.page.wait_for_selector('.CommentContent', timeout=30000)
            count = await self.page.evaluate(
                '() => document.querySelectorAll(".CommentContent").length'
            )
            print(f"  [OK] 评论已加载，可见 {count} 条")
            return True
        except Exception:
            pass

        # 没等到，可能是评论还在渲染中，再等 5 秒
        print("  [等待] CommentContent 未立即出现，额外等待 5s...")
        await self.page.wait_for_timeout(5000)

        count = await self.page.evaluate(
            '() => document.querySelectorAll(".CommentContent").length'
        )
        if count > 0:
            print(f"  [OK] 延迟加载成功，可见 {count} 条评论")
            return True

        print("  [失败] 评论区加载超时（20s 内未出现 CommentContent）")
        return False

    async def _scroll_and_save_comments(self, answer_id: str) -> int:
        """
        两阶段评论爬取策略（适配知乎虚拟滚动）：

        阶段一：滚动收集
          - 在全评论面板中向下滚动，加载所有根评论
          - 每轮滚动后提取当前可见的根评论并保存
          - 同时记录带有"查看全部 X 条回复"按钮的楼中楼 rootId
          - 不打开任何模态框，避免破坏滚动状态

        阶段二：逐个处理楼中楼
          - 重新打开全评论面板
          - 对每个收集到的楼中楼 rootId：
            1. 在面板中滚动找到该根评论
            2. 点击其"查看全部 X 条回复"按钮 → 打开模态框
            3. 在模态框内滚动提取所有子评论
            4. 关闭模态框，恢复面板
        """
        total_new = 0
        saved_ids = set()

        # ═══════════════════════════════════════════
        # 阶段一：滚动收集根评论 + 发现楼中楼
        # ═══════════════════════════════════════════
        print("  [阶段一] 滚动收集根评论...")
        discovered_threads = {}  # rootId -> {rootId, replyCount, text} (查看全部)
        inline_expanded_threads = {}  # rootId -> {rootId, replyCount} (展开其他)
        stale_rounds = 0
        scroll_round = 0
        prev_root_count = 0
        prev_saved_count = 0

        while stale_rounds < self.max_stale_rounds:
            scroll_round += 1

            # 提取并保存当前可见的根评论
            comments = await self._extract_all_comments()
            round_new = 0
            for c in comments:
                cid = c['id']
                if cid in saved_ids:
                    continue
                saved_ids.add(cid)
                inserted = self._insert_comment(
                    comment_id=cid,
                    answer_id=answer_id,
                    parent_id=c.get('parent_id'),
                    is_child=1 if c.get('is_child') else 0,
                    author_name=c.get('author_name', '匿名用户'),
                    content=c.get('content', ''),
                    like_count=c.get('like_count', 0),
                    reply_to=c.get('reply_to'),
                    created_time=c.get('created_time', ''),
                )
                if inserted:
                    round_new += 1
            total_new += round_new

            # 点击"展开其他x条回复"按钮（内联展开，不会打开模态框）
            # 这类按钮出现在回复数较少的楼层，点击后子评论直接在面板内展开
            # 同时记录每个楼层的 rootId 和预期回复数
            expand_results = await self.page.evaluate(r'''() => {
                const btns = [...document.querySelectorAll('button')];
                const results = [];
                for (const btn of btns) {
                    const text = (btn.textContent || '').trim();
                    const match = text.match(/展开其他\s*(\d+)\s*条回复/);
                    if (match) {
                        let el = btn.parentElement;
                        let rootId = '';
                        while (el) {
                            const did = el.getAttribute('data-id');
                            if (did) { rootId = did; break; }
                            el = el.parentElement;
                        }
                        btn.click();
                        results.push({
                            rootId: rootId,
                            replyCount: parseInt(match[1])
                        });
                    }
                }
                return results;
            }''')
            for ex in expand_results:
                rid = ex.get('rootId', '')
                if rid and rid not in inline_expanded_threads:
                    inline_expanded_threads[rid] = ex
            if expand_results:
                await self.page.wait_for_timeout(1000)  # 等待展开完成

            # 收集当前可见的楼中楼按钮信息（不点击）
            thread_buttons = await self.page.evaluate(r'''() => {
                const btns = [...document.querySelectorAll('button')];
                const results = [];
                for (const btn of btns) {
                    const text = (btn.textContent || '').trim();
                    const match = text.match(/查看全部\s*(\d+)\s*条回复/);
                    if (match) {
                        let el = btn.parentElement;
                        let rootId = '';
                        while (el) {
                            const did = el.getAttribute('data-id');
                            if (did) { rootId = did; break; }
                            el = el.parentElement;
                        }
                        if (rootId) {
                            results.push({
                                rootId: rootId,
                                replyCount: parseInt(match[1]),
                                text: text
                            });
                        }
                    }
                }
                return results;
            }''')

            # 记录新发现的楼中楼
            new_thread_count = 0
            for t in thread_buttons:
                rid = t['rootId']
                if rid not in discovered_threads:
                    discovered_threads[rid] = t
                    new_thread_count += 1

            # 增量滚动评论面板容器，并返回滚动位置信息
            scroll_info = await self.page.evaluate(r'''() => {
                let target = null;
                const container = document.querySelector('.css-34podr');
                if (container) {
                    target = container;
                } else {
                    const comment = document.querySelector('.CommentContent');
                    if (comment) {
                        let el = comment.parentElement;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                                if (el.scrollHeight > el.clientHeight) {
                                    target = el;
                                    break;
                                }
                            }
                            el = el.parentElement;
                        }
                    }
                }
                if (target) {
                    target.scrollTop += 600;
                    return {
                        found: true,
                        scrollTop: target.scrollTop,
                        scrollHeight: target.scrollHeight,
                        clientHeight: target.clientHeight,
                        atBottom: target.scrollTop + target.clientHeight >= target.scrollHeight - 50
                    };
                }
                window.scrollBy(0, 600);
                return { found: false, atBottom: false, scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
            }''')
            await self.page.wait_for_timeout(int(self.scroll_wait * 1000))

            # 检查根评论数量
            new_root_count = await self.page.evaluate(
                '() => document.querySelectorAll("[data-id]").length'
            )

            # stale 检测（区分「物理到底」和「加载中」）
            cur_saved_count = len(saved_ids)
            has_progress = (new_root_count > prev_root_count
                           or cur_saved_count > prev_saved_count
                           or new_thread_count > 0)
            at_bottom = scroll_info.get('atBottom', False) if scroll_info else False

            if has_progress:
                stale_rounds = 0
            elif at_bottom:
                # 物理已到底且没新内容，直接计为 stale
                stale_rounds += 1
            else:
                # 还没到底但没新内容 → 可能是懒加载延迟，多等一会儿
                await self.page.wait_for_timeout(3000)
                # 再检查一次是否有新内容加载出来
                retry_count = await self.page.evaluate(
                    '() => document.querySelectorAll("[data-id]").length'
                )
                if retry_count > new_root_count:
                    # 等待后有新内容了，不算 stale
                    stale_rounds = 0
                    new_root_count = retry_count
                else:
                    stale_rounds += 1
                    # 不在底部时容忍度更高（30轮 vs 默认10轮）
                    if stale_rounds < 30:
                        continue
            prev_root_count = new_root_count
            prev_saved_count = cur_saved_count

            if scroll_round % 5 == 0 or new_thread_count > 0:
                pct = ''
                if scroll_info and scroll_info.get('scrollHeight', 0) > 0:
                    pct = f" ({int(scroll_info['scrollTop'] / scroll_info['scrollHeight'] * 100)}%)"
                print(f"    [第{scroll_round}轮] 根评论 {new_root_count}, "
                      f"已保存 {len(saved_ids)}, 新增 {total_new}, "
                      f"发现楼中楼 {len(discovered_threads)}, "
                      f"内联展开 {len(inline_expanded_threads)}"
                      f"{pct}")

        print(f"  [阶段一完成] {scroll_round}轮, "
              f"保存 {len(saved_ids)} 条评论, 新增 {total_new}, "
              f"发现 {len(discovered_threads)} 个楼中楼(模态框), "
              f"{len(inline_expanded_threads)} 个内联展开")

        # 输出面板最底部的评论信息（"世界尽头"）
        try:
            last_comment = await self.page.evaluate(r'''() => {
                const contents = document.querySelectorAll('.CommentContent');
                if (contents.length === 0) return null;
                const lastEl = contents[contents.length - 1];
                const wrapper = lastEl.parentElement;
                if (!wrapper) return null;

                const strip = t => (t||'').replace(/[\u200b\u200c\u200d\ufeff\u00a0]/g, '').trim();

                // ID
                let commentId = '';
                let el = lastEl.parentElement;
                while (el) {
                    const did = el.getAttribute('data-id');
                    if (did) { commentId = did; break; }
                    el = el.parentElement;
                }

                // 内容
                const content = strip(lastEl.textContent).replace(/\n+/g, ' ').slice(0, 80);

                // 作者
                const authorLink = wrapper.querySelector('a[href*="/people/"]');
                const authorName = authorLink ? strip(authorLink.textContent) : '匿名用户';

                // 时间
                let createdTime = '';
                const spans = wrapper.querySelectorAll('span');
                for (const span of spans) {
                    const t = strip(span.textContent);
                    if (/^\d{4}-\d{2}-\d{2}$/.test(t) || /前$/.test(t) || t === '昨天' || t === '今天') {
                        createdTime = t; break;
                    }
                }

                // 点赞
                let likeCount = 0;
                const buttons = wrapper.querySelectorAll('button');
                for (const btn of buttons) {
                    const svg = btn.querySelector('svg');
                    if (svg && svg.className && svg.className.baseVal &&
                        svg.className.baseVal.includes('Heart')) {
                        const num = parseInt(strip(btn.textContent));
                        if (!isNaN(num)) likeCount = num;
                        break;
                    }
                }

                // 是否子评论
                let isChild = false;
                let p = lastEl.parentElement;
                while (p) {
                    const cls = (p.className || '').toString();
                    if (cls.includes('css-1kwt8l8')) { isChild = true; break; }
                    if (cls.includes('css-jp43l4')) break;
                    p = p.parentElement;
                }

                // 位置
                const idx = contents.length;
                return { id: commentId, content, author: authorName, time: createdTime,
                         likes: likeCount, isChild, position: idx, total: contents.length };
            }''')
            if last_comment:
                child_tag = '子评论' if last_comment.get('isChild') else '根评论'
                print(f"  [世界尽头] 面板最后一条评论 (第{last_comment['position']}/{last_comment['total']}条):")
                print(f"    ID:   {last_comment['id']}")
                print(f"    作者: {last_comment['author']}  时间: {last_comment['time']}  赞: {last_comment['likes']}  类型: {child_tag}")
                print(f"    内容: {last_comment['content']}...")
                # 保存到数据库
                try:
                    from datetime import datetime
                    self.db_conn.execute('''
                        INSERT OR REPLACE INTO scroll_bottom_log
                        (answer_id, total_visible, last_comment_id, last_author,
                         last_content, last_time, last_likes, last_is_child,
                         scroll_rounds, crawled_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        answer_id,
                        last_comment['total'],
                        last_comment['id'],
                        last_comment['author'],
                        last_comment['content'],
                        last_comment['time'],
                        last_comment['likes'],
                        1 if last_comment.get('isChild') else 0,
                        scroll_round,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    self.db_conn.commit()
                except Exception as db_err:
                    print(f"    [警告] 保存世界尽头到DB失败: {db_err}")
        except Exception as e:
            print(f"  [世界尽头] 无法获取最后评论: {e}")

        # ═══════════════════════════════════════════
        # 阶段二：逐个处理楼中楼
        # ═══════════════════════════════════════════
        if not discovered_threads:
            return total_new

        print(f"  [阶段二] 处理 {len(discovered_threads)} 个楼中楼...")
        processed_count = 0

        for root_id, thread_info in discovered_threads.items():
            reply_count = thread_info['replyCount']
            processed_count += 1
            print(f"    [{processed_count}/{len(discovered_threads)}] "
                  f"根评论 {root_id}: {reply_count} 条回复")

            # 确保全评论面板已打开
            panel_exists = await self.page.evaluate(
                '() => !!document.querySelector(".css-34podr")'
            )
            if not panel_exists:
                await self._enter_full_comment_page()
                await self.page.wait_for_timeout(1500)

            # 在面板中滚动，找到该根评论并点击其楼中楼按钮
            opened = False
            for scroll_try in range(60):  # 最多滚动 60 次寻找
                opened = await self.page.evaluate(r'''(rootId) => {
                    const container = document.querySelector('[data-id="' + rootId + '"]');
                    if (!container) return false;
                    const btn = [...container.querySelectorAll('button')].find(
                        b => /查看全部.*条回复/.test(b.textContent)
                    );
                    if (btn) {
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        btn.click();
                        return true;
                    }
                    return false;
                }''', root_id)
                if opened:
                    break

                # 没找到，继续滚动面板（坑 #1/#5: 加 overflow-y fallback）
                await self.page.evaluate(r'''() => {
                    const container = document.querySelector('.css-34podr');
                    if (container) {
                        container.scrollTop += 800;
                        return;
                    }
                    // Fallback: 从 CommentContent 向上找 overflow-y 容器
                    const comment = document.querySelector('.CommentContent');
                    if (comment) {
                        let el = comment.parentElement;
                        while (el && el !== document.body) {
                            const style = window.getComputedStyle(el);
                            if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                                if (el.scrollHeight > el.clientHeight) {
                                    el.scrollTop += 800;
                                    return;
                                }
                            }
                            el = el.parentElement;
                        }
                    }
                    window.scrollBy(0, 800);
                }''')
                await self.page.wait_for_timeout(800)

            if not opened:
                print(f"      [跳过] 无法找到根评论 {root_id} 的楼中楼按钮")
                continue

            await self.page.wait_for_timeout(2000)

            # 检测模态框是否打开
            has_modal = await self.page.evaluate(
                '() => !!document.querySelector(".css-1svde17")'
            )
            if not has_modal:
                await self.page.wait_for_timeout(1500)
                has_modal = await self.page.evaluate(
                    '() => !!document.querySelector(".css-1svde17")'
                )

            # 诊断：确认模态框状态
            modal_diag = await self.page.evaluate(r'''() => {
                const modal = document.querySelector('.css-1svde17');
                if (modal) {
                    const comments = modal.querySelectorAll('.CommentContent').length;
                    return {found: true, selector: '.css-1svde17', comments: comments};
                }
                // Fallback: 检查所有带 overflow-y 的大容器是否包含评论
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const cs = window.getComputedStyle(el);
                    if ((cs.overflowY === 'scroll' || cs.overflowY === 'auto') &&
                        el.scrollHeight > el.clientHeight + 100) {
                        const cc = el.querySelectorAll('.CommentContent').length;
                        if (cc > 0 && el.offsetHeight > 200) {
                            // 检查是否是新出现的容器（非原评论面板）
                            const cls = el.className.toString();
                            return {found: false, fallback: true, class: cls.substring(0, 80),
                                    comments: cc, scrollH: el.scrollHeight, clientH: el.clientHeight};
                        }
                    }
                }
                return {found: false, fallback: false, totalComments: document.querySelectorAll('.CommentContent').length};
            }''')
            if not modal_diag.get('found'):
                print(f"      [诊断] 模态框未检测到: {modal_diag}")

            # 在模态框内滚动提取子评论
            modal_new = 0
            modal_stale = 0
            prev_modal_count = 0
            for modal_round in range(200):
                # 坑 #6: 增量滚动模态框（用 getComputedStyle 找真正的可滚动容器）
                scroll_info = await self.page.evaluate(r'''() => {
                    // 优先在 .css-1svde17 模态框内找可滚动子元素
                    const modal = document.querySelector('.css-1svde17');
                    const searchRoot = modal || document;
                    
                    // 用 getComputedStyle 找真正的 overflow-y 滚动容器
                    const allEls = searchRoot.querySelectorAll('*');
                    for (const el of allEls) {
                        const cs = window.getComputedStyle(el);
                        if ((cs.overflowY === 'scroll' || cs.overflowY === 'auto') &&
                            el.scrollHeight > el.clientHeight + 50) {
                            const cc = el.querySelectorAll('.CommentContent').length;
                            if (cc > 0) {
                                const before = el.scrollTop;
                                el.scrollTop += 600;
                                return {scrolled: true, before: before, after: el.scrollTop,
                                        scrollH: el.scrollHeight, clientH: el.clientHeight, comments: cc};
                            }
                        }
                    }
                    
                    // 如果找不到内部可滚动容器，尝试滚动 modal 自身
                    if (modal && modal.scrollHeight > modal.clientHeight) {
                        const before = modal.scrollTop;
                        modal.scrollTop += 600;
                        return {scrolled: true, self: true, before: before, after: modal.scrollTop};
                    }
                    
                    return {scrolled: false};
                }''')
                if modal_round == 0:
                    print(f"      [滚动诊断] {scroll_info}")
                await self.page.wait_for_timeout(1500)

                # 点击模态框内的"展开其他 X 条回复"
                await self.page.evaluate(r'''() => {
                    const modal = document.querySelector('.css-1svde17');
                    if (!modal) return;
                    const btns = modal.querySelectorAll('button');
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        if (/展开其他.*条回复/.test(text)) {
                            btn.click();
                        }
                    }
                }''')

                # 提取模态框内的评论（限定在模态框 DOM 内）
                modal_comments = await self._extract_modal_comments()
                for c in modal_comments:
                    cid = c['id']
                    if cid == root_id:  # 跳过根评论本身（模态框内会显示）
                        continue
                    if cid in saved_ids:
                        continue
                    saved_ids.add(cid)
                    # 坑 #8: 模态框内一律视为子评论，parent_id = 当前楼中楼根ID
                    inserted = self._insert_comment(
                        comment_id=cid,
                        answer_id=answer_id,
                        parent_id=root_id,
                        is_child=1,
                        author_name=c.get('author_name', '匿名用户'),
                        content=c.get('content', ''),
                        like_count=c.get('like_count', 0),
                        reply_to=c.get('reply_to'),
                        created_time=c.get('created_time', ''),
                    )
                    if inserted:
                        modal_new += 1

                # 只计算模态框内的评论数（避免被面板评论数干扰）
                cur_count = await self.page.evaluate(r'''() => {
                    const modal = document.querySelector('.css-1svde17');
                    if (modal) return modal.querySelectorAll('.CommentContent').length;
                    return document.querySelectorAll('.CommentContent').length;
                }''')
                if cur_count == prev_modal_count:
                    modal_stale += 1
                    if modal_stale >= 5:
                        break
                else:
                    modal_stale = 0
                    prev_modal_count = cur_count

            total_new += modal_new
            if modal_new > 0:
                print(f"      → 保存了 {modal_new} 条子评论")

            # 关闭模态框
            await self.page.keyboard.press('Escape')
            await self.page.wait_for_timeout(1500)

            # 坑 #4: 验证模态框已关闭（排除 signFlowModal）
            still_modal = await self.page.evaluate(r'''() => {
                const closeBtn = document.querySelector('button[aria-label="关闭"]');
                if (!closeBtn) return false;
                const parent = closeBtn.closest('.signFlowModal');
                if (parent) return false;
                return true;
            }''')
            if still_modal:
                await self.page.keyboard.press('Escape')
                await self.page.wait_for_timeout(1000)

            # 坑 #3: 验证全评论面板是否存活（Escape 可能连锁关闭面板）
            panel_exists = await self.page.evaluate(
                '() => !!document.querySelector(".css-34podr")'
            )
            if not panel_exists:
                # Fallback: 检查 overflow-y 容器
                panel_exists = await self.page.evaluate(r'''() => {
                    const comment = document.querySelector('.CommentContent');
                    if (!comment) return false;
                    let el = comment.parentElement;
                    while (el && el !== document.body) {
                        const style = window.getComputedStyle(el);
                        if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                            if (el.scrollHeight > el.clientHeight) return true;
                        }
                        el = el.parentElement;
                    }
                    return false;
                }''')
            if not panel_exists:
                print("      [恢复] 面板被连带关闭，重新打开...")
                await self._enter_full_comment_page()
                await self.page.wait_for_timeout(2000)

            # 每处理 10 个楼中楼打印进度
            if processed_count % 10 == 0:
                print(f"    [进度] 已处理 {processed_count}/{len(discovered_threads)}, "
                      f"共保存 {len(saved_ids)} 条, 新增 {total_new}")

        print(f"  [阶段二完成] 处理了 {processed_count} 个楼中楼, "
              f"共保存 {len(saved_ids)} 条, 新增 {total_new}")

        # ═══════════════════════════════════════════
        # 楼中楼追溯汇总
        # ═══════════════════════════════════════════
        all_threads = {}
        for rid, info in discovered_threads.items():
            all_threads[rid] = {'type': '模态框', 'expected': info['replyCount']}
        for rid, info in inline_expanded_threads.items():
            all_threads[rid] = {'type': '内联展开', 'expected': info['replyCount']}

        if all_threads and self.db_conn:
            from datetime import datetime
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor = self.db_conn.cursor()
            print(f"\n  [楼中楼追溯] 共 {len(all_threads)} 个楼层")
            print(f"    {'类型':<8} {'rootId':<15} {'预期':>6} {'实际':>6} {'差距':>6}")
            print(f"    {'-'*45}")
            total_exp = 0
            total_act = 0
            for rid, tinfo in sorted(all_threads.items(),
                                      key=lambda x: -x[1]['expected']):
                exp = tinfo['expected']
                act = cursor.execute(
                    "SELECT COUNT(*) FROM comments WHERE parent_id=? AND answer_id=?",
                    (rid, answer_id)
                ).fetchone()[0]
                gap = exp - act
                total_exp += exp
                total_act += act
                flag = " ⚠" if gap > 0 else " ✓"
                print(f"    {tinfo['type']:<8} {rid:<15} {exp:>6} {act:>6} {gap:>+6}{flag}")
                # 写入 DB
                cursor.execute('''
                    INSERT OR REPLACE INTO thread_tracking
                    (answer_id, root_comment_id, thread_type,
                     expected_replies, actual_replies, crawled_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (answer_id, rid, tinfo['type'], exp, act, now))
            self.db_conn.commit()
            print(f"    {'-'*45}")
            print(f"    {'合计':<8} {'':<15} {total_exp:>6} {total_act:>6} "
                  f"{total_exp - total_act:>+6}")

        return total_new

    async def _extract_modal_comments(self) -> list:
        """
        从模态框 (.css-1svde17) 内提取评论数据
        与 _extract_all_comments 逻辑相同，但限定 DOM 范围在模态框内
        避免面板评论干扰模态框的 stale 检测
        """
        comments = await self.page.evaluate(r'''() => {
            const strip = t => (t||'').replace(/[\u200b\u200c\u200d\ufeff\u00a0]/g, '').trim();
            const results = [];
            // 限定在模态框内
            const modal = document.querySelector('.css-1svde17');
            const root = modal || document;
            const contentEls = root.querySelectorAll('.CommentContent');

            contentEls.forEach((contentEl, index) => {
                const wrapper = contentEl.parentElement;
                if (!wrapper) return;

                const content = strip(contentEl.textContent).replace(/\n+/g, ' ');

                const authorLink = wrapper.querySelector('a[href*="/people/"]');
                const authorName = authorLink ? strip(authorLink.textContent) : '匿名用户';
                const authorUrl = authorLink ? authorLink.href : '';
                let authorId = '';
                if (authorUrl) {
                    const parts = authorUrl.split('/people/');
                    if (parts.length > 1) authorId = parts[1].split('/')[0].split('?')[0];
                }

                let createdTime = '';
                const spans = wrapper.querySelectorAll('span');
                for (const span of spans) {
                    const t = strip(span.textContent);
                    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) { createdTime = t; break; }
                    if (/前$/.test(t) || t === '昨天' || t === '今天' || t === '刚刚') {
                        createdTime = t; break;
                    }
                }

                let likeCount = 0;
                const buttons = wrapper.querySelectorAll('button');
                for (const btn of buttons) {
                    const svg = btn.querySelector('svg');
                    if (svg && svg.className && svg.className.baseVal &&
                        svg.className.baseVal.includes('Heart')) {
                        const likeText = strip(btn.textContent);
                        const num = parseInt(likeText);
                        if (!isNaN(num)) likeCount = num;
                        break;
                    }
                }

                // 生成评论 ID
                let commentId = '';
                let parentId = null;
                let el = contentEl.parentElement;
                let foundSelf = false;
                while (el) {
                    const did = el.getAttribute('data-id');
                    if (did) {
                        if (!foundSelf) {
                            commentId = did;
                            foundSelf = true;
                        } else {
                            parentId = did;
                            break;
                        }
                    }
                    el = el.parentElement;
                }
                if (!commentId) commentId = 'm_' + index;

                results.push({
                    id: commentId,
                    author_name: authorName,
                    author_id: authorId,
                    content: content,
                    created_time: createdTime,
                    like_count: likeCount,
                    is_child: true,
                    parent_id: parentId,
                    reply_to: null,
                });
            });

            return results;
        }''')
        return comments or []

    async def _extract_all_comments(self) -> list:
        """
        用 JS 一次性从 DOM 中提取全部评论数据
        从稳定的 .CommentContent 类出发，向上遍历找 author/time/likes
        """
        comments = await self.page.evaluate(r'''() => {
            const strip = t => (t||'').replace(/[\u200b\u200c\u200d\ufeff\u00a0]/g, '').trim();
            const results = [];
            const contentEls = document.querySelectorAll('.CommentContent');

            contentEls.forEach((contentEl, index) => {
                const wrapper = contentEl.parentElement;
                if (!wrapper) return;

                // 评论文本
                const content = strip(contentEl.textContent).replace(/\n+/g, ' ');

                // 作者
                const authorLink = wrapper.querySelector('a[href*="/people/"]');
                const authorName = authorLink ? strip(authorLink.textContent) : '匿名用户';
                const authorUrl = authorLink ? authorLink.href : '';
                let authorId = '';
                if (authorUrl) {
                    const parts = authorUrl.split('/people/');
                    if (parts.length > 1) authorId = parts[1].split('/')[0].split('?')[0];
                }

                // 时间
                let createdTime = '';
                const spans = wrapper.querySelectorAll('span');
                for (const span of spans) {
                    const t = strip(span.textContent);
                    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) { createdTime = t; break; }
                    if (/前$/.test(t) || t === '昨天' || t === '今天' || t === '刚刚') {
                        createdTime = t; break;
                    }
                }

                // 点赞数
                let likeCount = 0;
                const buttons = wrapper.querySelectorAll('button');
                for (const btn of buttons) {
                    const svg = btn.querySelector('svg');
                    if (svg && svg.className && svg.className.baseVal &&
                        svg.className.baseVal.includes('Heart')) {
                        const likeText = strip(btn.textContent);
                        const num = parseInt(likeText);
                        if (!isNaN(num)) likeCount = num;
                        break;
                    }
                }

                // 是否子评论（子评论容器没有头像 css-1gomreu）
                const hasAvatar = wrapper.querySelector('a[href*="/people/"]')
                    ?.closest('[class*="css-"]')
                    ?.previousElementSibling
                    ?.querySelector('[class*="css-1gomreu"]');
                // 更可靠：检查祖先中是否有特定结构
                let isChild = false;
                let parent = contentEl.parentElement;
                // 子评论的直接父级结构不同于根评论
                // 根评论: div > div.css-jp43l4 > div.css-14nvvry > .CommentContent
                // 子评论: div > div.css-1kwt8l8 > div.css-14nvvry > .CommentContent
                while (parent) {
                    const cls = (parent.className || '').toString();
                    if (cls.includes('css-1kwt8l8')) { isChild = true; break; }
                    if (cls.includes('css-jp43l4')) { isChild = false; break; }
                    parent = parent.parentElement;
                }

                // 生成稳定的评论 ID（基于内容全文 + 作者 + 时间）
                // 从 DOM 的 data-id 属性获取知乎原生评论 ID
                let commentId = '';
                let parentId = null;
                let el = contentEl.parentElement;
                let foundSelf = false;
                while (el) {
                    const did = el.getAttribute('data-id');
                    if (did) {
                        if (!foundSelf) {
                            commentId = did;
                            foundSelf = true;
                        } else {
                            parentId = did;
                            break;
                        }
                    }
                    el = el.parentElement;
                }
                if (!commentId) commentId = 'b_' + index;

                results.push({
                    id: commentId,
                    author_name: authorName,
                    author_id: authorId,
                    content: content,
                    created_time: createdTime,
                    like_count: likeCount,
                    is_child: isChild,
                    parent_id: parentId,
                    reply_to: null,
                });
            });

            return results;
        }''')
        return comments or []

    # ================================================================
    # 数据库操作
    # ================================================================

    def _insert_comment(self, comment_id: str, answer_id: str,
                        parent_id: Optional[str], is_child: int,
                        author_name: str, content: str,
                        like_count: int, reply_to: Optional[str],
                        created_time: str) -> bool:
        """插入评论到数据库，已存在则跳过"""
        try:
            cursor = self.db_conn.cursor()
            from datetime import datetime
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                INSERT OR IGNORE INTO comments
                (id, answer_id, parent_id, is_child, author_name,
                 content, like_count, reply_to, created_time, source, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'browser', ?)
            ''', (comment_id, answer_id, parent_id, is_child,
                  author_name, content, like_count, reply_to, created_time, now))
            self.db_conn.commit()
            if cursor.rowcount > 0:
                return True
            else:
                self.stats['comments_skipped'] += 1
                return False
        except Exception as e:
            print(f"  [DB错误] {e}")
            return False

    # ================================================================
    # 批量运行
    # ================================================================

    async def run(self, gaps: List[Dict], max_answers: Optional[int] = None):
        """批量爬取有缺口的回答评论（支持断点续爬）"""
        total_before_filter = len(gaps)
        if max_answers:
            gaps = gaps[:max_answers]

        # 断点续爬：过滤掉已完成的回答
        skipped = 0
        remaining_gaps = []
        for gap in gaps:
            aid = str(gap['answer_id'])
            if self._is_answer_done(aid):
                skipped += 1
            else:
                remaining_gaps.append(gap)

        total = len(remaining_gaps)
        print(f"\n{'='*60}")
        print(f"开始批量补爬评论（断点续爬）")
        print(f"待处理回答: {total} (跳过已完成: {skipped})")
        print(f"模式: {'headless' if self.headless else 'headed（可视化）'}")
        print(f"页面间延迟: {self.delay_range[0]}-{self.delay_range[1]}s")
        print(f"{'='*60}")

        if total == 0:
            print("\n[完成] 所有回答已爬取完毕，无需继续")
            return

        for i, gap in enumerate(remaining_gaps, 1):
            answer_id = str(gap['answer_id'])
            question_id = str(gap['question_id'])
            expected = gap['expected']
            actual = gap['actual']
            gap_size = gap['gap']

            print(f"\n[{i}/{total}] 缺口 {gap_size} 条 "
                  f"(已有 {actual}/{expected}) Answer={answer_id}")

            await self.crawl_answer_comments(answer_id, question_id)

            if i < total:
                delay = random.uniform(*self.delay_range)
                print(f"  [等待] {delay:.1f}s")
                await asyncio.sleep(delay)

            if i % 10 == 0:
                self._print_stats()

        self._print_stats()

    def _print_stats(self):
        print(f"\n--- 统计 ---")
        print(f"  已处理回答: {self.stats['answers_processed']}")
        print(f"  新增评论: {self.stats['comments_inserted']}")
        print(f"  去重跳过: {self.stats['comments_skipped']}")
        print(f"  错误数: {self.stats['errors']}")

    # ================================================================
    # 发现模式（调试用）
    # ================================================================

    async def discover(self, answer_id: str, question_id: str):
        """发现模式：打开指定回答页，提取评论供调试"""
        url = f'https://www.zhihu.com/question/{question_id}/answer/{answer_id}'
        print(f"\n{'='*60}")
        print(f"选择器发现模式")
        print(f"URL: {url}")
        print(f"{'='*60}")

        try:
            await self.page.goto(url, wait_until='networkidle', timeout=45000)
        except Exception:
            await self.page.goto(url, wait_until='load', timeout=30000)
        await self.page.wait_for_timeout(3000)

        body_len = await self.page.evaluate(
            '() => document.body ? document.body.innerText.length : 0'
        )
        btn_count = await self.page.evaluate(
            '() => document.querySelectorAll("button").length'
        )
        title = await self.page.title()
        print(f"\n页面: {title}")
        print(f"内容长度: {body_len}, 按钮数: {btn_count}")

        if body_len < 200:
            print("[失败] 页面内容太少，可能被反爬拦截")
            return {}

        print("\n1. 点击评论按钮...")
        opened = await self._trigger_comment_section(answer_id)

        if opened:
            loaded = await self._wait_for_comments_loaded()
            comment_count = await self.page.evaluate(
                '() => document.querySelectorAll(".CommentContent").length'
            )
            print(f"\n2. 评论区已展开, 可见 {comment_count} 条 CommentContent")

            comments = await self._extract_all_comments()
            print(f"\n3. 提取到 {len(comments)} 条评论")
            for c in comments[:5]:
                print(f"   作者={c['author_name']}, 赞={c['like_count']}, "
                      f"时间={c['created_time']}, 子评论={c['is_child']}")
                print(f"   内容: {c['content'][:60]}...")

            return {'status': 'ok', 'total_comments': len(comments)}
        else:
            print("\n[失败] 无法打开评论区")
            buttons = await self.page.evaluate(r'''() => {
                return [...document.querySelectorAll('button')].slice(0, 20).map(b => ({
                    text: (b.textContent||'').replace(/[\u200b]/g,'').trim().slice(0, 50),
                    classes: b.className.toString().slice(0, 60),
                    ariaLabel: b.getAttribute('aria-label') || '',
                }));
            }''')
            print("\n页面中的 button 列表（前20个）:")
            for b in buttons:
                print(f"  text='{b['text']}' class='{b['classes']}' aria='{b['ariaLabel']}'")
            return {}

    async def close(self):
        """关闭浏览器和数据库"""
        if self.db_conn:
            self.db_conn.close()
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("[OK] 资源已释放")
