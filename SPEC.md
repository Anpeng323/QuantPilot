# 个人 AI 量化投研平台 — 规格说明书（SPEC）

> **项目代号**：Quant Workbench  
> **版本**：v1.1（基于已实现 quant_db v1 的迭代规划）  
> **状态**：Draft — 待评审  
> **最后更新**：2026-10-12  
> **作者**：self  
> **适用范围**：单人本地投研平台，不含实盘下单 / 多账户

---

## 0. TL;DR（一页摘要）

本 spec 在 **`quant_db/`** 已落地的 v1（行情 + RAG + 4 个 AI 分析器 + 自选池 + 笔记 + 持仓复盘 + FastAPI Web 控制台）基础上，向你的 7 大模块目标对齐，明确：
- 哪些 **已实现** → 标注 ✅（含代码路径）
- 哪些 **半成品** → 标注 🟡（含待补点）
- 哪些 **需新增** → 标注 ❌（含本 spec 的接口 / 表结构 / 接口签名设计）

**关键决策**：
1. 数据层：保留 DuckDB 单文件，主库 + 向量库（Chroma）+ 文件存储 三层
2. 复用 Qbot 的指标库思想，但**不**复制它的回测 / 实盘链路（与目标冲突）
3. LLM 默认走 MiniMax-M3，可切 Qwen/DeepSeek/Mock
4. 严格遵守"不抓付费研报、不爬付费数据、不做实盘"的边界
5. 优先 v1（基本面 + RAG + 单标的深度），Agent / 多 LLM 对账放到 v2+

---

## 1. 项目现状盘点

### 1.1 已实现资产（`/Users/quanlin/Desktop/项目/AI量化/`）

| 模块 | 资产 | 路径 | 状态 |
|---|---|---|---|
| 数据源 | adata SDK | `adata-main/` | ✅ 5 000+ A 股、行情/财务/行业/概念 |
| 参考 | Qbot（含 GUI、30+ 指标策略、通知） | `Qbot-main/` | ⚠️ 参考用，不集成 |
| 数据 ETL | DuckDB 主库 + 增量更新 | `quant_db/etl/` | ✅ 5 640 股 × 1 500 天 K 线，每日 17:00 跑批 |
| 财务核心 | `finance_core` 表 | `quant_db/etl/schema.sql` | 🟡 表已建，Tushare 接入为空 |
| 行业/概念 | `stock_industry`/`concept`/`stock_concept` | 同上 | 🟡 表已建，adata 数据未灌入 |
| 资金流 | `capital_flow`/`north_flow`/`margin_balance`/`dragon_tiger` | 同上 | 🟡 表已建 |
| 分钟线 | `minute_kline`（5/15/30/60） | 同上 | ✅ |
| AI 分析 | 4 个 analyzer | `ai/analyzers/*.py` | ✅ financial/risk/valuation/sentiment |
| LLM 客户端 | Qwen/DeepSeek/MiniMax/Mock | `ai/client.py` | ✅ |
| 提示词库 | 6 个模板 | `ai/prompts.py` | ✅ |
| RAG | loader + chunker + embedder (BGE) + Chroma + 链 | `knowledge/` | ✅ |
| 自选池 | 分组 + 标的 | `watchlist/` | ✅ |
| 笔记 | CRUD + 同步到向量库 | `notes/` | ✅ |
| 持仓/交易 | 台账 + 复盘 | `portfolio/` | ✅ |
| Web 后端 | FastAPI + 35+ 路由 | `web/` | ✅ |
| Web 前端 | ECharts + jQuery | `web/static/` | 🟡 8 个 tab（无 portfolio tab） |
| 导出 | CSV/Parquet/PNG | `exports/` | 🟡 无 PDF/Markdown |
| 定时任务 | launchd plist | `launchd/` | ✅ 每日 17:00 |
| 启动脚本 | mock/--live 模式 | `scripts/run_web.sh` | ✅ |
| 文档 | README + 项目内页 | ✅ |
| 成本审计 | `ai_analysis` 表 + `cost_cny` 字段 | ✅ |

代码体量：约 **2 230 行**（不含 adata / Qbot）。

### 1.2 Qbot 可借鉴清单（**仅做参考，不集成依赖**）

| Qbot 模块 | 路径 | 是否借鉴 | 借鉴方式 |
|---|---|---|---|
| 30+ 经典指标策略 | `qbot/strategies/*.py` | ✅ | 移植 6-8 个到 `indicators/`（MA/MACD/BOLL/RSI/KDJ/ARBR/ADX/StochRSI） |
| 因子计算 | `qbot/engine/...` | ✅ | 复用 `talib`/`pandas_ta`/`statsmodels` 思想 |
| GUI（wxPython） | `qbot/gui/` | ❌ | 你已有 Web，不要桌面 GUI（多端不一致） |
| 回测 backtrader | `qbot/strategies/*_bt.py` | ❌ | 本 spec 不做回测（投研 ≠ 量化交易） |
| 实盘 vnpy/easytrader | `pytrader/` | ❌ | **明确排除**（你已声明） |
| 飞书 / 邮件 / 微信通知 | `utils/larkbot.py`/`send_email.py`/`wxbot.py` | 🟡 | 借鉴 `send_email.py` 模式做预警邮件 |
| quantstats 可视化 | `requirements.txt` | ✅ | `web/static/` 加 `quantstats.html` 端 |
| 数据源 baostock / yfinance / efinance / akshare | `requirements.txt` | ✅ | akhare 已在 v1 用过；备用 baostock |

### 1.3 数据源对照（你列的目标 → 实际可用）

