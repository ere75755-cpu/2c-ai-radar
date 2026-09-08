# 2C AI Product Radar — Product Hunt Auto Sync MVP

基于现有 **2C AI Opportunity Radar v24** 改造。目标是把 Product Hunt 从“一次性人工扫描”升级成：

`PH API → 每日增量 → 去重/指标快照 → AI/规则分类 → 既有2C场景映射 → Dashboard自动读取`

## 你需要准备什么

1. 一个 GitHub repo（不要把密码或 token 发给任何人）。
2. Product Hunt Developer Token，存进 GitHub repo 的 `Settings → Secrets and variables → Actions`，名称必须是 `PRODUCTHUNT_TOKEN`。
3. **可选**：如果希望 LLM 自动生成场景匹配/启发，添加 `OPENAI_API_KEY`。不添加也能运行，脚本会使用保守关键词规则。

> Product Hunt 官方 API 文档说明 developer token 适合简单脚本；API 默认不得用于商业用途。公司内部长期使用前建议向 Product Hunt 确认许可。

## 目录

- `dashboard/index.html`：你的 v24 Dashboard + 自动数据同步 adapter。
- `data/products_raw.json`：PH 原始产品主表。
- `data/product_metrics.json`：每日 votes/comments/rating 快照。
- `data/products_auto.json`：自动分类后、可直接被 Dashboard 合并的产品。
- `data/scan_status.json`：最近一次任务状态。
- `config/scenes.json`：从 v24 抽出的场景 taxonomy（分类只能选这些 sceneId）。
- `scripts/fetch_producthunt.py`：官方 GraphQL API 抓取。
- `scripts/classify_products.py`：规则分类 + 可选 OpenAI 复核。
- `.github/workflows/producthunt_daily.yml`：北京时间每天 18:00 自动运行，也支持手工 Run workflow。

## 5 分钟本地测试

```bash
cd 2c-product-radar-mvp
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

设置 token 后：

```bash
# macOS/Linux
export PRODUCTHUNT_TOKEN="..."
python scripts/fetch_producthunt.py
python scripts/classify_products.py
python scripts/build_dashboard_data.py

# Windows PowerShell
$env:PRODUCTHUNT_TOKEN="..."
python scripts/fetch_producthunt.py
python scripts/classify_products.py
python scripts/build_dashboard_data.py
```

启动静态服务器（不要双击 file:// 打开，因为浏览器通常不允许本地 HTML fetch 相邻 JSON）：

```bash
python -m http.server 8000
```

打开 `http://localhost:8000/dashboard/`。

## GitHub 部署

1. 新建空 repo，把整个目录提交进去。
2. Repo → `Settings → Secrets and variables → Actions`：添加 `PRODUCTHUNT_TOKEN`；可选 `OPENAI_API_KEY`。
3. Repo → `Actions` → `Product Hunt Daily Sync` → `Run workflow`，先手工跑一次。
4. 确认 `data/products_raw.json` 与 `data/products_auto.json` 出现数据。
5. GitHub Pages：`Settings → Pages → Deploy from a branch → main / (root)`。
6. Pages 地址下的 `/dashboard/` 即可在线访问；之后 JSON 更新后 Dashboard 会自动读最新数据。

## 自动数据如何不覆盖人工评价

同步时会优先保留浏览器 LocalStorage 中的：

- `starred`
- `teamNote`
- `researchPriority`
- `researchWhy`

自动脚本只更新产品事实字段、PH 热度和自动分类字段。这解决了此前版本升级时人工评价容易丢失的问题。

**注意：** 当前人工评价仍然保存在“这台浏览器”的 LocalStorage，而不是 GitHub 数据库。MVP 有意这样设计，避免自动任务改坏人工数据。下一阶段如需多人协作，应把人工字段迁移到 Supabase/PostgreSQL。

## 当前 MVP 的边界

- PH API 提供的是 posts/指标数据；官方 GraphQL `PostsOrder` 有 `NEWEST/RANKING/VOTES`，但 API 并没有直接给 Dashboard 网页上的“Weekly #N / Monthly #N”字段。MVP 因此优先做**每日全量增量 + votes/comments 历史趋势**。如果后面必须复刻官网 Weekly/Monthly 显式排名，应单独做榜单采集模块，而不要把它假装成 API 原生 rank。
- `reviewsRating` 有评分，但 API schema 没有在 Post 上直接暴露“review count”字段；不要把 rating 当 review 数。
- 规则分类只是 fallback，适合“先跑起来”。启用 LLM 后场景匹配质量会明显更好，但仍建议 `sceneConfidence` 低的产品进入人工 Review。
- 本地下载的 HTML 可以离线查看 embedded snapshot；要持续自动更新，建议通过 GitHub Pages/Vercel 打开，或本地用 `python -m http.server`。

## 下一阶段建议

当 PH 跑稳后，按同一 `source / sourceProductId / sourceUrl` 结构接 GitHub Trending、YC，再做跨渠道同产品合并；当多人需要共享星标/团队评价时，再把数据层升级到 Supabase。
