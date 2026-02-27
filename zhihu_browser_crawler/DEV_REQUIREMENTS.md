# 知乎浏览器评论补爬工具 — 开发需求文档

> **面向读者**: 拿到 API 爬虫 (`zhihu_crawler_enhanced`) 源码和 SQLite 数据库后，从零开发浏览器补爬模块的开发者  
> **日期**: 2026-02-26（基于代码实际状态更新）

---

## 1. 你要解决什么问题

API 爬虫已经采集了知乎的「问题→回答→评论」数据，但 **知乎 API 对评论分页有硬上限**：

| 排序方式 | 单次可拉取 | 双排序合并后 |
|---------|-----------|------------|
| `order=normal` | ~200-300 条 | — |
| `order=score` | ~200-300 条 | — |
| 合并去重 | — | ~400-600 条 |

对于高热度回答（评论数 >600），API 爬虫**必然缺失**大量评论。你要做的是：

> **用 Playwright 浏览器自动化，模拟真人操作，打开知乎页面，滚动加载评论，从 DOM 中提取 API 无法获取的评论数据，写入同一个 SQLite 数据库。**

---

## 2. 前置条件

你接手时已有：

| 资产 | 路径 | 说明 |
|------|------|------|
| API 爬虫 | `zhihu_crawler_enhanced/` | 已跑通，代码无需修改 |
| SQLite 数据库 | `zhihu_crawler/data/zhihu.db` | 含 `questions`、`answers`、`comments` 三表 |
| 配置文件 | `zhihu_crawler/config.yaml` | 含有效 Cookie、关键词列表 |

数据库中你需要用到的关键字段：

```sql
-- answers 表：找出哪些回答有评论缺口
SELECT answer_id, question_id, comment_count  -- comment_count = API 返回的理论评论数
FROM answers WHERE status = 'done';

-- comments 表：计算实际已有多少
SELECT COUNT(*) FROM comments WHERE answer_id = ?;

-- 写入：INSERT OR IGNORE 自动去重
INSERT OR IGNORE INTO comments (comment_id, answer_id, parent_id, is_child, ...)
VALUES (?, ?, ?, ?, ...);
```

---

## 3. 知乎评论区 DOM 结构（2026-02 版本）

> [!CAUTION]
> **这是最容易让你踩坑的部分。** 知乎的评论区有 3 层嵌套 UI，每层的 DOM 结构不同，且使用混淆 CSS 类名。

### 3.1 三层评论 UI

