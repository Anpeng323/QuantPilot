# Quant Workbench · 用户指南（v1.1）

> 个人 AI 量化投研平台 · v1.1（2026-10-12）

---

## 启动

```bash
cd /Users/quanlin/Desktop/项目/AI量化/quant_db
source .venv/bin/activate
bash scripts/run_web.sh           # mock 模式（无需 API key）
bash scripts/run_web.sh --live    # live 模式（读 ~/.quant_env）
```

浏览器访问：<http://localhost:8765>

---

## 界面布局（金融科技风格）

### 顶栏
- 左：Quant Workbench 品牌
- 右：LLM 状态徽章（🟢 LIVE / 🟡 MOCK）

### 侧栏导航（3 个区段共 12 项）
- **数据**：📈 全市场快照 / 🔍 个股详情 / 📊 指数看板 / 📐 技术指标 / 📝 SQL 查询
- **投研**：🤖 AI 分析 / 📚 RAG 知识库 / 💬 提示词库
- **持仓**：⭐ 自选池 / 💼 持仓 / 🔔 预警 / 📔 笔记

### 主区域
深色主题（Bloomberg Terminal / TradingView 美学）：
- 背景：`#0a0e1a`（深海军蓝）
- 强调色：`#00d4aa`（翠绿/teal）
- 上涨：`#00d68f`，下跌：`#ff3b5c`
- 数据卡 / 表格 / 图表统一风格

---

## 功能速查

### 📐 技术指标
支持 4 个核心指标：MA / MACD / BOLL / RSI

```
股票代码: 000001
指标: MA / MACD / BOLL / RSI
参数 JSON: {"period": 5}
窗口天数: 60（默认）
```

API：`POST /api/v1/indicators/{name}/compute`

### 💬 提示词模板库
内置 6 个模板（不可改）：financial_profit / risk / valuation / sentiment / frameworkrag / review
可新建自定义模板；变量支持 jinja2 语法。

### 🔔 预警规则
支持的 metric：pe / pb / roe / gross_margin / change_pct
支持的 operator：> / < / >= / <= / == / !=

### 💼 持仓监控
显示当前价 / 市值 / 浮动盈亏 / 收益率
一键同步最新价

### 📤 导出
- Markdown：分析报告 / 笔记 / 自选池
- PDF：基于 reportlab（中文支持）
- Excel：自选池 / 持仓 + 交易记录

### 🤖 AI 模型路由
| 任务类型 | 模型 |
|---|---|
| 财报 / 估值 / DCF / 行业 / 风险 | MiniMax-M3 |
| 分类 / 抽取 / 摘要 / 情绪 | MiniMax-M3.1-Flash-Preview |
| RAG 长文本问答 | qwen-long |

---

## CLI 工具

### 单元测试
```bash
source .venv/bin/activate
python -m pytest tests/                 # 全部
python -m pytest tests/indicators/      # 单个模块
python -m pytest tests/ -k "test_ma"    # 关键字匹配
python -m pytest tests/ --cov=.         # 覆盖率
```

### 数据库维护
```bash
python -c "from etl.db import init_schema; init_schema()"
python -m etl.backfill
python -m etl.daily_update
```

---

## 开发约定

### TDD 流程
1. 先写测试：`tests/<module>/test_*.py`
2. 跑测试（RED）
3. 写实现直到测试通过（GREEN）
4. 重构（REFACTOR）

### 代码组织
```
ai/           AI 模块
alerts/      预警引擎
exports/      导出（MD / PDF / Excel）
indicators/  技术指标
knowledge/   RAG
notes/       投研笔记
portfolio/   持仓 + 交易 + 复盘
prompts/     提示词模板
watchlist/   自选池
web/         FastAPI
etl/         数据 ETL
data_ingest/ 数据接入
tests/       测试套件
```

---

## 路线图

- ✅ v1.1（本次）：技术指标 + 提示词库 + 预警规则 + 持仓快照 + 导出 + 模型路由 + 金融科技 UI
- 🔄 v1.2：财务三表 + 公告抓取 + 行业/宏观/DCF analyzer
- 🔄 v1.3：交易规则库 + 复盘 α/β 归因
- ⏳ v2+：多 Agent 工作流
