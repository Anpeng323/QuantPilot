# quant_db · AI 量化投研系统 — 规范文档（SPEC）

> **版本**：v2（基于用户愿景 + 项目真实现状重写）
> **状态**：Active — 执行中
> **最后更新**：2026-10-13
> **MVP 基线**：✅ 已打磨完成，tag `mvp-v1`（commit `a96d173`）；回归脚本 `scripts/regression_check.py` 全绿

---

## 0. 重要纠偏：项目真实完成度（2026-10-13 实测）

旧 SPEC 把 v1.1 标记为"未开始"，**与代码库严重不符**。经实测，以下功能**已在代码中完整落地**（后端 + 前端）：

| 能力 | 状态 | 关键文件 |
|---|---|---|
| 行情：A股/指数/ETF 日K、5分钟K、因子 | ✅ 已落地，至 2026-10-12 | `etl/`、`analysis/factors.py`、`data/quant.duckdb` |
| 自选池（分组/增删/预警） | ✅ 已落地 | `web/ai_routes.py` `/watchlist/*` + 前端 |
| 笔记系统（含向量同步） | ✅ 已落地 | `notes/`、`/notes/*` + 前端 |
| RAG 知识库（Chroma 向量库） | ✅ 已落地 | `knowledge/`（embedder/vector_store/rag_chain）+ `/rag/*` + 前端 |
| AI 分析引擎（财报/风险/情绪/估值） | ✅ 已落地（4 个分析器） | `ai/analyzers/` + `/ai/analyze` |
| 提示词模板库 | ✅ 已落地 | `prompts/` + 前端"提示词库" tab |
| 预警规则引擎 | ✅ 已落地（消费 finance_core） | `alerts/engine.py` + 前端"预警" tab |
| 持仓（快照/同步/交易/复盘） | ✅ 已落地 | `portfolio/` + 前端"持仓" tab |
| 技术指标模块（MA/MACD/BOLL/RSI/KDJ/ADX/ARBR/StochRSI） | ✅ 已落地 | `indicators/` + `/indicators` API |
| 导出（Markdown/PDF/Excel） | ✅ 已落地 | `exports/` + 前端导出按钮 |
| 模型路由（task_type→模型） | ✅ 已落地 | `ai/router.py` |
| LLM 客户端（MiniMax/Qwen/DeepSeek/Mock） | ✅ 已落地 | `ai/client.py` |
| AI 记忆接口 | 🟡 接口已落地（`/memory`），语义待增强 | `web/ai_routes.py` |

**真正的缺口**（决定了后续工作的重心）：

1. **数据是空的**：`stock_industry` 刚补到 5048/5640 只；`finance_core` 仅 40 只（盘后 18:30 定时任务回填中）。
2. **财务三表不完整**：`finance_balance`/`finance_profit`/`finance_cashflow` 三张表**不存在**（只有聚合的 `finance_core`）。
3. **分析器缺 4 类**：`industry.py`（行业）、`macro.py`（宏观）、`comparative.py`（多公司对比）、`valuation/dcf.py`（DCF）**文件不存在**。现有 `valuation.py` 仅为"基于历史价格的简化模板"，注释明确写"需接入财报库"。
4. **Agent 模块完全不存在**（0 文件）。
5. **文本/产业链类缺失**：公告抓取（仅路由、无爬虫）、资讯自动抓取与去噪、研报 OCR（扫描件）、宏观数据（CPI/PMI/利率）、产业链图谱——均无。
6. **小项**：EasyMDE 富编辑（仍是 textarea）、`USER_GUIDE.md` 文档。

> 注：代码层已存在，但**分析器目前只读日K+因子，未消费 `finance_core`**（`ai/analyzers/financial.py` 实测如此）。因此"补数据"之后还须"把数据接进分析器"才有价值。

---

## 1. 目标架构（用户愿景的 6 大模块）