```
┌── 回答页面 ──────────────────────────────────────────────────────┐
│  ┌── 内联评论区 ────────────────────────────────────────────┐   │
│  │  页面底部直接显示 前 ~20 条评论                            │   │
│  │  需要向下滚动页面才能看到底部的按钮                         │   │
│  │  底部有 [点击查看全部评论] 按钮 ← 必须先滚到这里            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                     │ 点击 (进入全评论面板)                      │
│                     ▼                                          │
│  ┌── 全评论面板 (.css-34podr) ─────────────────────────────┐   │
│  │  右侧弹出面板，有独立滚动容器（虚拟滚动）                 │   │
│  │  可加载全部根评论（300+ 条）                              │   │
│  │                                                         │   │
│  │  根评论下方可能出现两种按钮（极易混淆 → 见坑 #9）:         │   │
│  │  ┌─────────────────────────────────────────────────┐     │   │
│  │  │ A. [查看全部 xx 条回复]  (≥6 条) → 打开模态框    │     │   │
│  │  │ B. [展开其他 x 条回复]   (<6 条) → 原地展开      │     │   │
│  │  └─────────────────────────────────────────────────┘     │   │
│  │                     │ 仅 A 类按钮                       │   │
│  │                     ▼                                    │   │
│  │  ┌── 楼中楼模态框 (.css-1svde17) ──────────────────┐    │   │
│  │  │  独立的全屏模态框                                 │    │   │
│  │  │  显示该根评论的所有子回复                          │    │   │
│  │  │  有自己的滚动容器                                 │    │   │
│  │  │  关闭方式: Escape 或 X 按钮                       │    │   │
│  │  └──────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 关键选择器清单

| 元素 | 选择器 | 稳定性 | 说明 |
|------|--------|--------|------|
| 评论按钮 | `button.ContentItem-action` + 含"评论"文字 | ⚠️ 中 | 回答级别，避免点到问题级 |
| 评论内容节点 | `.CommentContent` | ✅ 稳定 | **唯一稳定的选择器**，所有层通用 |
| 全评论面板 | `.css-34podr` | ❌ 类名可能变 | 面板功能正常，但 CSS 类名是编译器生成的 hash，知乎前端重新部署后可能变成别的名字 |
| 楼中楼模态框 | `.css-1svde17` | ❌ 类名可能变 | 同上，模态框本身功能正常，只是代码定位用的类名可能失效 |
| 根评论容器 | `[data-id]` 属性 | ✅ 稳定 | `data-id` 值即评论 ID |
| 楼中楼按钮 | `button` + 正则 `/查看全部\s*\d+\s*条回复/` | ✅ 稳定 | 按文本匹配 |
| 模态框关闭按钮 | `button[aria-label="关闭"]` | ⚠️ 中 | 注意排除 `signFlowModal` 的关闭按钮 |
| 模态框内展开按钮 | `button` + 正则 `/展开其他.*条回复/` | ✅ 稳定 | 按文本匹配 |
| 作者链接 | `a[href*="/people/"]` | ✅ 稳定 | 评论包装节点内 |
| 点赞按钮 | SVG class 含 `Heart` 的 button | ⚠️ 中 | — |

> [!IMPORTANT]
> **必须实现选择器发现模式 (`--discover`)**。首次运行时验证上述选择器是否仍有效，输出匹配/未匹配状态。知乎每次迭代前端都可能改变 CSS 类名。

---

## 4. 爬取流程（两阶段策略）

### 4.1 整体流程

```
输入: answer_id + question_id
                │
                ▼
    ┌── 页面导航 ──┐
    │ goto 回答 URL │
    └──────┬───────┘
           ▼
    ┌── 检测评论区 ─┐
    │ 页面是否含有   │──→ 是 → 标记 comments_closed → 结束
    │"评论区已关闭"  │
    └──────┬───────┘
           ▼ 否
    ┌── 触发评论区 ──────────────────┐
    │ 点击回答底部的评论按钮           │
    │ （用 ContentItem-action 定位） │
    └──────┬────────────────────────┘
           ▼
    ┌── 滚动内联评论区 ─────────────────────────────────┐
    │ 内联评论区只显示前 ~20 条，底部有"点击查看全部评论"  │
    │ 必须用 window.scrollBy(0, 600) 向下滚动页面      │
    │ 直到"点击查看全部评论"按钮出现在视口中              │
    │ ⚠️ 注意：这里是滚动页面本身，不是滚动评论容器      │
    └──────┬───────────────────────────────────────────┘
           ▼
    ┌── 进入全评论面板 ─────────────────────────────────┐
    │ 点击"点击查看全部评论" → 右侧弹出全评论面板         │
    │ 此面板有独立滚动容器 (.css-34podr)                 │
    │ ⚠️ 进入面板后，滚动对象从 window 变为面板容器       │
    └──────┬───────────────────────────────────────────┘
           ▼
    ╔══════════════════════════════╗
    ║   阶段一: 在面板内滚动        ║  ← 不打开任何模态框
    ║                              ║
    ║  每轮:                       ║
    ║  1. 提取当前可见的根评论       ║
    ║  2. 点击"展开其他x条回复"按钮 ║  ← 原地展开，安全
    ║  3. 记录"查看全部"楼中楼元数据 ║  ← 只记录不点击
    ║  4. 增量滚动面板 +600px       ║
    ║  5. 重复直到底部(stale检测)   ║
    ║                              ║
    ║  stale 策略:                  ║
    ║  - 已到底部: 连续10轮无新内容  ║
    ║  - 未到底部: 容忍30轮+3s重试  ║
    ║                              ║
    ║  阶段一结束后:                ║
    ║  - 记录"世界尽头"到 DB        ║
    ╚════════════╤═════════════════╝
                 ▼
    ╔══════════════════════════════╗
    ║   阶段二: 楼中楼模态框处理    ║  ← 逐个处理
    ║                              ║
    ║  对每个 thread:              ║
    ║  1. 确认面板存在              ║
    ║  2. 在面板内滚动找到按钮      ║
    ║  3. 点击"查看全部"→模态框打开 ║
    ║  4. 模态框内滚动提取子评论    ║
    ║  5. Escape 关闭模态框        ║
    ║  6. 检查面板是否存活          ║
    ╚════════════╤═════════════════╝
                 ▼
            写入数据库
