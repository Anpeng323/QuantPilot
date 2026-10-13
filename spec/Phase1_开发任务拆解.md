# Phase 1 · 规则回测引擎 — 开发任务拆解

> 配套：`spec/01_规则回测引擎.md`（需求规格）
> 代码基线：`v1.3-indicator-chart`；数据库：DuckDB（`etl/schema.sql`）
> 目标：单股信号驱动的规则回测，T+1 撮合、A股真实成本、可审计绩效。

---

## 0. 现状盘点（避免重复造轮子）

| 已有资产 | 路径 | 复用方式 |
|---|---|---|
| 指标计算 | `indicators/registry.py` + `compute(df)` | **直接复用**：`get_indicator('ma', period=5).compute(df)` 产出 `ma5` 列；MACD 产出 `dif/dea/hist` |
| 日线数据 | `daily_kline`（`k_type=1` 日、`adjust_type=1` 前复权），列含 `open/high/low/close/volume/amount` | **数据源** |
| 基准指数 | `index_kline`（`index_code`, `close`） | 基准收益来源（沪深300 代码格式待确认，见 §6） |
| 只读查询 | `web/db.py` 的 `query_dict()` / `safe_sql_check()` | API 层 SQL 风格参考 |
| 截面动量样例 | `analysis/quick_backtest.py` | **不复用核心**（无成本、无 DSL、非单股信号），仅参考 metric 公式 |

**缺失（本 Phase 新建）**：信号 DSL、T+1 撮合、成本模型、绩效指标、存储、REST API、前端 UI。

---

## 1. 任务清单（按依赖排序）

### T1 · 信号 DSL 解析器  `backtest/signal.py`  ⏱ 1.5d
- 语法：`{indicator}[ params] {op} {value}` 用 `AND`/`OR` 连接，最多 4 个原子。
  - 例：`ma5 > ma20 AND macd_hist > 0`、`rsi14 < 30`
- 解析：分词 → 抽象为 `[(indicator, params, op, value), logic]` → 对指标计算后的 DataFrame 逐行求值 → 返回布尔 `signal: pd.Series`。
- 指标名/参数映射到 `indicators/registry`（含 `macd.hist` 特殊列解析）。
- 算子白名单：`> < >= <= == !=`。
- **验收**：`ma5 > ma20` 在已知 df 上求值结果与手算一致；非法指标/算子抛 `SignalParseError` 并列出可用项。

### T2 · 成本模型  `backtest/cost.py`  ⏱ 0.5d
- `compute_cost(side, price, quantity)` → 费用明细。
- 印花税 0.05%（仅卖出）、佣金万2.5（单笔最低 5 元）、过户费 0.001%（双边）、可选滑点。
- **验收**：10 万元卖出 → 印花税 50 + 佣金 25 + 过户费 1 = 76 元（与手算一致）。

### T3 · 绩效指标  `backtest/metrics.py`  ⏱ 1d
- 输入：权益曲线序列、基准序列、无风险利率（默认 0.025）。
- 输出：`total_return / annual_return / max_drawdown / sharpe / win_rate / excess_return`。
- 复用 `analysis/quick_backtest.py` 的 Sharpe/回撤公式口径并在注释固化。
- **验收**：给定手工权益曲线，各指标与离线 Excel 计算误差 < 1e-6。

### T4 · 基准加载  `backtest/benchmark.py`  ⏱ 0.5d
- `load_benchmark(index_code, start, end)` → 与回测区间对齐的日收益序列（基于 `index_kline.close`）。
- 与个股交易日对齐（缺失交易日 forward-fill）。

### T5 · 回测引擎核心  `backtest/engine.py`  ⏱ 2.5d  【关键】
- 流程：`load_kline` → `indicators.compute`（叠加信号列）→ `signal.evaluate`（得 T 日信号）→ **T+1 撮合** → `cost.apply` → 生成 `trades` + `equity_curve` → `metrics.compute`。
- **防未来函数硬规则**：信号在 T 日收盘计算，成交价取 T+1 的 `fill_price`（open/close 可选）；禁止在 T 日使用 T+1 及之后数据。
- 持仓：整百股；现金不足按 max(可用资金/价, 100股整数倍) 买入；信号转空或到期平仓。
- 缺失数据：区间缺失率 > 20% 中止并提示换区间；否则跳过该日并打 `data_flag`。
- 异常：计算溢出捕获 → 返回结构化错误（不崩溃）。
- **验收**：AC1.1 前三笔交易与手算一致；AC1.2 单测证明成交用 T+1 价（作弊策略被拒）。