```
┌──────────────────────────────────────────────────────────┐
│  投研工作台（前端 ECharts + FastAPI）                       │
│  自选池 · 研究卡片 · 笔记 · 提示词库 · 看板仪表盘            │
└───────────┬───────────────────────────────────┬──────────┘
            │                                    │
   ┌────────▼─────────┐              ┌──────────▼──────────┐
   │  AI 投研分析引擎   │              │   RAG 知识库         │
   │  财报/风险/估值/   │◄──检索──────►│  Chroma 向量库       │
   │  行业/宏观/对比    │              │  引用溯源/对比问答    │
   └────────┬─────────┘              └──────────┬──────────┘
            │                                    │
   ┌────────▼─────────┐  调度/编排   ┌──────────▼──────────┐
   │  Agent 工作流     │◄─────────────►│  数据接入模块        │
   │  检索/风险/汇总   │              │  行情/财报/公告/研报  │
   └──────────────────┘              │  资讯/产业链/宏观     │
                                     └──────────┬──────────┘
                                                │
                                     ┌──────────▼──────────┐
                                     │  系统基础能力         │
                                     │  单用户/向量库/LLM/   │
                                     │  导出/记忆           │
                                     └─────────────────────┘
```

---

## 2. 现状盘点（按愿景 6 模块对齐）

### 模块一 · 数据接入
| 子项 | 状态 | 说明 |
|---|---|---|
| A股/指数/ETF 基础行情（K线/量/换手/市值/PE·PB·ROE） | ✅/🟡 | 行情✅；PE/PB/ROE 来自 `finance_core`（正在回填，仅 40 只） |
| 多周期（日/周线） | 🟡 | 日线✅；周线需在 `etl` 加聚合 |
| 指标计算（MA/MACD/RSI/BOLL/自定义） | ✅ | `indicators/` 已含 8 类 + registry |
| 财报三表结构化 | 🟡 | `finance_core` 聚合表在填；三张明细表**不存在** |
| 公告抓取 + 关键信息提取 | ❌ | `/ingest/announcements` 路由在，但无爬虫（仅从 `doc` 表列出） |
| 研报 PDF 解析/OCR | 🟡/❌ | RAG 能解析**文本版** PDF；**扫描件 OCR 无** |
| 资讯（财经新闻/政策/宏观） | ❌ | `data_ingest/news.py` 仅手动录入占位 |
| 产业链数据/图谱 | ❌ | 无 |

### 模块二 · RAG 知识库
| 子项 | 状态 | 说明 |
|---|---|---|
| 智能检索（财报/研报/公告召回） | ✅ | `knowledge/rag_chain.py` + Chroma |
| 引用溯源（防幻觉） | 🟡 | 需核对 `rag_chain` 是否回传原文出处 |
| 文档管理（按股票/行业/时间归类、批量导入） | ✅ | `/rag/docs`、`/rag/upload`、`doc` 表带 `stock_code` |
| 对比问答（"对比 2024/2025 毛利率"） | ❌ | 当前为单轮问答，无时序对比能力 |

### 模块三 · AI 投研分析引擎（核心）
| 子项 | 状态 | 说明 |
|---|---|---|
| 财报智能解读（亮点/隐患/异常识别） | 🟡 | `financial.py` 存在但只读日K+因子，未用 `finance_core` |
| 盈利拆解（收入/成本/毛利归因） | ❌ | 需财务明细表支撑 |
| 风险扫描（商誉/质押/减持/诉讼/存货） | 🟡 | `risk.py` 存在；公告关键词（减持/质押）未接入 |
| 研报观点汇总（一致预期/分歧） | ❌ | 依赖研报 RAG 聚合 |
| 估值测算（DCF/PE·PB 模板） | 🟡/❌ | `valuation.py` 价格模板✅；**DCF 无** |
| 行业景气度汇总 | ❌ | 缺 `industry.py` |
| 行业横向对比 | ❌ | 缺 `comparative.py` |
| 宏观解读（利率/汇率/政策） | ❌ | 缺 `macro.py` + `macro_indicator` 表 |

### 模块四 · 投研工作台
| 子项 | 状态 |
|---|---|
| 标的自选池（分组：观察/持仓/行业） | ✅ |
| 研究卡片（行情+财务+AI摘要+笔记聚合） | 🟡（分析/笔记各自可用，缺统一聚合卡片 UI） |
| 笔记系统（图表/公式/同步向量） | ✅（公式 KaTeX/EasyMDE 富编辑 ❌） |
| 提示词模板库 | ✅ |
| 看板仪表盘（概览/预警列表/今日公告） | 🟡（持仓面板在；完整看板无） |

