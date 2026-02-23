"""
评论 DOM 解析模块
从 Playwright 页面中提取评论数据
"""
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional


class CommentParser:
    """从知乎页面 DOM 中解析评论"""

    # ---- 知乎评论区常见选择器（可能随版本变化，用 discover 模式校正） ----
    SELECTORS = {
        # 评论按钮（用于触发评论区展开）
        'comment_button': [
            'button[data-zop-retarget="comment"]',
            'button.ContentItem-action:has(.Icon--comment)',
            'button[aria-label*="评论"]',
        ],
        # 评论区容器
        'comment_panel': [
            '.Comments-container',
            '.CommentPanel',
            '.css-1ren4go',            # 新版 class
            '[class*="CommentList"]',
        ],
        # 单条评论
        'comment_item': [
            '.CommentItem',
            '.CommentListV2-item',
            '[class*="CommentItem"]',
        ],
        # 评论内容
        'comment_content': [
            '.CommentItem-content',
            '.CommentRichText',
            '[class*="CommentContent"]',
        ],
        # 评论作者
        'comment_author': [
            '.CommentItem-authorName',
            '.UserLink-link',
            '[class*="AuthorName"]',
        ],
        # 评论点赞数
        'comment_likes': [
            '.CommentItem-likeButton span',
            'button[aria-label*="赞同"] span',
            '[class*="like"] span',
        ],
        # 评论时间
        'comment_time': [
            '.CommentItem-time',
            'time',
            '[class*="time"]',
        ],
        # 子评论展开按钮
        'child_expand': [
            '.CommentItem-childToggle',
            'button:has-text("查看全部")',
            'button:has-text("条回复")',
        ],
        # 子评论容器
        'child_container': [
            '.CommentItem-childComments',
            '[class*="ChildComment"]',
        ],
        # "查看更多评论" / 加载按钮
        'load_more': [
            'button:has-text("展开更多")',
            'button:has-text("查看更多")',
            '.CommentListV2-footer button',
        ],
    }

    async def discover_selectors(self, page) -> Dict[str, str]:
        """
        发现模式：自动检测页面中实际存在的选择器
        返回每种元素对应的第一个匹配选择器
        """
        found = {}
        for name, candidates in self.SELECTORS.items():
            for selector in candidates:
                try:
                    count = await page.locator(selector).count()
                    if count > 0:
                        found[name] = selector
                        print(f"  [发现] {name}: '{selector}' (找到 {count} 个)")
                        break
                except Exception:
                    continue
            if name not in found:
                print(f"  [未找到] {name}: 所有候选选择器均未匹配")
        return found

    async def _find_selector(self, page, name: str) -> Optional[str]:
        """尝试多个候选选择器，返回第一个匹配的"""
        for selector in self.SELECTORS.get(name, []):
            try:
                if await page.locator(selector).count() > 0:
                    return selector
            except Exception:
                continue
        return None

    async def parse_root_comments(self, page) -> List[Dict]:
        """
        解析页面中所有可见的根评论
        :return: [{id, author, content, like_count, created_time, child_count}, ...]
        """
        item_sel = await self._find_selector(page, 'comment_item')
        if not item_sel:
            print("  [警告] 未找到评论元素选择器")
            return []

        comments = []
        items = page.locator(item_sel)
        count = await items.count()

        for i in range(count):
            item = items.nth(i)
            comment = await self._parse_single_comment(page, item)
            if comment:
                # 检查是否是子评论容器里的（跳过）
                # 根评论通常不在子评论容器内
                is_child = await self._is_child_comment(item)
                if not is_child:
                    # 计算子评论数量
                    child_count = await self._get_child_comment_count(item)
                    comment['child_count'] = child_count
                    comment['is_child'] = 0
                    comment['parent_id'] = None
                    comment['reply_to'] = None
                    comments.append(comment)

        return comments

    async def parse_child_comments(self, page, root_item, parent_id: str) -> List[Dict]:
        """
        展开并解析某条根评论的所有子评论
        :param page: Playwright page
        :param root_item: 根评论的 locator
        :param parent_id: 父评论 ID
        :return: [{id, author, content, like_count, reply_to, created_time}, ...]
        """
        # 先点击"查看全部 X 条回复"
        expand_sel = await self._find_selector(page, 'child_expand')
        if expand_sel:
            expand_btn = root_item.locator(expand_sel)
            while await expand_btn.count() > 0:
                try:
                    await expand_btn.first.click()
                    await page.wait_for_timeout(1000)
                except Exception:
                    break

        # 找子评论容器
        child_sel = await self._find_selector(page, 'child_container')
        if not child_sel:
            # 尝试在根评论内部直接找评论项
            child_sel = await self._find_selector(page, 'comment_item')
            if not child_sel:
                return []

        child_container = root_item.locator(child_sel)
        if await child_container.count() == 0:
            return []

        # 解析子评论中的每一项
        item_sel = await self._find_selector(page, 'comment_item')
        if not item_sel:
            return []

        child_items = child_container.locator(item_sel)
        children = []
        count = await child_items.count()

        for i in range(count):
            child = await self._parse_single_comment(page, child_items.nth(i))
            if child:
                child['is_child'] = 1
                child['parent_id'] = parent_id
                # 尝试提取 reply_to
                child['reply_to'] = await self._get_reply_to(child_items.nth(i))
                children.append(child)

        return children

    async def _parse_single_comment(self, page, item) -> Optional[Dict]:
        """解析单条评论"""
        try:
            # 提取评论 ID
            comment_id = await self._extract_comment_id(item)
            if not comment_id:
                return None

            # 作者
            author_sel = await self._find_selector(page, 'comment_author')
            author = ''
            if author_sel:
                author_el = item.locator(author_sel).first
                if await author_el.count() > 0:
                    author = (await author_el.text_content() or '').strip()

            # 内容
            content_sel = await self._find_selector(page, 'comment_content')
            content = ''
            if content_sel:
                content_el = item.locator(content_sel).first
                if await content_el.count() > 0:
                    content = (await content_el.text_content() or '').strip()

            # 点赞数
            likes_sel = await self._find_selector(page, 'comment_likes')
            like_count = 0
            if likes_sel:
                likes_el = item.locator(likes_sel).first
                if await likes_el.count() > 0:
                    likes_text = (await likes_el.text_content() or '').strip()
                    like_count = self._parse_like_count(likes_text)

            # 时间
            time_sel = await self._find_selector(page, 'comment_time')
            created_time = ''
            if time_sel:
                time_el = item.locator(time_sel).first
                if await time_el.count() > 0:
                    time_text = (await time_el.text_content() or '').strip()
                    created_time = self._parse_time(time_text)

            return {
                'id': comment_id,
                'author_name': author or '匿名用户',
                'content': content,
                'like_count': like_count,
                'created_time': created_time,
            }
        except Exception as e:
            print(f"  [解析异常] {e}")
            return None

    async def _extract_comment_id(self, item) -> Optional[str]:
        """从元素属性中提取评论 ID"""
        # 尝试多种属性
        for attr in ['data-id', 'id', 'data-comment-id', 'data-za-detail-view-element_id']:
            try:
                val = await item.get_attribute(attr)
                if val:
                    # 提取数字部分
                    numbers = re.findall(r'\d+', val)
                    if numbers:
                        return numbers[0]
            except Exception:
                continue

        # 尝试从 innerHTML 中找 comment id 模式
        try:
            html = await item.inner_html()
            # 知乎评论通常有 data-id="数字" 在子元素上
            match = re.search(r'data-id="(\d+)"', html)
            if match:
                return match.group(1)
        except Exception:
            pass

        return None

    async def _is_child_comment(self, item) -> bool:
        """判断某评论项是否是子评论"""
        try:
            # 检查父元素是否是子评论容器
            parent_class = await item.evaluate('''el => {
                const parent = el.parentElement;
                if (!parent) return '';
                return parent.className || '';
            }''')
            if 'child' in parent_class.lower() or 'reply' in parent_class.lower():
                return True
        except Exception:
            pass
        return False

    async def _get_child_comment_count(self, item) -> int:
        """获取根评论的子评论数量"""
        try:
            # 找"查看全部 X 条回复"或类似的文本
            text = await item.text_content() or ''
            match = re.search(r'(\d+)\s*条回复', text)
            if match:
                return int(match.group(1))
            match = re.search(r'查看全部\s*(\d+)', text)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 0

    async def _get_reply_to(self, item) -> Optional[str]:
        """提取子评论的回复对象"""
        try:
            text = await item.text_content() or ''
            match = re.search(r'回复\s*(.+?)[:：\s]', text)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_like_count(text: str) -> int:
        """解析点赞数文本"""
        if not text:
            return 0
        text = text.strip()
        if text in ('', '赞', '赞同'):
            return 0
        # "1.2k" → 1200
        match = re.match(r'([\d.]+)\s*[kK]', text)
        if match:
            return int(float(match.group(1)) * 1000)
        # "1.2w" / "1.2万" → 12000
        match = re.match(r'([\d.]+)\s*[wW万]', text)
        if match:
            return int(float(match.group(1)) * 10000)
        # 纯数字
        numbers = re.findall(r'\d+', text)
        return int(numbers[0]) if numbers else 0

    @staticmethod
    def _parse_time(text: str) -> str:
        """
        解析知乎时间文本为 ISO 格式
        支持: "刚刚", "X分钟前", "X小时前", "昨天", "X天前", "2024-01-15", "01-15"
        """
        if not text:
            return ''
        text = text.strip()
        now = datetime.now()

        if '刚刚' in text:
            return now.isoformat()[:19]

        match = re.match(r'(\d+)\s*分钟前', text)
        if match:
            return (now - timedelta(minutes=int(match.group(1)))).isoformat()[:19]

        match = re.match(r'(\d+)\s*小时前', text)
        if match:
            return (now - timedelta(hours=int(match.group(1)))).isoformat()[:19]

        if '昨天' in text:
            return (now - timedelta(days=1)).isoformat()[:19]

        match = re.match(r'(\d+)\s*天前', text)
        if match:
            return (now - timedelta(days=int(match.group(1)))).isoformat()[:19]

        # "2024-01-15" 格式
        match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}T00:00:00"

        # "01-15" 格式（当年）
        match = re.match(r'(\d{1,2})-(\d{1,2})', text)
        if match:
            return f"{now.year}-{int(match.group(1)):02d}-{int(match.group(2)):02d}T00:00:00"

        return text
