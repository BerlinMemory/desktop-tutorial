# 知乎爬虫 - 5分钟快速开始 🚀

一个简单易用的知乎爬虫，可以按关键词搜索并爬取问题、回答、评论。

---

## 第一步：安装依赖

```bash
pip install -r requirements.txt
```

---

## 第二步：配置 Cookie

### 2.1 复制配置文件

```bash
cp config.yaml.example config.yaml
```

### 2.2 获取知乎 Cookie

1. 登录知乎网站：https://www.zhihu.com
2. 按 **F12** 打开浏览器开发者工具
3. 点击 **Network（网络）** 标签页
4. 刷新页面（F5）
5. 点击任意请求，找到 **Request Headers**
6. 复制 **Cookie** 字段的完整内容

### 2.3 填入配置文件

编辑 `config.yaml`，将第 15 行的 `your_cookie_here` 替换为你复制的 Cookie：

```yaml
cookie: "你的Cookie内容"
```

---

## 第三步：设置关键词

在 `config.yaml` 中修改搜索关键词：

```yaml
keywords:
  - "你感兴趣的话题1"
  - "你感兴趣的话题2"
```

---

## 第四步：运行爬虫

```bash
# 完整运行（推荐首次使用）
python main.py

# 仅搜索问题
python main.py --search-only

# 仅爬取回答
python main.py --answers-only

# 仅爬取评论
python main.py --comments-only
```

---

## 第五步：导出数据

```bash
python main.py --export
```

导出的 CSV 文件位于 `data/exports/` 目录。

---

## 常用命令

```bash
# 查看统计信息
python main.py --stats

# 重试失败项
python main.py --retry-failed
```

---

## 调整爬取数量

编辑 `config.yaml` 中的限制设置：

```yaml
limits:
  questions_per_keyword: 20    # 每个关键词搜索多少问题
  answers_per_question: 100    # 每个问题爬多少回答（null=全部）
  comments_per_answer: 50      # 每个回答爬多少评论（null=全部）
```

**首次使用建议**：设置较小的数值测试（如 5、20、30）

---

## 断点续爬

程序支持断点续爬，中断后再次运行会自动从上次停止的地方继续：

```bash
python main.py    # 第一次运行
# Ctrl+C 中断
python main.py    # 继续运行，自动跳过已完成部分
```

---

## 常见问题

### 1. Cookie 无效？

- 重新登录知乎，获取新的 Cookie
- 确保复制了完整的 Cookie 内容

### 2. 爬取速度慢？

- 这是正常的，程序有限速保护（默认 1.5 req/s）
- 不建议提高速度，容易触发反爬

### 3. 部分数据失败？

```bash
python main.py --retry-failed
```

---

## 项目结构

```
zhihu_crawler/
├── config.yaml          # 你的配置（需自己创建）
├── main.py              # 运行入口
├── data/
│   ├── zhihu.db        # 数据库（自动生成）
│   └── exports/        # CSV 导出目录
└── ...
```

---

## 注意事项

- ⚠️ 仅供学习研究使用
- ⚠️ 请遵守知乎服务条款
- ⚠️ 不要过度爬取，避免给服务器造成压力

---

## 需要帮助？

查看完整文档：[README.md](README.md)

---

**祝你使用愉快！** 🎉