### T6 · 数据表 schema  `etl/schema.sql` 增量  ⏱ 0.5d
- 新增 `backtest_task` / `backtest_result` / `strategy_record`（详见 §3）。
- 在 `scripts/regression_check.py` 的「数据层」增加对三表的 existence 断言。

### T7 · 持久化  `backtest/repo.py`  ⏱ 1d
- `save_task(req, result)`、`list_tasks(limit)`、`get_task(id)`。
- 结果 JSON 序列化存入 `backtest_result.payload`（DuckDB 用 VARCHAR 存 JSON）。

### T8 · REST API  `web/routes_backtest.py` + `app.py` 注册  ⏱ 1d
- 端点见 §2。沿用 `web/db.py` 连接风格（读写连接、不做缓存）。
- 输入校验：日期区间、initial_cash>0、signal 合法性（先调 `signal.validate`）。

### T9 · 前端 UI  `web/static/app.js` + 页面  ⏱ 2d
- 回测表单：股票代码、日期区间（日历）、指标信号可视化拼接器（下拉选指标+算子+阈值自动拼 DSL）、成交价选择、初始资金。
- 结果页：权益曲线（叠加基准，沿用 v1.3 配色：基准灰、策略黄）、K线+买卖点标记（买绿/卖红）、交易明细表（分页 20 条/页，可导出 CSV）。
- 加载态骨架屏；空状态：「该策略在区间内未触发交易」。

### T10 · 回归门禁接入  `scripts/regression_check.py`  ⏱ 0.5d
- 新增「回测层」：对固定 (股票, 信号, 区间) 跑 `engine.run`，断言 trades 非空、metrics 字段齐全、与基线快照偏差在容忍内。
- 打标签 `v1.4-backtest`。

### T11 · 测试用例  `tests/backtest/*`  ⏱ 1.5d
- 详见 §4。

**关键路径**：T1→T2→T3→T4→T5（核心）→T6→T7→T8→T9→T10→T11。并行空间：T2/T3/T4 可与 T1 并行；T6 可与 T5 并行。

---

## 2. 接口契约（JSON）

### 2.1 `POST /api/v1/backtest/run`
请求：
```json
{
  "stock_code": "000001",
  "start": "20230101",
  "end": "20231231",
  "initial_cash": 100000,
  "signal": "ma5 > ma20 AND macd_hist > 0",
  "fill_price": "close",          // "open" | "close"
  "cost": {
    "commission": 0.00025,
    "min_commission": 5,
    "stamp": 0.0005,
    "transfer": 0.00001,
    "slippage": 0
  },
  "benchmark": "000300.SH",       // 可选，默认沪深300
  "save": true                    // 是否落库 backtest_task
}
```
响应（200）：
```json
{
  "task_id": "bt_xxx",
  "trades": [
    {"date":"2023-02-01","side":"buy","price":13.5,"quantity":700,"amount":9450,"fee":9.7}
  ],
  "equity_curve": [
    {"date":"2023-01-03","value":100000,"benchmark_value":100000}
  ],
  "metrics": {
    "total_return": 0.123,
    "annual_return": 0.118,
    "max_drawdown": -0.085,
    "sharpe": 1.32,
    "win_rate": 0.58,
    "excess_return": 0.041
  },
  "data_flag": null
}
```
错误（400）：
```json
{ "error": "回测区间无效，请检查起止日期", "field": "start" }
```
错误（400）：
```json
{ "error": "未知指标: xxx。可用: ma,macd,boll,rsi,kdj,arbr,adx,stochrsi", "field": "signal" }
```

### 2.2 `GET /api/v1/backtest/tasks?limit=20`
返回 `[{task_id, stock_code, signal, start, end, total_return, created_at}]`（按 created_at 倒序）。

### 2.3 `GET /api/v1/backtest/tasks/{task_id}`
返回单次完整结果（同 2.1 的 trades/equity_curve/metrics）。

### 2.4 `POST /api/v1/backtest/signal/validate`
请求 `{"signal":"ma5 > ma20"}` → `{ "valid": true }` 或 `{ "valid": false, "error": "..." }`。

---

## 3. 表结构（DuckDB DDL，追加到 `etl/schema.sql`）

