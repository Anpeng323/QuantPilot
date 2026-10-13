# Quant Workbench · API 参考

> Base URL（默认）：`http://localhost:8765`
> 股票行情走 `/api/stock/*`；其余业务走 `/api/v1/*`。
> 最后更新：2026-10-13

---

## 行情 / 个股

### GET `/api/stock/info`
```bash
curl "http://localhost:8765/api/stock/info?q=000001"
```

### GET `/api/stock/search`
```bash
curl "http://localhost:8765/api/stock/search?q=平安&limit=10"
```

### GET `/api/stock/kline`
日 K（k_type=1）。
```bash
curl "http://localhost:8765/api/stock/kline?code=000001&days=120"
# → { rows: [ {trade_date, open, high, low, close, pre_close, change, change_pct, volume, amount}, ... ] }
```

### GET `/api/stock/weekly`
周 K（由日 K 聚合，k_type=2）。
```bash
curl "http://localhost:8765/api/stock/weekly?code=000001&weeks=120"
```

### GET `/api/stock/minute`
5 分钟 K（默认最新交易日）；`date` 可选指定交易日。
```bash
curl "http://localhost:8765/api/stock/minute?code=000001&days=1"
```

### GET `/api/stock/minute/dates`
返回有 5 分钟数据的交易日列表。
```bash
curl "http://localhost:8765/api/stock/minute/dates?code=000001&limit=60"
```

### GET `/api/stock/{code}` / `/daily` / `/minute` / `/factors`
单标的详情、日 K、分钟、因子（server.py 暴露）。

---

## RAG 知识库  (`/api/v1`)

### GET `/rag/stats`
库内文档/分块统计。

### POST `/rag/upload`
上传文档（multipart）。
```bash
curl -F "file=@report.pdf" -F "stock_code=000001" -F "doc_type=report" \
     -F "title=2026Q3 研报" http://localhost:8765/api/v1/rag/upload
# → { doc_id, chunk_count }
```

### GET `/rag/docs`
列出文档（`stock_code`、`limit` 可选）。

### DELETE `/rag/docs/{doc_id}`

### POST `/rag/query`
```bash
curl -X POST http://localhost:8765/api/v1/rag/query \
  -H "Content-Type: application/json" \
  -d '{"question": "该公司 2025 年毛利率变化？", "top_k": 5}'
# → { answer, citations: [...] }  # citations 含原文出处（引用溯源）
```

---

## 笔记  (`/api/v1`)

### GET `/notes` / POST `/notes`
```bash
curl -X POST http://localhost:8765/api/v1/notes \
  -H "Content-Type: application/json" \
  -d '{"stock_code":"000001","title":"我的笔记","content_md":"# 标题\n正文 $E=mc^2$"}'
```

### GET/PUT/DELETE `/notes/{note_id}`

### POST `/notes/{note_id}/sync-vector`
把笔记同步进向量库，供 RAG 检索。

---

## 自选池  (`/api/v1`)

### GET/POST `/watchlist/groups`
列出 / 新建分组（如 观察池 / 持仓池 / 行业池）。

### DELETE `/watchlist/groups/{group_name}`

### GET `/watchlist/{group_name}`
分组内股票列表。

### POST/DELETE `/watchlist/{group_name}/{stock_code}`
加入 / 移除。

### GET `/watchlist/{group_name}/alerts`
该分组内股票的预警事件。

---

## AI 投研分析  (`/api/v1`)

### POST `/ai/analyze`
```bash
curl -X POST http://localhost:8765/api/v1/ai/analyze \
  -H "Content-Type: application/json" \
  -d '{"stock_code":"000001","analysis_type":"financial_summary","save":true}'
# analysis_type ∈ financial_summary | risk_scan | valuation | sentiment
# → { analysis_id, response, model, tokens, cost_cny, ... }
```
> 财报解读/估值会消费 `finance_core` 与 `finance_statement`；缺数据时自动降级提示先接入。

### GET `/ai/history`
历史分析记录。

### GET `/ai/result/{analysis_id}`
单条分析详情。

---

## 记忆  (`/api/v1`)

### GET/POST `/memory`
读取 / 更新用户投资偏好（供分析时贴合风格）。

---

## 数据接入  (`/api/v1`)

### GET/POST `/ingest/news`
列出 / 新增新闻（手动录入占位，自动抓取规划中）。

### GET `/ingest/announcements`
列出已入库公告。

### POST `/ingest/announcements`
触发公告抓取（akshare 东方财富）。
```bash
curl -X POST http://localhost:8765/api/v1/ingest/announcements \
  -H "Content-Type: application/json" -d '{"days":1}'
# → { dates, added }
```

---

## 技术指标  (`/api/v1`)

### GET `/indicators`
列出 8 个指标：`ma / macd / boll / rsi / kdj / adx / arbr / stochrsi`。

### POST `/indicators/{name}/compute`
```bash
curl -X POST http://localhost:8765/api/v1/indicators/ma/compute \
  -H "Content-Type: application/json" \
  -d '{"stock_code":"000001","params":{"period":5},"days":60}'
```

---

## 提示词模板 / 预警 / 持仓 / 导出 / 模型路由

（详见既有章节；路由前缀均为 `/api/v1`）

- **提示词**：`/prompts`（GET/POST）、`/prompts/{tid}`（GET/PUT/DELETE）
- **预警**：`/alerts/rules`（GET/POST/DELETE）、`/alerts/run`（POST）、`/alerts/events`（GET）
- **持仓**：`/portfolio/positions`、`/portfolio/positions/{id}/close`、`/portfolio/trades`、`/portfolio/review`、`/portfolio/reviews`
- **导出**：`/exports/analysis/{aid}/md`、`/exports/analysis/{aid}/pdf`、`/exports/watchlist/{group}/excel`
- **模型路由**：`/router/models`（GET）、`/router/config`（POST）

---

## 数据库表

| 表 | 用途 |
|---|---|
| `daily_kline` | 日/周/5 分钟 K（k_type: 1=日 2=周 5=5分钟） |
| `index_kline` / `minute_kline` | 指数 K / 分钟 K |
| `factors_daily` | Alpha 因子 |
| `finance_core` | 财务核心指标（EPS/ROE/营收/净利/同比） |
| `finance_statement` | 财务三表明细（长表 EAV：balance/profit/cashflow） |
| `macro_indicator` | 宏观（cpi/pmi/lpr_1y/lpr_5y） |
| `stock_basic` / `stock_industry` | 标的基础信息 / 申万行业 |
| `doc` | RAG 文档（含 doc_type='announcement' 公告） |
| `note` | 投研笔记 |
| `watchlist` / `watchlist_group` | 自选池 |
| `position` / `trade` / `trade_review` | 持仓 / 交易 / 复盘 |
| `ai_analysis` | AI 分析历史 |
| `prompt_template` / `alert_rule` / `alert_event` / `memory` | 模板 / 预警 / 事件 / 记忆 |

---

## 错误码

| HTTP | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 403 | 权限拒绝（如删除内置模板） |
| 404 | 资源不存在 |
| 422 | 请求体校验失败 |
| 500 | 服务器内部错误 |
