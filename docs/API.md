# Quant Workbench · API 参考（v1.1）

> Base URL：`http://localhost:8765/api/v1`

---

## LLM

### GET `/llm/info`
当前 LLM provider 信息 + embedding 状态。

```json
{
    "llm": {
        "provider": "minimax",
        "live": true,
        "minimax_model": "MiniMax-M3"
    },
    "embedding": { "model": "BAAI/bge-small-zh-v1.5" }
}
```

---

## 技术指标

### GET `/indicators`
列出所有可用指标。

```bash
curl http://localhost:8765/api/v1/indicators
# ["ma", "macd", "boll", "rsi"]
```

### POST `/indicators/{name}/compute`
计算指标。

```bash
curl -X POST http://localhost:8765/api/v1/indicators/ma/compute \
  -H "Content-Type: application/json" \
  -d '{
        "stock_code": "000001",
        "params": {"period": 5},
        "days": 60
      }'
```

返回：
```json
{
    "name": "ma",
    "params": {"period": 5},
    "rows": [
        {"trade_date": "2024-01-01T00:00:00", "open": 10.0, "high": 10.2, ...},
        ...
    ]
}
```

---

## 提示词模板

### GET `/prompts`
列出所有模板（自动 seed 内置模板）。

### POST `/prompts`
新建用户模板。

### GET `/prompts/{tid}`
查询单个模板。

### PUT `/prompts/{tid}`
更新用户模板（内置模板不可改）。

### DELETE `/prompts/{tid}`
删除用户模板（内置模板不可删）。

---

## 预警规则

### GET `/alerts/rules`
列出所有规则。

### POST `/alerts/rules`
创建规则。

```bash
curl -X POST http://localhost:8765/api/v1/alerts/rules \
  -H "Content-Type: application/json" \
  -d '{
        "name": "PE>50",
        "target_type": "stock",
        "target_value": "000001",
        "metric": "pe",
        "operator": ">",
        "threshold": 50.0
      }'
```

### DELETE `/alerts/rules/{rid}`
删除规则。

### POST `/alerts/run`
立即评估所有 active 规则，返回触发的 AlertEvent 列表。

### GET `/alerts/events`
历史触发事件列表。

---

## 持仓

### GET `/portfolio/snapshot`
获取持仓快照（每条 + 汇总）。

```json
{
    "rows": [
        {
            "position_id": "p1",
            "stock_code": "000001",
            "short_name": "平安银行",
            "cost_price": 10.5,
            "quantity": 100,
            "cost_total": 1050.0,
            "current_price": 12.0,
            "market_value": 1200.0,
            "floating_pnl": 150.0,
            "return_pct": 0.143,
            "open_date": "2024-01-01",
            "status": "open"
        }
    ],
    "total": {
        "position_count": 1,
        "valued_count": 1,
        "cost_total": 1050.0,
        "market_value": 1200.0,
        "floating_pnl": 150.0,
        "return_pct": 0.143
    }
}
```

### POST `/portfolio/sync-prices`
同步最新价（调用 provider，默认 adata）。

```bash
curl -X POST http://localhost:8765/api/v1/portfolio/sync-prices \
  -H "Content-Type: application/json" \
  -d '["000001", "000002"]'
```

---

## 导出

### GET `/exports/analysis/{aid}/md`
获取 Markdown 文本。

```json
{"content": "# AI 分析报告 - 000001...", "analysis_id": "a1"}
```

### GET `/exports/analysis/{aid}/pdf`
生成 PDF 文件（写入 `data/exports/`）。

```json
{"path": "/.../analysis_a1.pdf", "analysis_id": "a1"}
```

### GET `/exports/watchlist/{group}/excel`
生成 Excel 文件。

```json
{"path": "/.../watchlist_持仓池.xlsx", "group": "持仓池"}
```

---

## 模型路由

### GET `/router/models`
查看当前路由配置。

```json
{
    "config": {
        "financial_summary": "MiniMax-M3",
        "valuation": "MiniMax-M3",
        "sentiment": "MiniMax-M3.1-Flash-Preview",
        "rag_qa": "qwen-long",
        ...
    },
    "current": { ... }
}
```

### POST `/router/config`
覆盖路由（部分或全部）。

---

## 数据库表

| 表 | 用途 |
|---|---|
| `prompt_template` | 提示词模板（v1.1 新增） |
| `alert_rule` | 预警规则（v1.1 新增） |
| `alert_event` | 预警触发事件（v1.1 新增） |
| `latest_price` | 最新价缓存（v1.1 新增） |
| `ai_analysis` | AI 分析历史 |
| `doc` | RAG 文档元数据 |
| `note` | 投研笔记 |
| `watchlist` / `watchlist_group` | 自选池 |
| `position` | 持仓 |
| `trade` | 交易记录 |
| `trade_review` | 交易复盘 |
| `daily_kline` / `index_kline` / `minute_kline` | K 线 |
| `finance_core` | 财务核心指标 |
| `stock_basic` / `concept` / `stock_industry` | 标的基础信息 |

---

## 错误码

| HTTP | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 403 | 权限拒绝（如删除内置模板） |
| 404 | 资源不存在 |
| 422 | 请求体 Pydantic 校验失败 |
| 500 | 服务器内部错误 |
