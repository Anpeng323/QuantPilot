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
        prompts: loadPrompts,
        watchlist: loadWatchlistGroups,
        portfolio: loadPortfolio,
        alerts: loadAlerts,
        notes: loadNotes,
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
    const dates = rows.map(r => r.trade_date);
    const closes = rows.map(r => r.close);
    const series = [{ name: '收盘', type: 'line', data: closes, smooth: true, showSymbol: false,
                      lineStyle: { color: '#9ba6b8', width: 1 } }];
    let title = name.toUpperCase();
    if (name === 'ma') {
        const period = params.period || 5;
        title = `MA(${period})`;
        series.push({
            name: title, type: 'line', data: rows.map(r => r[`ma${period}`]),
            smooth: true, showSymbol: false,
            lineStyle: { color: '#00d4aa', width: 2 },
        });
    } else if (name === 'macd') {
        series.push({ name: 'DIF', type: 'line', data: rows.map(r => r.dif),
            smooth: true, lineStyle: { color: '#00d4aa' } });
        series.push({ name: 'DEA', type: 'line', data: rows.map(r => r.dea),
            smooth: true, lineStyle: { color: '#ffb800' } });
    } else if (name === 'boll') {
        series.push({ name: '上轨', type: 'line', data: rows.map(r => r.boll_up),
            smooth: true, lineStyle: { color: 'rgba(255,59,92,0.5)' } });
        series.push({ name: '中轨', type: 'line', data: rows.map(r => r.boll_mid),
            smooth: true, lineStyle: { color: '#00d4aa' } });
        series.push({ name: '下轨', type: 'line', data: rows.map(r => r.boll_down),
            smooth: true, lineStyle: { color: 'rgba(0,214,143,0.5)' } });
    } else if (name === 'rsi') {
        const period = params.period || 14;
        series.length = 0;  // 不要收盘
        series.push({ name: `RSI(${period})`, type: 'line', data: rows.map(r => r[`rsi${period}`]),
            smooth: true, lineStyle: { color: '#00d4aa', width: 2 },
            markLine: { silent: true, data: [
                { yAxis: 70, lineStyle: { color: '#ff3b5c' }, label: { formatter: '超买 70' } },
                { yAxis: 30, lineStyle: { color: '#00d68f' }, label: { formatter: '超卖 30' } },
            ]}});
    } else if (name === 'kdj') {
        series.length = 0;
        series.push({ name: 'K', type: 'line', data: rows.map(r => r.kdj_k), smooth: true, showSymbol: false, lineStyle: { color: '#00d4aa', width: 2 } });
        series.push({ name: 'D', type: 'line', data: rows.map(r => r.kdj_d), smooth: true, showSymbol: false, lineStyle: { color: '#ffb800' } });
        series.push({ name: 'J', type: 'line', data: rows.map(r => r.kdj_j), smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c' } });
        title = 'KDJ';
    } else if (name === 'arbr') {
        series.length = 0;
        series.push({ name: 'AR', type: 'line', data: rows.map(r => r.ar), smooth: true, showSymbol: false, lineStyle: { color: '#00d4aa' } });
        series.push({ name: 'BR', type: 'line', data: rows.map(r => r.br), smooth: true, showSymbol: false, lineStyle: { color: '#ffb800' } });
        title = 'ARBR';
    } else if (name === 'adx') {
        series.length = 0;
        series.push({ name: 'ADX', type: 'line', data: rows.map(r => r.adx), smooth: true, showSymbol: false, lineStyle: { color: '#00d4aa', width: 2 } });
        series.push({ name: '+DI', type: 'line', data: rows.map(r => r.plus_di), smooth: true, showSymbol: false, lineStyle: { color: '#ffb800' } });
        series.push({ name: '-DI', type: 'line', data: rows.map(r => r.minus_di), smooth: true, showSymbol: false, lineStyle: { color: '#ff3b5c' } });
        title = 'ADX';
    } else if (name === 'stochrsi') {
        series.length = 0;
        series.push({ name: 'StochRSI', type: 'line', data: rows.map(r => r.stochrsi), smooth: true, showSymbol: false, lineStyle: { color: '#00d4aa', width: 2 } });
        series.push({ name: 'StochRSI %D', type: 'line', data: rows.map(r => r.stochrsi_d), smooth: true, showSymbol: false, lineStyle: { color: '#ffb800' } });
        title = 'StochRSI';
    } else {
        // 通用：画出所有非 OHLCV 列
        series.length = 0;
        const skip = new Set(['trade_date', 'open', 'high', 'low', 'close', 'volume']);
        Object.keys(rows[0] || {}).filter(c => !skip.has(c)).forEach(c => {
            series.push({ name: c, type: 'line', data: rows.map(r => r[c]), smooth: true, showSymbol: false });
        });
        title = name.toUpperCase();
    }
    state.indicatorChart.setOption({
        backgroundColor: 'transparent',
        title: { text: title, textStyle: { color: '#00d4aa', fontSize: 14 }, left: 10, top: 5 },
        grid: { left: 50, right: 30, top: 50, bottom: 50 },
        xAxis: { type: 'category', data: dates },
        yAxis: { type: 'value', scale: true },
        tooltip: { trigger: 'axis' },
        legend: { textStyle: { color: '#9ba6b8' }, top: 25 },
        series,
    });
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
    const type = $('#ai-type').val();
    if (!stock) { alert('请输入股票'); return; }
    $('#ai-status').html('<span class="spinner"></span> 分析中...');
    $('#ai-result').text('');
    $('#ai-exports').hide();
    try {
        const data = await api.post('/api/v1/ai/analyze', { stock_code: stock, analysis_type: type });
        $('#ai-result').html(md2html(data.response || data.analysis || ''));
        state.aiAnalysisId = data.analysis_id;
        if (state.aiAnalysisId) $('#ai-exports').show();
        const cost = data.cost_cny ? ` · ¥${data.cost_cny.toFixed(4)}` : '';
        $('#ai-status').html(`✅ 完成 · ${data.tokens || 0} tokens${cost}`);
    } catch (e) {
        $('#ai-status').text('❌ 失败');
        $('#ai-result').html(`<div class="err">${e.message}</div>`);
    }
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
        $('#rag-ask-status').text(`✅ ${data.tokens || 0} tokens`);
    } catch (e) {
        $('#rag-ask-status').text('❌');
        $('#rag-answer').html(`<div class="err">${e.message}</div>`);
    }
}