```

### 4.2 为什么必须分两阶段

> [!WARNING]
> **一开始你可能会想"一边滚动一边处理楼中楼"** — 这行不通。原因：
> 1. 打开楼中楼模态框后，按 Escape 关闭时**可能连带关闭全评论面板**
> 2. 面板关闭后，你的滚动位置丢失，DOM 中的评论节点全部消失
> 3. 即使重新打开面板，也会从头开始，导致无限循环

### 4.3 前置步骤：从内联评论区进入全评论面板

> [!IMPORTANT]
> 这是新手最容易忽略的一步。知乎页面初始只显示内联评论（~20 条），**必须先进入全评论面板才能开始爬取**。

1. 点击回答底部的评论按钮 → 展开内联评论区
2. 用 `window.scrollBy(0, 600)` **向下滚动页面**（注意：此时还没有面板容器，只能滚页面）
3. 滚几次后找到「点击查看全部评论」或「查看全部评论」按钮
4. 点击该按钮 → 右侧弹出全评论面板
5. 等待 `.CommentContent` 出现 → 全评论面板加载完成

⚠️ 进入全评论面板后，**滚动操作的目标从 `window` 变为面板容器** `.css-34podr`。这是坑 #1 的核心原因。

### 4.4 阶段一详细要求

**目标**: 滚动面板到底，收集所有根评论 + 展开小楼层 + 发现大楼中楼

每轮循环做以下事情：
1. 提取当前可见的根评论（`_extract_all_comments()`），用 `saved_ids` 去重后保存
2. **点击所有可见的「展开其他 x 条回复」按钮**（内联展开，不打开模态框，不影响滚动）→ 展开后的子评论在下一轮提取中被捕获，同时记录到 `inline_expanded_threads`
3. 扫描「查看全部 N 条回复」按钮，记录 `{rootId, replyCount}` 到 `discovered_threads`（只记录不点击）
4. 增量滚动面板 `scrollTop += 600`
5. stale 检测（分两种场景）：
   - **已到物理底部** (`scrollTop + clientHeight >= scrollHeight - 50`)：连续 **10 轮**无新内容 → 认为到底
   - **未到物理底部**：可能是懒加载延迟，额外等待 3 秒后重试，容忍 **30 轮**才判定到底
   - 进度判定条件：根评论数增长 / 保存数增长 / 发现新楼中楼 → 重置 stale 计数

**阶段一结束后**：记录面板最底部的可见评论信息（"世界尽头"）到 `scroll_bottom_log` 表，用于判断爬虫是否真正滚到了底部。

**输出**: 根评论 + 小楼层子评论已保存到 DB，大楼中楼列表 `[{rootId, replyCount}, ...]`，内联展开列表 `[{rootId, replyCount}, ...]`

### 4.5 阶段二详细要求

**对每个楼中楼 thread**:

1. 检查 `.css-34podr` 面板是否存在，不存在则重新点击"查看全部评论"
2. 在面板中增量滚动，查找 `[data-id="rootId"]` 元素
3. 找到后定位其内部的"查看全部 X 条回复"按钮并点击
4. 等待 `.css-1svde17` 模态框出现
5. 在模态框内循环滚动（最多 200 轮）：
   - 用 `getComputedStyle` 找模态框内真正的 `overflow-y` 滚动容器，增量滚动 +600px
   - 点击模态框内的"展开其他 X 条回复"按钮（如果有）
   - 用 `_extract_modal_comments()` 提取模态框内的 `.CommentContent` 节点（限定 `.css-1svde17` 范围，避免面板评论干扰）
   - stale 检测（连续 **5** 轮模态框内评论数不变 → 到底了）
6. 按 Escape 关闭模态框

### 4.6 阶段二中的关闭与恢复

Escape 关闭模态框后，**必须验证全评论面板仍然存在**：

1. 检查 `.css-34podr` 是否仍在 DOM 中
2. 如果面板被连带关闭 → 重新点击"查看全部评论"恢复面板
3. 然后继续处理下一个楼中楼（从面板顶部重新滚动查找）

> [!CAUTION]
> 如果你把"展开其他x条回复"（不开模态框的 B 类按钮）误当作 A 类处理，Escape 会直接关闭全评论面板，因为根本没有模态框可关。见坑 #9。

---

## 5. ⚠️ 关键坑点清单

> [!CAUTION]
> 以下坑点全部来自实际开发中踩过的坑，每个都导致过严重 bug。**务必逐一阅读。**

### 坑 #1：`window.scrollBy()` 无效

知乎回答页面**进入全评论面板后**，页面设置了 `overflow-y: hidden`，**`window.scrollBy()` 和 `window.scrollTo()` 完全不起作用**。

⚠️ 但在前置步骤（从内联评论区找"点击查看全部评论"按钮时），`window.scrollBy(0, 600)` 是**有效的**，因为此时还没有弹出面板，页面本身可以滚动。

✅ 进入全评论面板后，找到实际的滚动容器，直接操作其 `scrollTop`：
```javascript
const container = document.querySelector('.css-34podr');
container.scrollTop += 600;
```

⚠️ 如果 `.css-34podr` 找不到，回退方案：从 `.CommentContent` 向上遍历 DOM，找 `overflow-y: scroll` 或 `overflow-y: auto` 的祖先元素。

---

### 坑 #2：虚拟滚动 — DOM 不保留全部评论

知乎全评论面板使用 **CSS 虚拟滚动**（不是传统的 `IntersectionObserver`）。只有视窗内的评论节点存在于 DOM 中，滚过去的评论会被从 DOM 中**移除**。

❌ 不能：先滚到底再遍历 DOM（旧的已消失）  
✅ 必须：每轮滚动后**立即**提取当前可见的评论节点

---

### 坑 #3：Escape 键的连锁效应（仅程序自动化会遇到）

> 你手动在浏览器中操作时不会遇到这个问题。这是程序自动化特有的坑。

程序用 `page.keyboard.press('Escape')` 关闭楼中楼模态框时：
- 如果第一次 Escape 没关掉模态框，程序会**快速再按一次**
- 第二次 Escape 的间隔太短（<1 秒），被全评论面板接收了
- 结果：模态框关了，**全评论面板也被关了**
- 人手动操作不会连按两次 Escape，所以只有自动化才会触发

✅ 每次 Escape 后，检查 `.css-34podr` 是否还在：
```python
panel_exists = await page.evaluate('() => !!document.querySelector(".css-34podr")')
if not panel_exists:
    await _enter_full_comment_page()  # 重新打开
