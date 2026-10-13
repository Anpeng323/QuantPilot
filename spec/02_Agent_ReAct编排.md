# Phase 2 · 模块4 Agent ReAct 编排 Spec

> 文档状态：落地级（待开发）
> 关联：主文档 `SPEC.md`、Phase 1 回测引擎、Phase 3 NL 回测、Phase 4 投资建议
> 前置依赖：Phase 0 基线、模块1/2 已落地能力、`ai/analyzers/*`、Phase 1 回测引擎（部分工具依赖）

---

## 1. 目标与范围

### 1.1 目标
将已落地的「单轮 RAG 问答 + 分析器」升级为**多步 ReAct Agent 编排**：大模型自主决定调用哪些工具（查行情、读财报、跑回测、比公司、检索知识库），串联成研究闭环。RAG 负责文档检索，Agent 负责工具调用，二者职责严格分离。

### 1.2 In Scope
- 意图识别与 Agent 路由。
- 3 个 Agent：数据查询 Agent、财报解析 Agent、策略分析 Agent。
- 统一工具注册表与工具调用规则（白名单、参数 schema、超时/重试）。
- ReAct 循环（Thought→Action→Observation）与终止条件。
- 合规：工具输出均携带免责声明头。

### 1.3 Out of Scope
- 自然语言自动生成回测代码（见 Phase 3，但本 Phase 先把 `run_backtest` 作为可调工具接入）。
- 投资建议文案生成（见 Phase 4，但 `suggest_investment` 工具预留）。

---

## 2. 依赖与前置
- 已落地：`knowledge/qa.ask`（RAG 单轮）、`ai/analyzers`（analyze_stock/compare_stocks/financial_health/valuation/risk/score）。
- Phase 1 回测引擎（提供 `run_backtest` 工具）。

---

## 3. Agent 分工与触发逻辑

### 3.1 触发逻辑
1. 用户输入自然语言 → **意图识别**（规则+LLM 分类：行情查询 / 财报解读 / 策略回测 / 对比 / 综合研究）。
2. 路由到主 Agent；主 Agent 进入 ReAct 循环。
3. 每轮：LLM 输出 Thought（推理）与 Action（工具+参数）→ 执行工具 → Observation 回填 → 下一轮，直至 `Final Answer` 或达最大步数（默认 6）。

### 3.2 三个 Agent
- **数据查询 Agent**：工具 `get_quote`（行情/技术指标）、`get_financials`（三表/估值）、`compare_stocks`（多股对比）。
- **财报解析 Agent**：工具 `rag_retrieve`（RAG 检索财报/公告切片）、`summarize_filing`（长文档摘要，基于 RAG 上下文）。
- **策略分析 Agent**：工具 `run_backtest`（Phase 1）、`generate_backtest_from_nl`（Phase 3 接入）、`suggest_investment`（Phase 4 接入）。

### 3.3 工具注册表（白名单）
| 工具 | 作用 | 输入 schema | 来源 |
|---|---|---|---|
| get_quote | 行情/指标 | stock_code, indicator?, days | indicators/registry, db |
| get_financials | 财务三表/估值 | stock_code, report_type | db |
| compare_stocks | 多股对比 | codes[], metrics[] | ai/analyzers/compare |
| rag_retrieve | 知识库检索 | query, top_k, source_filter | knowledge/qa |
| run_backtest | 规则回测 | Phase1 输入 schema | backtest engine |
| suggest_investment | 投资建议 | stock_code, context | Phase4 |

### 3.4 工具调用规则
- **白名单**：仅允许注册表工具，禁止任意代码执行（NL 代码执行专属 Phase 3 沙箱）。
- **参数校验**：按 schema 校验；缺失必填返回工具错误并由 Agent 自我纠正（最多 2 次）。
- **超时/重试**：单工具超时 15s，失败重试 1 次；仍失败返回降级 Observation（如「数据暂不可用」）。
- **可观测**：每次工具调用记录（工具名、参数、耗时、结果摘要）于对话上下文，供前端展示「思考过程」。

---

## 4. 输入 / 输出
- 输入：`{ question, stock_code?, history? }`。
- 输出：`{ answer, steps: [{thought, action, observation}], sources: [{doc, chunk}], disclaimer: true }`。
- 前端展示：最终答案 + 可折叠「AI 思考步骤」+ 数据来源回链 + 强制免责声明。

---

## 5. 异常流程
- **意图无法识别**：降级为 RAG 单轮问答（沿用现有 `ask`），并提示「已按文档检索回答」。
- **工具全部失败**：返回「暂无法完成该研究，请稍后重试」，保留已成功步骤。
- **超步数未收敛**：截断并给出部分结论 + 提示「问题较复杂，建议拆分为子问题」。
- **大模型超时**：返回 503 + 重试提示，前端展示加载失败态。

---

## 6. 验收标准
- AC2.1：问「对比茅台和五粮液估值」，Agent 调用 `compare_stocks` 并输出含两股对比的结论 + 思考步骤可见。
- AC2.2：问「查某股最新财报亮点」，路由财报解析 Agent，调用 `rag_retrieve` 且答案含来源切片回链。
- AC2.3：构造未注册工具名（如 `os.system`），Agent 拒绝调用（白名单生效）。
- AC2.4：所有最终答案均携带 `disclaimer: true` 且前端渲染免责声明。
- AC2.5：工具超时（mock 20s）时 Agent 重试 1 次后仍失败，返回降级 Observation 而非崩溃。

---

## 7. 风险
- 工具误调用/循环：应对——最大步数 + 重复动作检测。
- 幻觉（工具结果被改写）：应对——Observation 原样回填，答案须引用 sources。
- 成本失控：应对——步数上限 + 工具结果截断（token 上限）。
