# 知乎浏览器评论补爬工具

用 Playwright 浏览器自动化补爬知乎 API 无法获取的评论（评论翻页上限以外的部分）。

## 工作原理

1. 从现有数据库中查找 **API 评论数 < 预期评论数** 的回答
2. 用 Playwright 打开对应的知乎回答页面
3. JS 触发评论区展开 → 滚动加载全部评论 → DOM 解析
4. 写入同一个 SQLite 数据库（`INSERT OR IGNORE` 自动去重）

## 安装

```bash
cd zhihu_browser_crawler

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

## 使用

### 前提

- 已经用 `zhihu_crawler_enhanced` 跑过 API 爬虫
- 有 `config.yaml`（含有效 Cookie）
- 有 `data/zhihu.db`（含已爬取的数据）

### 查看缺口

```bash
# 统计概览
python main.py --stats

# 列出缺口 > 100 的回答
python main.py --list --min-gap 100
```

### 调试（首次使用必看）

```bash
# 发现模式：检测知乎 DOM 选择器是否匹配
python main.py --discover --answer-id 245054626
```

如果选择器未匹配，需要根据输出修改 `comment_parser.py` 中的 `SELECTORS`。

### 补爬评论

```bash
# 补爬缺口 > 50 的评论（headed 模式，可看到浏览器操作）
python main.py --min-gap 50

# 先跑 5 个试试效果
python main.py --min-gap 100 --max 5

# Headless 模式（稳定后使用）
python main.py --min-gap 50 --headless

# 爬取指定回答
python main.py --answer-id 2835057077
```

### 自定义路径

```bash
python main.py --config ../zhihu_crawler_enhanced/config.yaml \
               --db ../zhihu_crawler/data/zhihu.db \
               --min-gap 50
```

## 文件结构

| 文件 | 说明 |
|------|------|
| `main.py` | CLI 入口 |
| `gap_finder.py` | 数据库缺口查询 |
| `browser_crawler.py` | Playwright 爬取核心 |
| `comment_parser.py` | DOM 评论解析 |

## 注意事项

- **首次运行**务必用 `--discover` 模式验证选择器
- 知乎可能更新前端结构，导致选择器失效，此时需更新 `comment_parser.py`
- 大量爬取时建议 headed 模式 + 适当增大延迟
- 数据直接写入 API 爬虫的同一 DB，导出仍用 `zhihu_crawler_enhanced/export.py`