```

---

### 坑 #4：`signFlowModal` 干扰模态框检测

页面上**始终存在**一个隐藏的登录弹窗 `signFlowModal`（class 含 `Modal`，有 `button[aria-label="关闭"]`）。

❌ 不能用 `document.querySelector('[class*="Modal"]')` 或 `button[aria-label="关闭"]` 通用检测模态框  
✅ 检测楼中楼模态框时，必须排除 `signFlowModal`：
```javascript
const closeBtn = document.querySelector('button[aria-label="关闭"]');
if (!closeBtn) return false;
const parent = closeBtn.closest('.signFlowModal');
if (parent) return false;  // 这是登录弹窗，不是楼中楼
return true;
```

---

### 坑 #5：CSS 类名是编译器生成的 hash，可能随版本更新失效

`.css-34podr`、`.css-1svde17`、`.css-jp43l4` 等类名是知乎前端构建工具（CSS-in-JS）自动生成的 hash 值。**面板和模态框本身功能完全正常**——你手动浏览时不会有任何问题。但这些类名不是开发者手写的语义化名字，而是编译器根据样式内容算出来的 hash，知乎每次重新部署前端，hash 就可能变（比如 `.css-34podr` 变成 `.css-xyz789`）。

对我们的影响：代码里用 `document.querySelector('.css-34podr')` 定位面板，一旦类名变了就找不到了。

✅ 做法：
1. 优先用语义化选择器（`.CommentContent`、`[data-id]`、`a[href*="/people/"]`）——这些是开发者手写的，不会轻易变
2. 混淆类名作为 **fallback**，并在发现模式 (`--discover`) 中验证当前是否有效
3. 代码中集中管理所有选择器，方便统一更新

---

### 坑 #6：跳到底部只能看到底部

使用 `scrollTop = scrollHeight` 的跳底式滚动，会导致虚拟滚动只渲染底部的 ~20 条评论。**中间的大量评论（包括带楼中楼按钮的高互动评论）从未出现在 DOM 中**。

✅ 必须使用 **增量滚动** (`scrollTop += 600`)，每次前进一小段，让虚拟滚动引擎逐步渲染中间的评论。

---

### 坑 #7：评论 ID 的提取

知乎评论的 ID 通过 `data-id` 属性存储在评论容器节点上，不是在 `.CommentContent` 节点自身。

✅ 获取评论 ID：
```javascript
// 从 CommentContent 向上找带 data-id 的祖先
let el = commentContentNode.parentElement;
while (el) {
    const did = el.getAttribute('data-id');
    if (did) return did;
    el = el.parentElement;
}
```

⚠️ 有些评论节点**没有** `data-id`（注销用户、隐藏评论），跳过即可。

---

### 坑 #8：根评论 vs 子评论判定

不能用嵌套层级判断。实际判定方式：
- 根评论容器的 class 含 `css-jp43l4`
- 子评论容器的 class 含 `css-1kwt8l8`

但这些是混淆类名。更可靠的做法是通过 **上下文**：
- 阶段一提取的都是根评论（面板中直接可见的评论）
- 阶段二模态框内提取的都是子评论（`is_child = 1`，`parent_id = rootId`）

---

### 坑 #9：两种楼中楼按钮的区别（极易混淆）

知乎评论区的子评论展开有**两种完全不同的交互**：

| 按钮文字 | 回复数 | 点击后行为 | 处理阶段 |
|----------|--------|-----------|---------|
| `查看全部 xx 条回复` | ≥6 条 | 打开**模态框**（新的弹窗层） | 阶段二：需单独滚动模态框 |
| `展开其他 x 条回复` | <6 条 | **原地展开**，在面板内直接显示 | 阶段一：直接点击即可 |

混淆这两种按钮会导致严重 bug：
- 如果在阶段二对"展开其他"按钮发 Escape → 关掉的是全评论面板（因为根本没有模态框可关）
- 如果忽略"展开其他"按钮 → 丢失这些楼层的子评论

✅ 正确做法：
- **阶段一**：每轮滚动时，自动点击所有可见的 `展开其他.*条回复` 按钮（内联展开，不影响滚动）
- **阶段二**：只处理 `查看全部.*条回复` 按钮（打开模态框后滚动提取）

---

### 坑 #10：Windows 控制台编码

Windows 默认 GBK 编码，打印中文评论时可能报 `UnicodeEncodeError`。  
✅ 启动时设置：
```python
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
```

---

### 坑 #11：不要用内容去重

你可能会想通过 `GROUP BY answer_id, author_name, content HAVING COUNT(*) > 1` 来清理重复数据 — **不要这样做**。

同一个用户确实可能在同一个评论区发多条相同内容的评论（比如"共勉"发 5 次、"踢"发 7 次），每条都有**独立的知乎评论 ID**，是合法的不同评论。

我们项目早期曾用内容去重误删了 **219 条合法评论**（占比 0.3%），教训深刻。

✅ 去重**只能**基于评论 ID（`id` 主键），DB 的 `INSERT OR IGNORE` 已经自动处理  
❌ 绝对不要在导出/清理阶段按 `作者 + 内容` 做去重

---

## 6. 反检测要求

| 措施 | 必要性 | 说明 |
|------|--------|------|
| `playwright-stealth` | **必须** | 绕过 `navigator.webdriver` 检测 |
| Cookie 注入 | **必须** | 解析 Cookie 字符串 → `context.add_cookies()` |
| 预热访问 | 建议 | 先访问知乎首页，等 3-5 秒，再访问目标页 |
| 随机延迟 | 建议 | 每个操作间 1-3 秒随机等待 |
| Headed 模式 | 调试期必须 | 开发/调试阶段不要用 headless |

---

## 7. 数据库对接约定

### 7.1 共用同一个 DB

浏览器爬虫**不新建数据库**，直接写入 API 爬虫的 `zhihu.db`。好处：
- `INSERT OR IGNORE` 自动与 API 数据去重
- 导出仍用 API 爬虫的 `export.py`

### 7.2 写入格式

> [!WARNING]
> 注意列名是 **`id`**，不是 `comment_id`。这是 API 爬虫建表时定义的列名，写错会报 SQL 错误。

```sql
-- 首次运行时，给已有的 comments 表添加两列（已有 API 数据: source='api', inserted_at=NULL）
ALTER TABLE comments ADD COLUMN source TEXT DEFAULT 'api';
ALTER TABLE comments ADD COLUMN inserted_at TEXT DEFAULT NULL;

