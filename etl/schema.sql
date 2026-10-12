-- ============================================================
-- A股量化数据库 schema  (DuckDB)
-- 所有表设计为「主键 + 增量 upsert」，便于每日跑批追加
-- ============================================================

-- 1. 股票基础信息 ----------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_basic (
    stock_code    VARCHAR(6)  PRIMARY KEY,   -- 6位股票代码（000001 / 600000 / 830xxx）
    short_name    VARCHAR(50),                -- 简称
    exchange      VARCHAR(2),                 -- SH / SZ / BJ
    list_date     DATE,                       -- 上市日期
    updated_at    TIMESTAMP DEFAULT current_timestamp
);

-- 2. 日 K 线（核心）-----------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_kline (
    stock_code      VARCHAR(6),
    trade_date      DATE,
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    pre_close       DOUBLE,                  -- 昨收
    change          DOUBLE,                  -- 涨跌额
    change_pct      DOUBLE,                  -- 涨跌幅 %
    volume          BIGINT,                  -- 成交量（股）
    amount          DOUBLE,                  -- 成交额（元）
    turnover_ratio  DOUBLE,                  -- 换手率 %
    adjust_type     INT      DEFAULT 1,      -- 1=前复权 0=不复权 2=后复权
    updated_at      TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (stock_code, trade_date, adjust_type)
);

-- 月 K、周 K 复用同一张表（k_type 区分）；也支持 5/15/30/60min 分钟 K
-- trade_time 字段对日线为空，对分钟线必填
ALTER TABLE daily_kline ADD COLUMN IF NOT EXISTS trade_time TIMESTAMP;
ALTER TABLE daily_kline ADD COLUMN IF NOT EXISTS k_type      INT DEFAULT 1;  -- 1日 2周 3月 4季 5=5min ...

-- 3. 交易日历 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_calendar (
    trade_date     DATE PRIMARY KEY,
    trade_status   INT,    -- 1=交易日 0=非交易日
    day_week       INT,    -- 一周第几天（周日=0）
    source         VARCHAR(20) DEFAULT 'szse'
);

-- 4. 概念板块 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS concept (
    concept_code   VARCHAR(20),
    source         VARCHAR(20),       -- east/ths/baidu
    concept_name   VARCHAR(100),
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (concept_code, source)
);

-- 5. 股票 → 概念 映射 -------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_concept (
    stock_code     VARCHAR(6),
    concept_code   VARCHAR(20),
    source         VARCHAR(20),
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (stock_code, concept_code, source)
);

-- 6. 申万行业 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_industry (
    stock_code      VARCHAR(6),
    sw_code         VARCHAR(10),
    industry_name   VARCHAR(50),
    industry_type   VARCHAR(20),     -- 一级 / 二级
    source          VARCHAR(20) DEFAULT 'baidu',
    PRIMARY KEY (stock_code, sw_code, source)
);

-- 7. 财务核心指标 ------------------------------------------------------
CREATE TABLE IF NOT EXISTS finance_core (
    stock_code       VARCHAR(6),
    report_date      DATE,           -- 报告期 (季度末)
    announce_date    DATE,           -- 实际披露日（防未来函数的关键）
    eps              DOUBLE,
    roe              DOUBLE,         -- 净资产收益率 %
    gross_margin     DOUBLE,         -- 毛利率 %
    revenue          DOUBLE,         -- 营业收入（元）
    net_profit       DOUBLE,         -- 净利润（元）
    yoy_revenue      DOUBLE,         -- 营收同比 %
    yoy_profit       DOUBLE,         -- 净利润同比 %
    pe               DOUBLE,
    pb               DOUBLE,
    ps               DOUBLE,
    total_market_cap DOUBLE,
    updated_at       TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (stock_code, report_date)
);

-- 8. 个股资金流向 ------------------------------------------------------
CREATE TABLE IF NOT EXISTS capital_flow (
    stock_code      VARCHAR(6),
    trade_date      DATE,
    main_net_inflow DOUBLE,         -- 主力净流入（元）
    huge_net_inflow DOUBLE,         -- 大单
    big_net_inflow  DOUBLE,         -- 中单
    mid_net_inflow  DOUBLE,
    small_net_inflow DOUBLE,        -- 小单
    super_large_inflow DOUBLE,         -- 特大单
    PRIMARY KEY (stock_code, trade_date)
);

-- 9. 北向资金 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS north_flow (
    trade_date           DATE PRIMARY KEY,
    sh_hk_net_inflow     DOUBLE,    -- 沪股通净流入
    sz_hk_net_inflow     DOUBLE,    -- 深股通净流入
    total_net_inflow     DOUBLE     -- 合计
);

-- 10. 两融余额 --------------------------------------------------------
CREATE TABLE IF NOT EXISTS margin_balance (
    trade_date    DATE,
    stock_code    VARCHAR(6),       -- 留空表示全市场
    rz_balance    DOUBLE,              -- 融资余额
    rq_balance    DOUBLE,              -- 融券余额
    rz_ye         DOUBLE,              -- 融资余额
    PRIMARY KEY (trade_date, stock_code)
);