| 你要的数据 | 实际来源 | 备注 |
|---|---|---|
| A 股基础行情（K 线、量、换手率、市值、PE/PB/ROE） | adata + 新浪（已落地） + Tushare | ✅ |
| 多周期（日/周） | adata + `daily_kline.k_type` | ✅ |
| 技术指标（MA/MACD/布林/RSI） | 自建 `indicators/` + `pandas_ta` | ❌ 缺，待 v1.1 |
| 年/季报（利润表/资产负债/现金流） | adata `stock.finance` + Tushare | 🟡 部分 |
| 公告（cninfo） | **需自写爬虫**（合规：仅公开公告） | ❌ 缺，v1.2 |
| 研报 PDF 解析 | 公开来源（如慧博 / 萝卜投研）+ OCR | ❌ 缺，v1.2 |
| 资讯/新闻 | akshare 财经新闻接口 | ❌ 缺，v1.2 |
| 产业链 | **不抓**（多数卖私有），自维护静态 JSON | 🟡 可手工 |

---

## 2. 总体架构

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────────┐
│ Layer 6 · Web UI (FastAPI 静态页 + ECharts)              │
│   8 个 tab：行情/个股/SQL/指数/自选/笔记/RAG/AI/持仓      │
└──────────────────────────────────────────────────────────┘
                          ↑
