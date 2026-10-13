# Quant Workbench · 用户指南

> 个人 AI 量化投研平台 · 当前版本 v2（MVP 已打磨 + Phase A 数据夯实）
> 最后更新：2026-10-13

---

## 启动

```bash
cd /Users/quanlin/Desktop/项目/AI量化/quant_db
source .venv/bin/activate
bash scripts/run_web.sh           # mock 模式（无需 API key）
bash scripts/run_web.sh --live    # live 模式（读 ~/.quant_env 中的 LLM key）
```

浏览器访问：<http://localhost:8765>

> 盘后（18:30）由 macOS launchd 自动跑 `launchd/com.quanlin.quant.backfill.plist`：
> 行业 + 核心财务 + 三表明细 + 宏观 + 公告，全量增量回填。

---

## 界面布局（深色金融科技风格）

- **顶栏**：左为品牌，右为 LLM 状态徽章（🟢 LIVE / 🟡 MOCK）。
- **侧栏导航**（3 区段）：
  - 数据：📈 全市场快照 / 🔍 个股详情 / 📊 指数看板 / 📐 技术指标 / 📝 SQL 查询
  - 投研：🤖 AI 分析 / 📚 RAG 知识库 / 💬 提示词库
  - 持仓：⭐ 自选池 / 💼 持仓 / 🔔 预警 / 📔 笔记
- **主区域**深色主题（Bloomberg/TradingView 美学）：背景 `#0a0e1a`，上涨红 `#ef232a`，下跌白 `#ffffff`（成交量、MACD 柱均按此着色）。

---

## 功能速查

### 📈 全市场快照
全市场标的概览表格，支持按交易所/排序/搜索过滤，分页浏览。

### 🔍 个股详情
顶部为关键指标卡（最新价、涨跌幅、市值、PE/PB 等），下方子标签：
- **日 K**：蜡烛图 + 成交量（涨红跌白）+ MACD（红正白负）三栏。
- **周 K**：由日 K 聚合的周线（k_type=2），同样含成交量 + MACD。
- **5 分钟 K**：单日分钟级蜡烛图。
- **因子**：Alpha 因子（动量/波动/量比等）。
- **概览**：基础资料。

### 📊 指数看板
指数日 K 为 **K 线 → 成交量 → MACD** 三栏布局（成交量在 MACD 上方）；指数 5 分钟同样含成交量栏。

### 📐 技术指标
支持 8 个指标：`MA` / `MACD` / `BOLL` / `RSI` / `KDJ` / `ADX` / `ARBR` / `StochRSI`。
可调参数（JSON）+ 窗口天数，前端实时绘制。

### 🤖 AI 投研分析
对单只标的运行 4 类分析（由 LLM 自动路由最佳模型）：
- **财报解读**：基于 `finance_core` 近 8 期核心指标 + 三表明细风险科目（应收/存货/商誉/经营现金流占资产比）。
- **风险扫描**：商誉/质押/减持/诉讼/存货减值等雷点识别。
- **估值测算**：基于现价 + `finance_core`（EPS/ROE/毛利率）动态算 PE，输出估值区间。
- **研报观点汇总**：多家券商一致预期与分歧。

结果支持 **导出 Markdown / PDF**。

### 📚 RAG 知识库
- 上传 PDF / Word / Markdown / 公告 / 研报 → 切片 → 向量化（Chroma）→ 入库。
- 问答带**引用溯源**（防幻觉），可按股票/行业/时间归类。

### 💬 提示词模板库
内置 6 个投研模板（财报分析/风险排查/行业分析等），支持新建自定义模板（jinja2 变量）。

### ⭐ 自选池
分组管理（观察池/持仓池/行业池），支持增删与组内预警查看。

### 🔔 预警规则
支持 metric：`pe / pb / roe / gross_margin / change_pct`；operator：`> < >= <= == !=`。

### 💼 持仓与复盘
持仓快照（现价/市值/浮动盈亏/收益率）+ 一键同步最新价；交易记录与复盘。

### 📔 笔记
基于 **EasyMDE** 的 Markdown 富编辑器，支持 **KaTeX 公式**（`$...$` / `$$...$$` 在预览中渲染）；可同步到向量库供 RAG 检索。

### 📤 导出
- Markdown：分析报告 / 笔记 / 自选池
- PDF：reportlab（中文支持）
- Excel：自选池 / 持仓 + 交易记录

### 🤖 AI 模型路由
| 任务类型 | 模型 |
|---|---|
| 财报 / 估值 / 行业 / 风险 / 复盘 | MiniMax-M3 |
| 分类 / 抽取 / 摘要 / 情绪 | MiniMax-M3.1-Flash-Preview |
| RAG 长文本问答 | qwen-long |

---

## 数据接入（盘后自动，个人免费源）

| 数据 | 来源 | 存储 |
|---|---|---|
| 行情（日/指数/5分钟） | adata + 新浪 | `daily_kline` / `index_kline` / `minute_kline` |
| 财务指标（EPS/ROE/营收/净利/同比） | adata `get_core_index` | `finance_core` |
| 财务三表明细（约 25 个常用科目） | akshare 东方财富 | `finance_statement`（长表 EAV） |
| 宏观（CPI/PMI/LPR） | akshare | `macro_indicator` |
| 公告 | akshare 东方财富 | `doc`（doc_type='announcement'） |
| 申万行业 | adata | `stock_industry` |

---

## CLI 工具

```bash
# 测试
python -m pytest tests/

# 数据库
python -c "from etl.db import init_schema; init_schema()"

# 数据接入（均可 --force 全量 / --limit N 验证）
python -m data_ingest.industry
python -m data_ingest.finance_adata
python -m data_ingest.finance_statements   # 三表明细
python -m data_ingest.macro                 # 宏观
python -m data_ingest.announcement --days 3  # 公告（按交易日）
python -m data_ingest.weekly_kline          # 周 K 聚合（写入 daily_kline k_type=2）
```

---

## 开发约定

- **TDD**：先写测试（RED）→ 实现（GREEN）→ 重构。
- **防护**：每次改动跑 `python3 scripts/regression_check.py --api http://127.0.0.1:8765`，全绿即未破坏现有功能。基线 tag：`mvp-v1`。

### 代码组织
```
ai/            分析器 + 模型路由 + LLM 客户端
alerts/        预警引擎
data_ingest/  数据接入（行业/财务/三表/宏观/公告/周线）
etl/          ETL + 数据库 schema
exports/      导出 MD/PDF/Excel
indicators/   技术指标
knowledge/    RAG（embedder/vector_store/rag_chain）
notes/        投研笔记
portfolio/    持仓 + 交易 + 复盘
prompts/      提示词模板
watchlist/    自选池
web/          FastAPI 服务
```

---

## 路线图

- ✅ MVP：行情/因子/指数看板 + 图表配色统一 + git 基线 + 回归脚本
- ✅ Phase A（进行中）：财务三表明细 + 宏观 + 公告接入；finance_core 接通分析器；周 K；EasyMDE 笔记；本文档
- 🔄 Phase B：industry/macro/comparative/DCF 分析器；风险接入公告关键词；盈利拆解
- 🔄 Phase C：研究卡片聚合；看板仪表盘；交易规则库；复盘 α/β
- ⏳ Phase D：多 Agent 工作流（检索/风险/汇总 + 日报）
