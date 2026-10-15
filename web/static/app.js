/* ============================================================
   Quant Workbench · 前端控制
   ============================================================ */

// 全局 state
const state = {
    panel: 'snapshot',
    stockCode: null,
    watchlistGroup: null,
    noteId: null,
    aiAnalysisId: null,
    indicatorChart: null,
    stockChart: null,
    indexChart: null,
    currentIndexCode: null,
    // 智能问答 Agent 多轮对话
    agentHistory: [],      // [{role:'user'|'assistant', content}]
    agentThinking: false,
    agentInited: false,
};

// 简易 API 调用
const api = {
    async get(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return r.json();
    },
    async post(url, body) {
        const r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const txt = await r.text();
            throw new Error(`${url}: ${r.status} ${txt}`);
        }
        return r.json();
    },
    async put(url, body) {
        const r = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return r.json();
    },
    async del(url) {
        const r = await fetch(url, { method: 'DELETE' });
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return r.json();
    },
    async uploadFile(url, file, fields = {}) {
        const fd = new FormData();
        for (const [k, v] of Object.entries(fields)) fd.append(k, v);
        fd.append('file', file);
        const r = await fetch(url, { method: 'POST', body: fd });
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return r.json();
    }
};

const fmt = {
    num(v, dp = 2) {
        if (v === null || v === undefined) return '-';
        if (typeof v === 'number') return v.toLocaleString('zh-CN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
        return v;
    },
    pct(v) {
        if (v === null || v === undefined) return '-';
        const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
        const sign = v > 0 ? '+' : '';
        return `<span class="${cls}">${sign}${v.toFixed(2)}%</span>`;
    },
    pnl(v) {
        if (v === null || v === undefined) return '-';
        const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
        const sign = v > 0 ? '+' : '';
        return `<span class="${cls}">${sign}${v.toFixed(2)}</span>`;
    },
};

// ============================================================
// 导航切换
// ============================================================
function switchPanel(name) {
    state.panel = name;
    $('.nav-item').removeClass('active');
    $(`.nav-item[data-panel="${name}"]`).addClass('active');
    $('.panel').removeClass('active');
    $(`#panel-${name}`).addClass('active');
    // 进入页面时按需初始化
    const handlers = {
        snapshot: loadSnapshot,
        stock: () => {},
        indices: loadIndices,
        indicators: loadIndicatorPanel,
        sql: loadSchema,
        ai: () => {},
        rag: loadRagDocs,
        agent: initAgent,
        watchlist: loadWatchlistGroups,
        portfolio: loadPortfolio,
        alerts: loadAlerts,
        notes: loadNotes,
        news: loadNews,
    };
    if (handlers[name]) handlers[name]();
}

// ============================================================
// 顶栏 LLM 状态
// ============================================================
async function loadLLMStatus() {
    try {
        const info = await api.get('/api/v1/llm/info');
        const live = info.llm && info.llm.live;
        $('#status-dot').toggleClass('live', !!live).toggleClass('mock', !live);
        const model = info.llm.minimax_model || info.llm.qwen_model || info.llm.deepseek_model || info.llm.provider || 'unknown';
        $('#status-text').text(live ? `🟢 ${model} LIVE` : `🟡 ${model} MOCK`);
    } catch (e) {
        $('#status-text').text('⚪ 检测失败');
    }
}

// ============================================================
// 📈 全市场快照
// ============================================================
let snapPage = 1;
const snapPageSize = 50;

async function loadSnapshot() {
    try {
        const stats = await api.get('/api/stats');
        const tables = stats.tables || [];
        const stockCnt   = tables.find(t => t.tbl === 'stock_basic')?.stocks_or_keys || 0;
        const exch = stats.exchanges || {};
        const shCnt = exch.SH || 0;
        const szCnt = exch.SZ || 0;
        const bjCnt = exch.BJ || 0;
        const latestDate = stats.latest_trade_date || '-';
        $('#snapshot-metrics').html(`
            <div class="metric-card">
                <div class="metric-label">A 股标的</div>
                <div class="metric-value">${fmt.num(stockCnt, 0)}</div>
                <div class="metric-delta">含沪深北</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">上海</div>
                <div class="metric-value">${fmt.num(shCnt, 0)}</div>
                <div class="metric-delta">上交所 SH</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">深圳</div>
                <div class="metric-value">${fmt.num(szCnt, 0)}</div>
                <div class="metric-delta">深交所 SZ</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">北京</div>
                <div class="metric-value">${fmt.num(bjCnt, 0)}</div>
                <div class="metric-delta">北交所 BJ</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">最新数据日期</div>
                <div class="metric-value" style="font-size:18px">${latestDate}</div>
                <div class="metric-delta">数据更新截止</div>
            </div>
        `);
    } catch (e) {
        $('#snapshot-metrics').html(`<div class="err">${
e.message}</div>`);
    }
    await loadSnapRows();
}

async function loadSnapRows() {
    const params = new URLSearchParams({
        sort: $('#snap-sort').val(),
        order: $('#snap-order').val(),
        limit: snapPageSize,
        offset: (snapPage - 1) * snapPageSize,
    });
    const exch = $('#snap-exchange').val();
    if (exch) params.append('exchange', exch);
    const search = $('#snap-search').val().trim();
    if (search) params.append('q', search);   // 后端按 拼音首字母/中文/代码 模糊匹配全市场
    try {
        const data = await api.get(`/api/snapshot?${params}`);
        const rows = (data.rows || []);
        const html = rows.map(r => {
            const chg = parseFloat(r.change_pct);
            const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
            return `<tr>
                <td><code>${r.code}</code></td>
                <td>${r.name || ''}</td>
                <td><span class="info">${r.exchange || ''}</span></td>
                <td class="num">${fmt.num(r.close, 2)}</td>
                <td class="num ${cls}">${fmt.num(r.change, 2)}</td>
                <td class="num ${cls}">${fmt.num(chg, 2)}%</td>
                <td class="num">${fmt.num((r.volume||0)/1e8, 3)}</td>
                <td class="num">${fmt.num((r.amount||0)/1e8, 3)}</td>
            </tr>`;
        }).join('');
        $('#snap-tbody').html(html || '<tr><td colspan="8" class="text-muted text-center" style="padding:40px">无数据</td></tr>');
        const totalPages = Math.ceil((data.total || 0) / snapPageSize);
        $('#snap-pages').text(`/ 共 ${totalPages} 页 · ${data.total} 条`);
    } catch (e) {
        $('#snap-tbody').html(`<tr><td colspan="8" class="err">${e.message}</td></tr>`);
    }
}

// ============================================================
// 股票搜索自动联想（拼音首字母 / 中文 / 代码 模糊）
// ============================================================
function enableStockSuggest(sel, onPick) {
    const $input = $(sel);
    if (!$input.length) return;
    const $box = $('<div class="suggest-box"></div>');
    $input.parent().css('position', 'relative').append($box);
    let timer = null;

    function hide() { $box.hide(); }
    function render(list) {
        if (!list || !list.length) { hide(); return; }
        $box.empty();
        list.forEach(s => {
            $('<div class="suggest-item"></div>')
                .html(`<span class="sc">${s.stock_code}</span> <span class="sn">${s.short_name}</span>`)
                .on('mousedown', e => { e.preventDefault(); onPick(s); hide(); })
                .appendTo($box);
        });
        $box.show();
    }

    $input.on('input', () => {
        const q = $input.val().trim();
        clearTimeout(timer);
        if (!q) { hide(); return; }
        timer = setTimeout(async () => {
            try {
                const d = await api.get(`/api/stock/search?q=${encodeURIComponent(q)}&limit=12`);
                render(d.results);
            } catch (e) { hide(); }
        }, 180);
    });
    $input.on('blur', () => setTimeout(hide, 150));
    $input.on('keydown', e => { if (e.key === 'Escape') hide(); });
}

// ============================================================
// 🔍 个股详情
// ============================================================
async function loadStock(query) {
    if (!query) return;
    try {
        const info = await api.get(`/api/stock/info?q=${encodeURIComponent(query)}`);
        if (!info.stock_code) throw new Error('未找到股票');
        state.stockCode = info.stock_code;
        $('#stock-card').show();
        $('#stock-subtabs').show();
        renderStockMetrics(info);
        renderStockDaily();
    } catch (e) {
        alert(e.message);
    }
}

function renderStockMetrics(info) {
    const html = `
        <div class="metric-card">
            <div class="metric-label">代码</div>
            <div class="metric-value" style="font-size:18px">${info.stock_code}</div>
            <div class="metric-delta">${info.exchange || ''}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">名称</div>
            <div class="metric-value" style="font-size:18px">${info.short_name || ''}</div>
            <div class="metric-delta">上市：${info.list_date || '-'}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">最新收盘</div>
            <div class="metric-value">${fmt.num(info.last_close, 2)}</div>
            <div class="metric-delta ${info.last_pct >= 0 ? 'up' : 'down'}">${fmt.pct(info.last_pct)}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">最新交易日</div>
            <div class="metric-value" style="font-size:18px">${info.last_date || '-'}</div>
        </div>
    `;
    $('#stock-metrics').html(html);
}

// 切到表格类 Tab 前销毁图表实例，避免 canvas 残留
function disposeStockChart() {
    if (state.stockChart) { state.stockChart.dispose(); state.stockChart = null; }
    $('#stock-content').empty();
}

// 表格单元格：比率 → 百分比（原始值 0.05 显示为 5.00%），带涨跌色
function _pctCell(v, digits = 2, signed = true) {
    if (v === null || v === undefined) return '<td class="num">-</td>';
    const val = v * 100;
    const cls = signed ? (val > 0 ? 'up' : val < 0 ? 'down' : '') : '';
    const sign = (signed && val > 0) ? '+' : '';
    return `<td class="num ${cls}">${sign}${val.toFixed(digits)}%</td>`;
}

// 文本：比率 → 带色百分比 span（用于卡片）
function _pctVal(v, digits = 2) {
    if (v === null || v === undefined) return '-';
    const val = v * 100;
    const cls = val > 0 ? 'up' : val < 0 ? 'down' : 'flat';
    const sign = val > 0 ? '+' : '';
    return `<span class="${cls}">${sign}${val.toFixed(digits)}%</span>`;
}

function _metricCard(label, value, delta = '') {
    return `<div class="metric-card">
        <div class="metric-label">${label}</div>
        <div class="metric-value" style="font-size:18px">${value}</div>
        ${delta ? `<div class="metric-delta">${delta}</div>` : ''}
    </div>`;
}

function _sectionTitle(t) {
    return `<div class="card-title" style="margin:var(--space-3) 0 var(--space-2)">${t}</div>`;
}

// 因子 Tab：factors_daily 宽表（按交易日倒序）
async function renderStockFactors() {
    if (!state.stockCode) return;
    disposeStockChart();
    $('#stock-content').html('<div class="info">加载因子数据…</div>');
    try {
        const days = parseInt($('#stock-range').val(), 10) || 120;
        const data = await api.get(`/api/stock/factors?code=${state.stockCode}&days=${Math.min(days, 250)}`);
        const rows = data.rows || [];
        if (!rows.length) {
            $('#stock-content').html(`<div class="info">${data.message || '暂无因子数据'}</div>`);
            return;
        }
        const head = ['日期', '收盘', '动量5日', '动量10日', '动量20日', '动量60日',
            '波动20日', '波动60日', '量比20', '额比20', '价格位置20', 'BIAS5', 'BIAS20', 'BIAS60'];
        const body = rows.map(r => `<tr>
            <td>${r.trade_date}</td>
            <td class="num">${fmt.num(r.close, 2)}</td>
            ${_pctCell(r.mom_5d)}${_pctCell(r.mom_10d)}${_pctCell(r.mom_20d)}${_pctCell(r.mom_60d)}
            ${_pctCell(r.vol_20d, 1, false)}${_pctCell(r.vol_60d, 1, false)}
            <td class="num">${fmt.num(r.volume_ratio_20d, 2)}</td>
            <td class="num">${fmt.num(r.amount_ratio_20d, 2)}</td>
            ${_pctCell(r.price_position_20d, 1, false)}
            ${_pctCell(r.bias_ma5)}${_pctCell(r.bias_ma20)}${_pctCell(r.bias_ma60)}
        </tr>`).join('');
        $('#stock-content').html(`
            <div class="info" style="margin-bottom:8px">共 ${rows.length} 条 · 按交易日倒序（数据源 factors_daily）</div>
            <div class="table-wrap">
                <table>
                    <thead><tr>${head.map((h, i) => `<th class="${i === 0 ? '' : 'num'}">${h}</th>`).join('')}</tr></thead>
                    <tbody>${body}</tbody>
                </table>
            </div>`);
    } catch (e) {
        $('#stock-content').html(`<div class="err">${e.message}</div>`);
    }
}

// 概览 Tab：基础信息 + 最新行情 + 区间统计 + 估值/行业/因子快照
async function renderStockOverview() {
    if (!state.stockCode) return;
    disposeStockChart();
    $('#stock-content').html('<div class="info">加载概览…</div>');
    try {
        const d = await api.get(`/api/stock/overview?code=${state.stockCode}`);
        if (d.error) { $('#stock-content').html(`<div class="err">${d.error}</div>`); return; }
        const q = d.quote || {};
        const s = d.stats || {};
        const numOr = (v, dp = 2) => (v === null || v === undefined) ? '-' : fmt.num(v, dp);
        const pctOr = v => (v === null || v === undefined) ? '-' : fmt.num(v, 2) + '%';

        let html = _sectionTitle('基础信息') + '<div class="card-grid card-grid-4">' +
            _metricCard('代码', d.stock_code, d.exchange || '') +
            _metricCard('名称', d.short_name || '-', `上市：${d.list_date || '-'}`) +
            _metricCard('最新交易日', q.trade_date || '-') +
            _metricCard('最新收盘', numOr(q.close), fmt.pct(q.change_pct)) +
            '</div>';

        html += _sectionTitle('最新行情') + '<div class="card-grid card-grid-4">' +
            _metricCard('开盘 / 最高', `${numOr(q.open)} / ${numOr(q.high)}`) +
            _metricCard('最低 / 昨收', `${numOr(q.low)} / ${numOr(q.pre_close)}`) +
            _metricCard('成交量', numOr(q.volume, 0)) +
            _metricCard('成交额', numOr(q.amount, 0)) +
            _metricCard('换手率', q.turnover_ratio == null ? '-' : fmt.num(q.turnover_ratio, 2) + '%') +
            '</div>';

        html += _sectionTitle(`区间统计（近 ${s.bars || 0} 个交易日）`) + '<div class="card-grid card-grid-4">' +
            _metricCard('区间最高', numOr(s.high)) +
            _metricCard('区间最低', numOr(s.low)) +
            _metricCard('日均成交量', numOr(s.avg_volume, 0)) +
            _metricCard('日均成交额', numOr(s.avg_amount, 0)) +
            '</div>';

        const v = d.valuation;
        html += _sectionTitle('估值与财务') + (v
            ? '<div class="card-grid card-grid-4">' +
              _metricCard('报告期', v.report_date) +
              _metricCard('PE', numOr(v.pe)) +
              _metricCard('PB', numOr(v.pb)) +
              _metricCard('PS', numOr(v.ps)) +
              _metricCard('ROE', pctOr(v.roe)) +
              _metricCard('EPS', numOr(v.eps)) +
              _metricCard('总市值', numOr(v.total_market_cap, 0)) +
              _metricCard('营收', numOr(v.revenue, 0)) +
              _metricCard('净利润', numOr(v.net_profit, 0)) +
              _metricCard('营收同比', pctOr(v.yoy_revenue)) +
              _metricCard('净利同比', pctOr(v.yoy_profit)) +
              '</div>'
            : '<div class="info">暂无财务数据（finance_core 未覆盖该股）</div>');

        const inds = d.industries || [];
        html += _sectionTitle('所属行业') + (inds.length
            ? `<div class="info">${inds.map(i => `${i.industry_type || ''} ${i.industry_name}（${i.sw_code}）`).join(' · ')}</div>`
            : '<div class="info">暂无行业归属数据</div>');

        const f = d.factors;
        html += _sectionTitle('最新因子快照') + (f
            ? '<div class="card-grid card-grid-4">' +
              _metricCard('交易日', f.trade_date) +
              _metricCard('动量20日', _pctVal(f.mom_20d)) +
              _metricCard('动量60日', _pctVal(f.mom_60d)) +
              _metricCard('波动20日', f.vol_20d == null ? '-' : fmt.num(f.vol_20d * 100, 1) + '%') +
              _metricCard('量比20', numOr(f.volume_ratio_20d)) +
              _metricCard('价格位置20', f.price_position_20d == null ? '-' : fmt.num(f.price_position_20d * 100, 1) + '%') +
              _metricCard('BIAS20', f.bias_ma20 == null ? '-' : fmt.num(f.bias_ma20 * 100, 2) + '%') +
              '</div>'
            : '<div class="info">暂无因子数据</div>');

        $('#stock-content').html(html);
    } catch (e) {
        $('#stock-content').html(`<div class="err">${e.message}</div>`);
    }
}

// 计算 MACD（基于收盘价）：返回 {dif, dea, hist}
function emaSeries(arr, n) {    const k = 2 / (n + 1);
    let prev = arr[0];
    const out = [prev];
    for (let i = 1; i < arr.length; i++) { prev = arr[i] * k + prev * (1 - k); out.push(prev); }
    return out;
}
function computeMACD(closes, fast = 12, slow = 26, signal = 9) {
    const ef = emaSeries(closes, fast), es = emaSeries(closes, slow);
    const dif = closes.map((_, i) => ef[i] - es[i]);
    const dea = emaSeries(dif, signal);
    const hist = dif.map((v, i) => (v - dea[i]) * 2);
    return { dif, dea, hist };
}

// 成交量柱：按当根 K 涨跌着色（涨红 #ef232a / 跌白 #ffffff）
function volSeriesData(rows) {
    return rows.map(r => ({
        value: r.volume || 0,
        itemStyle: { color: (r.close ?? 0) >= (r.open ?? 0) ? '#ef232a' : '#ffffff' },
    }));
}

async function renderStockDaily(period) {
    if (!state.stockCode) return;
    try {
        const days = parseInt($('#stock-range').val(), 10) || 120;
        const url = period === 'weekly'
            ? `/api/stock/weekly?code=${state.stockCode}&weeks=${days}`
            : `/api/stock/kline?code=${state.stockCode}&days=${days}`;
        const data = await api.get(url);
        const rows = data.rows || [];   // 后端已按 trade_date 升序返回（由小到大）
        if (!state.stockChart) {
            $('#stock-content').empty();
            state.stockChart = echarts.init(document.getElementById('stock-content'), 'dark');
        }
        const dates = rows.map(r => r.trade_date);
        const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);   // 蜡烛图数据 [开,收,低,高]
        const vols = rows.map(r => r.volume || 0);
        const macd = computeMACD(rows.map(r => r.close));
        state.stockChart.setOption({
            backgroundColor: 'transparent',
            grid: [{ left: 50, right: 50, top: '5%', height: '42%' },
                   { left: 50, right: 50, top: '52%', height: '12%' },
                   { left: 50, right: 50, top: '68%', height: '16%' }],
            xAxis: [
                { type: 'category', data: dates, gridIndex: 0, boundaryGap: true },
                { type: 'category', data: dates, gridIndex: 1, boundaryGap: true },
                { type: 'category', data: dates, gridIndex: 2, boundaryGap: true },
            ],
            yAxis: [{ gridIndex: 0, scale: true },
                    { gridIndex: 1, axisLabel: { show: false } },
                    { gridIndex: 2, name: 'MACD', nameTextStyle: { color: '#9ba6b8', fontSize: 10 }, scale: true }],
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
            dataZoom: [
                { type: 'inside', xAxisIndex: [0, 1, 2], start: 50, end: 100 },
                { type: 'slider', xAxisIndex: [0, 1, 2], start: 50, end: 100, bottom: 4, height: 16 },
            ],
            series: [
                {
                    name: 'K线', type: 'candlestick', data: ohlc,
                    itemStyle: {
                        color: '#ef232a', color0: '#ffffff',        // 阳红 阴白（实心白柱）
                        borderColor: '#ef232a', borderColor0: '#ffffff',
                    },
                },
                { name: '成交量', type: 'bar', data: volSeriesData(rows), xAxisIndex: 1, yAxisIndex: 1 },
                { name: 'DIF', type: 'line', data: macd.dif, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#f5d000' } },
                { name: 'DEA', type: 'line', data: macd.dea, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#14b143' } },
                { name: 'MACD', type: 'bar', data: macd.hist, xAxisIndex: 2, yAxisIndex: 2,
                  itemStyle: { color: p => (p.data >= 0 ? '#ef232a' : '#ffffff') } },
            ],
        }, true);
    } catch (e) {
        $('#stock-content').html(`<div class="err">${e.message}</div>`);
    }
}

// 个股 5 分钟 K：蜡烛图（样式与日 K 一致），以一天为单位，可切其他天
async function renderStockMinute(date) {
    if (!state.stockCode) return;
    const sel = date || $('#minute-date').val();
    try {
        const url = `/api/stock/minute?code=${state.stockCode}` + (sel ? `&date=${sel}` : '');
        const data = await api.get(url);
        const rows = data.rows || [];
        if (!state.stockChart) {
            $('#stock-content').empty();
            state.stockChart = echarts.init(document.getElementById('stock-content'), 'dark');
        }
        const times = rows.map(r => (r.trade_time || '').slice(11, 16));  // HH:MM
        const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);     // 蜡烛 [开,收,低,高]
        const vols = rows.map(r => r.volume || 0);
        const macd = computeMACD(rows.map(r => r.close));
        const day = rows.length ? rows[0].trade_date : '';
        state.stockChart.setOption({
            backgroundColor: 'transparent',
            title: day ? { text: `${state.stockCode} 5分钟K（${day}）`, left: 10, top: 4,
                           textStyle: { color: '#9ba6b8', fontSize: 12, fontWeight: 'normal' } } : undefined,
            grid: [{ left: 50, right: 50, top: '9%', height: '40%' },
                   { left: 50, right: 50, top: '53%', height: '12%' },
                   { left: 50, right: 50, top: '69%', height: '16%' }],
            xAxis: [
                { type: 'category', data: times, gridIndex: 0, boundaryGap: true },
                { type: 'category', data: times, gridIndex: 1, boundaryGap: true },
                { type: 'category', data: times, gridIndex: 2, boundaryGap: true },
            ],
            yAxis: [{ gridIndex: 0, scale: true },
                    { gridIndex: 1, axisLabel: { show: false } },
                    { gridIndex: 2, name: 'MACD', nameTextStyle: { color: '#9ba6b8', fontSize: 10 }, scale: true }],
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
            dataZoom: [
                { type: 'inside', xAxisIndex: [0, 1, 2] },
                { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 4, height: 16 },
            ],
            series: [
                {
                    name: '5分钟K', type: 'candlestick', data: ohlc,
                    itemStyle: {
                        color: '#ef232a', color0: '#ffffff',        // 阳红 阴白（与日K一致）
                        borderColor: '#ef232a', borderColor0: '#ffffff',
                    },
                },
                { name: '成交量', type: 'bar', data: volSeriesData(rows), xAxisIndex: 1, yAxisIndex: 1 },
                { name: 'DIF', type: 'line', data: macd.dif, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#f5d000' } },
                { name: 'DEA', type: 'line', data: macd.dea, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#14b143' } },
                { name: 'MACD', type: 'bar', data: macd.hist, xAxisIndex: 2, yAxisIndex: 2,
                  itemStyle: { color: p => (p.data >= 0 ? '#ef232a' : '#ffffff') } },
            ],
        }, true);
    } catch (e) {
        $('#stock-content').html(`<div class="err">${e.message}</div>`);
    }
}

// 加载 5 分钟 K 可用交易日，填充下拉，默认最新一天并重绘
async function loadMinuteDates() {
    if (!state.stockCode) return;
    try {
        const data = await api.get(`/api/stock/minute/dates?code=${state.stockCode}&limit=60`);
        const dates = data.dates || [];
        const $sel = $('#minute-date');
        $sel.html((dates.map(d => `<option value="${d.date}">${d.date}（${d.bars}根）</option>`).join(''))
                 || '<option value="">无 5 分钟数据</option>');
        if (dates.length) renderStockMinute(dates[0].date);
    } catch (e) {
        $('#stock-content').html(`<div class="err">${e.message}</div>`);
    }
}

// ============================================================
// 📊 指数看板
// ============================================================
async function loadIndices() {
    try {
        const data = await api.get('/api/indices/board');
        const html = (data.indices || []).map(i => {
            const cls = i.change_pct >= 0 ? 'up' : 'down';
            return `
                <div class="metric-card" data-code="${i.code}">
                    <div class="metric-label">${i.name}</div>
                    <div class="metric-value">${fmt.num(i.close, 2)}</div>
                    <div class="metric-delta ${cls}">${fmt.pct(i.change_pct)}</div>
                </div>`;
        }).join('');
        $('#indices-grid').html(html);
        $('.metric-card[data-code]').on('click', function() {
            const code = $(this).data('code');
            // 重置为日 K 视图
            $('#index-subtabs .subtab').removeClass('active');
            $('#index-subtabs .subtab[data-sub="daily"]').addClass('active');
            $('#index-range').show();
            $('#index-minute-date').hide();
            renderIndexChart(code);
        });
        if (data.indices && data.indices.length) renderIndexChart(data.indices[0].code);
    } catch (e) {
        $('#indices-grid').html(`<div class="err">${e.message}</div>`);
    }
}

async function renderIndexChart(code) {
    try {
        const days = parseInt($('#index-range').val(), 10) || 120;
        const data = await api.get(`/api/indices/kline?code=${code}&days=${days}`);
        const rows = data.rows || [];   // 后端已按 trade_date 升序返回（由小到大）
        state.currentIndexCode = code;
        if (!state.indexChart) {
            state.indexChart = echarts.init(document.getElementById('index-chart'), 'dark');
        }
        const datesI = rows.map(r => r.trade_date);
        const macd = computeMACD(rows.map(r => r.close));
        state.indexChart.setOption({
            backgroundColor: 'transparent',
            grid: [{ left: 50, right: 30, top: '4%', height: '50%' },
                   { left: 50, right: 30, top: '58%', height: '15%' },
                   { left: 50, right: 30, top: '76%', height: '14%' }],
            xAxis: [
                { type: 'category', data: datesI, gridIndex: 0, boundaryGap: true },
                { type: 'category', data: datesI, gridIndex: 1, boundaryGap: true },
                { type: 'category', data: datesI, gridIndex: 2, boundaryGap: true },
            ],
            yAxis: [{ gridIndex: 0, scale: true },
                    { gridIndex: 1, axisLabel: { show: false } },
                    { gridIndex: 2, name: 'MACD', nameTextStyle: { color: '#9ba6b8', fontSize: 10 }, scale: true }],
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
            dataZoom: [
                { type: 'inside', xAxisIndex: [0, 1, 2], start: 50, end: 100 },
                { type: 'slider', xAxisIndex: [0, 1, 2], start: 50, end: 100, bottom: 4, height: 16 },
            ],
            series: [
                { name: code, type: 'candlestick',
                  data: rows.map(r => [r.open, r.close, r.low, r.high]),
                  itemStyle: {
                      color: '#ef232a', color0: '#ffffff',        // 阳红 阴白（实心白柱）
                      borderColor: '#ef232a', borderColor0: '#ffffff',
                  } },
                { name: '成交量', type: 'bar', data: volSeriesData(rows), xAxisIndex: 1, yAxisIndex: 1 },
                { name: 'DIF', type: 'line', data: macd.dif, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#f5d000' } },
                { name: 'DEA', type: 'line', data: macd.dea, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#14b143' } },
                { name: 'MACD', type: 'bar', data: macd.hist, xAxisIndex: 2, yAxisIndex: 2,
                  itemStyle: { color: p => (p.data >= 0 ? '#ef232a' : '#ffffff') } },
            ],
        }, true);
    } catch (e) {}
}

// 指数 5 分钟 K：蜡烛图 + 成交量 + MACD（与个股 5 分钟一致）
async function renderIndexMinute(date) {
    if (!state.currentIndexCode) return;
    const code = state.currentIndexCode;
    const sel = date || $('#index-minute-date').val();
    try {
        const url = `/api/indices/minute?code=${code}` + (sel ? `&date=${sel}` : '');
        const data = await api.get(url);
        const rows = data.rows || [];
        if (!state.indexChart) {
            state.indexChart = echarts.init(document.getElementById('index-chart'), 'dark');
        }
        const times = rows.map(r => (r.trade_time || '').slice(11, 16));  // HH:MM
        const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);     // 蜡烛 [开,收,低,高]
        const vols = rows.map(r => r.volume || 0);
        const macd = computeMACD(rows.map(r => r.close));
        const day = rows.length ? rows[0].trade_date : '';
        state.indexChart.setOption({
            backgroundColor: 'transparent',
            title: day ? { text: `${code} 5分钟K（${day}）`, left: 10, top: 4,
                           textStyle: { color: '#9ba6b8', fontSize: 12, fontWeight: 'normal' } } : undefined,
            grid: [{ left: 50, right: 50, top: '9%', height: '40%' },
                   { left: 50, right: 50, top: '53%', height: '12%' },
                   { left: 50, right: 50, top: '69%', height: '16%' }],
            xAxis: [
                { type: 'category', data: times, gridIndex: 0, boundaryGap: true },
                { type: 'category', data: times, gridIndex: 1, boundaryGap: true },
                { type: 'category', data: times, gridIndex: 2, boundaryGap: true },
            ],
            yAxis: [{ gridIndex: 0, scale: true },
                    { gridIndex: 1, axisLabel: { show: false } },
                    { gridIndex: 2, name: 'MACD', nameTextStyle: { color: '#9ba6b8', fontSize: 10 }, scale: true }],
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
            dataZoom: [
                { type: 'inside', xAxisIndex: [0, 1, 2] },
                { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 4, height: 16 },
            ],
            series: [
                { name: '5分钟K', type: 'candlestick', data: ohlc,
                  itemStyle: { color: '#ef232a', color0: '#ffffff', borderColor: '#ef232a', borderColor0: '#ffffff' } },
                { name: '成交量', type: 'bar', data: volSeriesData(rows), xAxisIndex: 1, yAxisIndex: 1 },
                { name: 'DIF', type: 'line', data: macd.dif, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#f5d000' } },
                { name: 'DEA', type: 'line', data: macd.dea, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1 }, itemStyle: { color: '#14b143' } },
                { name: 'MACD', type: 'bar', data: macd.hist, xAxisIndex: 2, yAxisIndex: 2,
                  itemStyle: { color: p => (p.data >= 0 ? '#ef232a' : '#ffffff') } },
            ],
        }, true);
    } catch (e) {
        $('#index-chart').html(`<div class="err">${e.message}</div>`);
    }
}

// 加载指数 5 分钟可用交易日，填充下拉，默认最新一天并重绘
async function loadIndexMinuteDates() {
    if (!state.currentIndexCode) return;
    try {
        const data = await api.get(`/api/indices/minute/dates?code=${state.currentIndexCode}&limit=60`);
        const dates = data.dates || [];
        const $sel = $('#index-minute-date');
        $sel.html((dates.map(d => `<option value="${d.date}">${d.date}（${d.bars}根）</option>`).join(''))
                 || '<option value="">无 5 分钟数据</option>');
        if (dates.length) renderIndexMinute(dates[0].date);
    } catch (e) {
        $('#index-chart').html(`<div class="err">${e.message}</div>`);
    }
}

// ============================================================
// 📐 技术指标
// ============================================================
async function loadIndicatorPanel() {
    await loadIndicatorList();
}

async function loadIndicatorList() {
    try {
        const names = await api.get('/api/v1/indicators');
        const sel = $('#ind-name');
        sel.empty();
        (names || []).forEach(n => sel.append(`<option value="${n}">${n.toUpperCase()}</option>`));
        await loadIndicatorParams();
    } catch (e) {
        $('#ind-name').html('<option value="">加载失败</option>');
    }
}

async function loadIndicatorParams() {
    const name = $('#ind-name').val();
    const box = $('#ind-params-form');
    if (!name) { box.empty(); return; }
    try {
        const meta = await api.get(`/api/v1/indicators/${name}`);
        const specs = meta.param_specs || [];
        box.empty();
        specs.forEach(s => {
            const id = `ind-param-${s.name}`;
            const val = (s.default !== null && s.default !== undefined) ? s.default : '';
            const input = (s.type === 'int' || s.type === 'float')
                ? `<input type="number" id="${id}" data-param="${s.name}" data-type="${s.type}" value="${val}" style="width:90px" title="${s.description || ''}">`
                : `<input type="text" id="${id}" data-param="${s.name}" data-type="str" value="${val}" title="${s.description || ''}">`;
            box.append(`<label class="param-label">${s.name}${input}</label>`);
        });
    } catch (e) {
        box.html(`<span class="err">参数加载失败: ${e.message}</span>`);
    }
}

async function computeIndicator() {
    const code = $('#ind-stock').val().trim();
    const name = $('#ind-name').val();
    const days = parseInt($('#ind-days').val() || 60);
    const params = {};
    $('#ind-params-form [data-param]').each(function () {
        const pname = $(this).data('param');
        const ptype = $(this).data('type');
        let v = $(this).val();
        if (ptype === 'int') v = parseInt(v) || 0;
        else if (ptype === 'float') v = parseFloat(v) || 0;
        params[pname] = v;
    });
    if (!code) {
        alert('请输入股票代码');
        return;
    }
    if (!name) {
        alert('请选择指标');
        return;
    }
    try {
        const data = await api.post(`/api/v1/indicators/${name}/compute`, {
            stock_code: code, params, days,
        });
        renderIndicatorChart(name, data.rows, params);
        // 渲染数据表
        const cols = Object.keys(data.rows[0] || {});
        const thead = `<tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>`;
        const tbody = data.rows.slice(-30).map(r =>
            `<tr>${cols.map(c => {
                const v = r[c];
                return `<td class="num">${v === null ? '-' : v}</td>`;
            }).join('')}</tr>`
        ).join('');
        $('#ind-table').html(`<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`);
    } catch (e) {
        alert(e.message);
    }
}

function renderIndicatorChart(name, rows, params) {
    if (!state.indicatorChart) {
        state.indicatorChart = echarts.init(document.getElementById('ind-chart'), 'dark');
    }
    if (!rows || !rows.length) return;
    const dates = rows.map(r => r.trade_date);
    // 蜡烛 [开,收,低,高]，A 股习惯：阳红 阴白（与个股 K 线一致）
    const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);
    const KSTYLE = { color: '#ef232a', color0: '#ffffff', borderColor: '#ef232a', borderColor0: '#ffffff' };
    const series = [];
    let title = name.toUpperCase();
    // 价格叠加类指标：用蜡烛图表达价格，指标线叠加其上
    const addCandle = () => series.push({ name: 'K线', type: 'candlestick', data: ohlc, itemStyle: KSTYLE });

    if (name === 'ma') {
        const period = params.period || 5;
        title = `MA(${period})`;
        addCandle();
        series.push({
            name: `MA${period}`, type: 'line', data: rows.map(r => r[`ma${period}`]),
            smooth: true, showSymbol: false,
            lineStyle: { color: '#f5d000', width: 2 },
        });
    } else if (name === 'macd') {
        series.push({ name: 'DIF', type: 'line', data: rows.map(r => r.dif),
            smooth: true, showSymbol: false, lineStyle: { color: '#f5d000', width: 1.5 } });
        series.push({ name: 'DEA', type: 'line', data: rows.map(r => r.dea),
            smooth: true, showSymbol: false, lineStyle: { color: '#14b143', width: 1.5 } });
        series.push({ name: 'MACD', type: 'bar', data: rows.map(r => r.hist),
            itemStyle: { color: p => (p.data >= 0 ? '#ef232a' : '#14b143') } });
    } else if (name === 'boll') {
        title = 'BOLL';
        addCandle();
        series.push({ name: '上轨', type: 'line', data: rows.map(r => r.boll_up),
            smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c', width: 1.5 } });
        series.push({ name: '中轨', type: 'line', data: rows.map(r => r.boll_mid),
            smooth: true, showSymbol: false, lineStyle: { color: '#f5d000', width: 1.5 } });
        series.push({ name: '下轨', type: 'line', data: rows.map(r => r.boll_down),
            smooth: true, showSymbol: false, lineStyle: { color: '#14b143', width: 1.5 } });
    } else if (name === 'rsi') {
        const period = params.period || 14;
        title = `RSI(${period})`;
        series.push({ name: `RSI(${period})`, type: 'line', data: rows.map(r => r[`rsi${period}`]),
            smooth: true, showSymbol: false, lineStyle: { color: '#00d4ff', width: 2 },
            markLine: { silent: true, data: [
                { yAxis: 70, lineStyle: { color: '#ff3b5c' }, label: { formatter: '超买 70' } },
                { yAxis: 30, lineStyle: { color: '#14b143' }, label: { formatter: '超卖 30' } },
            ]}});
    } else if (name === 'kdj') {
        title = 'KDJ';
        series.push({ name: 'K', type: 'line', data: rows.map(r => r.kdj_k), smooth: true, showSymbol: false, lineStyle: { color: '#f5d000', width: 1.5 } });
        series.push({ name: 'D', type: 'line', data: rows.map(r => r.kdj_d), smooth: true, showSymbol: false, lineStyle: { color: '#14b143', width: 1.5 } });
        series.push({ name: 'J', type: 'line', data: rows.map(r => r.kdj_j), smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c', width: 1.5 } });
    } else if (name === 'arbr') {
        title = 'ARBR';
        series.push({ name: 'AR', type: 'line', data: rows.map(r => r.ar), smooth: true, showSymbol: false, lineStyle: { color: '#f5d000', width: 1.5 } });
        series.push({ name: 'BR', type: 'line', data: rows.map(r => r.br), smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c', width: 1.5 } });
    } else if (name === 'adx') {
        title = 'ADX';
        series.push({ name: 'ADX', type: 'line', data: rows.map(r => r.adx), smooth: true, showSymbol: false, lineStyle: { color: '#f5d000', width: 2 } });
        series.push({ name: '+DI', type: 'line', data: rows.map(r => r.plus_di), smooth: true, showSymbol: false, lineStyle: { color: '#14b143', width: 1.5 } });
        series.push({ name: '-DI', type: 'line', data: rows.map(r => r.minus_di), smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c', width: 1.5 } });
    } else if (name === 'stochrsi') {
        title = 'StochRSI';
        series.push({ name: 'StochRSI', type: 'line', data: rows.map(r => r.stochrsi), smooth: true, showSymbol: false, lineStyle: { color: '#00d4ff', width: 2 } });
        series.push({ name: 'StochRSI %D', type: 'line', data: rows.map(r => r.stochrsi_d), smooth: true, showSymbol: false, lineStyle: { color: '#ff8a00', width: 1.5 } });
    } else {
        // 通用：画出所有非 OHLCV 列，按调色板区分颜色
        const skip = new Set(['trade_date', 'open', 'high', 'low', 'close', 'volume']);
        const PALETTE = ['#f5d000', '#14b143', '#ff3b5c', '#00d4ff', '#b15bff', '#ff8a00', '#00d68f', '#3b6bff', '#ff5ea8', '#a0a0a0'];
        Object.keys(rows[0]).filter(c => !skip.has(c)).forEach((c, i) => {
            series.push({ name: c, type: 'line', data: rows.map(r => r[c]), smooth: true, showSymbol: false,
                lineStyle: { color: PALETTE[i % PALETTE.length], width: 1.5 } });
        });
        title = name.toUpperCase();
    }
    state.indicatorChart.setOption({
        backgroundColor: 'transparent',
        color: ['#f5d000', '#14b143', '#ff3b5c', '#00d4ff', '#b15bff', '#ff8a00', '#00d68f', '#3b6bff', '#ff5ea8', '#a0a0a0'],
        title: { text: title, textStyle: { color: '#f5d000', fontSize: 14 }, left: 10, top: 5 },
        grid: { left: 55, right: 30, top: 50, bottom: 55 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', scale: true },
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
        legend: { textStyle: { color: '#9ba6b8' }, top: 25 },
        dataZoom: [
            { type: 'inside', xAxisIndex: 0, start: 40, end: 100 },
            { type: 'slider', xAxisIndex: 0, start: 40, end: 100, bottom: 6, height: 18 },
        ],
        series,
    }, true);
}

// ============================================================
// 📝 SQL
// ============================================================
async function loadSchema() {
    try {
        const data = await api.get('/api/v1/schema');
        const html = data.map(t => `
            <div class="schema-table" data-name="${t.table}">
                <div class="schema-table-name">${t.table} (${t.row_count})</div>
                ${t.columns.map(c => `<div class="schema-col" data-insert="${t.table}.${c.name}">${c.name} <span class="text-muted">${c.type}</span></div>`).join('')}
            </div>
        `).join('');
        $('#sql-schema').html(html);
        $('.schema-col').on('click', function() {
            const txt = $(this).data('insert');
            const ta = $('#sql-input');
            ta.val(ta.val() + ' ' + txt);
        });
    } catch (e) {
        $('#sql-schema').html(`<div class="err">${e.message}</div>`);
    }
}

async function runSQL() {
    const sql = $('#sql-input').val();
    $('#sql-error').text('');
    try {
        const data = await api.post('/api/sql/query', { query: sql });
        if (!data.rows || !data.rows.length) {
            $('#sql-table thead, #sql-table tbody').empty();
            $('#sql-status').text('0 行');
            return;
        }
        const cols = Object.keys(data.rows[0]);
        $('#sql-table thead').html(`<tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>`);
        $('#sql-table tbody').html(data.rows.map(r =>
            `<tr>${cols.map(c => `<td>${r[c] === null ? '-' : r[c]}</td>`).join('')}</tr>`
        ).join(''));
        $('#sql-status').text(`${data.rows.length} 行`);
    } catch (e) {
        $('#sql-error').text(e.message);
    }
}

// ============================================================
// 🤖 AI 分析
// ============================================================
async function runAI() {
    const stock = $('#ai-stock').val().trim();
    if (!stock) { alert('请输入股票'); return; }
    $('#ai-status').html('<span class="spinner"></span> 生成中...');
    $('#ai-result').text('');
    $('#ai-exports').hide();
    $('#ai-progress').show();
    $('#ai-steps').empty();
    setAIProgress('prepare', `📊 已接收 ${stock}，准备生成综合报告…`, 5);
    try {
        const resp = await fetch('/api/v1/ai/analyze/comprehensive/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: stock }),
        });
        if (!resp.ok) throw new Error(`/ai/analyze/comprehensive/stream: ${resp.status}`);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buf = '', data = null;
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            // SSE：每条以空行分隔
            let idx;
            while ((idx = buf.indexOf('\n\n')) !== -1) {
                const chunk = buf.slice(0, idx);
                buf = buf.slice(idx + 2);
                const ev = parseSSE(chunk);
                if (!ev) continue;
                if (ev.event === 'stage') {
                    setAIProgress(ev.data.stage, ev.data.label, ev.data.pct);
                } else if (ev.event === 'result') {
                    data = ev.data;
                    setAIProgress('render', '✅ 综合报告生成完成', 100);
                } else if (ev.event === 'error') {
                    throw new Error(ev.data.message || '分析失败');
                }
            }
        }
        if (!data) throw new Error('未收到分析结果');
        $('#ai-result').html(md2html(data.response || data.analysis || '') + aiReportEnd());
        state.aiAnalysisId = data.analysis_id;
        if (state.aiAnalysisId) $('#ai-exports').show();
        $('#ai-status').text(data.degraded ? '⚠️ 部分完成' : '✅ 完成');
        $('#ai-progress').hide();
    } catch (e) {
        $('#ai-status').text('❌ 失败');
        $('#ai-progress').hide();
        $('#ai-result').html(`<div class="err">${e.message}</div>`);
    }
}

// 报告结束标识：明确告诉用户内容已输出完毕（避免看起来被截断）
function aiReportEnd() {
    return '<div class="ai-report-end">'
        + '<span class="line"></span>'
        + '<span class="txt">✓ 以上为完整报告 · 完</span>'
        + '<span class="line"></span>'
        + '</div>';
}

function parseSSE(chunk) {
    // chunk 形如：event: xxx\ndata: {...}
    const lines = chunk.split('\n');
    let event = 'message', dataStr = '';
    for (const line of lines) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
    }
    if (!dataStr) return null;
    try { return { event, data: JSON.parse(dataStr) }; }
    catch { return null; }
}

// 动态步骤表：按事件到达顺序记录，已出现的步骤标 done，最新标 active
const _aiSteps = [];
function setAIProgress(stage, label, pct) {
    $('#ai-progress-label').text(label || '');
    if (typeof pct === 'number') $('#ai-progress-fill').css('width', pct + '%');
    if (!_aiSteps.find(s => s.stage === stage)) {
        _aiSteps.push({ stage, label });
    } else {
        _aiSteps.find(s => s.stage === stage).label = label;
    }
    const activeIdx = _aiSteps.length - 1;
    const $ol = $('#ai-steps').empty();
    _aiSteps.forEach((s, i) => {
        const cls = i < activeIdx ? 'done' : (i === activeIdx ? 'active' : '');
        $ol.append(`<li class="${cls}"><span class="dot"></span>${s.label}</li>`);
    });
}

async function exportAI(fmt) {
    if (!state.aiAnalysisId) return;
    try {
        const data = await api.get(`/api/v1/exports/analysis/${state.aiAnalysisId}/${fmt}`);
        if (fmt === 'md') {
            const blob = new Blob([data.content], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `analysis_${state.aiAnalysisId}.md`;
            a.click();
            URL.revokeObjectURL(url);
        } else if (fmt === 'pdf') {
            window.open(data.path, '_blank');
        }
    } catch (e) {
        alert(e.message);
    }
}

// 简易 Markdown 渲染
function md2html(md) {
    if (!md) return '';
    let html = md
        .replace(/^### (.*)$/gm, '<h3>$1</h3>')
        .replace(/^## (.*)$/gm, '<h2>$1</h2>')
        .replace(/^# (.*)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code>$1</code>')
        .replace(/^> (.*)$/gm, '<blockquote>$1</blockquote>');
    // 列表
    html = html.replace(/(^- .*\n?)+/gm, m => '<ul>' + m.trim().split('\n').map(l => `<li>${l.replace(/^- /, '')}</li>`).join('') + '</ul>');
    // 表格
    const tableRe = /((?:\|.+\|\n)+)/g;
    html = html.replace(tableRe, m => {
        const lines = m.trim().split('\n').filter(l => !/^\|[-:\s|]+\|$/.test(l));
        if (lines.length < 1) return m;
        const header = lines[0].split('|').slice(1, -1).map(c => `<th>${c.trim()}</th>`).join('');
        const body = lines.slice(1).map(l => '<tr>' + l.split('|').slice(1, -1).map(c => `<td>${c.trim()}</td>`).join('') + '</tr>').join('');
        return `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
    });
    html = html.replace(/\n/g, '<br>');
    return html;
}

// ============================================================
// 📚 RAG
// ============================================================
async function loadRagDocs() {
    try {
        const data = await api.get('/api/v1/rag/docs');
        const rows = data.documents || data || [];
        if (!rows.length) {
            $('#rag-docs-list').html('<div class="text-muted text-center" style="padding:40px">尚无文档 · 上传第一个 PDF 开始</div>');
            return;
        }
        const html = `<table><thead><tr><th>标题</th><th>类型</th><th>股票</th><th>分块</th><th>入库时间</th></tr></thead><tbody>` +
            rows.map(d => `<tr><td>${d.title || d.doc_id}</td><td>${d.doc_type || ''}</td><td>${d.stock_code || ''}</td><td>${d.chunk_count || 0}</td><td class="info">${d.ingested_at || ''}</td></tr>`).join('') +
            `</tbody></table>`;
        $('#rag-docs-list').html(html);
    } catch (e) {}
}

async function uploadRag() {
    const file = $('#rag-file')[0].files[0];
    if (!file) { alert('请选择文件'); return; }
    $('#rag-upload-status').html('<span class="spinner"></span> 上传中...');
    try {
        const data = await api.uploadFile('/api/v1/rag/upload', file, {
            stock_code: $('#rag-stock').val(),
            doc_type: $('#rag-doctype').val(),
            title: $('#rag-title').val(),
        });
        $('#rag-upload-status').html(`✅ 已入库 · ${data.chunk_count} 个分块`);
        await loadRagDocs();
    } catch (e) {
        $('#rag-upload-status').text('❌ ' + e.message);
    }
}

async function askRag() {
    const q = $('#rag-question').val().trim();
    if (!q) return;
    $('#rag-ask-status').html('<span class="spinner"></span> 思考中...');
    $('#rag-answer').text('');
    try {
        const data = await api.post('/api/v1/rag/query', {
            question: q, top_k: parseInt($('#rag-topk').val()),
        });
        let ans = md2html(data.answer || '');
        if (data.citations && data.citations.length) {
            ans += '<h3>📎 引用来源</h3><ul>' + data.citations.map((c, i) =>
                `<li>[${i+1}] ${c.doc_id} · page ${c.page} · 相似度 ${(c.score||0).toFixed(3)}</li>`
            ).join('') + '</ul>';
        }
        $('#rag-answer').html(ans);
        $('#rag-ask-status').text('✅');
    } catch (e) {
        $('#rag-ask-status').text('❌');
        $('#rag-answer').html(`<div class="err">${e.message}</div>`);
    }
}

// ============================================================
// 模块4 · AI 投研对话 Agent（多轮对话，MiniMax 驱动）
// ============================================================
const AGENT_WELCOME =
    '你好，我是由 MiniMax-M3 驱动的投研对话 Agent。\n\n' +
    '点下方推荐问题可以快速开始；**每一轮回答后都可以直接追问**' +
    '（例如「它的主要风险有哪些？」我会自动沿用上文提到的股票）。';

const AGENT_DEFAULT_SUGGESTIONS = [
    '贵州茅台最新财报亮点？',
    '对比茅台和五粮液的估值',
    '回测 600519 站上 60 日线买入',
];

function agentBubble(role, html) {
    const avatar = role === 'user' ? '🧑' : '🤖';
    return `<div class="chat-msg ${role}">` +
        `<div class="chat-avatar">${avatar}</div>` +
        `<div class="chat-bubble">${html}</div>` +
        `</div>`;
}

function agentScrollBottom() {
    const el = $('#agent-chat');
    if (el.length) el.scrollTop(el[0].scrollHeight);
}

// 推荐问题（输入框上方，可点击直发）
function agentRenderSuggestions(list) {
    const items = (list && list.length) ? list : AGENT_DEFAULT_SUGGESTIONS;
    const html = '<span class="suggest-label">推荐问题：</span>' +
        items.map(q => `<span class="suggest-chip" data-q="${escNews(q)}">${escNews(q)}</span>`).join('');
    $('#agent-suggest').html(html);
}

function initAgent() {
    if (state.agentInited) return;
    state.agentInited = true;
    agentReset();
}

function agentReset() {
    state.agentHistory = [];
    state.agentThinking = false;
    $('#agent-chat').empty().append(agentBubble('assistant', md2html(AGENT_WELCOME)));
    $('#agent-q').val('').css('height', 'auto').focus();
    agentRenderSuggestions(AGENT_DEFAULT_SUGGESTIONS);
    agentScrollBottom();
}

async function askAgent() {
    if (state.agentThinking) return;
    const q = $('#agent-q').val().trim();
    if (!q) return;

    state.agentThinking = true;
    $('#agent-ask').prop('disabled', true);
    $('#agent-q').val('').css('height', 'auto');

    // 用户气泡 + 记录（发送给后端的历史不含本条）
    const history = state.agentHistory.slice(-12);
    $('#agent-chat').append(agentBubble('user', escNews(q)));
    state.agentHistory.push({ role: 'user', content: q });

    // 助手占位气泡
    const $msg = $(agentBubble('assistant', '<span class="spinner"></span> 正在思考并调用工具…'))
        .appendTo('#agent-chat');
    const $bubble = $msg.find('.chat-bubble');
    agentScrollBottom();

    try {
        const data = await api.post('/api/v1/agent/ask', { question: q, history });
        const answer = data.answer || '（未获得回答）';
        let html = md2html(answer);

        if (data.steps && data.steps.length) {
            const sid = 'agent-steps-' + Date.now();
            html += `<div class="chat-steps">` +
                `<button class="chat-steps-toggle" data-target="${sid}">▸ AI 思考步骤（${data.steps.length}）</button>` +
                `<div class="chat-steps-body" id="${sid}" style="display:none">` +
                data.steps.map((s, i) =>
                    `<div class="step">` +
                    `<div class="step-head">步骤 ${i + 1} · ${escNews(s.action.tool)} · ${s.duration_ms || 0}ms</div>` +
                    `<div class="step-thought">💭 ${escNews(s.thought || '')}</div>` +
                    `<div class="step-args">⚙️ 参数：${escNews(JSON.stringify(s.action.args))}</div>` +
                    `<pre class="step-obs">${escNews(JSON.stringify(s.observation, null, 2))}</pre>` +
                    `</div>`).join('') +
                `</div></div>`;
        }

        if (data.sources && data.sources.length) {
            const list = data.sources.map(s =>
                `${escNews(s.doc_id || '')}${s.page ? '（p.' + escNews(String(s.page)) + '）' : ''}`
            ).join('、');
            html += `<div class="chat-sources">📎 数据来源（${data.sources.length}）：${list}</div>`;
        }

        if (data.degraded) {
            html += `<div class="chat-sources">⚠️ 本轮为降级回答（未走完整工具链）</div>`;
        }

        $bubble.html(html);
        // 结构化卡片（回测绩效曲线 / 交易明细等）
        if (data.cards && data.cards.length) {
            const $cards = $('<div class="chat-cards"></div>').appendTo($msg);
            data.cards.forEach(c => renderAgentCard(c, $cards));
        }
        state.agentHistory.push({ role: 'assistant', content: answer });
        // 每轮回答后刷新「追问」推荐
        agentRenderSuggestions(data.suggestions);
    } catch (e) {
        $bubble.html(`<div class="err">${escNews(e.message)}</div>`);
    } finally {
        state.agentThinking = false;
        $('#agent-ask').prop('disabled', false);
        agentScrollBottom();
        $('#agent-q').focus();
    }
}

// ============================================================
// 智能问答 · 结构化卡片渲染（回测绩效曲线 / 交易明细）
// ============================================================
let _agentCardSeq = 0;

function renderAgentCard(card, $container) {
    if (!card) return;
    if (card.type === 'backtest') return renderAgentBacktestCard(card, $container);
}

function renderAgentBacktestCard(c, $container) {
    const m = c.metrics || {};
    const pnlCls = v => (v == null ? '' : (v >= 0 ? 'up' : 'down'));
    const metric = (label, val, cls) =>
        `<div class="metric-card"><div class="metric-label">${label}</div>` +
        `<div class="metric-value ${cls || ''}">${val}</div></div>`;
    const metricsHtml = [
        metric('总收益率', fmt.pct(m.total_return_pct), pnlCls(m.total_return_pct)),
        metric('年化收益', fmt.pct(m.annual_return_pct), pnlCls(m.annual_return_pct)),
        metric('最大回撤', m.max_drawdown_pct == null ? '-' : '-' + fmt.num(Math.abs(m.max_drawdown_pct), 2) + '%', 'down'),
        metric('夏普比率', fmt.num(m.sharpe, 2)),
        metric('胜率', fmt.pct(m.win_rate_pct)),
        metric('交易次数', m.trade_count != null ? m.trade_count : '-'),
    ].join('');

    const chartId = 'agent-bt-chart-' + (++_agentCardSeq);
    const sub = c.input_signal
        ? `${escNews(c.stock_code)} · 信号 ${escNews(c.signal)} <span class="text-muted">（原始：${escNews(c.input_signal)}）</span>`
        : `${escNews(c.stock_code)} · 信号 ${escNews(c.signal)}`;
    const period = c.period ? `<span class="text-muted"> · ${escNews(c.period)}</span>` : '';

    const tradesHtml = renderAgentBtTrades(c.trades);

    const $card = $(`
      <div class="agent-card">
        <div class="agent-card-head">
          <span class="agent-card-title">📈 回测结果</span>
          <span class="agent-card-sub2">${sub}${period}</span>
        </div>
        <div class="agent-card-metrics">${metricsHtml}</div>
        <div class="agent-card-chart" id="${chartId}"></div>
        <div class="agent-card-tradestitle">交易明细（${ (c.trades || []).length }）</div>
        <div class="table-wrap">${tradesHtml}</div>
        ${c.warning ? `<div class="chat-sources">⚠️ ${escNews(c.warning)}</div>` : ''}
      </div>`).appendTo($container);

    // 权益曲线
    const curve = c.equity_curve || [];
    const el = document.getElementById(chartId);
    if (el) {
        const chart = echarts.init(el);
        chart.setOption({
            tooltip: { trigger: 'axis' },
            grid: { left: 56, right: 16, top: 18, bottom: 28 },
            xAxis: { type: 'category', data: curve.map(d => d.date),
                     axisLabel: { fontSize: 10, color: '#8a94a6' } },
            yAxis: { type: 'value', scale: true,
                     axisLabel: { fontSize: 10, color: '#8a94a6' } },
            series: [{
                name: '策略净值', type: 'line', data: curve.map(d => d.value),
                showSymbol: false, smooth: false,
                lineStyle: { color: '#00d4aa', width: 2 },
                itemStyle: { color: '#00d4aa' },
                areaStyle: { color: 'rgba(0,212,170,0.08)' },
            }],
        });
    }
}

function renderAgentBtTrades(trades) {
    if (!trades || !trades.length) {
        return '<div class="text-muted text-center" style="padding:18px">区间内未触发交易</div>';
    }
    const rows = trades.map(t => {
        const cls = t.side === 'buy' ? 'up' : 'down';
        const side = t.side === 'buy' ? '买入' : '卖出';
        const pnl = (t.pnl != null)
            ? `<td class="num ${t.pnl >= 0 ? 'up' : 'down'}">${fmt.num(t.pnl, 2)}</td>`
            : '<td class="num">-</td>';
        return `<tr>
            <td>${escNews(t.date)}</td>
            <td class="${cls}">${side}</td>
            <td class="num">${fmt.num(t.price, 3)}</td>
            <td class="num">${fmt.num(t.quantity, 0)}</td>
            <td class="num">${fmt.num(t.amount, 2)}</td>
            <td class="num">${fmt.num(t.fee, 2)}</td>
            ${pnl}
        </tr>`;
    }).join('');
    return `<table><thead><tr>
        <th>日期</th><th>方向</th><th class="num">价格</th><th class="num">数量</th>
        <th class="num">金额</th><th class="num">手续费</th><th class="num">盈亏</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

// ============================================================
// ⭐ 自选池
// ============================================================
async function loadWatchlistGroups() {
    try {
        const data = await api.get('/api/v1/watchlist/groups');
        const html = (data || []).map(g =>
            `<div class="wl-group" data-group="${g.group_name}">${g.group_name}</div>`
        ).join('');
        $('#wl-groups-list').html(html || '<div class="text-muted" style="padding:12px">无分组</div>');
        $('.wl-group').on('click', function() {
            const g = $(this).data('group');
            state.watchlistGroup = g;
            $('.wl-group').removeClass('active');
            $(this).addClass('active');
            $('#wl-add-bar').show();
            loadWatchlistItems(g);
        });
    } catch (e) {}
}

async function loadWatchlistItems(group) {
    try {
        const data = await api.get(`/api/v1/watchlist/${group}`);
        const rows = data.items || data || [];
        const html = `<table>
            <thead><tr><th>代码</th><th>名称</th><th>备注</th><th>操作</th></tr></thead>
            <tbody>${rows.map(it => `
                <tr>
                    <td><code>${it.stock_code}</code></td>
                    <td>${it.short_name || ''}</td>
                    <td>${it.note || ''}</td>
                    <td><button class="del-wl" data-code="${it.stock_code}" class="danger">删除</button></td>
                </tr>
            `).join('')}</tbody></table>`;
        $('#wl-items').html(html || '<div class="text-muted text-center" style="padding:40px">空</div>');
        $('.del-wl').on('click', async function() {
            await api.del(`/api/v1/watchlist/${group}/${$(this).data('code')}`);
            await loadWatchlistItems(group);
        });
    } catch (e) {}
}

async function createGroup() {
    const name = $('#wl-new-group').val().trim();
    const desc = $('#wl-new-group-desc').val().trim();
    if (!name) return;
    try {
        await api.post('/api/v1/watchlist/groups', { group_name: name, description: desc });
        $('#wl-new-group').val(''); $('#wl-new-group-desc').val('');
        await loadWatchlistGroups();
    } catch (e) { alert(e.message); }
}

async function addToGroup() {
    if (!state.watchlistGroup) return;
    const stock = $('#wl-add-stock').val().trim();
    if (!stock) return;
    try {
        await api.post(`/api/v1/watchlist/${state.watchlistGroup}`, { stock_code: stock });
        $('#wl-add-stock').val('');
        await loadWatchlistItems(state.watchlistGroup);
    } catch (e) { alert(e.message); }
}

// ============================================================
// 💼 持仓
// ============================================================
async function loadPortfolio() {
    try {
        const data = await api.get('/api/v1/portfolio/snapshot');
        const rows = data.rows || [];
        const total = data.total || {};
        $('#pf-summary').html(`
            <div class="metric-card">
                <div class="metric-label">持仓数</div>
                <div class="metric-value">${total.position_count || 0}</div>
                <div class="metric-delta">${total.valued_count || 0} 个有当前价</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">成本总额</div>
                <div class="metric-value">¥${fmt.num(total.cost_total, 0)}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">当前市值</div>
                <div class="metric-value">¥${fmt.num(total.market_value, 0)}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">浮动盈亏</div>
                <div class="metric-value ${total.floating_pnl >= 0 ? 'up' : 'down'}">${fmt.pnl(total.floating_pnl)}</div>
                <div class="metric-delta ${total.return_pct >= 0 ? 'up' : 'down'}">${fmt.pct(total.return_pct * 100)}</div>
            </div>
        `);
        if (!rows.length) {
            $('#pf-table').html('<div class="text-muted text-center" style="padding:40px">无持仓 · 先在 Python 端录入</div>');
            return;
        }
        const html = `<table>
            <thead><tr><th>代码</th><th>名称</th><th class="num">成本</th><th class="num">数量</th><th class="num">现价</th><th class="num">市值</th><th class="num">盈亏</th><th class="num">收益率</th></tr></thead>
            <tbody>${rows.map(r => {
                const cls = r.floating_pnl >= 0 ? 'up' : 'down';
                return `<tr>
                    <td><code>${r.stock_code}</code></td>
                    <td>${r.short_name || ''}</td>
                    <td class="num">${fmt.num(r.cost_price, 3)}</td>
                    <td class="num">${r.quantity}</td>
                    <td class="num">${r.current_price === null ? '-' : fmt.num(r.current_price, 2)}</td>
                    <td class="num">${r.market_value === null ? '-' : fmt.num(r.market_value, 0)}</td>
                    <td class="num ${cls}">${r.floating_pnl === null ? '-' : fmt.pnl(r.floating_pnl)}</td>
                    <td class="num ${cls}">${r.return_pct === null ? '-' : fmt.pct(r.return_pct * 100)}</td>
                </tr>`;
            }).join('')}</tbody></table>`;
        $('#pf-table').html(html);
    } catch (e) {
        $('#pf-table').html(`<div class="err">${e.message}</div>`);
    }
}

async function syncPrices() {
    $('#pf-status').html('<span class="spinner"></span> 同步中...');
    try {
        const codes = $('code').map((i, el) => $(el).text()).get()
            .filter(c => /^\d{6}$/.test(c));
        const unique = [...new Set(codes)].slice(0, 20);
        const data = await api.post('/api/v1/portfolio/sync-prices', unique);
        $('#pf-status').text(`✅ ${data.n_updated} 只股票已更新`);
        await loadPortfolio();
    } catch (e) {
        $('#pf-status').text('❌ ' + e.message);
    }
}

// ============================================================
// 🔔 预警
// ============================================================
async function loadAlerts() {
    try {
        const data = await api.get('/api/v1/alerts/rules');
        const rules = data || [];
        $('#alert-rules-list').html(rules.length ? `<table>
            <thead><tr><th>名称</th><th>目标</th><th>指标</th><th>条件</th><th>阈值</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>${rules.map(r => `<tr>
                <td>${r.name}</td>
                <td>${r.target_value}</td>
                <td>${r.metric}</td>
                <td>${r.operator}</td>
                <td class="num">${r.threshold}</td>
                <td>${r.is_active ? '✅' : '⏸️'}</td>
                <td><button class="del-alert" data-id="${r.rule_id}" class="danger">删除</button></td>
            </tr>`).join('')}</tbody></table>` : '<div class="text-muted text-center" style="padding:40px">无规则</div>');

        $('.del-alert').on('click', async function() {
            await api.del(`/api/v1/alerts/rules/${$(this).data('id')}`);
            await loadAlerts();
        });

        const events = await api.get('/api/v1/alerts/events');
        $('#alert-events-list').html(events.length ? `<table>
            <thead><tr><th>时间</th><th>规则</th><th>股票</th><th>指标</th><th>值</th><th>消息</th></tr></thead>
            <tbody>${events.map(e => `<tr>
                <td>${e.triggered_at || ''}</td>
                <td>${e.rule_id}</td>
                <td><code>${e.stock_code}</code></td>
                <td>${e.metric}</td>
                <td class="num">${e.value}</td>
                <td>${e.message}</td>
            </tr>`).join('')}</tbody></table>` : '<div class="text-muted text-center" style="padding:40px">无触发事件</div>');
    } catch (e) {}
}

async function createAlert() {
    const name = $('#alert-name').val().trim();
    const target = $('#alert-target').val().trim();
    const metric = $('#alert-metric').val();
    const op = $('#alert-op').val();
    const threshold = parseFloat($('#alert-threshold').val());
    if (!name || !target || isNaN(threshold)) { alert('请填写完整'); return; }
    try {
        await api.post('/api/v1/alerts/rules', {
            name, target_type: 'stock', target_value: target,
            metric, operator: op, threshold,
        });
        $('#alert-name').val(''); $('#alert-target').val(''); $('#alert-threshold').val('');
        await loadAlerts();
    } catch (e) { alert(e.message); }
}

async function runAlerts() {
    $('#alert-run-status').html('<span class="spinner"></span> 评估中...');
    try {
        const events = await api.post('/api/v1/alerts/run', {});
        $('#alert-run-status').text(`✅ 触发 ${events.length} 个事件`);
        await loadAlerts();
    } catch (e) { $('#alert-run-status').text('❌ ' + e.message); }
}

// ============================================================
// 📔 笔记
// ============================================================
async function loadNotes() {
    try {
        const notes = await api.get('/api/v1/notes');
        const list = notes || [];
        $('#notes-list').html(list.length ? list.map(n => `
            <div class="note-item" data-id="${n.note_id}">
                <div>${n.title || '(无标题)'}</div>
                <div class="note-item-meta">${n.stock_code || '通用'} · ${n.updated_at ? n.updated_at.substring(0,16) : ''}</div>
            </div>
        `).join('') : '<div class="text-muted" style="padding:12px">无笔记</div>');
        $('.note-item').on('click', function() {
            const id = $(this).data('id');
            state.noteId = id;
            $('.note-item').removeClass('active');
            $(this).addClass('active');
            const n = list.find(x => x.note_id === id);
            if (n) {
                $('#note-stock').val(n.stock_code || '');
                $('#note-title').val(n.title || '');
                if (state.noteEditor) state.noteEditor.value(n.content_md || '');
                else $('#note-content').val(n.content_md || '');
            }
        });
    } catch (e) {}
}

async function newNote() {
    state.noteId = null;
    $('#note-stock').val('');
    $('#note-title').val('');
    if (state.noteEditor) state.noteEditor.value('');
    else $('#note-content').val('');
    $('.note-item').removeClass('active');
}

async function saveNote() {
    const stock = $('#note-stock').val().trim();
    const title = $('#note-title').val().trim();
    const content = state.noteEditor ? state.noteEditor.value() : $('#note-content').val();
    if (!title) { alert('请输入标题'); return; }
    try {
        if (state.noteId) {
            await api.put(`/api/v1/notes/${state.noteId}`, { stock_code: stock, title, content_md: content });
        } else {
            const r = await api.post('/api/v1/notes', { stock_code: stock, title, content_md: content });
            state.noteId = r.note_id;
        }
        $('#note-status').text('✅ 已保存');
        await loadNotes();
    } catch (e) { $('#note-status').text('❌ ' + e.message); }
}

async function deleteNote() {
    if (!state.noteId) return;
    if (!confirm('删除笔记？')) return;
    try {
        await api.del(`/api/v1/notes/${state.noteId}`);
        state.noteId = null;
        if (state.noteEditor) state.noteEditor.value('');
        else $('#note-content').val('');
        $('#note-title').val('');
        await loadNotes();
    } catch (e) { alert(e.message); }
}

async function syncNote() {
    if (!state.noteId) return;
    try {
        await api.post(`/api/v1/notes/${state.noteId}/sync-vector`, {});
        $('#note-status').text('✅ 已同步到向量库');
    } catch (e) { $('#note-status').text('❌ ' + e.message); }
}

// ============================================================
// 新闻聚合模块
// ============================================================
let newsPage = 1;

function escNews(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function loadNews() {
    newsPage = 1;
    $('#news-page').val(1);
    await renderNewsList();
}

async function renderNewsList() {
    const source = $('#news-source').val();
    const stock = $('#news-stock').val().trim();
    const tag = $('#news-tag').val().trim();
    const q = $('#news-q').val().trim();
    const params = new URLSearchParams({ limit: 30, page: newsPage });
    if (source) params.set('source', source);
    if (stock) params.set('stock_code', stock);
    if (tag) params.set('tag', tag);
    if (q) params.set('q', q);
    $('#news-status').text('加载中…');
    try {
        const data = await api.get(`/api/v1/ingest/news?${params.toString()}`);
        renderNewsCards(data, '#news-list');
        $('#news-status').text(`第 ${newsPage} 页 · 本页 ${data.length} 条`);
    } catch (e) {
        $('#news-status').text('加载失败：' + e.message);
    }
}

function renderNewsCards(items, container) {
    const $c = $(container);
    if (!items || items.length === 0) {
        $c.html('<div class="news-empty">暂无新闻。点击「抓取最新」聚合抓取，或调整筛选条件。</div>');
        return;
    }
    $c.html(items.map(newsCardHtml).join(''));
    $c.find('.news-ai-btn').off('click').on('click', function () {
        const docId = $(this).data('doc');
        const $ai = $(this).closest('.news-card').find('.news-ai');
        if ($ai.data('loaded')) { $ai.toggle(); return; }
        newsAnalyze(docId, $ai, $(this));
    });
}

const SRC_LABELS = { kr36: '36氪', eastmoney: '东方财富', sina: '新浪财经', stcn: '证券时报', rss: '自定义RSS' };

function newsCardHtml(n) {
    const src = (n.source || 'unknown').toLowerCase().replace(/[^a-z0-9]/g, '');
    const srcLabel = SRC_LABELS[n.source] || n.source || '未知';
    const badge = `<span class="source-badge ${src}">${escNews(srcLabel)}</span>`;
    const stockTag = n.stock_code ? `<span class="stock-tag">${escNews(n.stock_code)}</span>` : '';
    const tag = n.tag ? `<span>🏷️ ${escNews(n.tag)}</span>` : '';
    // 有精确时间则显示到秒，否则回退到日期
    const when = n.publish_time ? String(n.publish_time).slice(0, 19) : (n.publish_date || '');
    const date = when ? `<span>🕒 ${escNews(when)}</span>` : '';
    const title = n.url
        ? `<a class="news-title" href="${escNews(n.url)}" target="_blank" rel="noopener">${escNews(n.title)}</a>`
        : `<span class="news-title">${escNews(n.title)}</span>`;
    // 摘要与标题重复时不渲染那行（部分来源无独立摘要）
    const norm = s => (s || '').replace(/\s+/g, '');
    const summary = (n.summary || '').trim();
    const showSummary = summary && norm(summary) !== norm(n.title);
    return `<div class="news-card" data-doc="${escNews(n.doc_id)}">
      <div class="news-head">${badge}${title}</div>
      ${showSummary ? `<div class="news-summary">${escNews(summary)}</div>` : ''}
      <div class="news-meta">${stockTag}${tag}${date}</div>
      <div class="news-actions">
        <button class="news-ai-btn" data-doc="${escNews(n.doc_id)}">🤖 AI 解读</button>
      </div>
      <div class="news-ai" style="display:none"></div>
    </div>`;
}

async function newsAnalyze(docId, $ai, $btn, force) {
    $btn.prop('disabled', true).text('分析中…');
    try {
        const res = await api.post('/api/v1/news/analyze', { doc_id: docId, force: !!force });
        if (res.error) throw new Error(res.error);
        renderNewsAi($ai, res, docId);
        $ai.data('loaded', true).show();
    } catch (e) {
        $ai.html(`<div class="text-xs" style="color:var(--danger,#e5484d)">分析失败：${escNews(e.message)}</div>`).show();
    } finally {
        $btn.prop('disabled', false).text('🤖 AI 解读');
    }
}

function renderNewsAi($ai, res, docId) {
    const sent = res.sentiment || '中性';
    const conf = res.confidence || '';
    const summary = res.summary || '';
    const pts = Array.isArray(res.key_points) ? res.key_points : [];
    const impact = res.impact || '';
    const risk = res.risk || '';
    const watch = res.watch || '';
    const confTag = conf ? `<span class="text-xs text-muted">置信度 ${escNews(conf)}</span>` : '';
    const cachedTag = res.cached ? ' <span class="text-xs text-muted">（缓存）</span>' : '';
    const ptsHtml = pts.length
        ? `<div style="margin-top:8px"><span class="ai-label">要点</span><ul class="ai-points">${pts.map(p => `<li>${escNews(p)}</li>`).join('')}</ul></div>`
        : '';
    const row = (label, text) =>
        text ? `<div class="ai-impact"><span class="ai-label">${label}</span>${escNews(text)}</div>` : '';
    $ai.html(
        `<div><span class="ai-label">情绪</span><span class="sentiment-pill ${sent}">${escNews(sent)}</span> ${confTag}${cachedTag}` +
        `<a class="ai-refresh" href="javascript:void(0)" data-doc="${escNews(docId)}">重新分析</a></div>` +
        (summary ? `<div style="margin-top:6px"><span class="ai-label">摘要</span>${escNews(summary)}</div>` : '') +
        ptsHtml +
        row('影响', impact) +
        row('风险', risk) +
        row('关注', watch) +
        `<div class="text-xs text-muted" style="margin-top:6px">⚠️ AI 观点仅供参考，不构成投资建议</div>`
    );
    $ai.find('.ai-refresh').off('click').on('click', function () {
        const $cardBtn = $(this).closest('.news-card').find('.news-ai-btn');
        newsAnalyze($(this).data('doc'), $ai, $cardBtn, true);
    });
}

async function loadStockNews(code) {
    if (!code) return;
    const $c = $('#stock-news');
    $c.html('<div class="news-empty">加载中…</div>');
    try {
        const data = await api.get(`/api/v1/stock/${code}/news?limit=30`);
        renderNewsCards(data, '#stock-news');
    } catch (e) {
        $c.html('<div class="news-empty">相关新闻加载失败：' + e.message + '</div>');
    }
}

// ============================================================
// 初始化
// ============================================================
$(function() {
    // 导航
    $('.nav-item').on('click', function() { switchPanel($(this).data('panel')); });

    // 笔记：EasyMDE 富编辑器（Markdown + KaTeX 公式预览）
    if (window.EasyMDE) {
        state.noteEditor = new EasyMDE({
            element: document.getElementById('note-content'),
            spellChecker: false,
            autosave: { enabled: false },
            toolbar: ['bold', 'italic', 'heading', '|', 'quote', 'code', 'table',
                      '|', 'unordered-list', 'ordered-list', '|', 'link', 'preview', 'side-by-side', 'fullscreen'],
            previewRender: function (text, preview) {
                const html = window.marked ? marked.parse(text) : text;
                if (window.renderMathInElement && preview) {
                    setTimeout(() => renderMathInElement(preview, {
                        delimiters: [
                            { left: '$$', right: '$$', display: true },
                            { left: '$', right: '$', display: false },
                        ],
                    }), 0);
                }
                return html;
            },
        });
    }

    // 全市场快照
    $('#snap-search, #snap-exchange, #snap-sort, #snap-order').on('change keyup', function() {
        snapPage = 1; $('#snap-page').val(1); loadSnapRows();
    });
    // 快照搜索自动联想：选中后填入代码并筛选
    enableStockSuggest('#snap-search', s => {
        $('#snap-search').val(s.stock_code);
        snapPage = 1; $('#snap-page').val(1); loadSnapRows();
    });
    $('#snap-prev').on('click', () => { if (snapPage > 1) { snapPage--; $('#snap-page').val(snapPage); loadSnapRows(); }});
    $('#snap-next').on('click', () => { snapPage++; $('#snap-page').val(snapPage); loadSnapRows(); });

    // 个股
    $('#stock-go').on('click', () => loadStock($('#stock-input').val().trim()));
    $('#stock-input').on('keypress', e => { if (e.which === 13) loadStock($('#stock-input').val().trim()); });
    // 股票搜索自动联想：选中后填入代码
    enableStockSuggest('#stock-input', s => { $('#stock-input').val(s.stock_code); loadStock(s.stock_code); });
    enableStockSuggest('#ind-stock', s => $('#ind-stock').val(s.stock_code));
    enableStockSuggest('#ai-stock', s => $('#ai-stock').val(s.stock_code));
    enableStockSuggest('#rag-stock', s => $('#rag-stock').val(s.stock_code));
    enableStockSuggest('#wl-add-stock', s => $('#wl-add-stock').val(s.stock_code));
    enableStockSuggest('#note-stock', s => $('#note-stock').val(s.stock_code));
    $('#stock-subtabs .subtab').on('click', function() {
        $('#stock-subtabs .subtab').removeClass('active');
        $(this).addClass('active');
        const sub = $(this).data('sub');
        const isK = sub === 'daily' || sub === 'weekly';
        $('#stock-range').toggle(isK || sub === 'factors');
        $('#minute-date').toggle(sub === 'minute');
        const isNews = sub === 'news';
        $('#stock-content').toggle(!isNews);
        $('#stock-news').toggle(isNews);
        if (sub === 'weekly') renderStockDaily('weekly');
        else if (isK) renderStockDaily('daily');
        else if (sub === 'minute') loadMinuteDates();
        else if (sub === 'factors') renderStockFactors();
        else if (sub === 'info') renderStockOverview();
        else if (isNews) loadStockNews(state.stockCode);
    });

    // 个股详情：日 K / 周 K 日期范围
    $('#stock-range').on('change', () => {
        const sub = $('#stock-subtabs .subtab.active').data('sub');
        if (sub === 'daily') renderStockDaily('daily');
        else if (sub === 'weekly') renderStockDaily('weekly');
        else if (sub === 'factors') renderStockFactors();
    });

    // 个股详情：5 分钟 K 选择其他天
    $('#minute-date').on('change', () => renderStockMinute());

    // 指标
    $('#ind-compute').on('click', computeIndicator);
    $('#ind-name').on('change', loadIndicatorParams);

    // SQL
    $('#sql-run').on('click', runSQL);
    $('#sql-clear').on('click', () => $('#sql-input').val(''));

    // 指数看板：子标签切换（日 K / 5分钟K）
    $('#index-subtabs .subtab').on('click', function() {
        if (!state.currentIndexCode) return;
        $('#index-subtabs .subtab').removeClass('active');
        $(this).addClass('active');
        const sub = $(this).data('sub');
        if (sub === 'daily') {
            $('#index-range').show();
            $('#index-minute-date').hide();
            renderIndexChart(state.currentIndexCode);
        } else if (sub === 'minute') {
            $('#index-range').hide();
            $('#index-minute-date').show();
            loadIndexMinuteDates();
        }
    });

    // 指数看板：日 K 日期范围筛选
    $('#index-range').on('change', () => {
        if (state.currentIndexCode && $('#index-subtabs .subtab.active').data('sub') === 'daily')
            renderIndexChart(state.currentIndexCode);
    });

    // 指数看板：5 分钟 K 选择其他天
    $('#index-minute-date').on('change', () => renderIndexMinute());

    // AI
    $('#ai-run').on('click', runAI);
    $('#ai-export-md').on('click', () => exportAI('md'));
    $('#ai-export-pdf').on('click', () => exportAI('pdf'));

    // RAG
    $('#rag-upload').on('click', uploadRag);
    $('#rag-ask').on('click', askRag);
    $('#rag-question').on('keypress', e => { if ((e.metaKey || e.ctrlKey) && e.which === 13) askRag(); });

    // 模块4 · 智能问答 Agent（对话式）
    $('#agent-ask').on('click', askAgent);
    $('#agent-clear').on('click', function () {
        if (state.agentHistory.length && !confirm('清空当前对话？')) return;
        agentReset();
    });
    // 点击推荐问题：填入输入框（不直接发送，留给用户修改后再发）
    $('#agent-suggest').on('click', '.suggest-chip', function () {
        if (state.agentThinking) return;
        const $q = $('#agent-q');
        $q.val($(this).data('q')).trigger('input').focus();
    });
    // Enter 发送 / Shift+Enter 换行；输入框自动增高
    $('#agent-q')
        .on('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault();
                askAgent();
            }
        })
        .on('input', function () {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 160) + 'px';
        });
    // 思考步骤折叠（事件委托，兼容多条消息）
    $('#agent-chat').on('click', '.chat-steps-toggle', function () {
        const $body = $('#' + $(this).data('target'));
        $body.toggle();
        const open = $body.is(':visible');
        $(this).text((open ? '▾' : '▸') + ' AI 思考步骤' +
            $(this).text().replace(/^[▸▾]\s*AI 思考步骤/, ''));
    });

    // 自选池
    $('#wl-create-group').on('click', createGroup);
    $('#wl-add-go').on('click', addToGroup);

    // 持仓
    $('#pf-sync-prices').on('click', syncPrices);

    // 预警
    $('#alert-create').on('click', createAlert);
    $('#alert-run').on('click', runAlerts);

    // 笔记
    $('#note-new').on('click', newNote);
    $('#note-save').on('click', saveNote);
    $('#note-sync').on('click', syncNote);
    $('#note-delete').on('click', deleteNote);

    // ===== 新闻聚合 =====
    $('#news-crawl').on('click', async function () {
        const $btn = $(this);
        $btn.prop('disabled', true).text('抓取中…');
        $('#news-status').text('正在聚合抓取多来源新闻…');
        try {
            const data = await api.post('/api/v1/ingest/news/crawl', { limit: 100 });
            const parts = Object.entries(data.per_source || {}).map(([k, v]) =>
                `${SRC_LABELS[k] || k} ${v.error ? '✗' : '+' + (v.fetched || 0)}`);
            $('#news-status').text(`抓取完成：${parts.join(' / ')} · 新增 ${data.added} 条`);
            newsPage = 1; $('#news-page').val(1);
            await renderNewsList();
        } catch (e) {
            $('#news-status').text('抓取失败：' + e.message);
        } finally {
            $btn.prop('disabled', false).text('🔄 抓取最新');
        }
    });
    $('#news-refresh').on('click', () => { newsPage = 1; $('#news-page').val(1); renderNewsList(); });
    $('#news-source, #news-stock, #news-tag, #news-q').on('change', () => {
        newsPage = 1; $('#news-page').val(1); renderNewsList();
    });
    $('#news-prev').on('click', () => { if (newsPage > 1) { newsPage--; $('#news-page').val(newsPage); renderNewsList(); } });
    $('#news-next').on('click', () => { newsPage++; $('#news-page').val(newsPage); renderNewsList(); });
    $('#news-page').on('change', () => {
        const p = parseInt($('#news-page').val(), 10);
        if (p >= 1) { newsPage = p; renderNewsList(); }
    });

    // ===== Phase 3: NL 自然语言策略回测 =====
    const BT = { chart: null };

    // 回测股票输入：与搜索栏同一套模糊匹配（中文 / 拼音首字母 / 代码）解析成 6 位代码；
    // 前端解析不到就原样提交，交给后端再解析（如 600519.SH 这类写法）
    async function resolveBtStock(raw) {
        if (/^\d{6}$/.test(raw)) return raw;
        try {
            const d = await api.get(`/api/stock/search?q=${encodeURIComponent(raw)}&limit=1`);
            const hit = (d.results || [])[0];
            if (hit) { $('#bt-stock').val(hit.stock_code); return hit.stock_code; }
        } catch (e) { /* 忽略，交给后端解析 */ }
        return raw;
    }

    async function runNlBacktest() {
        const stockRaw = $('#bt-stock').val().trim();
        const nl = $('#bt-nl').val().trim();
        if (!stockRaw) { $('#bt-status').text('请填写股票代码/名称'); return; }
        if (!nl) { $('#bt-status').text('请填写策略描述'); return; }
        const stock = await resolveBtStock(stockRaw);
        $('#bt-status').text('正在生成代码…');
        resetBtSteps();
        setBtStep('gen', 'active');
        $('#bt-steps').show();
        $('#bt-error').hide().text('');
        const payload = {
            natural_language: nl,
            stock_code: stock,
            days: parseInt($('#bt-days').val(), 10),
            fill_price: $('#bt-fill').val(),
            initial_cash: parseFloat($('#bt-cash').val()) || 100000,
            benchmark: $('#bt-benchmark').val() || null,
            save: $('#bt-save').is(':checked'),
        };
        let data;
        try {
            data = await api.post('/api/v1/backtest/nl/run', payload);
        } catch (e) {
            $('#bt-status').text('');
            $('#bt-error').show().text('请求失败：' + e.message);
            setBtStep('gen', 'fail');
            return;
        }
        $('#bt-status').text('');
        setBtStep('gen', 'done');
        setBtStep('ast', data.ast_passed ? 'done' : 'fail');
        setBtStep('exec', data.executed ? 'done' : 'fail');
        setBtStep('bt', (data.executed && data.metrics) ? 'done' : 'fail');
        $('#bt-code').text(data.generated_code || '（未生成代码）');
        renderBtRules(data);

        if (data.error) {
            let msg = data.error;
            if (data.suggestions && data.suggestions.length) msg += '\n建议：' + data.suggestions.join('；');
            if (data.sandbox_kind) msg += '\n[' + data.sandbox_kind + ']';
            // 清空上一次残留的结果，避免旧数字误导
            $('#bt-metrics').html('<div class="metric-card"><div class="metric-label">结果</div><div class="metric-value">未生成</div></div>');
            renderBtChart({ equity_curve: [], trades: [], kline: [], indicators: {} });
            $('#bt-trades').html('<div class="text-muted text-center" style="padding:20px">无交易记录</div>');
            $('#bt-error').show().text(msg);
            return;
        }
        $('#bt-error').hide();
        const trades = data.trades || [];
        renderBtMetrics(data.metrics, trades, data);
        renderBtChart(data);
        renderBtTrades(trades);
        let info = '';
        if (data.strategy_id) info = '已保存为策略 ' + data.strategy_id + (data.duplicate ? '（与已有策略重复，已去重）' : '');
        if (data.data_flag) info += (info ? '；' : '') + data.data_flag;
        $('#bt-strategy').text(info);
        loadSavedStrategies();
    }

    function resetBtSteps() { $('#bt-steps .bt-step').removeClass('active done fail'); }
    function setBtStep(step, cls) { $('#bt-steps .bt-step[data-step="' + step + '"]').addClass(cls); }

    // 买卖规则条：自然语言买/卖侧规则 + 引擎执行语义（T+1 成交），与回测实现同源
    function renderBtRules(data) {
        const r = data.rules || {};
        const ex = data.execution || {};
        const parts = [];
        if (r.buy) parts.push('<span style="color:#e05252">买入</span>：' + escHtml(r.buy));
        if (r.sell) parts.push('<span style="color:#2e9e5b">卖出</span>：' + escHtml(r.sell));
        if (!parts.length && r.expr_desc) parts.push('信号：' + escHtml(r.expr_desc));
        if (ex.buy && ex.sell) parts.push('<span class="text-muted">执行：' + escHtml(ex.buy) + '，' + escHtml(ex.sell) + '（按' + escHtml(ex.fill_price || '次日收盘价') + '成交）</span>');
        const $el = $('#bt-rules');
        if (parts.length) { $el.html(parts.join('<br>')).show(); } else { $el.hide(); }
    }
    function pct(x) { return x == null ? '-' : (x * 100).toFixed(2) + '%'; }
    function escHtml(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }

    function renderBtMetrics(m, trades, data) {
        if (!m || (m.total_return === undefined && (!trades || trades.length === 0))) {
            $('#bt-metrics').html('<div class="metric-card"><div class="metric-label">结果</div><div class="metric-value">无交易</div></div>');
            return;
        }
        // 基准：先看后端实际对标的指数，再看权益曲线里的基准序列拿基准区间收益
        const benchCode = (data || {}).benchmark;
        const benchVals = ((data || {}).equity_curve || [])
            .map(d => d.benchmark_value).filter(v => v != null);
        const benchTotal = benchVals.length > 1 ? benchVals[benchVals.length - 1] / benchVals[0] - 1 : null;
        let excessVal = '-', excessNote = '';
        if (!benchCode) {
            excessNote = '未启用基准';
        } else if (benchTotal == null) {
            excessNote = '基准数据缺失（' + benchCode + '）';
        } else {
            excessVal = m.excess_return == null ? '-' : pct(m.excess_return);
            excessNote = '基准 ' + pct(benchTotal) + (BENCH_NAMES[benchCode] ? '（' + BENCH_NAMES[benchCode] + '）' : '');
        }
        const items = [
            ['总收益', pct(m.total_return), ''],
            ['年化收益', pct(m.annual_return), '按 252 交易日复利折算'],
            ['最大回撤', pct(m.max_drawdown), ''],
            ['夏普比率', m.sharpe == null ? '-' : m.sharpe.toFixed(2), '扣无风险利率 2.5%'],
            ['胜率', m.win_rate == null ? '-' : pct(m.win_rate), '已平仓交易'],
            ['超额收益', excessVal, excessNote],
            ['交易次数', trades ? trades.length : (m.trade_count != null ? m.trade_count : '-'), ''],
        ];
        $('#bt-metrics').html(items.map(([k, v, note]) =>
            `<div class="metric-card"><div class="metric-label">${k}</div><div class="metric-value">${v}</div>`
            + (note ? `<div class="metric-note">${escHtml(note)}</div>` : '') + '</div>'
        ).join(''));
    }

    // 基准指数代码 → 中文名（后端 /backtest/benchmarks 会覆盖）
    const BENCH_NAMES = { sh000300: '沪深300', sh000001: '上证指数', sh000905: '中证500' };

    async function loadBenchmarks() {
        try {
            const d = await api.get('/api/v1/backtest/benchmarks');
            (d.benchmarks || []).forEach(b => { BENCH_NAMES[b.code] = b.name; });
            const opts = (d.benchmarks || []).map(b =>
                `<option value="${escHtml(b.code)}"${b.default ? ' selected' : ''}>${escHtml(b.name)}（${escHtml(b.code)}）</option>`);
            opts.push('<option value="none">不对比</option>');
            $('#bt-benchmark').html(opts.join(''));
        } catch (e) {
            $('#bt-benchmark').html('<option value="">（基准列表加载失败，点击下拉重试）</option>');
        }
    }
    // 加载失败后点开下拉时自动重试一次
    $('#bt-benchmark').on('focus mousedown', function () {
        if (!$(this).find('option[value="sh000300"]').length && !$(this).data('retrying')) {
            $(this).data('retrying', true);
            loadBenchmarks().finally(() => $(this).removeData('retrying'));
        }
    });

    // 后端日期形如 2026-10-13 00:00:00，展示时统一截到日
    const btDate = s => String(s).slice(0, 10);
    const btTxText = t => `${t.side === 'buy' ? '买入' : '卖出'} ${fmt.num(t.quantity, 0)} 股 @ ${fmt.num(t.price, 3)}`;
    const btVol = v => {
        if (v == null) return '-';
        const n = Math.abs(v);
        if (n >= 1e8) return (v / 1e8).toFixed(2) + '亿';
        if (n >= 1e4) return (v / 1e4).toFixed(2) + '万';
        return fmt.num(v, 0);
    };
    const btWan = v => (v == null ? '' : (Math.abs(v) >= 1e4 ? (v / 1e4).toFixed(1) + '万' : fmt.num(v, 0)));
    // 提示框里的一笔成交：方向/价格 + 触发规则 + 判定日指标值
    const btTradeTip = t => {
        const head = `<span style="color:${t.side === 'buy' ? '#ff3b5c' : '#00d68f'};font-weight:600">`
            + `${btTxText(t)}${t.pnl != null ? `，盈亏 ${fmt.num(t.pnl, 2)}` : ''}</span>`;
        const rule = t.rule_text
            ? `规则：${t.rule_text}${t.rule_basis ? `（依据 ${t.rule_basis}）` : ''}`
            : '';
        const when = t.decide_date ? `判定日 ${btDate(t.decide_date)}` : '';
        const vals = Object.entries(t.indicator_values || {})
            .filter(([, v]) => v != null)
            .map(([k, v]) => `${k.toUpperCase()} ${fmt.num(v, 3)}`)
            .join('  ');
        return [head, [rule, when].filter(Boolean).join(' · '), vals].filter(Boolean).join('<br>');
    };

    function renderBtChart(data) {
        const el = document.getElementById('bt-chart');
        if (BT.chart) { BT.chart.dispose(); BT.chart = null; }
        const curve = (data && data.equity_curve) || [];
        const kline = (data && data.kline) || [];
        const trades = (data && data.trades) || [];
        const ind = (data && data.indicators) || {};
        if (!curve.length) {
            el.innerHTML = '<div class="text-muted text-center" style="padding:40px">无权益曲线（未触发交易）</div>';
            return;
        }
        BT.chart = echarts.init(el);

        const dates = curve.map(d => d.date);
        const idx = new Map(dates.map((d, i) => [d, i]));
        const UP = '#ff3b5c', DOWN = '#00d68f';      // 买=红，卖=绿（与站内涨跌配色一致）
        const tradesByDate = {};
        trades.forEach(t => { (tradesByDate[t.date] = tradesByDate[t.date] || []).push(t); });

        // 有 K 线：日K(+买卖点) / 成交量 / MACD / 权益 四个子图，共享日期轴，联动缩放
        if (kline.length === dates.length) {
            const candles = kline.map(k => [k.open, k.close, k.low, k.high]);
            const volumes = kline.map(k => ({
                value: k.volume == null ? null : k.volume,
                itemStyle: { color: k.close >= k.open ? UP : DOWN, opacity: 0.6 },
            }));
            const macdBar = (ind.hist || []).map(v => ({
                value: v,
                itemStyle: { color: (v == null ? 0 : v) >= 0 ? UP : DOWN, opacity: 0.75 },
            }));
            // 买卖点：三角标在成交价上下，并直接标出触发规则
            const buyPts = [], sellPts = [];
            trades.forEach(t => {
                const i = idx.get(t.date);
                if (i == null) return;
                const pt = {
                    value: [i, t.price],
                    label: { formatter: t.rule_short ? `买 ${t.rule_short}` : '买' },
                };
                if (t.side === 'buy') buyPts.push(pt);
                else {
                    pt.label = { formatter: t.rule_short ? `卖 ${t.rule_short}` : '卖' };
                    sellPts.push(pt);
                }
            });
            BT.chart.setOption({
                animation: false,
                legend: {
                    type: 'scroll', top: 0, textStyle: { fontSize: 11 },
                    data: ['日K', '买入', '卖出', '成交量', 'MACD柱', 'DIF', 'DEA', '策略权益', '基准'],
                },
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
                    formatter: ps => {
                        const d = ps && ps.length ? ps[0].axisValue : null;
                        const i = idx.get(d);
                        if (i == null) return '';
                        const k = kline[i] || {};
                        const rows = [`<b>${btDate(d)}</b>`];
                        rows.push(`开 ${k.open}  高 ${k.high}  低 ${k.low}  收 ${k.close}`);
                        if (k.volume != null) rows.push(`成交量 ${btVol(k.volume)}`);
                        if (ind.dif) rows.push(`DIF ${fmt.num(ind.dif[i], 3)}  DEA ${fmt.num((ind.dea || [])[i], 3)}  MACD柱 ${fmt.num((ind.hist || [])[i], 3)}`);
                        rows.push(`策略权益 ${fmt.num((curve[i] || {}).value, 2)}` +
                            (curve[i] && curve[i].benchmark_value != null ? `　基准 ${fmt.num(curve[i].benchmark_value, 2)}` : ''));
                        const ts = tradesByDate[d] || [];
                        if (ts.length) rows.push('─────');
                        ts.forEach(t => rows.push(btTradeTip(t)));
                        return rows.join('<br>');
                    },
                },
                axisPointer: { link: [{ xAxisIndex: 'all' }] },
                grid: [
                    { left: 66, right: 22, top: 30, height: '30%' },
                    { left: 66, right: 22, top: '38%', height: '11%' },
                    { left: 66, right: 22, top: '54%', height: '13%' },
                    { left: 66, right: 22, top: '72%', height: '17%' },
                ],
                xAxis: [
                    { type: 'category', gridIndex: 0, data: dates, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false } },
                    { type: 'category', gridIndex: 1, data: dates, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false } },
                    { type: 'category', gridIndex: 2, data: dates, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false } },
                    { type: 'category', gridIndex: 3, data: dates, boundaryGap: true, axisLabel: { fontSize: 10, formatter: btDate } },
                ],
                yAxis: [
                    { gridIndex: 0, scale: true, name: '股价', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
                    { gridIndex: 1, scale: true, name: '成交量', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 9, formatter: btVol }, splitNumber: 2 },
                    { gridIndex: 2, scale: true, name: 'MACD', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 9 }, splitNumber: 2 },
                    { gridIndex: 3, scale: true, name: '权益', nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10, formatter: btWan }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
                ],
                dataZoom: [
                    { type: 'inside', xAxisIndex: [0, 1, 2, 3] },
                    { type: 'slider', xAxisIndex: [0, 1, 2, 3], bottom: 4, height: 20, borderColor: 'transparent', textStyle: { fontSize: 9 } },
                ],
                series: [
                    {
                        name: '日K', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: candles,
                        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
                    },
                    {
                        name: '买入', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, data: buyPts, z: 10,
                        symbol: 'triangle', symbolSize: 9, symbolOffset: [0, 15], itemStyle: { color: UP },
                        label: { show: true, position: 'bottom', distance: 2, color: UP, fontSize: 10, fontWeight: 'bold' },
                        labelLayout: { hideOverlap: true },
                    },
                    {
                        name: '卖出', type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, data: sellPts, z: 10,
                        symbol: 'triangle', symbolRotate: 180, symbolSize: 9, symbolOffset: [0, -15], itemStyle: { color: DOWN },
                        label: { show: true, position: 'top', distance: 2, color: DOWN, fontSize: 10, fontWeight: 'bold' },
                        labelLayout: { hideOverlap: true },
                    },
                    { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes, barWidth: '60%' },
                    {
                        name: 'MACD柱', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, data: macdBar, barWidth: '60%',
                        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: 'rgba(255,255,255,0.25)' }, data: [{ yAxis: 0 }] },
                    },
                    { name: 'DIF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: ind.dif || [], showSymbol: false, lineStyle: { color: '#faad14', width: 1.4 }, itemStyle: { color: '#faad14' } },
                    { name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: ind.dea || [], showSymbol: false, lineStyle: { color: '#40a9ff', width: 1.4 }, itemStyle: { color: '#40a9ff' } },
                    {
                        name: '策略权益', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: curve.map(d => d.value),
                        showSymbol: false, lineStyle: { color: '#faad14', width: 2 }, itemStyle: { color: '#faad14' },
                        // 权益图上用竖线标出每个买卖发生的交易日
                        markLine: {
                            silent: true, symbol: 'none', label: { show: false },
                            data: trades.filter(t => idx.has(t.date)).map(t => ({
                                xAxis: idx.get(t.date),
                                lineStyle: { color: t.side === 'buy' ? UP : DOWN, type: 'dashed', width: 1, opacity: 0.5 },
                            })),
                        },
                    },
                    {
                        name: '基准', type: 'line', xAxisIndex: 3, yAxisIndex: 3,
                        data: curve.map(d => d.benchmark_value == null ? null : d.benchmark_value),
                        showSymbol: false, lineStyle: { color: '#8c8c8c', width: 1.5, type: 'dashed' }, itemStyle: { color: '#8c8c8c' },
                    },
                ],
            });
            return;
        }
        // 无 K 线数据（如行情缺失）时的兜底：只画权益曲线，但仍标注买卖点
        const vals = curve.map(d => d.value);
        const bench = curve.map(d => d.benchmark_value == null ? null : d.benchmark_value);
        const marks = [];
        trades.forEach(t => {
            const i = idx.get(t.date);
            if (i == null) return;
            marks.push({
                coord: [i, vals[i]],
                value: t.side === 'buy' ? '买' : '卖',
                itemStyle: { color: t.side === 'buy' ? UP : DOWN },
            });
        });
        BT.chart.setOption({
            tooltip: {
                trigger: 'axis',
                formatter: ps => {
                    if (!ps || !ps.length) return '';
                    const d = ps[0].axisValue;
                    const rows = [`<b>${btDate(d)}</b>`];
                    ps.forEach(p => { if (p.value != null) rows.push(`${p.seriesName} ${fmt.num(p.value, 2)}`); });
                    (tradesByDate[d] || []).forEach(t => rows.push(btTradeTip(t)));
                    return rows.join('<br>');
                },
            },
            legend: { data: ['策略权益', '基准', '买入', '卖出'], top: 0, textStyle: { fontSize: 11 } },
            grid: { left: 62, right: 18, top: 30, bottom: 42 },
            xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, formatter: btDate } },
            yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 10 } },
            dataZoom: [
                { type: 'inside' },
                { type: 'slider', bottom: 2, height: 16, borderColor: 'transparent', textStyle: { fontSize: 9 } },
            ],
            series: [
                {
                    name: '策略权益', type: 'line', data: vals, showSymbol: false,
                    lineStyle: { color: '#faad14', width: 2 }, itemStyle: { color: '#faad14' },
                    markPoint: {
                        symbol: 'pin', symbolSize: 34, label: { fontSize: 10, color: '#0b1220', fontWeight: 'bold' },
                        data: marks,
                    },
                },
                { name: '基准', type: 'line', data: bench, showSymbol: false, lineStyle: { color: '#8c8c8c', width: 1.5, type: 'dashed' }, itemStyle: { color: '#8c8c8c' } },
            ],
        });
    }

    function renderBtTrades(trades) {
        if (!trades || !trades.length) {
            $('#bt-trades').html('<div class="text-muted text-center" style="padding:24px">该策略在区间内未触发交易</div>');
            return;
        }
        const rows = trades.map(t => {
            const cls = t.side === 'buy' ? 'up' : 'down';
            const side = t.side === 'buy' ? '买入' : '卖出';
            const pnl = t.pnl != null
                ? `<td class="num ${t.pnl >= 0 ? 'up' : 'down'}">${fmt.num(t.pnl, 2)}</td>`
                : '<td class="num">-</td>';
            // 触发规则：策略描述给出的可读规则 + 生成代码里的量化依据（含判定日指标值）
            const vals = Object.entries(t.indicator_values || {})
                .filter(([, v]) => v != null)
                .map(([k, v]) => `${k.toUpperCase()} ${fmt.num(v, 3)}`)
                .join('  ');
            const detail = [t.rule_basis ? `依据 ${t.rule_basis}` : '', vals].filter(Boolean).join(' · ');
            const rule = (t.rule_text ? escHtml(t.rule_text) : '-')
                + (detail ? `<br><span class="text-muted">${escHtml(detail)}</span>` : '');
            return `<tr>
                <td>${btDate(t.date)}</td>
                <td>${t.decide_date ? btDate(t.decide_date) : '-'}</td>
                <td class="${cls}">${side}</td>
                <td class="num">${fmt.num(t.price, 3)}</td>
                <td class="num">${fmt.num(t.quantity, 0)}</td>
                <td class="num">${fmt.num(t.amount, 2)}</td>
                <td class="num">${fmt.num(t.fee, 2)}</td>
                <td style="font-size:12px;min-width:220px">${rule}</td>
                ${pnl}
            </tr>`;
        }).join('');
        $('#bt-trades').html(`<table><thead><tr>
            <th>成交日</th><th>判定日</th><th>方向</th><th class="num">价格</th><th class="num">数量</th>
            <th class="num">金额</th><th class="num">手续费</th><th>触发规则</th><th class="num">盈亏</th>
        </tr></thead><tbody>${rows}</tbody></table>`);
    }

    async function loadSavedStrategies() {
        try {
            const data = await api.get('/api/v1/backtest/strategies?limit=20');
            const rows = (data.strategies || []).map(s => `<tr>
                <td><code>${s.strategy_id}</code></td>
                <td>${escHtml(s.name)}</td>
                <td>${s.source}</td>
                <td>${s.created_at}</td>
                <td><button class="ghost bt-view" data-id="${s.strategy_id}">查看代码</button></td>
            </tr>`).join('');
            $('#bt-saved').html(`<table><thead><tr>
                <th>ID</th><th>名称</th><th>来源</th><th>创建时间</th><th>操作</th>
            </tr></thead><tbody>${rows || '<tr><td colspan="5" class="text-muted text-center" style="padding:24px">暂无保存的策略</td></tr>'}</tbody></table>`);
            $('.bt-view').off('click').on('click', async function () {
                const id = $(this).data('id');
                try {
                    const d = await api.get('/api/v1/backtest/strategies/' + id);
                    $('#bt-code').text(d.definition || '（无代码）');
                    $('#bt-strategy').text('查看已保存策略 ' + id);
                } catch (e) { alert('加载失败：' + e.message); }
            });
        } catch (e) { /* 忽略 */ }
    }

    $('#bt-run').on('click', runNlBacktest);
    $('#bt-nl').on('keypress', e => { if (e.which === 13) runNlBacktest(); });
    loadBenchmarks();
    // 回测股票框自动联想：与全市场行情/个股搜索保持一致的模糊查询体验
    enableStockSuggest('#bt-stock', s => {
        $('#bt-stock').val(s.stock_code);
        $('#bt-status').text(`${s.stock_code} ${s.short_name || ''}`.trim());
    });
    $('#bt-copy').on('click', () => {
        const t = $('#bt-code').text();
        if (navigator.clipboard) navigator.clipboard.writeText(t);
        $('#bt-copy').text('已复制');
        setTimeout(() => $('#bt-copy').text('复制'), 1200);
    });
    $('.nav-item[data-panel="bt"]').on('click', loadSavedStrategies);

    // 启动
    loadLLMStatus();
    loadSnapshot();
    setInterval(loadLLMStatus, 30000);
});
