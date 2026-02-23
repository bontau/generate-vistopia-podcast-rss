# Vistopia 播客 RSS 补全脚本

## 背景与目的

`梁文道·八分` 的官网目录（`/detail/11`）包含完整历史节目，但官方 RSS（`/rss/program/11.xml`）并不总是包含全部条目。  
本仓库提供脚本 `generate_vistopia_rss.py`，用于自动生成两类 RSS：

- `missing`：只输出“官网有、原始 RSS 没有”的缺失条目
- `complete`：输出官网完整条目（并尽量复用原始 RSS 已有 item）

这样即使未来节目重新更新，也可以重复执行脚本，持续得到最新的补全或完整 RSS。

## 功能说明

- 自动补全缺失节目（`missing`）或生成完整镜像（`complete`）
- 对生成条目自动补全 `pubDate`
  - 默认：从 `https://www.vistopia.com.cn/article/{article_id}` 抓取真实日期（北京时间）
  - 可选：跳过抓取，使用回退日期（减少请求量）
- `description` / `content:encoded` / `itunes:summary` 同步为文章正文内容
- 保持 RSS 根节点头部命名空间与原始 feed 对齐（`dc/sy/admin/atom/rdf/content/googleplay/itunes/fireside`）
- 对抓取进度输出日志（便于观察卡在哪一集）

## 脚本文件

- `generate_vistopia_rss.py`

脚本已配置为 `uv` 可直接运行（script mode）。

## 使用方式

### 1) 生成缺失条目 RSS（推荐）

```bash
uv run generate_vistopia_rss.py missing -o rss-program-11-missing-only.xml
```

### 2) 生成完整条目 RSS

```bash
uv run generate_vistopia_rss.py complete -o rss-program-11-complete.xml
```

### 3) 跳过 `pubDate` 抓取（减少对网站请求）

```bash
uv run generate_vistopia_rss.py missing --skip-pubdate-scrape -o rss-program-11-missing-only.xml
```

## 常用参数

- `missing | complete`：运行模式（必填）
- `-o, --output`：输出文件路径
- `--rss-url`：原始 RSS 地址（默认 `https://api.vistopia.com.cn/rss/program/11.xml`）
- `--article-list-url`：官网目录 API（默认 `https://api.vistopia.com.cn/api/v1/content/article_list?content_id=11&count=1001`）
- `--self-link`：可选，覆盖输出 RSS 中 `atom:link` 的 `href`
- `--image-url`：可选，合成 item 时使用的封面图
- `--author`：可选，合成 item 的 `itunes:author`
- `--subtitle`：可选，合成 item 的 `itunes:subtitle`
- `--skip-pubdate-scrape`：跳过文章页日期抓取（减少请求，使用回退 `pubDate`）
- `--no-article-page-date`：旧参数，等价于 `--skip-pubdate-scrape`

## 输出说明

脚本执行后会打印统计信息，例如：

- 官网节目总数
- 原始 RSS 条目数
- 输出条目数
- 输出文件路径

并在抓取阶段输出进度，例如：

- `[pubDate] 17/122 article_id=538494 -> 2022.04.01`
- `[description] 45/122 article_id=572307 -> ok`

## 验证 XML（可选）

```bash
xmllint --noout rss-program-11-missing-only.xml
xmllint --noout rss-program-11-complete.xml
```

## 说明

- `missing` feed 不会包含原始 RSS 已有的条目（例如 `再见。珍重。再见`），这是预期行为。
- 若你只更新了 `rss-program-11-missing-only.xml`，可直接提交并推送到 GitHub Pages，无需每次重跑 `complete`。
