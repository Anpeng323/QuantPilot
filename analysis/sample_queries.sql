-- ============================================================
-- 常用分析 SQL 模板（基于 DuckDB）
-- 用法： source scripts/run_query.sh < this_file
--       或在 jupyter 里 %sql < this
-- ============================================================

-- 1. 看最新一天的全市场快照（昨日收盘价 + 涨跌幅）
.headers on
.mode markdown

SELECT
    a.stock_code,
    b.short_name,
    b.exchange,
    a.trade_date,
    a.open,
    a.high,
    a.low,
    a.close,
    a.change_pct,
    ROUND(a.volume / 1e8, 2) AS volume_yi,
    ROUND(a.amount / 1e8, 2) AS amount_yi
FROM daily_kline a
JOIN stock_basic b USING (stock_code)
WHERE a.k_type = 1
  AND a.trade_date = (SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1)
ORDER BY a.change_pct DESC
LIMIT 20;


-- 2. 找近 20 日涨幅最大的 50 只（剔除 ST/新股）
SELECT
    a.stock_code,
    b.short_name,
    ROUND((a.close / NULLIF(a_20.close, 0) - 1) * 100, 2) AS pct_20d
FROM daily_kline a
JOIN stock_basic b USING (stock_code)
JOIN daily_kline a_20
  ON a.stock_code = a_20.stock_code
 AND a_20.k_type = 1
 AND a_20.trade_date = (SELECT MIN(trade_date) FROM daily_kline
                         WHERE stock_code=a.stock_code AND k_type=1
                           AND trade_date >= CURRENT_DATE - INTERVAL '30 days')
WHERE a.k_type = 1
  AND a.trade_date = (SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1)
  AND a.volume > 0
  AND b.short_name NOT LIKE '*ST%'
  AND b.short_name NOT LIKE 'ST%'
ORDER BY pct_20d DESC
LIMIT 50;


-- 3. 计算每只票的 20 日均线偏离度（昨天 vs MA20）
SELECT
    a.stock_code,
    b.short_name,
    a.close AS close_today,
    ROUND(AVG(c.close), 2) AS ma20,
    ROUND((a.close / AVG(c.close) - 1) * 100, 2) AS bias_pct
FROM daily_kline a
JOIN stock_basic b USING (stock_code)
JOIN daily_kline c
  ON c.stock_code = a.stock_code
 AND c.k_type = 1
 AND c.trade_date BETWEEN a.trade_date - INTERVAL '30 days' AND a.trade_date
WHERE a.k_type = 1
  AND a.trade_date = (SELECT MAX(trade_date) FROM daily_kline WHERE k_type=1)
GROUP BY a.stock_code, b.short_name, a.close, a.trade_date
HAVING AVG(c.close) > 0
ORDER BY bias_pct ASC
LIMIT 30;


-- 4. 数据健康度检查
SELECT
    'stock_basic' AS tbl, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks FROM stock_basic
UNION ALL
SELECT 'daily_kline', COUNT(*), COUNT(DISTINCT stock_code) FROM daily_kline WHERE k_type=1
UNION ALL
SELECT 'trade_calendar', COUNT(*), NULL FROM trade_calendar
UNION ALL
SELECT 'index_kline', COUNT(*), COUNT(DISTINCT index_code) FROM index_kline
UNION ALL
SELECT 'update_log', COUNT(*), NULL FROM update_log;


-- 5. 最近 5 次更新日志
SELECT
    run_time,
    job_name,
    table_name,
    status,
    duration_sec,
    message
ORDER BY start_time DESC
LIMIT 20;


-- 6. 上证指数最新行情
SELECT * FROM index_kline
WHERE index_code = 'sh000001'
ORDER BY trade_date DESC LIMIT 5;