### 模块五 · Agent 智能工作流
| 子项 | 状态 |
|---|---|
| ReAct 投研 Agent（多步任务编排） | ❌ |
| 多 Agent 分工（检索/风险/汇总） | ❌ |
| 定时日报 Agent | ❌ |

### 模块六 · 系统基础能力
| 子项 | 状态 |
|---|---|
| 用户权限（单用户） | ✅ |
| 向量库（Chroma / 可换 Milvus） | ✅（Chroma 已用） |
| LLM 接入（MiniMax 等可切换） | ✅（MiniMax/Qwen/DeepSeek/Mock） |
| 导出（MD/PDF 存档） | ✅ |
| 记忆（AI 记住偏好） | 🟡（`/memory` 接口在，语义待增强） |

---

## 3. 数据架构现状

**已有表**（DuckDB `data/quant.duckdb`）：`stock_basic`、`trade_calendar`、`daily_kline`、`minute_kline`、`index_kline`、`factors_daily`、`stock_industry`（5048）、`finance_core`（40 只，回填中）、`doc`（RAG/公告/新闻统一存，含 `doc_type`）、`note`、`watchlist*`、`alert_*`、`portfolio*`、`ai_analysis`、`prompt_template`、`memory`。

**缺失表**：`finance_balance`、`finance_profit`、`finance_cashflow`（三张明细表）、`macro_indicator`、`industry_chain`、`announcement`（可用 `doc` 表 `doc_type='announcement'` 替代）。

**数据来源策略**（个人免费额度）：
- 行情/指数/ETF/分钟：`adata` + 新浪（已落地）
- 财务/行业：`adata`（`stock.finance.get_core_index`、`stock.info.get_industry_sw`，**免 token**）✅ 已验证
- 财务三表明细 / 更全数据：`tushare`（需 token，可选升级）
- 宏观（CPI/PMI/利率）：`akshare`
- 公告：交易所公开接口（cninfo 等），注意合规与反爬

---

## 4. 分阶段推进计划

> 原则：**数据先夯实 → 分析器吃数据 → 文本/产业链 → 工作台聚合 → Agent**。每阶段结束跑 `scripts/regression_check.py --api ...` 确保 MVP 不回退。

### Phase A · 数据夯实 + v1.1 收尾（约 1–2 周）
**目标**：让"已落地的代码"真正有数据可用。
- [ ] **财务三表明细**：新建 `finance_balance/profit/cashflow`，用 adata/tushare 回填（盘后定时）。
- [ ] **宏观数据**：`macro_indicator`（CPI/PMI/利率）via akshare，新建表 + 接入。
- [ ] **公告抓取**：cninfo 公开接口爬虫 → 入 `doc` 表（`doc_type='announcement'`）；launchd 每日 19:00。
- [ ] **把 `finance_core` 接进分析器**：`financial.py`/`valuation.py` 改为消费 `finance_core`（EPS/ROE/营收/净利/同比），让财报解读与估值不再"空转"。
- [ ] **周线聚合**：`etl` 增加周 K 生成。
- [ ] **EasyMDE 富编辑**替换笔记 textarea + KaTeX 公式。
- [ ] **文档**：`USER_GUIDE.md`、`API.md` 补全流程。
- 验收：分析器对任意有财务数据的股票能产出含真实指标的洞察；公告能自动入库。

### Phase B · 分析深度（约 2–3 周）
**目标**：补齐 4 个缺失分析器 + DCF。
- [ ] `ai/analyzers/industry.py`：行业景气度（政策/供需/价格）。
- [ ] `ai/analyzers/macro.py`：宏观对板块影响解读。
- [ ] `ai/analyzers/comparative.py`：同赛道多公司指标对比表。
- [ ] `ai/analyzers/valuation/dcf.py`：DCF 测算 + 前端表单 + `/valuation/dcf`。
- [ ] **风险扫描接入公告**：`risk.py` 引入公告关键词（减持/定增/诉讼/质押）。
- [ ] **盈利拆解**：基于三表明细的收入/成本/毛利归因。
- 验收：单标的分析覆盖"财报+风险+估值+DCF"；行业/宏观/对比分析器可调用。

