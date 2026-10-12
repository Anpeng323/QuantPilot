# Quant DB — A股量化数据库

基于 [adata](https://github.com/1nchaos/adata) 的 A 股金融数据 ETL 系统，把每天的行情数据落入 DuckDB，供后续量化研究 / 回测 / AI 模型使用。

## 🎯 设计目标

- ✅ 一次拉取、本地缓存、所有研究跑在数据库上
- ✅ 每日自动增量更新（launchd 定时）
- ✅ 简单、零依赖（除 Python + adata）
- ✅ SQL 直查，方便对接 pandas / polars / Qlib

## 📁 目录结构

```
quant_db/
├── data/quant.duckdb          # 主数据库（单文件）
├── etl/
│   ├── db.py                  # DuckDB 连接
│   ├── schema.sql             # 表结构 DDL
│   ├── utils.py               # 日志、限流、upsert
│   ├── fetchers.py            # 网络 fetcher（新浪、腾讯）
│   ├── backfill.py            # 首次全量回填
│   └── daily_update.py        # 每日增量更新
├── scripts/
│   └── install_launchd.sh     # launchd 安装/卸载/手动跑
├── launchd/
│   └── com.quanlin.quant.daily.plist
├── analysis/
│   ├── query.py               # SQL REPL
│   └── sample_queries.sql     # 常用分析模板
├── logs/                      # 运行日志
└── .venv/                     # Python 虚拟环境
```

## 🚀 快速开始

### 1. 激活虚拟环境
```bash
cd /Users/quanlin/Desktop/项目/AI量化/quant_db
source .venv/bin/activate
```

### 2. 初始化数据库（建表，已存在则幂等）
```bash
python3 -c "from etl.db import init_schema; init_schema()"
```

### 3. 首次全量回填（建议先用 --limit 小数据验证）
```bash
# 验证：用 5 只票试跑
python3 -m etl.backfill --limit 5

# 全市场（5640 只 × 1500 天 ≈ 37 分钟）
python3 -m etl.backfill
```

### 4. 手动跑每日增量
```bash
python3 -m etl.daily_update           # 全量更新（最近 30 天）
python3 -m etl.daily_update --only kline --datalen 5   # 只更新 K 线最近 5 天
```

### 5. 装 launchd 定时任务（每日 17:00 自动跑）
```bash
bash scripts/install_launchd.sh install    # 安装
bash scripts/install_launchd.sh status     # 查看
bash scripts/install_launchd.sh run-now    # 立即跑一次
bash scripts/install_launchd.sh uninstall  # 卸载
```

### 6. 查询数据
```bash
# REPL 模式
python3 -m analysis.query

# 单条查询
python3 -m analysis.query -e "SELECT COUNT(*) FROM daily_kline"

# 跑 SQL 文件
python3 -m analysis.query -f analysis/sample_queries.sql

# 直接用 python
python3 -c "
from etl.db import get_conn
with get_conn(read_only=True) as conn:
    print(conn.execute('SELECT stock_code, COUNT(*) FROM daily_kline GROUP BY 1 ORDER BY 1 LIMIT 10').fetchall())
"
```

## 🗄️ 数据库表

| 表名 | 用途 | 主键 | 数据源 |
|---|---|---|---|
| `stock_basic` | 股票基础信息 | stock_code | adata 内置 CSV |
| `daily_kline` | 日/周/月 K 线 + 分钟线 | (stock_code, trade_date, k_type, adjust_type) | 新浪 |
| `index_kline` | 指数 K 线 | (index_code, trade_date) | 新浪 |
| `trade_calendar` | 交易日历 | trade_date | adata (深交所) |
| `concept` | 概念板块列表 | (concept_code, source) | adata (待接入) |
| `stock_concept` | 股票-概念映射 | (stock_code, concept_code, source) | adata (待接入) |
| `stock_industry` | 申万行业 | (stock_code, sw_code, source) | adata (待接入) |
| `finance_core` | 财务核心指标 | (stock_code, report_date) | adata (待接入) |
| `capital_flow` | 个股资金流向 | (stock_code, trade_date) | adata (待接入) |
| `north_flow` | 北向资金 | trade_date | adata (待接入) |
| `margin_balance` | 两融余额 | (trade_date, stock_code) | adata (待接入) |
| `dragon_tiger` | 龙虎榜 | (trade_date, stock_code, buyer_name, side) | adata (待接入) |
| `update_log` | 更新日志 | (run_time, job_name, table_name, id) | 自动 |

## ⚠️ 已知限制

- 新浪 datalen 上限 1500 条/请求 → 日 K 约 6 年（个人 quant 足够）
- 东方财富 push2his 接口目前不稳定，已用新浪替代
- 数据库文件锁：同一时间只能一个写连接（已通过单连接 + 批量事务解决）

## 🔧 故障排查

| 问题 | 解决 |
|---|---|
| `Conflicting lock on quant.duckdb` | 有进程占用；`lsof data/quant.duckdb` 找 PID |
| `daily_kline 写入很慢` | 检查 batch_size，调大到 500-1000 |
| `更新任务没跑` | `launchctl list \| grep quanlin.quant` 看是否加载 |
| `代理失败 (push2his)` | 切到 fetcher.py 用 sina；东方财富接口暂时不可达 |

---

## 🌐 Web UI（验证数据用）

启动：`bash scripts/run_web.sh`，浏览器打开 http://localhost:8765

**功能**：
- 📈 **全市场快照**：当日所有股票，排序/分页/搜索
- 🔍 **个股详情**：日 K + 分时 + 因子 + 概览（交互式 ECharts）
- 📝 **SQL 查询**：只允许 SELECT，物理上不会破坏数据库
- 📊 **指数看板**：6 个核心指数 + 蜡烛图

**API 端点**（也可以程序化调用）：

| 端点 | 说明 |
|---|---|
| `GET /api/health` | 健康检查 |
| `GET /api/stats` | 数据健康度（各表行数） |
| `GET /api/snapshot?exchange=&sort=&order=&limit=&offset=&q=` | 最新一日全市场列表 |
| `GET /api/stocks/search?q=` | 股票搜索（最多 20） |
| `GET /api/stock/{code}` | 个股基本信息 + 最新价 |
| `GET /api/stock/{code}/daily?start=&end=` | 日 K |
| `GET /api/stock/{code}/minute?date=` | 5min K |
| `GET /api/stock/{code}/factors?date=` | Alpha 因子 |
| `GET /api/indices/latest` | 6 指数最新 |
| `GET /api/indices/{code}/daily` | 指数 K |
| `POST /api/sql` `{"sql": "..."}` | SELECT-only 查询 |

**安全**：所有 DuckDB 连接为 `read_only=True`，SQL 端点会校验只允许 `SELECT/WITH/EXPLAIN` 并拒绝所有 DDL/DML 关键字。


---

## 🤖 AI 投研平台 (v1)

在 Quant DB 之上新增的 AI 模块。Web 界面有 8 个 tab（4 个数据 + 4 个 AI 投研）。

### 🎯 4 大核心能力

1. **📚 RAG 知识库**：PDF/Word/Markdown 上传 → 切片 → 向量化 → 检索问答（带引用溯源）
2. **🤖 AI 投研引擎**：财报解读 / 风险扫描 / 估值测算 / 研报观点汇总（4 个 analyzer）
3. **🎯 自选池 + 笔记 + 记忆**：分组管理、Markdown 笔记（可同步到向量库）、用户偏好持久化
4. **💼 持仓 + 交易 + 复盘**：手动录入、AI 自动归因（胜率、改进点）

### 🚀 启动

```bash
bash scripts/run_web.sh
# 浏览器打开 http://localhost:8765
```

### 🔑 切真实 LLM（默认是 mock）

```bash
export LLM_PROVIDER=qwen              # 或 deepseek
export QWEN_API_KEY=sk-xxx
# 重启服务，状态条会从 🟡 MOCK 变 🟢 LIVE
```

切真实模式后：
- 4 个 analyzer 会按真实财报/股票数据 → 生成自然语言报告
- RAG 问答会用真实 LLM 做答案生成 + 引用溯源

### 📂 AI 模块代码结构

```
quant_db/
├── ai/
│   ├── client.py        # 统一 LLM 接口（Qwen/DeepSeek/Mock）
│   ├── prompts.py       # 投研 Prompt 模板库
│   ├── memory.py        # 用户偏好
│   └── analyzers/
│       ├── financial.py # 财报解读
│       ├── risk.py      # 风险扫描
│       ├── valuation.py # 估值测算
│       └── sentiment.py # 研报观点汇总
├── knowledge/               # RAG 模块
│   ├── loader.py        # PDF/Word/MD 加载
│   ├── chunker.py       # 滑动窗口切片
│   ├── embedder.py      # BGE-small-zh 本地 embedding
│   ├── vector_store.py  # Chroma 持久化（data/chroma/）
│   └── rag_chain.py     # 完整 RAG 链
├── watchlist/               # 自选池
│   ├── pool.py          # 分组 + 项
│   └── alerts.py        # 预警规则
├── notes/                    # 笔记
│   ├── note.py          # CRUD
│   └── sync.py          # 同步到向量库
├── portfolio/                # 复盘
│   ├── position.py      # 持仓台账
│   ├── trade.py         # 交易记录
│   └── review.py        # AI 复盘
├── data_ingest/              # 数据接入（手动）
│   ├── finance.py       # 财报（Tushare）
│   ├── announcement.py  # 公告
│   └── news.py          # 新闻
└── web/ai_routes.py         # 35 个 /api/v1/* 路由
```

### 🆕 数据库表（v1 新增）

- `doc` — RAG 文档元数据
- `note` — 笔记
- `watchlist` / `watchlist_group` — 自选池
- `ai_analysis` — AI 分析历史（用于成本审计）
- `position` — 持仓
- `trade` — 交易记录
- `trade_review` — AI 复盘

### 📊 路由清单 (`/api/v1/*`)

- `GET  /llm/info` `POST /llm/embed-warmup`
- `GET/POST/DELETE /watchlist/groups`, `/watchlist/{group}`, `/watchlist/{group}/{code}`
- `GET/POST/PUT/DELETE /notes`, `/notes/{id}/sync-vector`
- `POST /rag/upload`, `GET /rag/docs`, `DELETE /rag/docs/{id}`, `POST /rag/query`
- `POST /ai/analyze`, `GET /ai/history`, `GET /ai/result/{id}`
- `GET/POST /portfolio/positions`, `/portfolio/trades`, `POST /portfolio/review`
- `GET/POST /memory`
- `POST /ingest/news`, `GET /ingest/announcements`

### ⚠️ 当前限制（mock 模式下）

- 4 个 AI analyzer 在 mock 模式返回"模板化说明"；设置 API key 后即变真实
- RAG 检索 → embedding 是真实 BGE-small-zh（已下载 100MB 模型），但 LLM 回答部分在 mock 模式是模板化
- 嵌入本地 Embedding 模型在首次 RAG 上传时会自动下载

### 🧭 v2 路线（不在本次范围）

- 多 Agent 工作流（数据检索 + 风险审查 + 观点汇总 Agent）
- 研报 OCR（扫描型 PDF）
- 自动爬虫每日任务（cninfo 公告 / 研报 / 新闻）
- 估值模型 v2（DCF + 敏感性分析）
- 移动端 PWA


---

## 🔑 配置真实 LLM（MiniMax M3）

**1. 创建环境配置文件（一次性）**

```bash
cp ~/.quant_env.example ~/.quant_env
# 用编辑器填入你的真实 API key
```

**2. 编辑 `~/.quant_env`**

```bash
export LLM_PROVIDER=minimax
export MINIMAX_API_KEY=<你的 MiniMax API 真实 key>
export MINIMAX_MODEL=MiniMax-M3     # 可选：MiniMax-M2.7、MiniMax-M3.1-Flash-Preview
```

> ⚠️  **重要安全提示**：你的 API key 等同于密码，建议只放在本地 `~/.quant_env`，并加入 `.gitignore`（已加）。

**3. 启动**

```bash
bash scripts/run_web.sh --live   # 读 ~/.quant_env
```

不加 `--live` 则是 mock 模式（无 key 也能跑）。

**支持的 provider**

| Provider | 环境变量 | 模型 |
|---|---|---|
| **MiniMax**（默认）| `LLM_PROVIDER=minimax` + `MINIMAX_API_KEY` | MiniMax-M3 / M2.7 / M3.1-Flash-Preview |
| Qwen | `LLM_PROVIDER=qwen` + `QWEN_API_KEY` | qwen-plus / qwen-long |
| DeepSeek | `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY` | deepseek-chat |
| Mock | `LLM_PROVIDER=mock` | （无 key，模板化回答）|

**实测成本（MiniMax M3）**

| 操作 | Tokens | 成本 | 耗时 |
|---|---|---|---|
| 财报解读 | ~3500 | ¥0.018 | ~33s |
| 风险扫描 | ~2400 | ¥0.013 | ~19s |
| 估值测算 | ~2500 | ¥0.013 | ~24s |
| 研报汇总 | ~1050 | ¥0.007 | ~12s |
| RAG 问答 | ~880  | ¥0.005 | ~6s |

**100 次调用总成本约 ¥5-10**（按上面估值），适合日常个人使用。