┌──────────────────────────────────────────────────────────┐
│ Layer 5 · API 网关（FastAPI router /api/v1/*）            │
│   - 数据查询 / RAG / AI 分析 / 自选池 / 笔记 / 持仓      │
└──────────────────────────────────────────────────────────┘
                          ↑
┌──────────────────────────────────────────────────────────┐
│ Layer 4 · 业务模块（pure Python）                         │
│   ai/ knowledge/ watchlist/ notes/ portfolio/ indicators  │
└──────────────────────────────────────────────────────────┘
                          ↑
┌──────────────────────────────────────────────────────────┐
│ Layer 3 · 数据接入（adata 封、liège-cninfo、本地爬虫）    │
│   data_ingest/{finance,announcement,news,macro}          │
└──────────────────────────────────────────────────────────┘
                          ↑
┌──────────────────────────────────────────────────────────┐
│ Layer 2 · 存储                                             │
│   - DuckDB（结构化时序数据）→ data/quant.duckdb            │
│   - Chroma（向量数据）→ data/chroma/                      │
│   - 本地文件系统（PDF/MD 上传）→ data/uploads/             │
└──────────────────────────────────────────────────────────┘
                          ↑
┌──────────────────────────────────────────────────────────┐
│ Layer 1 · 外部数据源                                       │
│   adata (新浪/同花顺/百度) · Tushare · akshare · 公网     │
└──────────────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.10+ | 已有 venv |
| 数据库 | DuckDB | 单文件，列式，pandas 友好，零部署 |
| 向量库 | Chroma（SQLite 后端） | 轻量，持久化目录即可 |
| Embedding | BAAI/bge-small-zh-v1.5（本地） | 中文友好，100 MB，已下载 |
| LLM | MiniMax-M3（默认）/ Qwen / DeepSeek / Mock | OpenAI 兼容协议，统一客户端 |
| Web 后端 | FastAPI + uvicorn | 已有，async 友好 |
| Web 前端 | 静态 HTML + ECharts + jQuery | 已有，最小依赖 |
| 定时 | uv-cron + launchd plist | macOS 已有 |
| 打包 | 未来可打 .app（PyInstaller） | 暂缓 |

### 2.3 数据流（典型 AI 分析链路）

```
[用户点击 "财报解读 000001"]
   ↓
[POST /api/v1/ai/analyze {stock_code, type=financial}]
   ↓
[ai/analyzers/financial.py: build_context()]
   ├── DuckDB: SELECT ... FROM finance_core WHERE stock_code='000001'
   ├── DuckDB: SELECT ... FROM daily_kline LIMIT 30
   └── DuckDB: SELECT ... FROM factors_daily LIMIT 5
   ↓
[jinja2 渲染 PROMPT_FINANCIAL]
   ↓
[ai/client.py: chat() → MiniMax-M3-M3]
   ↓
[响应 Markdown 文本]
   ↓
[ai_analysis 表落库（成本审计）]
   ↓
[返回 {analysis_id, response, tokens, cost_cny}]
   ↓
[前端 #ai-result innerHTML = response]
```

---

## 3. 七大模块详细规格

### 模块一：行情 / 财务 / 文本数据接入 ✅🟡❌

#### 3.1 行情数据 ✅

**现状**：DuckDB `daily_kline`（5640 股 × ~1500 天）+ `minute_kline`（5/15/30/60）+ `index_kline` + `factors_daily`。

**保留**。**新增**：

| 待办 | 内容 | 优先级 |
|---|---|---|
| 多周期周/月线 | 已通过 `k_type` 字段支持，前端缺切换控件 | P1 |
| 自定义指标公式 | 新建 `indicators/` 模块（见 3.4） | P1 |
| 复权切换 | 表有字段，前端无 UI（v1.1 加） | P1 |

#### 3.2 基本面数据 🟡

**现状**：`finance_core` 表已建（PE/PB/PS/ROE/营收/净利/yoy），Tushare 接入为空。

**新增**：

```
data_ingest/
├── finance.py        # Tushare 真实接入（需 token）
├── announcement.py   # cninfo 公开公告抓取（合规范围）
├── news.py           # akshare 新闻聚合
├── macro.py          # CPI/PMI/利率/汇率（新增）
└── industry.py       # 申万行业数据（adata 已能拉）
```

| 表 | 字段 | 数据源 | 状态 |
|---|---|---|---|
| `finance_core` | 已建 | adata + Tushare | ✅ 表 / 🟡 数据 |
| `finance_balance` | stock_code/report_date/... 80 字段 | Tushare `balance_sheet` | ❌ 新建 |
| `finance_profit` | stock_code/report_date/... 60 字段 | Tushare `income` | ❌ 新建 |
| `finance_cashflow` | stock_code/report_date/... 80 字段 | Tushare `cashflow` | ❌ 新建 |
| `announcement` | ann_id/stock_code/title/content/sentiment/publish_date | cninfo（公开） | ❌ 新建 |
| `news` | news_id/title/url/source/summary/stock_codes/publish_date | akshare + RSS | 🟡 半成品（缺一手 doc） |
| `macro_indicator` | date/category/value | 国家统计局 / akshare | ❌ 新建 |
| `stock_industry_chain` | stock_code/industry/relation/upstream/downstream | 手工维护 JSON | ❌ 新建 |

**合规边界**：
- ✅ cninfo 公开公告（巨潮资讯网，所有公告都公开）
- ✅ Tushare 个人免费额度（2 000 次/天，积分达 2000 可解锁更多）
- ✅ akshare / adata（开源库自带数据源）
- ❌ 慧博 / 同花顺 iFinD / Wind 等付费终端
- ❌ 任何券商研报全文（仅做 OCR + 标题+摘要入库，不外发）

#### 3.3 公告抓取（v1.2）

```python
# data_ingest/cninfo.py —— 伪代码
def fetch_cninfo_announcements(stock_code: str, days: int = 30) -> List[Ann]:
    """从巨潮资讯网公开公告列表抓取（用户级 request，无反爬挑战）"""
    url = f"http://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "pageNum": 1, "pageSize": 30,
        "column": "sse" if code.startswith("6") else "szse",
        "tabName": "fulltext",
        "stock": f"{code},9900025076",  # 9900025076 = 通用 stock orgId
        "seDate": f"{start_date}~{end_date}",
    }
    # POST → JSON → 解析 title/pdf_url/publish_date
    return [Ann.from_dict(d) for d in resp.json()["announcements"]]
```

入库到 `announcement` 表，**仅标题 + 摘要 + URL**，PDF 内容通过用户手动上传入库（避免批量下载侵权争议）。

#### 3.4 指标计算（v1.1 新建）

**目标**：移植 Qbot 的 6-8 个核心指标到本地计算（前端可视化）。

```
quant_db/indicators/
├── __init__.py
├── base.py            # Indicator 抽象类
├── ma.py              # 简单/加权/Wilder 平滑
├── macd.py            # MACD（DIF/DEA/HIST）
├── boll.py            # 布林线（up/mid/down）
├── rsi.py             # 相对强弱
├── kdj.py             # 随机指标
├── arbr.py            # 情绪指标
├── adx.py             # 趋势强度
└── registry.py        # 指标注册表
```

**接口**：
```python
class Indicator:
    name: str
    def compute(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        """入参 OHLCV DataFrame，返回加列的 DataFrame"""
```

**数据流**：前端选择指标 → 后端从 DuckDB 拉 K 线 → 调用 `Indicator.compute()` → 返回 JSON → ECharts 渲染。

#### 3.5 产业链（v1.3，**手工为主**）

不抓外部产业链图谱（多数商业）。允许用户维护：
```
data/stock_industry_chain.json
{
  "300750": {  # 宁德时代
    "industry_l1": "电力设备",
    "industry_l2": "电池",
    "upstream": ["锂矿", "电解液", "隔膜"],
    "downstream": ["新能源车", "储能"],
    "stocks_upstream":   ["002460", "002709", "002812"],  # 赣锋锂业/天赐材料/恩捷股份
    "stocks_downstream": ["002594", "300750"]  # 比亚迪/宁德自身
}
```

通过 RAG 入库后可问："宁德上游有哪些公司？"。

---

### 模块二：RAG 知识库 ✅

#### 3.6 现状

- ✅ PDF/Word/Markdown loader（`knowledge/loader.py`）
- ✅ 滑动窗口切片 512/64（`knowledge/chunker.py`）
- ✅ BGE-small-zh embedding（`knowledge/embedder.py`）
- ✅ Chroma 持久化（`knowledge/vector_store.py`）
- ✅ RAG 链（`knowledge/rag_chain.py`：召回 → 拼 prompt → LLM）
- ✅ 上传 / 列表 / 删除 API（`/api/v1/rag/upload`, `/docs`, `/docs/{id}`）
- ✅ 问答 API（`/api/v1/rag/query`），返回 `citations[]`
- ✅ 笔记同步到向量库（`notes/sync.py`）

#### 3.7 新增 / 优化

| 待办 | 内容 | 优先级 |
|---|---|---|
| 扫描型 PDF OCR | Tesseract / PaddleOCR 接入 | P2（v1.2） |
| 表格 / 图片提取 | pdfplumber 已能取表，图片缺 | P2 |
| 多文档对比问答 | "对比 2024/2025 财报" → 多 chunk 召回 + LLM 综合 | P1 |
| 按股票过滤召回 | `rag_chain.query(stock_code='000001')` 已支持，前端 UI 加 | P1 |
| 召回质量指标 | 加 `score_threshold` 过滤掉 score<0.3 的 chunk | P2 |
| 标签管理 | `doc` 表已有 `industry_code`，前端按行业筛选 | P2 |
| 批量导入 | 整个目录递归上传 | P2 |

#### 3.8 RAG 答案质量基线（验收用例）

```python
# tests/test_rag_quality.py
@pytest.mark.parametrize("q,expected_in_citation", [
    ("宁德时代 2024 年营收", "300750"),
    ("比亚迪 2024 年毛利率", "002594"),
    ("长江电力分红率", "600900"),
])
def test_rag_citation_accuracy(q, expected_in_citation):
    resp = rag_chain.query(q)
    assert any(expected_in_citation in str(c) for c in resp["citations"])
    assert "参考资料中没有提到" not in resp["answer"]
```

#### 3.9 合规边界

- 上传文档 = 用户自负责任
- AI 回答必须附 `citations[]`（防止幻觉，已实现）
- 用户笔记入向量库 **默认排除** 投资金额 / 个人账户数字（用 regex mask）

---

### 模块三：AI 投研分析引擎 ✅🟡❌

#### 3.10 单标的深度分析 ✅（4 个 analyzer 已落地）

| Analyzer | 现状 | 输入数据 | 输出 |
|---|---|---|---|
| `financial.py` | ✅ | 30 天 K 线 + 因子 + 行业 | 财报解读（亮点 / 隐患 / 判断） |
| `risk.py` | ✅ | 高波动日 + 因子 | 风险扫描（高/中/低） |
| `valuation.py` | ✅ | PE/PB/PS + 历史百分位 | 估值测算（简版 PE/PB） |
| `sentiment.py` | ✅ | 研报文本摘要（来自 RAG 召回） | 一致预期 / 分歧 |

**保留**。优化方向：
1. 引入完整三张表（balance/profit/cashflow）→ 财务分析深度 ↑
2. 风险扫描引入 `announcement` 表（减持 / 质押 / 诉讼关键词）→ 风险点 ↑
3. 估值加入 DCF 模板（见 3.12）

#### 3.11 行业 & 宏观分析 ❌（v1.2 新建）

```
ai/analyzers/
├── industry.py        # 行业横向对比（新建）
├── macro.py           # 宏观解读（新建）
└── comparative.py     # 多公司对比（新建）
```

**`industry.py`**：
```python
def run(industry_l1: str, save: bool = True) -> dict:
    """
    1. 从 DuckDB 拉 industry_l1 下所有股票
    2. 计算每家 PE/PB/ROE/营收增速 的均值 / 中位数 / 排名
    3. 拼 prompt：PROMPT_INDUSTRY（新建模板）
    4. LLM 输出：行业景气度判断 + 头部 / 尾部公司特征
    """
```

**`macro.py`**：
```python
def run(date_range: tuple) -> dict:
    """
    1. 从 macro_indicator 拉 CPI/PMI/利率/汇率
    2. 拉同期 notice_chain 的行业涨跌幅
    3. 拼 PROMPT_MACRO
    4. LLM 输出：宏观→哪些板块利好 / 利空
    """
```

**`comparative.py`**：
```python
def run(stock_codes: list[str], save: bool = True) -> dict:
    """
    对比 N 家公司 财务三张表 + 估值 + 涨跌幅
    """
```

#### 3.12 DCF 估值模板（v1.2 新建）

不要求 GUI 复杂计算器，先做"AI 辅助输入参数 + Python 计算"。

```python
# valuation/dcf.py
def dcf_valuation(
    stock_code: str,
    revenue_growth_5y: list[float],   # AI 根据历史营收 + 行业增速推
    fcf_margin: float,                # 自由现金流 / 营收
    wacc: float,                      # 加权平均资本成本
    terminal_growth: float = 0.025,
) -> dict:
    """标准两阶段 DCF，返回每股内在价值"""
```

**前端**：表单填 4 个数 → 调 `/api/v1/valuation/dcf` → 返回 {per_share_intrinsic, sensitivity_matrix}。

#### 3.13 提示词常量集合（追加模板）

```python
# ai/prompts.py 追加
PROMPT_INDUSTRY = """基于以下行业横向对比数据 ..."""
PROMPT_MACRO    = """基于以下宏观 + 板块涨跌幅 ..."""
PROMPT_COMPARE  = """对比以下 {n} 家公司的 ..."""
PROMPT_DCF_INPUT = """你是 DCF 估值专家。基于 {stock_code} 的财务数据 ..."""
```

---

### 模块四：投研工作台 🟡

#### 4.1 自选池 ✅

| 功能 | 状态 | 路径 |
|---|---|---|
| 创建分组 | ✅ | `/api/v1/watchlist/groups` |
| 删除分组 | ✅ | 同上 |
| 添加标的（按名称或代码搜索） | ✅ | `/api/v1/watchlist/{group}` |
| 删除标的 | ✅ | 同上 |
| 列表 + 概览 | ✅ | 同上 |
| **看板仪表盘**（每组关键指标预警） | ❌ | 待 v1.1 |
| **导入 / 导出**（CSV） | ❌ | 待 v1.1 |

#### 4.2 研究卡片 🟡

**目标**：每只标的生成一张"投研卡片"，聚合 行情 + 财务 + AI 摘要 + 个人笔记。

**当前 UI**：`panel-stock` 已能显示 K 线 + 因子 + 概览，但**未聚合 AI 摘要 + 笔记**。

**改造**：在 `stock-card` 区块追加 4 个子卡片：
1. **行情**（已有）：K 线 / 因子
2. **财务**（待加）：最近季度财务三表关键字段
3. **AI 摘要**（待加）：自动跑 4 个 analyzer，结果折叠在卡片
4. **个人笔记**（待加）：所有 stock_code 关联的 note 列表

#### 4.3 笔记系统 ✅

| 功能 | 状态 | 备注 |
|---|---|---|
| Markdown 编辑 | ✅ | `textarea` |
| 关联股票 | ✅ | `note.stock_code` |
| 标签 | ✅ | `note.tags` (VARCHAR 逗号分隔) |
| 同步到向量库 | ✅ | `notes/sync.py` |
| 插入图表 | 🟡 | textarea 不支持，需切 Monaco / CodeMirror | P2 |
| 插入公式 | 🟡 | KaTeX 渲染，已加为前端可选库 | P2 |

**改造建议**（P2）：
- 把 textarea 换成 [EasyMDE](https://github.com/Ionaru/easy-markdown-editor)（30 KB，支持图片粘贴 / 表格 / 公式）
- 在笔记渲染时启用 `markdown-it-katex`

#### 4.4 提示词模板库 🟡

**现状**：`ai/prompts.py` 6 个常量。

**新增**：
```
ai/prompts/
├── __init__.py        # 重新导出
├── financial.py
├── research.py         # 投研通用（提问模板）
├── custom/            # 用户自定义（DB 存储）
└── registry.py        # 注册到 prompt_template 表
```

**DB**：
```sql
CREATE TABLE prompt_template (
    template_id    VARCHAR PRIMARY KEY,
    name           VARCHAR(64),
    category       VARCHAR(32),       -- financial/risk/industry/macro/custom
    body           VARCHAR,             -- jinja2 模板
    variables      VARCHAR,            -- 变量声明 ['stock_code', 'short_name', ...]
    is_builtin     BOOLEAN DEFAULT false,
    created_at     TIMESTAMP
);
```

**API**：`/api/v1/prompts` (GET/POST/PUT/DELETE)  
**UI**："提示词库" tab（P1）

#### 4.5 看板仪表盘 ❌（v1.1 新建）

```
panel-dashboard
├── 自选池（监控池）所有股票今日涨跌
├── 关键指标卡片（PE 中位数、PB 中位数、ROE 中位数）
├── 预警列表（触发预警规则的事件，见 5.5）
└── 今日公告（复盘 @自选池的公告）
```

---

### 模块五：资产跟踪 + 复盘 🟡

#### 5.1 持仓台账 ✅

| 功能 | 状态 | 备注 |
|---|---|---|
| 录入（成本、数量、日期） | ✅ | `position.py` |
| 列表 | ✅ | 同上 |
| **当前估值（市值 + 浮动盈亏）** | 🟡 | 数据可计算，但 UI 缺 |
| **同步当前价（每日拉一次）** | ❌ | 缺，需 v1.1 加 daily sync |

#### 5.2 交易记录 ✅

| 功能 | 状态 | 备注 |
|---|---|---|
| 手动录入 | ✅ | `trade.py` |
| 标记买卖逻辑 | ✅ | `logic_text` |
| 关联笔记 | ✅ | `note_id` |
| **关联当时的研究卡片** | ❌ | 缺，待 v1.1 |

#### 5.3 交易复盘 🟡

**现状**：`portfolio/review.py` 已有 AI 复盘（PROMPT_REVIEW）。

**优化**：
1. 引入同期 benchmark（沪深 300）计算 α / β
2. 引入当时的研究笔记 → AI 总结"当时判断 vs 实际结果"差异
3. 输出多维度归因表：

```
| 维度       | 收益贡献 | 说明                       |
| 行业 β     | +5.2%    | 同期新能源板块上涨 8%      |
| 个股 α     | -1.3%    | 跑输板块 6.5%             |
| 择时       | +0.8%    | 低位买入贡献              |
| 总收益     | +4.7%    | α + β + 择时             |
```

#### 5.4 交易规则库 ❌（v1.3）

```
portfolio/
└── rules.py          # 用户自定义规则 CRUD
```

**DB**：
```sql
CREATE TABLE trade_rule (
    rule_id      VARCHAR PRIMARY KEY,
    name         VARCHAR(64),
    category     VARCHAR(32),       -- entry/exit/risk/sizing
    rule_text    VARCHAR,           -- 自由文本，如"PE > 50 不买"
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP
);
```

**AI 校验**：`POST /api/v1/rules/check {stock_code, idea}` → 调 LLM 判断"想法 vs 规则冲突点"。

#### 5.5 预警提醒 🟡（v1.1 完善）

**现状**：`watchlist/alerts.py` 已有规则定义。

**新增**：
```sql
CREATE TABLE alert_rule (
    rule_id      VARCHAR PRIMARY KEY,
    name         VARCHAR(64),
    target_type  VARCHAR(16),     -- stock/group/portfolio
    target_value VARCHAR(32),     -- '000001' or 'group:持仓池'
    metric       VARCHAR(32),     -- pe/pb/roe/change_pct/announcement/...
    operator     VARCHAR(4),      -- > / < / ==
    threshold    DOUBLE,
    channel      VARCHAR(16),     -- email/lark/web
    is_active    BOOLEAN DEFAULT true
);
```

**触发**：
- 估值类：每日 17:30 跑（K 线更新后）
- 公告类：每日 19:00 爬完自选池公告后
- 持仓类：每日收盘后

**通知**：v1 用邮件（`utils/send_email.py` 参考）；后续 lark。

---

### 模块六：Agent 智能工作流 ❌（v2+ 延后）

**MVP 不做**。设计预留：

```python
# agents/  (v2 新建)
├── base.py            # Agent 抽象
├── data_agent.py      # 数据检索 Agent
├── risk_agent.py      # 风险审查 Agent
├── synthesis_agent.py # 观点汇总 Agent
├── orchestrator.py    # ReAct 调度
└── tasks/             # 预定义工作流
    ├── daily_report.py
    └── full_research.py
```

**触发**：
- 手动：UI 按钮 "生成完整投研简报"
- 自动：cron 每日 20:00 跑自选池日报

---

### 模块七：系统基础能力 🟡

| 能力 | 状态 | 备注 |
|---|---|---|
| 单用户 | ✅ | 无 auth（本地单用户） |
| Chroma 向量库 | ✅ | 已用 |
| LLM 切换 | ✅ | MiniMax/Qwen/DeepSeek/Mock |
| **导出 Markdown / PDF** | 🟡 | 仅有 CSV |
| **记忆 / 偏好** | ✅ | `ai/memory.py` + `data/user_memory.json` |
| **模型路由（按场景选模型）** | ❌ | 待 v1.1：财报/研报用 M3，分类/抽取用 Flash |

#### 7.1 导出（v1.1）

```
exports/
├── md.py       # Markdown（已能，HTML→MD）
├── pdf.py      # PDF（weasyprint / reportlab）
└── excel.py    # Excel（openpyxl）
```

API：
- `GET /api/v1/exports/notes/{id}/md` → 下载 .md
- `GET /api/v1/exports/analysis/{id}/pdf` → 下载 .pdf（渲染 Markdown）
- `GET /api/v1/exports/watchlist/{group}/excel` → 下载 .xlsx

#### 7.2 模型路由（v1.1）

```python
# ai/router.py
def route_prompt(task_type: str) -> str:
    """
    - financial_summary / risk_scan / dcf / industry → MiniMax-M3
    - classification / extraction / quick_qa         → MiniMax-M3-Flash
    - rag_qa                                       → qwen-long（128k 上下文）
    """
```

---

## 4. 数据库 schema（最终版）

在 `quant_db/etl/schema.sql` 基础上**追加**：

```sql
-- ========== v1.1 增量 ==========

-- 完整三表
CREATE TABLE IF NOT EXISTS finance_balance (
    stock_code  VARCHAR(6),
    report_date DATE,
    total_assets DOUBLE,
    total_liab  DOUBLE,
    total_equity DOUBLE,
    goodwill    DOUBLE,
    ar          DOUBLE,        -- 应收账款
    inventory   DOUBLE,
    short_debt  DOUBLE,
    long_debt   DOUBLE,
    PRIMARY KEY (stock_code, report_date)
);

CREATE TABLE IF NOT EXISTS finance_profit (
    stock_code  VARCHAR(6),
    report_date DATE,
    revenue     DOUBLE,
    cogs        DOUBLE,        -- 营业成本
    sell_exp    DOUBLE,
    admin_exp   DOUBLE,
    rd_exp      DOUBLE,
    finance_exp DOUBLE,
    op_profit   DOUBLE,
    net_profit  DOUBLE,
    gross_margin DOUBLE,
    net_margin  DOUBLE,
    PRIMARY KEY (stock_code, report_date)
);

CREATE TABLE IF NOT EXISTS finance_cashflow (
    stock_code  VARCHAR(6),
    report_date DATE,
    op_cash     DOUBLE,
    inv_cash     DOUBLE,
    fin_cash    DOUBLE,
    capex       DOUBLE,
    fcf         DOUBLE,        -- 自由现金流 = op_cash - capex
    PRIMARY KEY (stock_code, report_date)
);

-- 公告
CREATE TABLE IF NOT EXISTS announcement (
    ann_id      VARCHAR PRIMARY KEY,
    stock_code  VARCHAR(6),
    title       VARCHAR(256),
    publish_date DATE,
    source      VARCHAR(20) DEFAULT 'cninfo',
    url         VARCHAR(512),
    content     VARCHAR,       -- 用户上传或手动录入
    sentiment   VARCHAR(16),   -- 利好/利空/中性
    categories  VARCHAR,       -- 减持/定增/业绩预告/...
    ingested_at TIMESTAMP
);
CREATE INDEX idx_ann_stock_date ON announcement(stock_code, publish_date DESC);

-- 宏观
CREATE TABLE IF NOT EXISTS macro_indicator (
    date     DATE,
    category VARCHAR(32),      -- cpi/pmi/interest_rate/fx
    name     VARCHAR(64),
    value    DOUBLE,
    yoy      DOUBLE,
    PRIMARY KEY (date, category, name)
);

-- 产业链（手工维护）
CREATE TABLE IF NOT EXISTS industry_chain (
    stock_code  VARCHAR(6),
    industry_l1 VARCHAR(32),
    industry_l2 VARCHAR(32),
    relation    VARCHAR(16),    -- upstream/downstream/peer
    target_stock VARCHAR(6),
    target_name  VARCHAR(64),
    PRIMARY KEY (stock_code, relation, target_stock)
);

-- 提示词模板
CREATE TABLE IF NOT EXISTS prompt_template (
    template_id VARCHAR PRIMARY KEY,
    name        VARCHAR(64),
    category    VARCHAR(32),
    body        VARCHAR,
    variables   VARCHAR,
    is_builtin  BOOLEAN DEFAULT false,
    created_at  TIMESTAMP
);

-- 预警规则
CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id      VARCHAR PRIMARY KEY,
    name         VARCHAR(64),
    target_type  VARCHAR(16),
    target_value VARCHAR(32),
    metric       VARCHAR(32),
    operator     VARCHAR(4),
    threshold    DOUBLE,
    channel      VARCHAR(16),
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP
);

-- 预警触发日志
CREATE TABLE IF NOT EXISTS alert_event (
    event_id    VARCHAR PRIMARY KEY,
    rule_id     VARCHAR,
    stock_code  VARCHAR(6),
    metric      VARCHAR(32),
    value       DOUBLE,
    triggered_at TIMESTAMP,
    message     VARCHAR,
    sent_status VARCHAR(16)   -- pending/sent/failed
);

-- 交易规则库
CREATE TABLE IF NOT EXISTS trade_rule (
    rule_id      VARCHAR PRIMARY KEY,
    name         VARCHAR(64),
    category     VARCHAR(32),
    rule_text    VARCHAR,
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP
);

-- 估值假设（DCF 输入）
CREATE TABLE IF NOT EXISTS valuation_assumption (
    stock_code  VARCHAR(6),
    rev_growth_5y VARCHAR,    -- JSON: [0.15, 0.12, 0.10, 0.08, 0.05]
    fcf_margin    DOUBLE,
    wacc          DOUBLE,
    terminal_g    DOUBLE,
    updated_at    TIMESTAMP,
    PRIMARY KEY (stock_code)
);
```

---

## 5. API 路由清单（v1.1 目标）

在 `quant_db/web/ai_routes.py` 现有 35+ 路由基础上**追加**：

```
# 数据接入（v1.1）
POST   /ingest/finance                 # Tushare 拉财报
POST   /ingest/announcement/{code}     # cninfo 抓公告
POST   /ingest/macro                   # akshare 拉宏观
POST   /ingest/news                    # RSS 抓新闻

# 行业 / 宏观分析（v1.2）
POST   /ai/industry/{l1}
POST   /ai/macro
POST   /ai/compare                     # body: {codes: ['000001','000002']}

# 估值（v1.2）
POST   /valuation/dcf                  # body: {stock_code, assumptions}

# 提示词模板（v1.1）
GET    /prompts
POST   /prompts
PUT    /prompts/{id}
DELETE /prompts/{id}

# 预警规则（v1.1）
GET    /alerts/rules
POST   /alerts/rules
DELETE /alerts/rules/{id}
GET    /alerts/events                  # 历史触发

# 交易规则（v1.3）
GET    /rules
POST   /rules
POST   /rules/check                    # AI 校验新想法

# 看板（v1.1）
GET    /dashboard/overview             # 自选池概览

# 导出（v1.1）
GET    /exports/notes/{id}/md
GET    /exports/analysis/{id}/pdf
GET    /exports/watchlist/{group}/excel

# 持仓 / 复盘增强（v1.1）
GET    /portfolio/snapshot             # 当前市值 + 浮动盈亏
POST   /portfolio/sync-prices          # 同步当前价
POST   /portfolio/review/compare       # 对比研究笔记 vs 实际
```

**总数**：v1 现有 35 → v1.1 目标 60+。

---

## 6. Web UI 规划（v1.1 目标）

```
┌─ 顶栏 ──────────────────────────────────────────────────────────────┐
│ 📊 Quant Workbench · 个人 AI 投研平台        [🤖 MiniMax-M3 LIVE] │
└────────────────────────────────────────────────────────────────┘
┌─ 标签页 ───────────────────────────────────────────────────────────┐
│ [📈 行情] [🔍 个股] [📊 行业] [🌐 宏观] [📝 SQL]                   │
│ [⭐ 自选] [📔 笔记] [📚 RAG] [🤖 AI] [💼 持仓] [⚙ 规则] [📤 导出] │
└────────────────────────────────────────────────────────────────┘
```

| Tab | 现有 | 改造点 |
|---|---|---|
| 📈 行情 | ✅ snapshot | 加「多周期切换」「复权切换」 |
| 🔍 个股 | ✅ stock | 加子卡片：AI 摘要 + 笔记列表 + 财务三表 |
| 📊 行业 | ❌ 新建 | 横向对比表 + AI 行业分析 |
| 🌐 宏观 | ❌ 新建 | 指标卡片 + AI 解读 |
| 📝 SQL | ✅ | 保留（已能用） |
| ⭐ 自选 | ✅ watchlist | 加「看板模式」+「导入/导出」 |
| 📔 笔记 | ✅ notes | 切 EasyMDE + 公式渲染 |
| 📚 RAG | ✅ rag | 加「按股票过滤」+「多文档对比」 |
| 🤖 AI | ✅ ai | 加 4 个新 analyzer（industry/macro/compare/dcf_input） |
| 💼 持仓 | ❌ 新建 | 列表 + 浮动盈亏 + 复盘入口 |
| ⚙ 规则 | ❌ 新建 | 交易规则库 + AI 校验 |
| 📤 导出 | ❌ 新建 | MD / PDF / Excel 下载 |

---

## 7. 非功能性需求

### 7.1 性能

| 指标 | 目标 |
|---|---|
| 单股查询响应 | < 500 ms（纯 DB） |
| RAG 召回 + LLM 回答 | < 30 s（MiniMax-M3-M3） |
| AI 单标的分析 | < 60 s（financial + risk + valuation 三件套并行） |
| 每日增量更新耗时 | < 30 min（5 640 股 × 30 天） |
| Web 首屏 | < 1 s（静态资源已缓存） |

### 7.2 成本（实测基线，MiniMax-M3）

| 操作 | Tokens | 单次成本 | 月度（每天一次自选池 10 株） |
|---|---|---|---|
| 财报解读 | 3 500 | ¥0.018 | ¥5.4 |
| 风险扫描 | 2 400 | ¥0.013 | ¥3.9 |
| 估值测算 | 2 500 | ¥0.013 | ¥3.9 |
| RAG 问答 | 880 | ¥0.005 | 视用量 |
| DCF 输入辅助 | 1 500 | ¥0.008 | 偶发 |
| **小计** | | | **约 ¥15-30/月** |

embedding 本地 BGE → **0 成本**。

### 7.3 安全 / 隐私

- ✅ API key 仅在 `~/.quant_env`（已加 ignore）
- ✅ DuckDB 单文件本地持久化
- ✅ LLM 调用**只传数据片段**，不传整张表（按 top_k 切片）
- 🟡 笔记入向量库需 regex mask 个人敏感数字（待加）
- ❌ 移动端访问（暂缓）
- ⚠️ 公告 PDF 内容如含个人姓名 / 住址等，自动 mask

### 7.4 合规

- ✅ 不抓付费研报（仅用户上传）
- ✅ 不爬 cninfo 反爬接口（用合规公开查询）
- ✅ 不做实盘下单
- ✅ 不外发用户笔记 / 持仓数据到云端（除 LLM 调用，prompt 不留持久记录——MiniMax API 30 天后清）
- ⚠️ 个人版不需要 ICP / 等保

---

## 8. 实施路线图

### v1.1（本月 / 1-2 周）— **基本面 + 工作台 + 导出**

- [ ] Tushare token 配置 + 财务三表接入
- [ ] `indicators/` 6 个核心指标
- [ ] 提示词模板库 DB + API
- [ ] 预警规则引擎 + UI 入口
- [ ] 导出 MD / PDF / Excel
- [ ] 持仓页面（snapshot + sync-prices）
- [ ] EasyMDE 替换 textarea
- [ ] 模型路由（财报→M3，分类→Flash）
- [ ] 文档：`docs/USER_GUIDE.md`、`docs/API.md`

**交付**：日常可用的「投研工作台」。

### v1.2（4-6 周）— **公告 + 行业 + 宏观 + DCF**

- [ ] cninfo 公告抓取（合规）
- [ ] 公告入库到 `announcement` 表
- [ ] 风险 analyzer 引入公告关键词（减持 / 诉讼 / 质押）
- [ ] 行业横向对比 analyzer
- [ ] 宏观解读 analyzer
- [ ] 多公司对比 analyzer
- [ ] DCF 估值模块
- [ ] 扫描型 PDF OCR（PaddleOCR）
- [ ] RAG 多文档对比
- [ ] launchd 加每日 19:00 公告抓取任务

**交付**：单标的深度 + 行业宏观两维度齐全。

### v1.3（按需）— **规则库 + 复盘增强**

- [ ] 交易规则库 DB + API
- [ ] AI 校验新想法 vs 规则
- [ ] 复盘引入 α/β 归因
- [ ] 看板仪表盘

### v2+（暂缓）— **Agent**

- [ ] 数据检索 / 风险审查 / 观点汇总 3 个 Agent
- [ ] ReAct 编排器
- [ ] 每日 20:00 自选日报自动跑
- [ ] 移动端 PWA（如果需要）

---

## 9. 验收用例（MVP 必过）

```python
# tests/test_mvp.py
def test_mvp_end_to_end():
    # 1. 行情：拉 30 天 K 线
    df = get_kline("000001", days=30)
    assert len(df) == 30
    assert "close" in df.columns

    # 2. 财务：拉最近季度
    fin = get_finance_core("000001")
    assert fin["roe"] is not None

    # 3. RAG：上传 PDF + 提问
    rag_chain.ingest_file("tests/data/000001_2024_annual.pdf", doc_type="pdf", stock_code="000001")
    resp = rag_chain.query("2024 年营收多少？", stock_code="000001")
    assert len(resp["citations"]) > 0
    assert "2024" in resp["answer"] or "参考资料中没有" in resp["answer"]

    # 4. AI 分析：4 个 analyzer 都能跑
    for t in ["financial", "risk", "valuation", "sentiment"]:
        r = run_analyzer(t, "000001", save=False)
        assert "response" in r
        assert len(r["response"]) > 100

    # 5. 自选池 + 笔记 + 持仓 CRUD
    add_note("000001", "测试笔记", "# 标题\n正文")
    add_position("001001", cost=10.0, qty=100, date="2024-01-01")
    add_trade("001001", "buy", price=10.0, qty=100, date="2024-01-01")

    # 6. 复盘
    review = run_review()
    assert review["trade_count"] >= 1

    # 7. LLM 切换
    os.environ["LLM_PROVIDER"] = "mock"
    r = run_analyzer("financial", "000001", save=False)
    assert "Mock" in r["response"]
```

---

## 10. 附录

### 10.1 Qbot 借鉴清单（具体文件）

| 借鉴 | 来源文件 | 目标路径 | 备注 |
|---|---|---|---|
| BOLL 指标 | `qbot/strategies/boll_strategy.py` | `quant_db/indicators/boll.py` | 改纯计算 + 返回 DataFrame |
| MACD 指标 | `qbot/strategies/util.py` `computeMACD` | `quant_db/indicators/macd.py` | 同样改造 |
| ADX | `qbot/strategies/adx_strategy.py` | `quant_db/indicators/adx.py` |  |
| KDJ | `qbot/strategies/k_lines.py` | `quant_db/indicators/kdj.py` |  |
| 邮件通知 | `utils/send_email.py` | `quant_db/utils/notify.py` | 抽出通用 send_email |

**不借鉴**：所有 `*_bt.py`（回测）、`gui/*`（GUI）、`pytrader/*`（实盘）。

### 10.2 风险登记册

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| adata 公开接口被反爬 | 中 | 高 | 已有备份：sina + akshare + baostock |
| Tushare 个人额度耗尽 | 中 | 中 | adata 备份；按需升级积分 |
| LLM 成本失控 | 低 | 中 | 成本审计表 + 月度预算上限 |
| DuckDB 写锁冲突 | 低 | 低 | 单连接 + 批量事务（已实现） |
| 公告抓取被 cninfo 限流 | 中 | 中 | 控制 QPS=2，重试 + 退避 |
| OCR 准确率低 | 中 | 低 | PaddleOCR + 用户手动校正 |
| 用户笔记含敏感数据 | 中 | 高 | regex mask 入向量库 |

### 10.3 参考资料

- adata 文档：https://adata.30006124.xyz/
- Tushare 文档：https://tushare.pro/document/2
- akshare 文档：https://akshare.akfamily.xyz/
- BGE-small-zh：https://huggingface.co/BAAI/bge-small-zh-v1.5
- Chroma：https://docs.trychroma.com/
- MiniMax API：OpenAI 兼容协议，base_url 见 `ai/client.py`
- DuckDB：https://duckdb.org/docs/

---

## 11. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-12 | v1.1 | 初版，基于已实现 quant_db v1 增量规划 |
|  |  |  |

---

**自检 Checklist**（评审时核对）：
- [ ] 是否覆盖你列出的全部 7 大模块？
- [ ] 是否明确"已实现 / 半成品 / 待建"三类？
- [ ] Qbot 借鉴清单是否清晰？
- [ ] 路线图是否分阶段且每阶段可交付？
- [ ] 验收用例是否覆盖核心链路？
- [ ] 合规边界是否明确？