// ============================================================
// 💬 提示词库
// ============================================================
async function loadPrompts() {
    try {
        const data = await api.get('/api/v1/prompts');
        const html = `<table>
            <thead><tr><th>ID</th><th>名称</th><th>分类</th><th>变量</th><th>内置</th><th>操作</th></tr></thead>
            <tbody>${(data || []).map(p => `
                <tr>
                    <td><code>${p.template_id}</code></td>
                    <td>${p.name}</td>
                    <td><span class="info">${p.category}</span></td>
                    <td>${(p.variables || []).join(', ')}</td>
                    <td>${p.is_builtin ? '🔒' : '✏️'}</td>
                    <td>${!p.is_builtin ? `<button class="del-prompt" data-id="${p.template_id}" class="danger">删除</button>` : ''}</td>
                </tr>
            `).join('')}</tbody></table>`;
        $('#prompts-list').html(html);
        $('.del-prompt').on('click', async function() {
            const id = $(this).data('id');
            if (!confirm(`删除模板 ${id}？`)) return;
            await api.del(`/api/v1/prompts/${id}`);
            await loadPrompts();
        });
    } catch (e) {
        $('#prompts-list').html(`<div class="err">${e.message}</div>`);
    }
}

async function createPrompt() {
    const name = $('#prompt-name').val().trim();
    const category = $('#prompt-category').val().trim() || 'custom';
    const body = $('#prompt-body').val().trim();
    let variables = [];
    try {
        const v = $('#prompt-vars').val().trim();
        if (v) variables = JSON.parse(v);
    } catch (e) { alert('变量 JSON 错误'); return; }
    if (!name || !body) { alert('名称和正文必填'); return; }
    try {
        await api.post('/api/v1/prompts', { name, category, body, variables, is_builtin: false });
        await loadPrompts();
        $('#prompt-name').val(''); $('#prompt-body').val(''); $('#prompt-vars').val('');
    } catch (e) { alert(e.message); }
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
        const isDaily = sub === 'daily' || sub === 'weekly';
        $('#stock-range').toggle(isDaily);
        $('#minute-date').toggle(sub === 'minute');
        if (sub === 'weekly') renderStockDaily('weekly');
        else if (isDaily) renderStockDaily('daily');
        else if (sub === 'minute') loadMinuteDates();
        else if (sub === 'info') alert('概览数据待接入');
        else if (sub === 'factors') alert('因子数据请见 SQL');
    });

    // 个股详情：日 K / 周 K 日期范围
    $('#stock-range').on('change', () => {
        const sub = $('#stock-subtabs .subtab.active').data('sub');
        if (sub === 'daily') renderStockDaily('daily');
        else if (sub === 'weekly') renderStockDaily('weekly');
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

    // 提示词
    $('#prompt-create').on('click', createPrompt);

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

    // 启动
    loadLLMStatus();
    loadSnapshot();
    setInterval(loadLLMStatus, 30000);
});