### Phase C · 文本与产业链（约 2 周）
- [ ] **资讯自动抓取 + 去噪**：`data_ingest/news.py` 接 akshare/新闻源，去重降噪、过滤自媒体。
- [ ] **研报 OCR**：扫描件 PDF → PaddleOCR → 入库 RAG。
- [ ] **产业链图谱**：`industry_chain` 表 + 简易图谱（上下游/行业分类）。
- [ ] **RAG 对比问答**：支持"对比不同年份/公司"的时序对比查询。
- 验收：新闻自动流入；扫描研报可检索；产业链可可视化。

### Phase D · 工作台聚合增强（约 2 周）
- [ ] **研究卡片**：聚合 行情 + 财务 + AI 摘要 + 个人笔记 的统一卡片 UI。
- [ ] **看板仪表盘**：自选池概览、关键指标卡片、预警列表、今日公告。
- [ ] **交易规则库**：`trade_rule` 表 + `/rules` + AI 校验新想法。
- [ ] **复盘 α/β 归因**：vs 沪深300 benchmark。
- [ ] **记忆增强**：`/memory` 充实投资偏好语义，分析时贴合风格。
- 验收：打开一只股票即见完整投研卡片；看板一眼掌握自选池风险。

### Phase E · Agent 工作流（约 3 周）
- [ ] **数据检索 Agent**：查行情/拉财报/取公告。
- [ ] **风险审查 Agent**：专挑利空、识别雷点。
- [ ] **观点汇总 Agent**：整合多方生成报告。
- [ ] **ReAct 编排器**：串联上述完成"查财报→检索公告→对比行业→出简报"。
- [ ] **定时日报 Agent**：每日 20:00 自选池公告+简版投研日报。
- 验收：一句"给我 XX 公司的投研简报"自动产出多步报告。

### Phase F · 扩展（按需）
- [ ] 移动端 PWA；多模型对比（同 prompt 跑 M3 vs Qwen）；向量库换 Milvus；产业链图谱交互增强。

---

## 5. 技术选型与约束

- **存储**：DuckDB（行情/结构化）、Chroma（向量）、`doc` 表统一存文档元数据。
- **LLM**：MiniMax-M3（深度）/ M3.1-Flash（轻量）/ qwen-long（RAG 长文），经 `ai/client.py` 统一 OpenAI 兼容接口；`ai/router.py` 按 task_type 选模。
- **数据源**：`adata`（免费、免 token，已验证行情/财务/行业）、`akshare`（宏观/新闻）、`tushare`（可选升级，需 token）。
- **前端**：原生 JS + ECharts；服务 FastAPI（`web/`）。
- **调度**：macOS launchd（`launchd/*.plist`）每日盘后任务。
- **防护**：git 基线 `mvp-v1` + `scripts/regression_check.py` 三层回归（数据/前端/API）。

---

## 6. 风险与开放问题

- **Tushare token**：财务三表明细可用 adata 免费拿；如需更全数据再申请 token（环境变量注入，不入库）。
- **公告抓取合规**：仅用交易所公开接口，控制频率，避免反爬封禁。
- **数据延迟**：行情依赖盘后 `etl.daily_update`；分析器应在数据缺口时优雅降级（提示"暂无财务数据"而非报错）。
- **大模型成本**：深度分析默认走 M3，轻量任务走 Flash；`estimate_cost` 已埋点。

---

## 7. 进度跟踪（Checklist）

| Phase | 关键交付 | 状态 |
|---|---|---|
| A 数据夯实 | 三表明细/宏观/公告/数据接入分析器/EasyMDE/文档 | 🟡 进行中（finance_core+industry 回填已启动） |
| B 分析深度 | industry/macro/comparative/DCF/风险接公告/盈利拆解 | ❌ |
| C 文本产业链 | 资讯去噪/研报OCR/产业链/对比问答 | ❌ |
| D 工作台 | 研究卡片/看板/规则库/复盘αβ/记忆 | ❌ |
| E Agent | 3 Agent + 编排 + 日报 | ❌ |
| F 扩展 | PWA/多模型/ Milvus | ❌ |

---

## 8. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-12 | v1.1 | 初版（基于旧 quant_db v1 的增量规划，**状态标记已过时**） |
| 2026-10-13 | MVP | MVP 打磨：数据对齐至 2026-10-12；图表配色统一；git 基线 `mvp-v1` + 回归脚本 |
| 2026-10-13 | v2 | **重写 SPEC**：纠正"v1.1 未开始"的误判；按用户 6 大愿景模块对齐真实现状；给出 A–F 六阶段推进计划 |
