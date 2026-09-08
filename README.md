# 2C AI Radar · v31 + Product Hunt Auto Sync

这是以 `2c_ai_opportunity_dashboard_v31_map_dedupe_exact_single_scene.html` 为主版本的自动同步版。

## 数据流

Product Hunt API → `scripts/fetch_producthunt.py` → `data/products_raw.json` → `scripts/classify_products.py` → `data/products_auto.json` → `dashboard/index.html`

- v31 的 72 个场景、UI、机会地图、人工星标、团队评价、研究优先级、迁移人工数据能力都保留。
- 已经在 v31 里的产品：自动同步只刷新 PH ID、votes、comments、rating、抓取状态等自动字段，不覆盖人工内容和 `sceneId`。
- 新抓到的 AI 产品：按 v31 的 72 场景 taxonomy 自动映射并加入产品池，之后可在 Dashboard 中人工修改。
- 没有 `OPENAI_API_KEY` 也能运行，使用规则分类；配置后可启用 LLM 分类。

## GitHub Secrets

必须：`PRODUCTHUNT_TOKEN`

可选：`OPENAI_API_KEY`

## 第一次运行

1. 把整个项目结构上传到 GitHub（尤其是 `.github/`, `config/`, `dashboard/`, `data/`, `scripts/`）。
2. Repository → Settings → Secrets and variables → Actions → 添加 `PRODUCTHUNT_TOKEN`。
3. Actions → Product Hunt Daily Sync → Run workflow。
4. 成功后 `data/products_raw.json` / `data/products_auto.json` 会被 bot commit 更新。

## Dashboard 在线化

`dashboard/index.html` 需要通过 HTTP(S) 打开才能自动 fetch `../data/products_auto.json`。推荐用 GitHub Pages。直接双击本地 HTML（`file://`）时，浏览器通常会阻止相对路径 fetch；此时 Dashboard 会继续使用 v31 内置数据和 LocalStorage，不会丢人工数据。

## 重要：人工数据保护

对 v31 里已有产品，以下内容由本地 v31 优先：场景映射、产品介绍、商业模式、研究理由、星标、团队评价、研究优先级等。自动同步不会覆盖这些字段。
