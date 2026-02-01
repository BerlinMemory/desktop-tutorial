# 知乎爬虫

一个功能完整的知乎爬虫工具，支持按关键词搜索问题、爬取回答和评论（包括楼中楼），并导出为 CSV 格式。

## 功能特性

- ✅ 按关键词搜索知乎问题
- ✅ 爬取问题下的所有回答（支持数千条）
- ✅ 爬取回答下的所有评论（包括子评论/楼中楼）
- ✅ 断点续爬（中断后可继续）
- ✅ 数据导出为 CSV 格式
- ✅ 智能限速、自适应降速
- ✅ 自动重试、指数退避
- ✅ SQLite 本地存储

## 项目结构

```
zhihu_crawler/
├── config.yaml        # 配置文件
├── main.py            # 运行入口
├── crawler.py         # 爬取逻辑
├── database.py        # 数据库操作
├── http_client.py     # HTTP 客户端
├── export.py          # 导出 CSV
├── requirements.txt   # 依赖列表
└── data/
    ├── zhihu.db       # SQLite 数据库
    └── exports/       # CSV 导出目录
```

## 快速开始

### 1. 安装依赖

```bash
cd zhihu_crawler
pip install -r requirements.txt
```

### 2. 配置 Cookie

**重要：** 必须配置知乎登录 Cookie 才能正常运行。

获取 Cookie 的步骤：

1. 登录知乎网页版：https://www.zhihu.com
2. 打开浏览器开发者工具（按 F12）
3. 切换到 **Network（网络）** 标签页
4. 刷新页面
5. 点击任意请求，在右侧找到 **Request Headers**
6. 复制 **Cookie** 字段的完整内容
7. 粘贴到 `config.yaml` 中的 `cookie` 字段

示例：

```yaml
cookie: "d_c0=ABCD...; _zap=1234...; ..."
```

### 3. 配置搜索关键词

编辑 `config.yaml`，修改 `keywords` 字段：

```yaml
keywords:
  - "Python编程"
  - "机器学习"
  - "Web开发"
```

### 4. 运行爬虫

```bash
# 完整爬取流程（搜索 -> 回答 -> 评论）
python main.py

# 仅执行搜索阶段
python main.py --search-only

# 仅爬取回答
python main.py --answers-only

# 仅爬取评论
python main.py --comments-only
```

### 5. 导出数据

```bash
# 导出所有数据到 CSV
python main.py --export

# 或者单独运行导出脚本
python export.py
```

## 使用说明

### 配置文件说明

`config.yaml` 中的主要配置项：

```yaml
# Cookie（必填）
cookie: "your_cookie_here"

# 搜索关键词（必填）
keywords:
  - "关键词1"
  - "关键词2"

# 爬取限制
limits:
  questions_per_keyword: 20      # 每个关键词搜索多少个问题
  answers_per_question: null     # 每个问题爬多少回答，null=全部
  comments_per_answer: null      # 每个回答爬多少评论，null=全部

# 限速配置
rate_limit:
  requests_per_second: 1.5       # 每秒请求数（建议 1-2）
  retry_times: 3                 # 重试次数
  retry_backoff: 2               # 重试等待倍数
```

### 命令行参数

```bash
python main.py [选项]

选项：
  --config FILE         指定配置文件（默认：config.yaml）
  --retry-failed        重试失败的项目
  --stats               仅显示统计信息
  --export              导出数据到 CSV
  --search-only         仅执行搜索阶段
  --answers-only        仅执行回答爬取阶段
  --comments-only       仅执行评论爬取阶段
```

### 断点续爬

程序支持断点续爬，中断后再次运行会自动跳过已完成的部分：

```bash
# 第一次运行（中途中断）
python main.py
^C  # Ctrl+C 中断

# 继续运行（自动从断点继续）
python main.py
```

### 重试失败项

如果有部分数据爬取失败，可以重试：

```bash
python main.py --retry-failed
```

### 查看统计信息

```bash
python main.py --stats
```

输出示例：

```
数据库统计信息
============================================================

问题:
  总计: 40
  待处理: 0
  已完成: 38
  失败: 2

回答:
  总计: 523
  待处理: 0
  已完成: 520
  失败: 3

评论:
  总计: 2847
  主评论: 1523
  子评论: 1324
```

## 数据导出

### 导出格式

程序会生成以下 CSV 文件：

1. **questions_[时间戳].csv** - 问题数据
2. **answers_[时间戳].csv** - 回答数据
3. **comments_[时间戳].csv** - 评论数据
4. **zhihu_full_[时间戳].csv** - 完整联表数据

### 导出命令

```bash
# 导出所有数据
python export.py

# 仅导出问题
python export.py --type questions

# 仅导出回答
python export.py --type answers

# 仅导出评论
python export.py --type comments

# 导出完整数据
python export.py --type full
```

## 反爬策略

程序内置了多种反爬对策：

- ✅ Cookie 登录态验证
- ✅ User-Agent 随机轮换
- ✅ 全局限速（令牌桶算法）
- ✅ 自适应降速（遇到限流时自动减速）
- ✅ 指数退避重试（失败后等待 2s, 4s, 8s...）
- ✅ 请求超时处理

## 合规边界

⚠️ **重要提示**

本项目仅供学习研究使用，使用时请遵守以下规则：

- ✅ 只爬取公开可见内容
- ✅ 严格限速，不对服务器造成压力
- ✅ 不绕过登录、验证码、风控机制
- ✅ 不采集敏感个人信息
- ❌ 不用于商业目的

## 常见问题

### 1. Cookie 无效或过期

**现象**：爬取时返回 403 或 401 错误

**解决**：重新登录知乎，获取新的 Cookie

### 2. 触发限流

**现象**：频繁出现 429 错误

**解决**：降低 `requests_per_second` 的值，建议设为 1.0 或更低

### 3. 某些问题/回答爬取失败

**现象**：部分数据状态为 "failed"

**解决**：使用 `--retry-failed` 参数重试

### 4. 数据库文件过大

**现象**：`zhihu.db` 文件占用空间大

**解决**：这是正常现象，SQLite 会存储所有数据。可以定期导出 CSV 后删除数据库文件

## 技术架构

### 数据库设计

使用 SQLite 存储数据，包含三张表：

- **questions** - 问题表
- **answers** - 回答表
- **comments** - 评论表（支持楼中楼）

### 状态流转

所有数据项都有状态字段：

- `pending` - 待处理
- `done` - 已完成
- `failed` - 失败

程序启动时只处理 `pending` 状态的项，实现断点续爬。

### 限速机制

使用令牌桶算法进行限速，遇到限流时自动增加延迟，恢复正常后自动减速。

## 开发相关

### 测试数据库模块

```bash
cd zhihu_crawler
python database.py
```

### 测试 HTTP 客户端

```bash
python http_client.py
```

### 调试模式

修改 `crawler.py` 中的日志输出，可以查看更详细的调试信息。

## 许可证

本项目仅供学习研究使用，请勿用于商业目的。

## 作者

知乎爬虫项目

## 更新日志

### v1.0.0 (2024-02-01)

- ✅ 初始版本发布
- ✅ 支持搜索、回答、评论爬取
- ✅ 支持断点续爬
- ✅ 支持 CSV 导出