-- 浏览器爬虫写入时
INSERT OR IGNORE INTO comments
  (id, answer_id, parent_id, is_child, author_name, content, like_count, reply_to, created_time, source, inserted_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'browser', ?);  -- inserted_at 传当前时间
```

- `id`: 从 `data-id` 属性提取的纯数字字符串（即评论 ID）
- `parent_id`: 根评论为 `NULL`，子评论为所属根评论的 ID
- `is_child`: 根评论 `0`，子评论 `1`
- `created_time`: ISO 格式 (`2024-01-15T00:00:00`)，这是**评论在知乎上的发表时间**
- `source`: 固定写 `'browser'`，用于区分 API 爬取（`'api'`）和浏览器爬取的数据
- `inserted_at`: 写入数据库的时间（`YYYY-MM-DD HH:MM:SS`），用于追溯每条数据的入库时间。API 爬虫写入的旧数据此列为 `NULL`（无法追溯）

### 7.3 断点续爬进度跟踪

代码使用独立的 `browser_crawl_progress` 表跟踪每个回答的爬取进度（不修改原 answers 表）：

```sql
CREATE TABLE IF NOT EXISTS browser_crawl_progress (
    answer_id   TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/crawling/done/failed/comments_closed
    comments_found INTEGER DEFAULT 0,
    started_at  TEXT,
    finished_at TEXT
);
```

| status 值 | 含义 | 重新运行时 |
|-----------|------|----------|
| `crawling` | 正在爬取（或中途崩溃） | 会重新爬取 |
| `done` | 爬取流程完成 | 跳过 |
| `failed` | 爬取失败 | 会重新爬取 |
| `comments_closed` | 评论区已被答主/管理员关闭 | 跳过（不再重试） |

> [!NOTE]
> `done` 不代表所有评论都已采集。它表示爬取流程已跑完（滚到底 + 楼中楼处理完），但由于知乎虚拟滚动和反爬限制，高评论数的回答仍可能有较大缺口。

#### 评论区关闭检测

知乎允许答主或管理员关闭评论区。此时评论按钮仍显示评论数（如 `1672 条评论`），但点击后评论区内显示 **"评论区已关闭"**。

检测时机：
1. **点击评论按钮后，等待 `.CommentContent` 加载超时时** — 检查 `document.body.innerText.includes("评论区已关闭")`
2. **评论按钮未找到时的兜底检测** — 同上

> [!IMPORTANT]
> "评论区已关闭" 文字**只在点击评论按钮后**才出现在 DOM 中。页面初始加载时检测不到此文字，所以检测必须放在评论按钮点击之后。

---

## 8. 实际文件结构

```
zhihu_browser_crawler/
├── main.py              # CLI 入口：--stats / --list / --discover / --answer-id / --min-gap
│                        #         --show-progress / --reset-progress
│                        #         --export-tracking / --export-bottom
│                        #         --headless / --delay / --max
├── gap_finder.py        # 缺口查询：对比 comment_count vs 实际 COUNT(*)
├── browser_crawler.py   # 核心爬取：setup → crawl → teardown
│                        #   评论提取逻辑内联在 JS evaluate 中
│                        #   _extract_all_comments() — 面板内提取
│                        #   _extract_modal_comments() — 模态框内提取
├── requirements.txt     # playwright, playwright-stealth
├── check_dupes.py       # 检查重复评论
├── clean_dups.py        # 清理重复评论（基于 ID）
├── clean_all_dups.py    # 批量清理重复
├── debug_scroll.py      # 调试滚动逻辑
├── debug_extract.py     # 调试评论提取
├── inspect_dom.py       # DOM 检查工具
├── patch_id.py          # ID 修复工具
└── DEV_REQUIREMENTS.md  # 本文档
```

> [!NOTE]
> 原设计建议拆分出 `comment_parser.py`，但实际开发中评论提取逻辑内联在 `browser_crawler.py` 的 JavaScript `evaluate()` 中完成，因为提取逻辑必须在浏览器 DOM 环境中执行，拆分到 Python 文件意义不大。

---

## 9. 推荐的调试流程

1. **`--discover` 模式** → 验证选择器是否匹配当前知乎版本
2. **单条回答、headed 模式** → `python main.py --answer-id <ID>` 看浏览器操作
3. **确认阶段一** → 根评论数量是否持续增长，楼中楼是否被发现
4. **确认阶段二** → 模态框是否正确打开/关闭，面板是否在 Escape 后存活
5. **第二次运行同一 answer** → 验证 `INSERT OR IGNORE` 去重，新增应为 0
6. **批量运行、headless** → `python main.py --min-gap 100 --headless`

---

## 10. 楼中楼追溯功能

阶段二处理完所有楼中楼后，爬虫会汇总每个楼层的预期回复数 vs 实际采集数，写入 `thread_tracking` 表：

```sql
CREATE TABLE IF NOT EXISTS thread_tracking (
    answer_id        TEXT NOT NULL,
    root_comment_id  TEXT NOT NULL,
    thread_type      TEXT NOT NULL,     -- '模态框' 或 '内联展开'
    expected_replies INTEGER NOT NULL,  -- 按钮上显示的回复数
    actual_replies   INTEGER DEFAULT 0, -- DB 中实际采集到的子评论数
    crawled_at       TEXT,
    PRIMARY KEY (answer_id, root_comment_id)
);
```

### 追溯类型

| thread_type | 来源 | 说明 |
|-------------|------|------|
| `模态框` | `discovered_threads` | "查看全部 xx 条回复"→打开模态框→在模态框内滚动采集 |
| `内联展开` | `inline_expanded_threads` | "展开其他 x 条回复"→原地展开→通过阶段一常规提取捕获 |

### 控制台输出

阶段二完成后会打印追溯汇总表，带 ✓/⚠ 标记：

```
  [楼中楼追溯] 共 15 个楼层
    类型     rootId             预期   实际   差距
    -----------------------------------------
    模态框   10272198652         43     43     +0 ✓
    模态框   10434978629         28     25     +3 ⚠
    内联展开 366812336            4      4     +0 ✓
    -----------------------------------------
    合计                         75     72     +3