```sql
-- ========== v1.4 增量：规则回测 ==========
CREATE TABLE IF NOT EXISTS backtest_task (
    task_id       VARCHAR PRIMARY KEY,
    stock_code    VARCHAR(6),
    start_date    DATE,
    end_date      DATE,
    signal        VARCHAR(256),
    initial_cash  DOUBLE,
    fill_price    VARCHAR(8),          -- open / close
    benchmark     VARCHAR(16),
    created_at    TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_bt_stock ON backtest_task(stock_code);

-- 单次结果（payload 存完整 JSON，避免宽表膨胀）
CREATE TABLE IF NOT EXISTS backtest_result (
    task_id       VARCHAR PRIMARY KEY,
    total_return  DOUBLE,
    annual_return DOUBLE,
    max_drawdown  DOUBLE,
    sharpe        DOUBLE,
    win_rate      DOUBLE,
    excess_return DOUBLE,
    trade_count   INT,
    data_flag     VARCHAR(64),
    payload       VARCHAR,             -- JSON: trades[] + equity_curve[]
    created_at    TIMESTAMP DEFAULT current_timestamp
);

-- 策略记录（Phase 3 NL 回测也写入，含代码哈希去重）
CREATE TABLE IF NOT EXISTS strategy_record (
    strategy_id   VARCHAR PRIMARY KEY,
    name          VARCHAR(128),
    source        VARCHAR(16),         -- rule / nl
    definition    VARCHAR,             -- DSL 或生成代码
    code_hash     VARCHAR(64),         -- 去重键
    metrics_json  VARCHAR,             -- 最近一次绩效快照
    created_at    TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_strategy_hash ON strategy_record(code_hash);
```

---

## 4. 测试用例清单

### 单元测试 `tests/backtest/`
- `test_signal.py`
  - `test_parse_ma_cross`：`ma5 > ma20` 在样例 df 求值正确。
  - `test_parse_macd_hist`：`macd_hist > 0` 用 `hist` 列。
  - `test_invalid_indicator`：未知指标抛 `SignalParseError` 且消息含可用列表。
  - `test_combine_and_or`：`A AND B OR C` 优先级求值。
- `test_cost.py`
  - `test_sell_100k`：印花税50+佣金25+过户费1=76。
  - `test_min_commission`：小额成交佣金取最低 5 元。
  - `test_buy_no_stamp`：买入不计印花税。
- `test_metrics.py`
  - `test_known_curve`：手工权益曲线各指标与 Excel 一致（误差<1e-6）。
  - `test_flat_curve`：无波动时 sharpe=0、max_drawdown=0。
- `test_engine.py`  【防未来函数核心】
  - `test_t_plus_1_fill`：构造「T 日收盘买入」作弊策略，断言成交价为 T+1 价（非 T 日），证明无未来函数。
  - `test_hand_calc_match`：已知 (股票, 信号, 区间) 前三笔交易与手算一致（AC1.1）。
  - `test_lot_size`：持仓为整百股，现金不足时向下取整到百股。
  - `test_missing_data_abort`：区间缺失>20% 中止并返回提示。
  - `test_no_trigger`：信号全程不成立 → trades 为空、data_flag 提示。

### 接口测试 `tests/backtest/test_api.py`
- `test_run_success`：合法请求 200 且 metrics 字段齐全（AC1.5）。
- `test_invalid_date`：起>止 → 400。
- `test_invalid_cash`：initial_cash<=0 → 400。
- `test_unknown_indicator`：signal 含未知指标 → 400 且消息含可用列表（AC1.4）。
- `test_validate_endpoint`：合法/非法 signal 经 `/signal/validate` 返回正确 valid。

### 回归门禁（scripts/regression_check.py 回测层）
- 固定 (000001, `ma5 > ma20`, 2023 全年) 跑 `engine.run`，断言 trades 非空、metrics 与基线快照偏差 < 阈值。

---

## 5. 验收标准映射（对应 `01_规则回测引擎.md`）

| AC | 验证方式 |
|---|---|
| AC1.1 交易手算一致 | `test_engine.test_hand_calc_match` |
| AC1.2 防未来函数 | `test_engine.test_t_plus_1_fill`（单测证明 T+1 成交） |
| AC1.3 成本模型正确 | `test_cost.test_sell_100k` |
| AC1.4 非法参数结构化错误 | `test_api.test_invalid_date/cash/unknown_indicator` |
| AC1.5 权益曲线+基准+全字段 | `test_api.test_run_success` + 回归门禁 |

---

## 6. 开放问题
- **基准代码格式**：`index_kline.index_code` 实际写入值（如 `000300.SH` 或 `sh.000300`）需与 `etl` 写入对齐，T4 前确认。
- **复权选择**：默认 `adjust_type=1`（前复权）回测；是否需支持后复权对比？暂定仅前复权。
- **做空/融券**：v3.1 不做杠杆/做空，信号转空仅平仓不反向，T5 据此实现。
- **多股票组合回测**：本期仅单股；截面/组合回测留待后续（现有 quick_backtest 可作参考重构）。
