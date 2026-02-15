"""Inline HTML/CSS/JS for the dashboard — no external files needed."""

HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi Trading Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'SF Mono',SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace;font-size:13px}
a{color:#58a6ff}
.header{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d}
.header h1{font-size:16px;font-weight:600;color:#f0f6fc}
.header .meta{display:flex;gap:16px;font-size:12px;color:#8b949e}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:middle}
.status-dot.live{background:#3fb950} .status-dot.disconnected{background:#f85149}
.tab-bar{display:flex;gap:0;background:#161b22;border-bottom:1px solid #30363d;padding:0 16px}
.tab-btn{padding:8px 20px;font-size:13px;font-weight:600;color:#8b949e;background:transparent;border:none;border-bottom:2px solid transparent;cursor:pointer;font-family:inherit;transition:color .2s,border-color .2s}
.tab-btn:hover{color:#c9d1d9}
.tab-btn.active{color:#f0f6fc;border-bottom-color:#58a6ff}
.tab-btn .tab-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px}
.tab-dot.btc{background:#f7931a} .tab-dot.eth{background:#627eea}

/* Summary bar */
.summary-bar{display:flex;gap:1px;background:#30363d;padding:1px}
.stat-card{flex:1;background:#0d1117;padding:14px 16px;text-align:center}
.stat-card .stat-value{font-size:26px;font-weight:700;color:#f0f6fc;font-variant-numeric:tabular-nums}
.stat-card .stat-label{font-size:11px;text-transform:uppercase;color:#8b949e;margin-top:4px;letter-spacing:0.5px}
.stat-value.pos{color:#3fb950} .stat-value.neg{color:#f85149} .stat-value.neutral{color:#8b949e}

.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:#30363d;padding:1px}
.grid>.panel{background:#0d1117;padding:12px}
.wide{grid-column:span 2} .full{grid-column:span 3}
.panel h2{font-size:11px;text-transform:uppercase;color:#8b949e;margin-bottom:8px;letter-spacing:0.5px}
.val{font-size:22px;font-weight:700;color:#f0f6fc}
.sub{font-size:11px;color:#8b949e;margin-top:2px}
.bar-row{display:flex;align-items:center;gap:6px;margin:3px 0}
.bar-label{width:48px;text-align:right;font-size:11px;color:#8b949e;flex-shrink:0}
.bar-track{flex:1;height:14px;background:#21262d;border-radius:3px;position:relative;overflow:hidden}
.bar-fill{height:100%;border-radius:3px;transition:width .4s ease}
.bar-val{position:absolute;right:4px;top:0;line-height:14px;font-size:10px;color:#c9d1d9}
.bar-fill.pos{background:#3fb950} .bar-fill.neg{background:#f85149} .bar-fill.neutral{background:#8b949e}
.edge-bar{height:20px;background:#21262d;border-radius:4px;position:relative;margin:8px 0}
.edge-fill{height:100%;border-radius:4px;transition:width .4s ease}
.edge-marker{position:absolute;top:-2px;bottom:-2px;width:2px;background:#f0f6fc;border-radius:1px}
.edge-label{font-size:11px;color:#8b949e;margin-top:2px}
.verdict{margin-top:6px;padding:6px 8px;border-radius:4px;font-size:12px;font-weight:600}
.verdict.trade{background:#1a3a1a;color:#3fb950;border:1px solid #238636}
.verdict.no-trade{background:#2a1a1a;color:#f85149;border:1px solid #da3633}
.verdict.no-market{background:#1a1a2a;color:#8b949e;border:1px solid #30363d}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #21262d}
.kv:last-child{border-bottom:none}
.kv .k{color:#8b949e} .kv .v{color:#c9d1d9;font-weight:500}
.feat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:4px}
.feat-cell{display:flex;justify-content:space-between;padding:3px 6px;background:#161b22;border-radius:3px;font-size:11px}
.feat-cell .fn{color:#8b949e} .feat-cell .fv{font-weight:600}
.fv.high-pos{color:#3fb950} .fv.low-pos{color:#56d364} .fv.neutral{color:#8b949e} .fv.low-neg{color:#ffa657} .fv.high-neg{color:#f85149}
#log{max-height:180px;overflow-y:auto;font-size:11px;line-height:1.8}
.log-entry{padding:2px 6px;border-radius:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-entry.trade-log{background:#1a3a1a;color:#3fb950}
.log-entry.reject-log{background:#2a1a1a;color:#f85149}
.log-entry.no-market-log{background:#161b22;color:#8b949e}
.pos-row{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-radius:4px;font-size:12px;background:#161b22;margin-bottom:4px}
.pos-row.pos-yes{border-left:3px solid #3fb950} .pos-row.pos-no{border-left:3px solid #f85149}
.pos-side{font-weight:700;font-size:11px;padding:2px 6px;border-radius:3px}
.pos-side.yes{background:#1a3a1a;color:#3fb950} .pos-side.no{background:#2a1a1a;color:#f85149}
.trade-hist-list{display:flex;flex-direction:column;gap:4px}
.trade-hist-row{display:flex;align-items:center;gap:8px;padding:5px 8px;background:#161b22;border-radius:4px;font-size:12px}
.trade-arrow{font-size:18px;font-weight:700;width:22px;text-align:center}
.trade-arrow.win{color:#3fb950} .trade-arrow.loss{color:#f85149}
.trade-pnl{font-weight:600;min-width:60px;text-align:right}
.trade-pnl.win{color:#3fb950} .trade-pnl.loss{color:#f85149}
.trade-meta{color:#8b949e;font-size:11px}
.trade-action{color:#c9d1d9;font-weight:500;text-transform:capitalize}
#reconnect-banner{display:none;position:fixed;top:0;left:0;right:0;background:#da3633;color:#fff;text-align:center;padding:6px;font-size:12px;font-weight:600;z-index:999}
.countdown{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.countdown.urgent{color:#f85149;animation:pulse 1s ease-in-out infinite}
.countdown.warning{color:#ffa657}
.countdown.ok{color:#3fb950}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.btc-ticker{display:flex;align-items:baseline;gap:8px}
.btc-ticker .val{font-variant-numeric:tabular-nums}
.btc-delta{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.btc-delta.up{color:#3fb950} .btc-delta.down{color:#f85149} .btc-delta.flat{color:#8b949e}
.btc-arrow{font-size:16px}
.stat-highlight{font-size:18px;font-weight:700}
.stat-highlight.win{color:#3fb950} .stat-highlight.loss{color:#f85149} .stat-highlight.neutral{color:#8b949e}

/* Collapsible features */
.collapsible-header{cursor:pointer;display:flex;align-items:center;gap:6px;user-select:none}
.collapsible-header .toggle-arrow{transition:transform .2s;font-size:10px;color:#8b949e}
.collapsible-header .toggle-arrow.open{transform:rotate(90deg)}
.collapsible-body{overflow:hidden;transition:max-height .3s ease;max-height:0}
.collapsible-body.open{max-height:600px}

/* Compact settlement badges */
.settle-inline{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.settle-badge{display:inline-flex;align-items:center;gap:3px;padding:2px 8px;background:#161b22;border-radius:3px;font-size:11px;white-space:nowrap}
.settle-badge.yes{color:#3fb950} .settle-badge.no{color:#f85149}

/* Highlighted positions panel */
#p-pos-risk{border:1px solid #30363d;border-left:3px solid #58a6ff}

/* Section divider within combined panel */
.panel-divider{border:none;border-top:1px solid #21262d;margin:8px 0}
</style>
</head>
<body>
<div id="reconnect-banner">Disconnected — reconnecting&hellip;</div>

<div class="header">
  <h1>KALSHI TRADING BOT</h1>
  <div class="meta">
    <span><span class="status-dot live" id="status-dot"></span><span id="conn-status">Live</span></span>
    <span>Cycle #<span id="cycle">0</span></span>
    <span>Uptime <span id="uptime">0s</span></span>
    <span>Mode: <span id="mode">--</span></span>
  </div>
</div>

<div class="tab-bar" id="tab-bar">
  <button class="tab-btn active" data-asset="BTC"><span class="tab-dot btc"></span>BTC</button>
  <button class="tab-btn" data-asset="ETH"><span class="tab-dot eth"></span>ETH</button>
</div>

<!-- Summary Bar -->
<div class="summary-bar">
  <div class="stat-card">
    <div class="stat-value" id="sum-balance">--</div>
    <div class="stat-label">Balance</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" id="sum-pnl">--</div>
    <div class="stat-label">Total P&amp;L</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" id="sum-trades">--</div>
    <div class="stat-label">Trades Today</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" id="sum-winrate">--</div>
    <div class="stat-label">Win Rate</div>
  </div>
  <div class="stat-card">
    <div class="stat-value neg" id="sum-fees">--</div>
    <div class="stat-label">Fees Paid</div>
  </div>
</div>

<div class="grid">
  <!-- Row 1: Market | Price | Prediction -->
  <div class="panel" id="p-market">
    <h2>Market</h2>
    <div class="val" id="market-ticker">--</div>
    <div class="sub" id="market-title" style="color:#f0f6fc;font-size:13px;font-weight:600;margin:4px 0">--</div>
    <div class="countdown ok" id="market-countdown">--:--</div>
    <div class="sub" id="market-volume">--</div>
  </div>

  <div class="panel" id="p-btc">
    <h2 id="price-header">BTC Prices</h2>
    <div class="btc-ticker">
      <div class="val" id="btc-price">--</div>
      <span class="btc-arrow" id="btc-arrow"></span>
    </div>
    <div class="btc-delta" id="btc-delta"></div>
    <div style="margin-top:6px;border-top:1px solid #21262d;padding-top:6px">
      <div class="kv"><span class="k">Coinbase</span><span class="v" id="price-coinbase" style="color:#58a6ff">--</span></div>
      <div class="kv"><span class="k">Kraken</span><span class="v" id="price-binance" style="color:#f0e68c">--</span></div>
      <div class="kv"><span class="k">Kalshi Strike</span><span class="v" id="price-kalshi" style="color:#d2a8ff">--</span></div>
    </div>
    <div style="margin-top:4px;border-top:1px solid #21262d;padding-top:4px">
      <div class="kv"><span class="k">X-Spread</span><span class="v" id="cross-spread">--</span></div>
      <div class="kv"><span class="k">Lead signal</span><span class="v" id="cross-lead">--</span></div>
      <div class="kv"><span class="k">Implied P(YES)</span><span class="v" id="btc-implied">--</span></div>
    </div>
  </div>

  <div class="panel" id="p-prediction">
    <h2>Prediction</h2>
    <div class="val" id="pred-prob">--</div>
    <div class="sub" id="pred-conf">Confidence: --</div>
    <div style="margin-top:8px" id="signal-bars"></div>
  </div>

  <!-- Row 2: Edge Analysis (wide) | Positions + Risk (1 col) -->
  <div class="panel wide" id="p-edge">
    <h2>Edge Analysis</h2>
    <div style="display:flex;gap:24px">
      <div style="flex:1">
        <div class="kv"><span class="k">Side</span><span class="v" id="edge-side">--</span></div>
        <div class="kv"><span class="k">Raw edge</span><span class="v" id="edge-raw">--</span></div>
        <div class="kv"><span class="k">Fee drag</span><span class="v" id="edge-fee">--</span></div>
        <div class="kv"><span class="k">Net edge</span><span class="v" id="edge-net">--</span></div>
        <div class="kv"><span class="k">Threshold</span><span class="v" id="edge-thresh">--</span></div>
      </div>
      <div style="flex:1">
        <div class="edge-label">Net Edge vs Threshold</div>
        <div class="edge-bar">
          <div class="edge-fill" id="edge-fill" style="width:0;background:#3fb950"></div>
          <div class="edge-marker" id="edge-marker" style="left:0"></div>
        </div>
        <div id="edge-verdict" class="verdict no-market">Waiting for data&hellip;</div>
        <div style="margin-top:8px">
          <div class="kv"><span class="k">YES bid</span><span class="v" id="ob-yes-bid">--</span></div>
          <div class="kv"><span class="k">NO bid</span><span class="v" id="ob-no-bid">--</span></div>
          <div class="kv"><span class="k">Spread</span><span class="v" id="ob-spread">--</span></div>
          <div class="kv"><span class="k">Fair value</span><span class="v" id="ob-fair-value" style="color:#58a6ff">--</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="panel" id="p-pos-risk">
    <h2>Positions &amp; Risk</h2>
    <div id="positions-list"><span class="sub">No open positions</span></div>
    <hr class="panel-divider">
    <div class="kv"><span class="k">Exposure</span><span class="v" id="risk-exposure">--</span></div>
    <div class="kv"><span class="k">Vol regime</span><span class="v" id="risk-vol">--</span></div>
    <div class="kv"><span class="k">Consec. wins</span><span class="v" id="risk-wins">--</span></div>
    <div class="kv"><span class="k">Consec. losses</span><span class="v" id="risk-losses">--</span></div>
    <div class="kv"><span class="k">Last P&amp;L</span><span class="v" id="risk-last-pnl">--</span></div>
    <div style="margin-top:6px;border-top:1px solid #21262d;padding-top:4px">
      <div class="kv"><span class="k">YES depth</span><span class="v" id="ob-yes-depth">--</span></div>
      <div class="kv"><span class="k">NO depth</span><span class="v" id="ob-no-depth">--</span></div>
      <div class="kv"><span class="k">OB implied</span><span class="v" id="ob-implied">--</span></div>
      <div class="kv"><span class="k">Strike</span><span class="v" id="ob-strike">--</span></div>
    </div>
    <div style="margin-top:4px;border-top:1px solid #21262d;padding-top:4px">
      <div class="kv"><span class="k">Taker Buy</span><span class="v" id="taker-buy" style="color:#3fb950">--</span></div>
      <div class="kv"><span class="k">Taker Sell</span><span class="v" id="taker-sell" style="color:#f85149">--</span></div>
    </div>
  </div>

  <!-- Row 3: Settlements (full width, compact inline) -->
  <div class="panel full" id="p-settlements">
    <h2>Kalshi Settlements</h2>
    <div style="display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;align-items:center;gap:8px">
        <span class="tab-dot btc" style="flex-shrink:0"></span>
        <span style="font-weight:600;color:#f0f6fc;width:28px;flex-shrink:0">BTC</span>
        <div id="settle-hist-BTC" class="settle-inline"><span class="sub">Loading&hellip;</span></div>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="tab-dot eth" style="flex-shrink:0"></span>
        <span style="font-weight:600;color:#f0f6fc;width:28px;flex-shrink:0">ETH</span>
        <div id="settle-hist-ETH" class="settle-inline"><span class="sub">Loading&hellip;</span></div>
      </div>
    </div>
  </div>

  <!-- Row 4: Recent Trades (full width) -->
  <div class="panel full" id="p-trade-history">
    <h2>Recent Trades</h2>
    <div style="display:flex;gap:32px">
      <div style="flex:1">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
          <span class="tab-dot btc"></span><span style="font-weight:600;color:#f0f6fc">BTC</span>
        </div>
        <div id="trade-hist-BTC" class="trade-hist-list"><span class="sub">No trades yet</span></div>
      </div>
      <div style="width:1px;background:#30363d"></div>
      <div style="flex:1">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
          <span class="tab-dot eth"></span><span style="font-weight:600;color:#f0f6fc">ETH</span>
        </div>
        <div id="trade-hist-ETH" class="trade-hist-list"><span class="sub">No trades yet</span></div>
      </div>
    </div>
  </div>

  <!-- Row 5: Features (collapsible, collapsed by default) -->
  <div class="panel full" id="p-features">
    <div class="collapsible-header" id="features-toggle">
      <span class="toggle-arrow" id="features-arrow">&#9654;</span>
      <h2 style="margin-bottom:0">Features</h2>
    </div>
    <div class="collapsible-body" id="features-body">
      <div class="feat-grid" id="feat-grid" style="margin-top:8px"></div>
    </div>
  </div>

  <!-- Row 6: Decision log (full width, last 15) -->
  <div class="panel full" id="p-log">
    <h2>Decision Log</h2>
    <div id="log"></div>
  </div>
</div>

<script>
(function(){
  const $ = id => document.getElementById(id);

  // Active asset tab
  let activeAsset = 'BTC';
  // Per-asset price tracking for delta display
  const priceState = {};  // { BTC: {prev, first}, ETH: {prev, first} }
  // Latest full state for re-render on tab switch
  let latestState = null;

  // Tab click handlers
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeAsset = btn.dataset.asset;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (latestState) render(latestState);
    });
  });

  // Features collapsible toggle
  let featuresOpen = false;
  $('features-toggle').addEventListener('click', () => {
    featuresOpen = !featuresOpen;
    $('features-body').classList.toggle('open', featuresOpen);
    $('features-arrow').classList.toggle('open', featuresOpen);
  });

  let evtSource = null;
  let reconnectTimer = null;

  function connect() {
    if (evtSource) { try { evtSource.close(); } catch(e){} }
    evtSource = new EventSource('/events');
    evtSource.onopen = () => {
      $('reconnect-banner').style.display = 'none';
      $('status-dot').className = 'status-dot live';
      $('conn-status').textContent = 'Live';
    };
    evtSource.onmessage = e => {
      try {
        const data = JSON.parse(e.data);
        latestState = data;
        render(data);
      } catch(err) { console.error('render error', err); }
    };
    evtSource.onerror = () => {
      $('reconnect-banner').style.display = 'block';
      $('status-dot').className = 'status-dot disconnected';
      $('conn-status').textContent = 'Disconnected';
      evtSource.close();
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, 3000);
    };
  }

  function render(s) {
    // Header
    $('cycle').textContent = s.cycle || 0;
    $('uptime').textContent = fmtDuration(s.uptime_seconds || 0);
    $('mode').textContent = s.mode || '--';

    // Auto-detect available assets from per_asset keys and add tabs dynamically
    const pa = s.per_asset || {};
    updateTabBar(Object.keys(pa));

    // Pick per-asset data for the active tab
    const assetData = pa[activeAsset] || {};
    const m = assetData.market || s.market || {};
    const snap = assetData.snapshot || s.snapshot || {};
    const pred = assetData.prediction || s.prediction || {};
    const edge = assetData.edge || s.edge || {};
    const feats = assetData.features || s.features || {};

    // --- Summary Bar (shared data from risk) ---
    const risk = s.risk || {};
    renderSummaryBar(risk);

    // Update price panel header
    $('price-header').textContent = activeAsset + ' Prices';

    // Market
    $('market-ticker').textContent = m.ticker || '--';
    $('market-title').textContent = m.yes_sub_title || m.title || '--';
    $('market-volume').textContent = m.volume != null ? 'Vol: ' + fmtVol(m.volume) : '--';
    if (m.close_time) { window._closeTime = new Date(m.close_time).getTime(); }
    else { window._closeTime = null; }

    // Price with live ticker (per-asset tracking)
    const newPrice = snap.btc_price || null;
    if (!priceState[activeAsset]) priceState[activeAsset] = {prev: null, first: null};
    const ps = priceState[activeAsset];
    if (newPrice) {
      $('btc-price').textContent = '$' + Number(newPrice).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
      if (ps.prev != null && ps.prev !== newPrice) {
        const diff = newPrice - ps.prev;
        const pctChange = ((newPrice - ps.first) / ps.first * 100);
        const arrow = $('btc-arrow');
        const delta = $('btc-delta');
        if (diff > 0) {
          arrow.textContent = '\\u25B2'; arrow.className = 'btc-arrow'; arrow.style.color = '#3fb950';
          delta.className = 'btc-delta up';
        } else if (diff < 0) {
          arrow.textContent = '\\u25BC'; arrow.className = 'btc-arrow'; arrow.style.color = '#f85149';
          delta.className = 'btc-delta down';
        } else {
          arrow.textContent = ''; delta.className = 'btc-delta flat';
        }
        const sign = pctChange >= 0 ? '+' : '';
        delta.textContent = sign + pctChange.toFixed(3) + '% (' + (diff >= 0 ? '+' : '') + diff.toFixed(2) + ')';
      }
      if (!ps.first) ps.first = newPrice;
      ps.prev = newPrice;
    } else {
      $('btc-price').textContent = '--';
      $('btc-arrow').textContent = '';
      $('btc-delta').textContent = '';
    }

    // Three labeled prices
    const fmtUsd = v => '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    $('price-coinbase').textContent = newPrice ? fmtUsd(newPrice) : '--';
    $('price-binance').textContent = snap.binance_btc_price ? fmtUsd(snap.binance_btc_price) : '--';
    $('price-kalshi').textContent = snap.strike_price ? fmtUsd(snap.strike_price) : '--';
    $('btc-implied').textContent = pct(snap.implied_prob);
    if (snap.cross_exchange_spread != null) {
      const bps = (snap.cross_exchange_spread * 10000).toFixed(1);
      const el = $('cross-spread');
      el.textContent = bps + ' bps';
      el.style.color = snap.cross_exchange_spread > 0 ? '#3fb950' : snap.cross_exchange_spread < 0 ? '#f85149' : '#8b949e';
    } else { $('cross-spread').textContent = '--'; }
    if (snap.cross_exchange_lead != null) {
      const bps = (snap.cross_exchange_lead * 10000).toFixed(1);
      const el = $('cross-lead');
      el.textContent = bps + ' bps';
      el.style.color = snap.cross_exchange_lead > 0 ? '#3fb950' : snap.cross_exchange_lead < 0 ? '#f85149' : '#8b949e';
    } else { $('cross-lead').textContent = '--'; }

    // Prediction
    $('pred-prob').textContent = pred.probability != null ? 'P(YES) = ' + pct(pred.probability) : '--';
    $('pred-conf').textContent = 'Confidence: ' + pct(pred.confidence);
    renderSignalBars(pred.signals || {});

    // Edge
    $('edge-side').textContent = edge.side || '--';
    $('edge-raw').textContent = edge.raw_edge != null ? edge.raw_edge.toFixed(4) : '--';
    $('edge-fee').textContent = edge.fee_drag != null ? edge.fee_drag.toFixed(4) : '--';
    $('edge-net').textContent = edge.net_edge != null ? edge.net_edge.toFixed(4) : '--';
    $('edge-thresh').textContent = edge.min_threshold != null ? edge.min_threshold.toFixed(4) : '--';

    const maxScale = 0.10;
    const netE = edge.net_edge || 0;
    const thresh = edge.min_threshold || 0;
    const fillPct = Math.min(100, (netE / maxScale) * 100);
    const threshPct = Math.min(100, (thresh / maxScale) * 100);
    const ef = $('edge-fill');
    ef.style.width = fillPct + '%';
    ef.style.background = netE >= thresh && thresh > 0 ? '#3fb950' : '#f85149';
    $('edge-marker').style.left = threshPct + '%';

    const vd = $('edge-verdict');
    vd.textContent = edge.decision || 'Waiting for data...';
    if (edge.passed) { vd.className = 'verdict trade'; }
    else if (edge.decision) { vd.className = 'verdict no-trade'; }
    else { vd.className = 'verdict no-market'; }
    if (edge.using_fair_value) {
      $('edge-side').textContent = (edge.side || '--') + ' (FV)';
    }

    // Orderbook (now split between edge panel and pos-risk panel)
    const ob = snap.orderbook || {};
    $('ob-yes-bid').textContent = ob.best_yes_bid || '--';
    $('ob-no-bid').textContent = ob.best_no_bid || '--';
    $('ob-spread').textContent = ob.spread || '--';
    $('ob-yes-depth').textContent = ob.yes_depth != null ? ob.yes_depth : '--';
    $('ob-no-depth').textContent = ob.no_depth != null ? ob.no_depth : '--';
    $('ob-implied').textContent = pct(ob.implied_prob);
    $('ob-strike').textContent = snap.strike_price ? '$' + Number(snap.strike_price).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2}) : '--';
    const fv = $('ob-fair-value');
    if (snap.statistical_fair_value != null) {
      fv.textContent = pct(snap.statistical_fair_value);
      fv.style.color = '#58a6ff';
    } else {
      fv.textContent = '--';
      fv.style.color = '#8b949e';
    }

    const fmtUsdK = v => v != null ? '$' + (v >= 1000000 ? (v/1000000).toFixed(1) + 'M' : v >= 1000 ? (v/1000).toFixed(0) + 'K' : v.toFixed(0)) : '--';
    $('taker-buy').textContent = fmtUsdK(snap.taker_buy_volume);
    $('taker-sell').textContent = fmtUsdK(snap.taker_sell_volume);

    // Features
    const fg = $('feat-grid');
    fg.innerHTML = '';
    for (const [k,v] of Object.entries(feats)) {
      const cell = document.createElement('div');
      cell.className = 'feat-cell';
      const cls = valClass(v);
      cell.innerHTML = '<span class="fn">' + k + '</span><span class="fv ' + cls + '">' + fmtNum(v) + '</span>';
      fg.appendChild(cell);
    }

    // --- Shared sections (all assets) ---

    // Positions
    const positions = s.positions || [];
    const pl = $('positions-list');
    if (positions.length === 0) {
      pl.innerHTML = '<span class="sub">No open positions</span>';
    } else {
      pl.innerHTML = positions.map(p => {
        const isYes = p.side.toLowerCase() === 'yes';
        const rowCls = isYes ? 'pos-yes' : 'pos-no';
        const sideCls = isYes ? 'yes' : 'no';
        return '<div class="pos-row ' + rowCls + '">' +
          '<span style="color:#c9d1d9">' + p.ticker + '</span>' +
          '<span><span class="pos-side ' + sideCls + '">' + p.side.toUpperCase() + '</span> x' + p.count + ' @ ' + p.avg_price + '</span>' +
          '</div>';
      }).join('');
    }

    // Risk stats (combined panel, balance removed — in summary bar)
    $('risk-losses').textContent = risk.consecutive_losses != null ? risk.consecutive_losses : '--';
    $('risk-wins').textContent = risk.consecutive_wins != null ? risk.consecutive_wins : '--';
    $('risk-vol').textContent = risk.vol_regime || '--';
    $('risk-exposure').textContent = risk.exposure != null ? '$' + Number(risk.exposure).toFixed(2) : '--';

    const lp = $('risk-last-pnl');
    if (risk.last_pnl != null) {
      const sign = risk.last_pnl >= 0 ? '+' : '';
      lp.textContent = sign + '$' + Number(risk.last_pnl).toFixed(2);
      lp.style.color = risk.last_pnl >= 0 ? '#3fb950' : '#f85149';
    } else {
      lp.textContent = '--';
      lp.style.color = '#8b949e';
    }

    // Kalshi settlement history (compact inline badges)
    const settleHist = s.settlement_history || {};
    for (const asset of ['BTC', 'ETH']) {
      const el = $('settle-hist-' + asset);
      const markets = settleHist[asset] || [];
      if (markets.length === 0) {
        el.innerHTML = '<span class="sub">Loading&hellip;</span>';
      } else {
        el.innerHTML = markets.map(m => {
          const isYes = m.result === 'yes';
          const arrow = isYes ? '\\u25B2' : '\\u25BC';
          const cls = isYes ? 'yes' : 'no';
          const label = m.result.toUpperCase();
          const time = m.close_time ? new Date(m.close_time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
          return '<span class="settle-badge ' + cls + '">' + arrow + label + ' ' + time + '</span>';
        }).join('');
      }
    }

    // Trade history (last 5 per asset)
    const tradeHist = s.trade_history || {};
    for (const asset of ['BTC', 'ETH']) {
      const el = $('trade-hist-' + asset);
      const trades = tradeHist[asset] || [];
      if (trades.length === 0) {
        el.innerHTML = '<span class="sub">No trades yet</span>';
      } else {
        el.innerHTML = trades.slice().reverse().map(t => {
          const isWin = t.pnl >= 0;
          const arrow = isWin ? '\\u25B2' : '\\u25BC';
          const cls = isWin ? 'win' : 'loss';
          const sign = isWin ? '+' : '';
          const action = t.action.replace('_', ' ');
          return '<div class="trade-hist-row">' +
            '<span class="trade-arrow ' + cls + '">' + arrow + '</span>' +
            '<span class="trade-action">' + action + '</span>' +
            '<span style="color:#8b949e">' + t.side.toUpperCase() + '</span>' +
            '<span class="trade-pnl ' + cls + '">' + sign + '$' + t.pnl.toFixed(2) + '</span>' +
            '<span class="trade-meta">' + t.time + '</span>' +
            '</div>';
        }).join('');
      }
    }

    // Decision log (last 15 entries)
    const decisions = s.recent_decisions || [];
    const logDiv = $('log');
    logDiv.innerHTML = '';
    const logEntries = decisions.slice(-15);
    for (let i = logEntries.length - 1; i >= 0; i--) {
      const d = logEntries[i];
      const el = document.createElement('div');
      const cls = d.type === 'trade' ? 'trade-log' : d.type === 'no_market' ? 'no-market-log' : 'reject-log';
      el.className = 'log-entry ' + cls;
      el.textContent = d.time + '  #' + d.cycle + '  ' + d.summary;
      logDiv.appendChild(el);
    }
  }

  // Render summary bar stats
  function renderSummaryBar(risk) {
    const bal = $('sum-balance');
    if (risk.balance != null) {
      bal.textContent = '$' + Number(risk.balance).toFixed(2);
      bal.className = 'stat-value' + (risk.total_pnl > 0 ? ' pos' : risk.total_pnl < 0 ? ' neg' : '');
    } else {
      bal.textContent = '--';
      bal.className = 'stat-value neutral';
    }

    const pnl = $('sum-pnl');
    if (risk.total_pnl != null) {
      const sign = risk.total_pnl >= 0 ? '+' : '';
      pnl.textContent = sign + '$' + Number(risk.total_pnl).toFixed(2);
      pnl.className = 'stat-value' + (risk.total_pnl >= 0 ? ' pos' : ' neg');
    } else {
      pnl.textContent = '--';
      pnl.className = 'stat-value neutral';
    }

    const trades = $('sum-trades');
    trades.textContent = risk.trades_today != null ? risk.trades_today : '--';
    trades.className = 'stat-value';

    const wr = $('sum-winrate');
    if (risk.total_settled != null && risk.total_settled > 0) {
      wr.textContent = (risk.win_rate * 100).toFixed(1) + '%';
      wr.className = 'stat-value' + (risk.win_rate >= 0.5 ? ' pos' : ' neg');
    } else {
      wr.textContent = '--';
      wr.className = 'stat-value neutral';
    }

    const fees = $('sum-fees');
    if (risk.total_fees != null && risk.total_fees > 0) {
      fees.textContent = '-$' + Number(risk.total_fees).toFixed(2);
      fees.className = 'stat-value neg';
    } else {
      fees.textContent = '$0.00';
      fees.className = 'stat-value neutral';
    }
  }

  // Dynamically add/update tab buttons for discovered assets
  const knownAssets = new Set(['BTC', 'ETH']);
  const dotColors = {BTC:'#f7931a', ETH:'#627eea'};
  function updateTabBar(assets) {
    for (const a of assets) {
      if (knownAssets.has(a)) continue;
      knownAssets.add(a);
      const btn = document.createElement('button');
      btn.className = 'tab-btn';
      btn.dataset.asset = a;
      const dot = document.createElement('span');
      dot.className = 'tab-dot';
      dot.style.background = dotColors[a] || '#8b949e';
      btn.appendChild(dot);
      btn.appendChild(document.createTextNode(a));
      btn.addEventListener('click', () => {
        activeAsset = a;
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        if (latestState) render(latestState);
      });
      $('tab-bar').appendChild(btn);
    }
  }

  function renderSignalBars(signals) {
    const names = ['momentum', 'technical', 'flow', 'mean_reversion', 'cross_exchange', 'taker_flow', 'settlement', 'cross_asset', 'time_decay'];
    const labels = ['Mom', 'Tech', 'Flow', 'MRev', 'Fund', 'XExch', 'Liq', 'Takr', 'Settl', 'XAst', 'TDec'];
    const container = $('signal-bars');
    container.innerHTML = '';
    for (let i = 0; i < names.length; i++) {
      const v = signals[names[i]] || 0;
      const pct = Math.min(100, Math.abs(v) * 100);
      const cls = v > 0.01 ? 'pos' : v < -0.01 ? 'neg' : 'neutral';
      container.innerHTML +=
        '<div class="bar-row">' +
        '<span class="bar-label">' + labels[i] + '</span>' +
        '<div class="bar-track">' +
        '<div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div>' +
        '<div class="bar-val">' + v.toFixed(3) + '</div>' +
        '</div></div>';
    }
  }

  function pct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '--'; }
  function fmtNum(v) {
    if (v == null) return '--';
    if (Math.abs(v) >= 1000) return v.toFixed(0);
    if (Math.abs(v) >= 1) return v.toFixed(2);
    if (Math.abs(v) >= 0.01) return v.toFixed(4);
    return v.toExponential(2);
  }
  function valClass(v) {
    if (v == null) return 'neutral';
    if (v > 0.5) return 'high-pos';
    if (v > 0.01) return 'low-pos';
    if (v < -0.5) return 'high-neg';
    if (v < -0.01) return 'low-neg';
    return 'neutral';
  }
  function fmtDuration(sec) {
    if (sec < 60) return Math.floor(sec) + 's';
    if (sec < 3600) return Math.floor(sec/60) + 'm';
    const h = Math.floor(sec/3600);
    const m = Math.floor((sec%3600)/60);
    return h + 'h ' + m + 'm';
  }

  function fmtVol(v) {
    if (v >= 1000) return (v/1000).toFixed(1) + 'k';
    return v;
  }

  function tickCountdown() {
    const el = $('market-countdown');
    if (!window._closeTime) { el.textContent = '--:--'; el.className = 'countdown ok'; return; }
    const remaining = Math.max(0, Math.floor((window._closeTime - Date.now()) / 1000));
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = m + ':' + String(s).padStart(2, '0');
    if (remaining <= 0) { el.textContent = 'EXPIRED'; el.className = 'countdown urgent'; }
    else if (remaining <= 60) { el.className = 'countdown urgent'; }
    else if (remaining <= 180) { el.className = 'countdown warning'; }
    else { el.className = 'countdown ok'; }
  }

  setInterval(tickCountdown, 1000);
  connect();
})();
</script>
</body>
</html>
"""