```

### 导出

```bash
python main.py --export-tracking                    # 导出到默认路径
python main.py --export-tracking --tracking-output path/to/output.csv  # 指定路径
```

---

## 11. 世界尽头记录功能

阶段一滚动结束后，爬虫记录面板**最底部的可见评论**信息到 `scroll_bottom_log` 表。这用于判断爬虫是否真正滚到了评论区底部。

```sql
CREATE TABLE IF NOT EXISTS scroll_bottom_log (
    answer_id        TEXT PRIMARY KEY,
    total_visible    INTEGER,       -- 面板中可见的评论总数
    last_comment_id  TEXT,          -- 最底部评论的 ID
    last_author      TEXT,          -- 最底部评论的作者
    last_content     TEXT,          -- 最底部评论的内容片段
    last_time        TEXT,          -- 最底部评论的时间
    last_likes       INTEGER DEFAULT 0,
    last_is_child    INTEGER DEFAULT 0, -- 0=根评论, 1=子评论
    scroll_rounds    INTEGER DEFAULT 0, -- 阶段一总滚动轮数
    crawled_at       TEXT
);
```

### 用途

通过对比 `total_visible`（面板可见数）和 `answers.comment_count`（期望数）判断覆盖率：

- **total_visible ≈ comment_count**：滚到了真正的底部，评论基本全覆盖
- **total_visible 远小于 comment_count**：知乎虚拟滚动/反爬限制了加载量，有大量评论未渲染

### 导出

```bash
python main.py --export-bottom                     # 导出到默认路径
python main.py --export-bottom --bottom-output path/to/output.csv  # 指定路径
```

---

## 12. CLI 命令参考

### 查看类（不启动浏览器）

```bash
python main.py --stats                       # 显示缺口统计摘要
python main.py --list --min-gap 100          # 列出缺口>100的回答
python main.py --show-progress               # 显示断点续爬进度（各状态计数）
python main.py --export-tracking             # 导出楼中楼追溯数据到 CSV
python main.py --export-bottom               # 导出世界尽头日志到 CSV
```

### 爬取类（启动浏览器）

```bash
python main.py --min-gap 50                  # 补爬缺口>50的评论（headed模式）
python main.py --min-gap 100 --max 10        # 先跑10个试试
python main.py --min-gap 50 --headless       # headless 模式
python main.py --min-gap 1 --delay 20        # 页面间延迟20-30秒
python main.py --answer-id 2835057077        # 爬取指定回答的评论
python main.py --discover --answer-id 245054626  # 选择器发现模式（调试用）
```

### 管理类

```bash
python main.py --reset-progress              # 清除进度，强制重新爬取所有回答
```

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--config` | `../zhihu_crawler/config.yaml` | 配置文件路径 |
| `--db` | `../zhihu_crawler/data/zhihu.db` | 数据库路径 |
| `--min-gap` | 50 | 最小评论缺口阈值 |
| `--max` | 无限制 | 最多处理多少个回答 |
| `--delay` | 15 | 页面间最小延迟秒数（范围为 delay ~ delay+10） |
| `--headless` | 否 | 无头模式（不弹出浏览器窗口） |
