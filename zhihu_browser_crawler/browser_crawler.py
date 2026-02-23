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
import random
import sqlite3
import sys
import time
from typing import Dict, List, Optional

# Windows 控制台 UTF-8 兼容
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
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
        self.max_stale_rounds = config.get('max_stale_rounds', 5)

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
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
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
        """自动创建进度跟踪表（如不存在）"""
        self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS browser_crawl_progress (
                answer_id   TEXT PRIMARY KEY,
                status      TEXT NOT NULL DEFAULT 'pending',
                comments_found INTEGER DEFAULT 0,
                started_at  TEXT,
                finished_at TEXT
            )
        ''')
        self.db_conn.commit()

    def _is_answer_done(self, answer_id: str) -> bool:
        """检查该回答是否已完成爬取"""
        row = self.db_conn.execute(
            'SELECT status FROM browser_crawl_progress WHERE answer_id = ?',
            (answer_id,)
        ).fetchone()
        return row is not None and row[0] == 'done'

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
                print("  [警告] 多次尝试未能打开评论区，跳过")
                self._mark_answer_failed(answer_id)
                return 0

            # 等待评论区加载（headless 模式下可能较慢）
            loaded = await self._wait_for_comments_loaded()
            if not loaded:
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
        边滚动边提取边存储。
        知乎评论结构：~10 条根评论 + 每条下面隐藏的子评论（需要点击"查看全部 X 条回复"展开）。
        策略：反复点击展开按钮 → 滚动加载 → 提取保存 → 直到没有新内容。
        """
        prev_dom_count = 0
        stale_rounds = 0
        scroll_round = 0
        total_new = 0
        saved_ids = set()

        while stale_rounds < self.max_stale_rounds:
            scroll_round += 1

            # 1. 点击所有"查看全部 X 条回复"和"展开其他 X 条回复"按钮
            clicked = await self.page.evaluate(r'''() => {
                const btns = document.querySelectorAll('button');
                let clickCount = 0;
                for (const btn of btns) {
                    const text = (btn.textContent || '').trim();
                    if (/查看全部.*条回复|展开其他.*条回复|展开更多/.test(text)) {
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        btn.click();
                        clickCount++;
                    }
                }
                return clickCount;
            }''')
            if clicked > 0:
                if scroll_round <= 5:
                    print(f"    [第{scroll_round}轮] 点击了 {clicked} 个展开按钮")
                # 展开按钮点击后需要等待内容加载
                await self.page.wait_for_timeout(2000)

            # 2. 滚动：把最后一条评论滚动到可见区域
            await self.page.evaluate(r'''() => {
                const comments = document.querySelectorAll('.CommentContent');
                if (comments.length > 0) {
                    comments[comments.length - 1].scrollIntoView({
                        behavior: 'smooth', block: 'end'
                    });
                } else {
                    window.scrollTo(0, document.body.scrollHeight);
                }
            }''')

            wait_ms = int(self.scroll_wait * 1000)
            await self.page.wait_for_timeout(wait_ms)

            # 当前 DOM 中的评论数
            current_dom_count = await self.page.evaluate(
                '() => document.querySelectorAll(".CommentContent").length'
            )

            if current_dom_count == prev_dom_count:
                stale_rounds += 1
            else:
                stale_rounds = 0
                prev_dom_count = current_dom_count

            # 每 3 轮 或 有新评论时，提取并保存
            if scroll_round % 3 == 0 or stale_rounds == 0:
                comments = await self._extract_all_comments()
                round_new = 0
                for c in comments:
                    cid = c['id']
                    if cid in saved_ids:
                        continue  # 本次运行已保存过
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

            if scroll_round % 10 == 0:
                print(f"    滚动第{scroll_round}轮, DOM {current_dom_count} 条, "
                      f"已保存 {len(saved_ids)} 条, 新增 {total_new} 条")

        # 最后再提取一次，确保不遗漏
        comments = await self._extract_all_comments()
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
                total_new += 1

        print(f"  [滚动完成] 共 {scroll_round} 轮, "
              f"DOM {prev_dom_count} 条, 已保存 {len(saved_ids)} 条, 新增 {total_new} 条")
        return total_new

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
            cursor.execute('''
                INSERT OR IGNORE INTO comments
                (id, answer_id, parent_id, is_child, author_name,
                 content, like_count, reply_to, created_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (comment_id, answer_id, parent_id, is_child,
                  author_name, content, like_count, reply_to, created_time))
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