-- 11. 龙虎榜 ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS dragon_tiger (
    trade_date     DATE,
    stock_code     VARCHAR(6),
    reason         VARCHAR(200),       -- 上榜原因
    buyer_name     VARCHAR(100),       -- 营业部名称（买/卖各 top5）
    side           VARCHAR(10),        -- buy / sell
    net_buy        DOUBLE,             -- 净额
    buy_amount     DOUBLE,
    sell_amount    DOUBLE,
    PRIMARY KEY (trade_date, stock_code, buyer_name, side)
);

-- 12. 指数日 K -------------------------------------------------------
CREATE TABLE IF NOT EXISTS index_kline (
    index_code     VARCHAR(10),       -- sh.000001 / sz.399006
    trade_date     DATE,
    open           DOUBLE,
    high           DOUBLE,
    low            DOUBLE,
    close          DOUBLE,
    volume         BIGINT,
    amount         DOUBLE,
    change_pct     DOUBLE,
    PRIMARY KEY (index_code, trade_date)
);

-- 12.1 指数分钟 K（5min）------------------------------------------------
CREATE TABLE IF NOT EXISTS index_minute_kline (
    index_code     VARCHAR(10),
    trade_time     TIMESTAMP,    -- 含日期 + 时间（精确到分）
    trade_date     DATE,         -- 仅日期，方便按日查
    k_type         INT,          -- 5/15/30/60
    open           DOUBLE,
    high           DOUBLE,
    low            DOUBLE,
    close          DOUBLE,
    volume         BIGINT,
    amount         DOUBLE,
    updated_at     TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (index_code, trade_time, k_type)
);
CREATE INDEX IF NOT EXISTS idx_idx_minute_date       ON index_minute_kline(trade_date);
CREATE INDEX IF NOT EXISTS idx_idx_minute_code_date  ON index_minute_kline(index_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_idx_minute_k_type     ON index_minute_kline(k_type);

-- 13. 每日更新日志 ----------------------------------------------------
CREATE TABLE IF NOT EXISTS update_log (
    id            BIGINT,
    run_time      TIMESTAMP DEFAULT current_timestamp,
    job_name      VARCHAR(50),         -- backfill / daily_update
    table_name    VARCHAR(50),
    rows_added    INT,
    rows_updated  INT,
    status        VARCHAR(20),         -- success / failed
    message       TEXT,
    duration_sec  DOUBLE,
    PRIMARY KEY (run_time, job_name, table_name, id)
);

-- 索引（加速常用查询） --------------------------------------------
CREATE INDEX IF NOT EXISTS idx_kline_date       ON daily_kline(trade_date);
CREATE INDEX IF NOT EXISTS idx_kline_code_date  ON daily_kline(stock_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_capital_date    ON capital_flow(trade_date);
CREATE INDEX IF NOT EXISTS idx_concept_stock   ON stock_concept(stock_code);

-- update_log 自增序列
CREATE SEQUENCE IF NOT EXISTS seq_log START 1;

-- 14. 分钟 K 线（5min/15min/30min/60min）--------------------------------
CREATE TABLE IF NOT EXISTS minute_kline (
    stock_code      VARCHAR(6),
    trade_time      TIMESTAMP,    -- 含日期 + 时间（精确到分）
    trade_date      DATE,         -- 仅日期，方便按日查
    k_type          INT,          -- 5/15/30/60
    open            DOUBLE,
    high            DOUBLE,
    low             DOUBLE,
    close           DOUBLE,
    volume          BIGINT,
    amount          DOUBLE,
    updated_at      TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (stock_code, trade_time, k_type)
);
CREATE INDEX IF NOT EXISTS idx_minute_date        ON minute_kline(trade_date);
CREATE INDEX IF NOT EXISTS idx_minute_code_date   ON minute_kline(stock_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_minute_k_type      ON minute_kline(k_type);

-- ============== AI 投研平台 v1 ==============

-- 文档元数据
CREATE TABLE IF NOT EXISTS doc (
    doc_id          VARCHAR PRIMARY KEY,
    doc_type        VARCHAR,
    source          VARCHAR(32),
    stock_code      VARCHAR(6),
    industry_code   VARCHAR(10),
    title           VARCHAR(256),
    publish_date    DATE,
    file_path       VARCHAR(512),
    url             VARCHAR(512),
    chunk_count     INT DEFAULT 0,
    ingested_at     TIMESTAMP DEFAULT current_timestamp,
    metadata        VARCHAR
);

-- 自选池
CREATE TABLE IF NOT EXISTS watchlist_group (
    group_name      VARCHAR(32) PRIMARY KEY,
    description     VARCHAR(256),
    sort_order      INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watchlist (
    group_name      VARCHAR(32),
    stock_code      VARCHAR(6),
    added_at        TIMESTAMP DEFAULT current_timestamp,
    note            VARCHAR(256),
    PRIMARY KEY (group_name, stock_code)
);

-- 笔记
CREATE TABLE IF NOT EXISTS note (
    note_id         VARCHAR PRIMARY KEY,
    stock_code      VARCHAR(6),
    title           VARCHAR(256),
    content_md      VARCHAR,
    tags            VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    updated_at      TIMESTAMP DEFAULT current_timestamp,
    synced_to_vector BOOLEAN DEFAULT false,
    embedding_id    VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_note_stock ON note(stock_code);

-- AI 分析历史
CREATE TABLE IF NOT EXISTS ai_analysis (
    analysis_id     VARCHAR PRIMARY KEY,
    stock_code      VARCHAR(6),
    analysis_type   VARCHAR(32),
    prompt          VARCHAR,
    response        VARCHAR,
    citations       VARCHAR,
    model_used      VARCHAR(32),
    tokens_used     INT,
    cost_cny        DOUBLE,
    duration_ms     INT,
    created_at      TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_ai_stock ON ai_analysis(stock_code);

-- 持仓
CREATE TABLE IF NOT EXISTS position (
    position_id     VARCHAR PRIMARY KEY,
    stock_code      VARCHAR(6),
    cost_price      DOUBLE,
    quantity        INT,
    cost_total      DOUBLE,
    open_date       DATE,
    note            VARCHAR,
    status          VARCHAR(16) DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_position_stock ON position(stock_code);

-- 交易记录
CREATE TABLE IF NOT EXISTS trade (
    trade_id        VARCHAR PRIMARY KEY,
    stock_code      VARCHAR(6),
    direction       VARCHAR(4),
    price           DOUBLE,
    quantity        INT,
    amount          DOUBLE,
    trade_date      TIMESTAMP,
    logic_text      VARCHAR,
    note_id         VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_trade_stock ON trade(stock_code);

-- 复盘
CREATE TABLE IF NOT EXISTS trade_review (
    review_id       VARCHAR PRIMARY KEY,
    period          VARCHAR(32),
    ai_summary      VARCHAR,
    win_rate        DOUBLE,
    avg_return      DOUBLE,
    alpha           DOUBLE,
    beta            DOUBLE,
    total_return    DOUBLE,
    trade_count     INT,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

-- 数据接入任务日志
CREATE TABLE IF NOT EXISTS ingest_log (
    job_id          VARCHAR PRIMARY KEY,
    job_type        VARCHAR(32),
    status          VARCHAR(16),
    rows_added      INT,
    message         VARCHAR,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP
);

-- ========== v1.1 增量：提示词模板 ==========
CREATE TABLE IF NOT EXISTS prompt_template (
    template_id VARCHAR PRIMARY KEY,
    name        VARCHAR(64),
    category    VARCHAR(32),
    body        VARCHAR,
    variables   VARCHAR,             -- JSON 数组字符串，如 ["stock_code","context"]
    is_builtin  BOOLEAN DEFAULT false,
    created_at  TIMESTAMP DEFAULT current_timestamp,
    updated_at  TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_prompt_category ON prompt_template(category);

-- ========== v1.1 增量：预警规则 / 事件 ==========
CREATE TABLE IF NOT EXISTS alert_rule (
    rule_id      VARCHAR PRIMARY KEY,
    name         VARCHAR(64),
    target_type  VARCHAR(16),
    target_value VARCHAR(64),
    metric       VARCHAR(32),
    operator     VARCHAR(4),
    threshold    DOUBLE,
    channel      VARCHAR(16) DEFAULT 'web',
    is_active    BOOLEAN DEFAULT true,
    created_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS alert_event (
    event_id     VARCHAR PRIMARY KEY,
    rule_id      VARCHAR,
    stock_code   VARCHAR(6),
    metric       VARCHAR(32),
    value        DOUBLE,
    triggered_at TIMESTAMP DEFAULT current_timestamp,
    message      VARCHAR,
    sent_status  VARCHAR(16) DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_alert_event_rule ON alert_event(rule_id);
CREATE INDEX IF NOT EXISTS idx_alert_event_time ON alert_event(triggered_at DESC);

-- ========== v1.1 增量：财务三表明细（长表 EAV） ==========
-- 东方财富口径（akshare 免费），按报告期存储投研常用科目，兼容银行/制造业差异
CREATE TABLE IF NOT EXISTS finance_statement (
    stock_code     VARCHAR(6),
    statement_type VARCHAR(8),    -- balance / profit / cashflow
    report_date    DATE,
    item_key       VARCHAR(40),   -- 规范化科目键（英文）
    item_name      VARCHAR(60),   -- 中文名（可读）
    value          DOUBLE,
    PRIMARY KEY (stock_code, statement_type, report_date, item_key)
);
CREATE INDEX IF NOT EXISTS idx_fin_stmt_stock ON finance_statement(stock_code, statement_type);

-- ========== v1.1 增量：宏观指标 ==========
CREATE TABLE IF NOT EXISTS macro_indicator (
    indicator  VARCHAR(30),   -- cpi / pmi / lpr_1y / lpr_5y / m2 ...
    trade_date DATE,
    value      DOUBLE,
    unit       VARCHAR(10),
    PRIMARY KEY (indicator, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_macro_ind ON macro_indicator(indicator);
