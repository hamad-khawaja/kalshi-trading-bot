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
.trade-tag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px}
.trade-tag.directional{background:#1a2a3a;color:#58a6ff}
.trade-tag.settlement-ride{background:#2a1a2a;color:#d2a8ff}
.trade-tag.fomo{background:#2a2a1a;color:#d29922}
.trade-tag.market-making{background:#1a2a1a;color:#3fb950}
.trade-tag.averaging{background:#2a1a1a;color:#f0883e}
.trade-tag.trend-continuation{background:#1a2a2a;color:#79c0ff}
.toggle-wrap{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.toggle-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;min-width:54px}
.toggle-label.active{color:#3fb950}
.toggle-label.paused{color:#f85149;animation:pulse 2s ease-in-out infinite}
.toggle-track{width:40px;height:20px;border-radius:10px;position:relative;transition:background .3s,border-color .3s;border:1px solid}
.toggle-track.active{background:#1a3a1a;border-color:#238636}
.toggle-track.paused{background:#2a1a1a;border-color:#da3633}
.toggle-knob{position:absolute;top:2px;width:14px;height:14px;border-radius:50%;transition:left .3s,background .3s}
.toggle-track.active .toggle-knob{left:22px;background:#3fb950}
.toggle-track.paused .toggle-knob{left:2px;background:#f85149}
.toggle-track.disabled{background:#161b22;border-color:#21262d;opacity:0.4;cursor:not-allowed}
.toggle-track.disabled .toggle-knob{left:2px;background:#484f58}
.toggle-wrap.disabled{opacity:0.4;cursor:not-allowed}
.toggle-label.disabled{color:#484f58}
.toggle-divider{width:1px;height:18px;background:#30363d}
/* Mode badge */
.mode-badge{padding:2px 10px;border-radius:4px;font-size:12px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase}
.mode-badge.paper{background:#1a3a1a;color:#3fb950;border:1px solid #238636}
.mode-badge.live{background:#2a1a1a;color:#f85149;border:1px solid #da3633;animation:pulse 2s ease-in-out infinite}
.mode-switch-btn{padding:3px 10px;font-size:11px;font-weight:600;border:1px solid #30363d;border-radius:4px;cursor:pointer;font-family:inherit;background:#161b22;color:#8b949e;transition:all .2s;margin-left:4px}
.mode-switch-btn:hover{color:#c9d1d9;border-color:#8b949e}
.mode-switch-btn.disabled{opacity:0.4;cursor:not-allowed}
body.live-mode{border-top:3px solid #f85149}
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

/* Chart view */
#chart-view{display:none;padding:16px}
#chart-view.visible{display:block}
.chart-controls{display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
.chart-btn{padding:4px 12px;font-size:11px;font-weight:600;border:1px solid #30363d;border-radius:4px;cursor:pointer;font-family:inherit;background:#161b22;color:#8b949e;transition:all .2s}
.chart-btn:hover{color:#c9d1d9;border-color:#8b949e}
.chart-btn.active{background:#1a3a1a;color:#3fb950;border-color:#238636}
.chart-container{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:16px;margin-bottom:16px}
.chart-stats{display:flex;gap:1px;background:#30363d;border-radius:6px;overflow:hidden;margin-bottom:16px}
.chart-stat{flex:1;background:#0d1117;padding:12px 16px;text-align:center}
.chart-stat .cs-val{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.chart-stat .cs-label{font-size:10px;text-transform:uppercase;color:#8b949e;margin-top:2px;letter-spacing:0.5px}
#strategy-stats{display:flex;gap:6px;padding:8px 16px;flex-wrap:wrap}
.strat-stat{background:#161b22;border:1px solid #30363d;border-top:3px solid #8b949e;border-radius:6px;padding:8px 12px;min-width:130px;flex:1}
.ss-name{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px}
.ss-pnl{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.ss-detail{font-size:11px;color:#8b949e;margin-top:2px}
/* P&L summary cards */
.pnl-summary-bar{display:flex;gap:16px;margin-bottom:16px}
.pnl-card{flex:1;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;position:relative}
.pnl-card.paper{border-top:3px solid #3fb950}
.pnl-card.live{border-top:3px solid #f85149}
.pnl-card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.pnl-card-title{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px}
.pnl-card.paper .pnl-card-title{color:#3fb950}
.pnl-card.live .pnl-card-title{color:#f85149}
.pnl-range-btns{display:flex;gap:4px}
.pnl-range-btn{padding:2px 8px;font-size:10px;font-weight:600;border:1px solid #30363d;border-radius:3px;cursor:pointer;font-family:inherit;background:transparent;color:#8b949e;transition:all .2s}
.pnl-range-btn:hover{color:#c9d1d9;border-color:#8b949e}
.pnl-range-btn.active{background:#1a3a1a;color:#3fb950;border-color:#238636}
.pnl-card-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.pnl-stat{text-align:center}
.pnl-stat-val{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.pnl-stat-label{font-size:10px;text-transform:uppercase;color:#8b949e;margin-top:2px;letter-spacing:0.5px}
/* Mode section headers */
.mode-section{margin-bottom:24px}
.mode-section-header{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#161b22;border:1px solid #30363d;border-radius:6px;cursor:pointer;user-select:none;margin-bottom:12px}
.mode-section-header:hover{background:#1c2128}
.mode-section-arrow{font-size:10px;color:#8b949e;transition:transform .2s}
.mode-section-arrow.open{transform:rotate(90deg)}
.mode-section-title{font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px}
.mode-section-header.paper .mode-section-title{color:#3fb950}
.mode-section-header.live .mode-section-title{color:#f85149}
.mode-section-count{font-size:11px;color:#8b949e}
.mode-section-body{overflow:hidden;transition:max-height .3s ease;max-height:0}
.mode-section-body.open{max-height:5000px}
.trade-table{width:100%;border-collapse:collapse;font-size:12px}
.trade-table th{text-align:left;padding:6px 8px;border-bottom:1px solid #30363d;color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;font-weight:600}
.trade-table td{padding:6px 8px;border-bottom:1px solid #21262d}
.trade-table tr:hover{background:#161b22}
.trade-table .pnl-pos{color:#3fb950;font-weight:600} .trade-table .pnl-neg{color:#f85149;font-weight:600}
.trade-table .side-yes{color:#3fb950} .trade-table .side-no{color:#f85149}
.trade-table .type-tag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:600;text-transform:uppercase}
.type-tag.directional{background:#1a2a3a;color:#58a6ff}
.type-tag.settlement_ride{background:#2a1a2a;color:#d2a8ff}
.type-tag.settlement-ride{background:#2a1a2a;color:#d2a8ff}
.type-tag.fomo{background:#2a2a1a;color:#d29922}
.type-tag.certainty_scalp{background:#1a2a2a;color:#56d4dd}
.type-tag.market_making{background:#1a2a1a;color:#3fb950}
.type-tag.averaging{background:#2a1a1a;color:#f0883e}
.type-tag.trend_continuation{background:#1a2a2a;color:#79c0ff}
.type-tag.stop_loss{background:#2a1a1a;color:#f85149}
.type-tag.take_profit{background:#1a3a1a;color:#3fb950}
.type-tag.settle{background:#1a1a2a;color:#8b949e}
.type-tag.pre_expiry{background:#2a2a1a;color:#ffa657}
.type-tag.thesis_break{background:#2a1a2a;color:#d2a8ff}
.nav-bar{display:flex;gap:0;background:#161b22;border-bottom:1px solid #30363d;padding:0 16px}
.nav-btn{padding:8px 20px;font-size:13px;font-weight:600;color:#8b949e;background:transparent;border:none;border-bottom:2px solid transparent;cursor:pointer;font-family:inherit;transition:color .2s,border-color .2s}
.nav-btn:hover{color:#c9d1d9}
.nav-btn.active{color:#f0f6fc;border-bottom-color:#58a6ff}
.strategy-bar{display:flex;align-items:center;gap:6px;padding:6px 16px;background:#161b22;border-bottom:1px solid #30363d}
.strategy-bar .strat-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px;margin-right:4px;font-weight:600}
.strategy-bar .toggle-wrap{gap:5px}
.strategy-bar .toggle-label{font-size:10px;min-width:32px;letter-spacing:0.3px}
.strategy-bar .toggle-track{width:32px;height:16px;border-radius:8px}
.strategy-bar .toggle-knob{width:10px;height:10px;top:2px}
.strategy-bar .toggle-track.active .toggle-knob{left:18px}
.strategy-bar .toggle-track.paused .toggle-knob{left:2px}
/* Settings view */
#settings-view{display:none;padding:16px}
.settings-sub-tabs{display:flex;gap:4px;margin-bottom:12px}
.settings-sub-tab{padding:4px 12px;border-radius:4px;background:#161b22;color:#8b949e;border:1px solid #30363d;cursor:pointer;font-size:12px;font-weight:500}
.settings-sub-tab.active{background:#1a2332;color:#58a6ff;border-color:#58a6ff}
.settings-section{margin-bottom:16px}
.settings-section h3{font-size:12px;text-transform:uppercase;color:#58a6ff;margin-bottom:6px;letter-spacing:0.5px;font-weight:600}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:2px}
.setting-row{display:flex;justify-content:space-between;padding:4px 8px;background:#161b22;border-radius:3px;font-size:12px}
.setting-key{color:#8b949e}
.setting-val{color:#c9d1d9;font-weight:500;max-width:60%;text-align:right;word-break:break-all}
.setting-row.highlighted{background:#1a2332;border-left:2px solid #58a6ff}
.setting-row.highlighted .setting-key{color:#58a6ff;font-weight:600}
.setting-row.highlighted .setting-val{color:#e6edf3}
.setting-row.diff{background:#2a1a1a;border-left:2px solid #f0883e}
.setting-row.diff .setting-key{color:#f0883e;font-weight:600}
.setting-row.diff .setting-val{color:#f0883e}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
</head>
<body>
<div id="reconnect-banner">Disconnected — reconnecting&hellip;</div>

<div class="header">
  <h1>KALSHI TRADING BOT</h1>
  <div class="meta">
    <span><span class="status-dot live" id="status-dot"></span><span id="conn-status">Live</span></span>
    <span>Cycle #<span id="cycle">0</span></span>
    <span>Uptime <span id="uptime">0s</span></span>
    <span>Active <span id="active-time">0s</span></span>
    <span id="mode-badge" class="mode-badge paper">PAPER</span>
    <button id="mode-switch-btn" class="mode-switch-btn" onclick="switchMode()">Switch to Live</button>
    <span id="utc-clock" style="font-weight:600;font-size:13px;padding:2px 8px;border-radius:4px">--:--:-- EST</span>
    <div class="toggle-wrap" id="trade-toggle" onclick="toggleTrading()">
      <span class="toggle-label active" id="toggle-label">Active</span>
      <div class="toggle-track active" id="toggle-track"><div class="toggle-knob"></div></div>
    </div>
    <div class="toggle-divider"></div>
    <div class="toggle-wrap disabled" id="qh-toggle" onclick="toggleQuietHours()">
      <span class="toggle-label disabled" id="qh-label">Quiet Hrs</span>
      <div class="toggle-track disabled" id="qh-track"><div class="toggle-knob"></div></div>
    </div>
    <div class="toggle-divider"></div>
    <div class="toggle-wrap" id="btc-toggle" onclick="toggleBTC()">
      <span class="toggle-label active" id="btc-label">BTC</span>
      <div class="toggle-track active" id="btc-track"><div class="toggle-knob"></div></div>
    </div>
    <div class="toggle-divider"></div>
    <div class="toggle-wrap" id="eth-toggle" onclick="toggleETH()">
      <span class="toggle-label active" id="eth-label">ETH</span>
      <div class="toggle-track active" id="eth-track"><div class="toggle-knob"></div></div>
    </div>
  </div>
</div>

<div class="nav-bar">
  <button class="nav-btn active" data-view="dashboard" onclick="switchView('dashboard')">Dashboard</button>
  <button class="nav-btn" data-view="trades" onclick="switchView('trades')">Trades</button>
  <button class="nav-btn" data-view="settings" onclick="switchView('settings')">Settings</button>
</div>

<div class="strategy-bar" id="strategy-bar">
  <span class="strat-label">Strategies:</span>
  <div class="toggle-wrap" data-strategy="directional" onclick="toggleStrategy('directional')">
    <span class="toggle-label active">DIR</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="fomo" onclick="toggleStrategy('fomo')">
    <span class="toggle-label active">FOMO</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="certainty_scalp" onclick="toggleStrategy('certainty_scalp')">
    <span class="toggle-label active">CERT</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="settlement_ride" onclick="toggleStrategy('settlement_ride')">
    <span class="toggle-label active">SETT</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="trend_continuation" onclick="toggleStrategy('trend_continuation')">
    <span class="toggle-label active">TREND-C</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="market_making" onclick="toggleStrategy('market_making')">
    <span class="toggle-label active">MM</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <span class="strat-label" style="margin-left:8px">Guards:</span>
  <div class="toggle-wrap" data-strategy="phase_filter" onclick="toggleStrategy('phase_filter')">
    <span class="toggle-label active">PHASE</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="trend_guard" onclick="toggleStrategy('trend_guard')">
    <span class="toggle-label active">TREND</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="mm_vol_filter" onclick="toggleStrategy('mm_vol_filter')">
    <span class="toggle-label active">MMVOL</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="ppe_filter" onclick="toggleStrategy('ppe_filter')">
    <span class="toggle-label active">PPE</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
  <div class="toggle-wrap" data-strategy="tc_extreme_vol_filter" onclick="toggleStrategy('tc_extreme_vol_filter')">
    <span class="toggle-label active">TCVOL</span>
    <div class="toggle-track active"><div class="toggle-knob"></div></div>
  </div>
</div>

<div id="dashboard-view">
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
    <div class="stat-value neutral" id="sum-pnl-btc">--</div>
    <div class="stat-label"><span class="tab-dot btc" style="width:6px;height:6px;display:inline-block;vertical-align:middle;margin-right:3px"></span>BTC P&amp;L</div>
  </div>
  <div class="stat-card">
    <div class="stat-value neutral" id="sum-pnl-eth">--</div>
    <div class="stat-label"><span class="tab-dot eth" style="width:6px;height:6px;display:inline-block;vertical-align:middle;margin-right:3px"></span>ETH P&amp;L</div>
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
    <div class="val"><a id="market-ticker" href="#" target="_blank" rel="noopener" style="color:#58a6ff;text-decoration:none">--</a></div>
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
      <div class="kv"><span class="k">Chainlink</span><span class="v" id="price-chainlink" style="color:#375bd2">--</span></div>
      <div class="kv"><span class="k">Oracle div</span><span class="v" id="chainlink-div">--</span></div>
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
</div><!-- end dashboard-view -->

<div id="chart-view">
  <!-- P&L Summary Bar -->
  <div class="pnl-summary-bar">
    <div class="pnl-card paper">
      <div class="pnl-card-header">
        <span class="pnl-card-title">Paper Trading</span>
        <div class="pnl-range-btns" id="pnl-range-paper">
          <button class="pnl-range-btn active" data-range="1d" onclick="setPnlRange('paper','1d')">1D</button>
          <button class="pnl-range-btn" data-range="1w" onclick="setPnlRange('paper','1w')">1W</button>
          <button class="pnl-range-btn" data-range="1m" onclick="setPnlRange('paper','1m')">1M</button>
          <button class="pnl-range-btn" data-range="1y" onclick="setPnlRange('paper','1y')">1Y</button>
        </div>
      </div>
      <div class="pnl-card-stats">
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-total-paper">--</div><div class="pnl-stat-label">Total P&amp;L</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-trades-paper">--</div><div class="pnl-stat-label">Trades</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-winrate-paper">--</div><div class="pnl-stat-label">Win Rate</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-fees-paper">--</div><div class="pnl-stat-label">Fees</div></div>
      </div>
    </div>
    <div class="pnl-card live">
      <div class="pnl-card-header">
        <span class="pnl-card-title">Live Trading</span>
        <div class="pnl-range-btns" id="pnl-range-live">
          <button class="pnl-range-btn active" data-range="1d" onclick="setPnlRange('live','1d')">1D</button>
          <button class="pnl-range-btn" data-range="1w" onclick="setPnlRange('live','1w')">1W</button>
          <button class="pnl-range-btn" data-range="1m" onclick="setPnlRange('live','1m')">1M</button>
          <button class="pnl-range-btn" data-range="1y" onclick="setPnlRange('live','1y')">1Y</button>
        </div>
      </div>
      <div class="pnl-card-stats">
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-total-live">--</div><div class="pnl-stat-label">Total P&amp;L</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-trades-live">--</div><div class="pnl-stat-label">Trades</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-winrate-live">--</div><div class="pnl-stat-label">Win Rate</div></div>
        <div class="pnl-stat"><div class="pnl-stat-val" id="pnl-fees-live">--</div><div class="pnl-stat-label">Fees</div></div>
      </div>
    </div>
  </div>

  <!-- Paper Trading Section -->
  <div class="mode-section" id="section-paper">
    <div class="mode-section-header paper" onclick="toggleModeSection('paper')">
      <span class="mode-section-arrow open" id="arrow-paper">&#9654;</span>
      <span class="mode-section-title">Paper Trading</span>
      <span class="mode-section-count" id="count-paper"></span>
      <span style="flex:1"></span>
      <button class="chart-btn" onclick="event.stopPropagation();refreshTrades()" style="border-color:#58a6ff;color:#58a6ff;font-size:10px;padding:2px 8px">Refresh</button>
    </div>
    <div class="mode-section-body open" id="body-paper">
      <div id="strategy-stats-paper" style="display:flex;gap:6px;padding:0 0 8px;flex-wrap:wrap"></div>
      <div class="chart-controls" id="filters-paper">
        <button class="chart-btn active" data-filter="all" onclick="setFilter('paper','chart','all')">All</button>
        <button class="chart-btn" data-filter="BTC" onclick="setFilter('paper','chart','BTC')">BTC</button>
        <button class="chart-btn" data-filter="ETH" onclick="setFilter('paper','chart','ETH')">ETH</button>
        <span style="color:#30363d;margin:0 4px">|</span>
        <button class="chart-btn active" data-action="all" onclick="setFilter('paper','action','all')">All Types</button>
        <button class="chart-btn" data-action="settle" onclick="setFilter('paper','action','settle')">Settlement</button>
        <button class="chart-btn" data-action="stop_loss" onclick="setFilter('paper','action','stop_loss')">Stop Loss</button>
        <button class="chart-btn" data-action="take_profit" onclick="setFilter('paper','action','take_profit')">Take Profit</button>
        <button class="chart-btn" data-action="thesis_break" onclick="setFilter('paper','action','thesis_break')">Thesis Break</button>
        <span style="color:#30363d;margin:0 4px">|</span>
        <button class="chart-btn active" data-strategy="all" onclick="setFilter('paper','strategy','all')">All Strategies</button>
        <button class="chart-btn" data-strategy="directional" onclick="setFilter('paper','strategy','directional')">Directional</button>
        <button class="chart-btn" data-strategy="settlement_ride" onclick="setFilter('paper','strategy','settlement_ride')">Settlement Ride</button>
        <button class="chart-btn" data-strategy="fomo" onclick="setFilter('paper','strategy','fomo')">FOMO</button>
        <button class="chart-btn" data-strategy="market_making" onclick="setFilter('paper','strategy','market_making')">Market Making</button>
        <button class="chart-btn" data-strategy="certainty_scalp" onclick="setFilter('paper','strategy','certainty_scalp')">Certainty Scalp</button>
        <button class="chart-btn" data-strategy="trend_continuation" onclick="setFilter('paper','strategy','trend_continuation')">Trend Cont.</button>
      </div>
      <div class="chart-container"><canvas id="equity-chart-paper" height="300"></canvas></div>
      <div class="chart-container"><canvas id="pnl-chart-paper" height="200"></canvas></div>
      <div class="chart-container" style="max-height:400px;overflow-y:auto">
        <table class="trade-table">
          <thead><tr><th>Time</th><th>Market</th><th>Side</th><th>Action</th><th>Strategy</th><th>Count</th><th>Price</th><th>Fees</th><th>P&amp;L</th></tr></thead>
          <tbody id="trade-table-body-paper"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Live Trading Section -->
  <div class="mode-section" id="section-live">
    <div class="mode-section-header live" onclick="toggleModeSection('live')">
      <span class="mode-section-arrow open" id="arrow-live">&#9654;</span>
      <span class="mode-section-title">Live Trading</span>
      <span class="mode-section-count" id="count-live"></span>
      <span style="flex:1"></span>
    </div>
    <div class="mode-section-body open" id="body-live">
      <div id="strategy-stats-live" style="display:flex;gap:6px;padding:0 0 8px;flex-wrap:wrap"></div>
      <div class="chart-controls" id="filters-live">
        <button class="chart-btn active" data-filter="all" onclick="setFilter('live','chart','all')">All</button>
        <button class="chart-btn" data-filter="BTC" onclick="setFilter('live','chart','BTC')">BTC</button>
        <button class="chart-btn" data-filter="ETH" onclick="setFilter('live','chart','ETH')">ETH</button>
        <span style="color:#30363d;margin:0 4px">|</span>
        <button class="chart-btn active" data-action="all" onclick="setFilter('live','action','all')">All Types</button>
        <button class="chart-btn" data-action="settle" onclick="setFilter('live','action','settle')">Settlement</button>
        <button class="chart-btn" data-action="stop_loss" onclick="setFilter('live','action','stop_loss')">Stop Loss</button>
        <button class="chart-btn" data-action="take_profit" onclick="setFilter('live','action','take_profit')">Take Profit</button>
        <button class="chart-btn" data-action="thesis_break" onclick="setFilter('live','action','thesis_break')">Thesis Break</button>
        <span style="color:#30363d;margin:0 4px">|</span>
        <button class="chart-btn active" data-strategy="all" onclick="setFilter('live','strategy','all')">All Strategies</button>
        <button class="chart-btn" data-strategy="directional" onclick="setFilter('live','strategy','directional')">Directional</button>
        <button class="chart-btn" data-strategy="settlement_ride" onclick="setFilter('live','strategy','settlement_ride')">Settlement Ride</button>
        <button class="chart-btn" data-strategy="fomo" onclick="setFilter('live','strategy','fomo')">FOMO</button>
        <button class="chart-btn" data-strategy="market_making" onclick="setFilter('live','strategy','market_making')">Market Making</button>
        <button class="chart-btn" data-strategy="certainty_scalp" onclick="setFilter('live','strategy','certainty_scalp')">Certainty Scalp</button>
        <button class="chart-btn" data-strategy="trend_continuation" onclick="setFilter('live','strategy','trend_continuation')">Trend Cont.</button>
      </div>
      <div class="chart-container"><canvas id="equity-chart-live" height="300"></canvas></div>
      <div class="chart-container"><canvas id="pnl-chart-live" height="200"></canvas></div>
      <div class="chart-container" style="max-height:400px;overflow-y:auto">
        <table class="trade-table">
          <thead><tr><th>Time</th><th>Market</th><th>Side</th><th>Action</th><th>Strategy</th><th>Count</th><th>Price</th><th>Fees</th><th>P&amp;L</th></tr></thead>
          <tbody id="trade-table-body-live"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<div id="settings-view">
  <div class="settings-sub-tabs">
    <button class="settings-sub-tab active" onclick="switchSettingsTab('running')">Running</button>
    <button class="settings-sub-tab" onclick="switchSettingsTab('live')">Live</button>
    <button class="settings-sub-tab" onclick="switchSettingsTab('diff')">Diff</button>
  </div>
  <div id="settings-content"></div>
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
    $('active-time').textContent = fmtDuration(s.active_trading_seconds || 0);
    updateModeBadge(s.mode || 'paper', s.positions || []);

    // Update quiet hours from server config
    if (s.quiet_hours_est) quietHoursEST = s.quiet_hours_est;

    // Sync trading toggles with server state
    if (s.trading_paused != null) {
      updateToggleButton(s.trading_paused);
      updateQHToggle(s.quiet_hours_override || false, s.trading_paused);
    }
    if (s.btc_disabled != null) {
      updateBTCToggle(s.btc_disabled);
    }
    if (s.eth_disabled != null) {
      updateETHToggle(s.eth_disabled);
    }
    if (s.strategy_toggles) {
      updateStrategyToggles(s.strategy_toggles);
    }

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
    renderSummaryBar(risk, s.per_asset_pnl);

    // Update price panel header
    $('price-header').textContent = activeAsset + ' Prices';

    // Market (with Kalshi link)
    const tickerEl = $('market-ticker');
    tickerEl.textContent = m.ticker || '--';
    const kalshiUrl = buildKalshiUrl(m.ticker, m.event_ticker);
    if (kalshiUrl) {
      tickerEl.href = kalshiUrl;
      tickerEl.style.cursor = 'pointer';
    } else {
      tickerEl.removeAttribute('href');
    }
    $('market-title').textContent = m.yes_sub_title || m.title || '--';
    $('market-volume').textContent = m.volume != null ? 'Vol: ' + fmtVol(m.volume) : '--';
    if (m.close_time) { window._closeTime = new Date(m.close_time).getTime(); }
    else { window._closeTime = null; }

    // Price with live ticker (per-asset tracking)
    const newPrice = snap.spot_price || null;
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
    $('price-binance').textContent = snap.secondary_spot_price ? fmtUsd(snap.secondary_spot_price) : '--';
    $('price-kalshi').textContent = snap.strike_price ? fmtUsd(snap.strike_price) : '--';
    // Chainlink oracle
    const clEl = $('price-chainlink');
    if (snap.chainlink_oracle_price) {
      clEl.textContent = fmtUsd(snap.chainlink_oracle_price);
      if (snap.chainlink_round_updated) clEl.textContent += ' \u2713';
    } else { clEl.textContent = '--'; }
    const clDiv = $('chainlink-div');
    if (snap.chainlink_divergence != null) {
      const bps = (snap.chainlink_divergence * 10000).toFixed(1);
      clDiv.textContent = bps + ' bps';
      clDiv.style.color = snap.chainlink_divergence > 0 ? '#3fb950' : snap.chainlink_divergence < 0 ? '#f85149' : '#8b949e';
    } else { clDiv.textContent = '--'; clDiv.style.color = '#8b949e'; }
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
          const time = (m.open_time || m.close_time) ? new Date(m.open_time || m.close_time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'}) : '';
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
          const pnl = t.pnl != null ? t.pnl : 0;
          const isWin = pnl >= 0;
          const arrow = isWin ? '\\u25B2' : '\\u25BC';
          const cls = isWin ? 'win' : 'loss';
          const sign = isWin ? '+' : '';
          const action = t.action.replace('_', ' ');
          const st = t.signal_type || '';
          const tagCls = st.replace('_', '-');
          const tagLabel = st ? st.replace('_', ' ') : '';
          const tagHtml = tagLabel ? '<span class="trade-tag ' + tagCls + '">' + tagLabel + '</span>' : '';
          const ep = t.entry_price ? '$' + t.entry_price.toFixed(2) : '';
          const bp = t.spot_price ? 'BTC $' + t.spot_price.toLocaleString() : '';
          const sk = t.strike ? 'K $' + t.strike.toLocaleString() : '';
          const priceInfo = [ep, sk, bp].filter(Boolean).join(' | ');
          return '<div class="trade-hist-row">' +
            '<span class="trade-arrow ' + cls + '">' + arrow + '</span>' +
            tagHtml +
            '<span class="trade-action">' + action + '</span>' +
            '<span style="color:#8b949e">' + t.side.toUpperCase() + '</span>' +
            '<span style="color:#8b949e">$' + (t.size || 0).toFixed(2) + '</span>' +
            '<span class="trade-pnl ' + cls + '">' + sign + '$' + pnl.toFixed(2) + '</span>' +
            '<span class="trade-meta">' + t.time + '</span>' +
            (priceInfo ? '<div style="font-size:10px;color:#6e7681;margin-left:18px">' + priceInfo + '</div>' : '') +
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
  function renderSummaryBar(risk, perAssetPnl) {
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

    // Per-asset P&L
    for (const [asset, elId] of [['BTC', 'sum-pnl-btc'], ['ETH', 'sum-pnl-eth']]) {
      const el = $(elId);
      const val = (perAssetPnl || {})[asset];
      if (val != null && val !== 0) {
        const sign = val >= 0 ? '+' : '';
        el.textContent = sign + '$' + Number(val).toFixed(2);
        el.className = 'stat-value' + (val >= 0 ? ' pos' : ' neg');
      } else {
        el.textContent = '$0.00';
        el.className = 'stat-value neutral';
      }
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
    const names = ['momentum', 'technical', 'flow', 'mean_reversion', 'cross_exchange', 'taker_flow', 'settlement', 'cross_asset', 'chainlink', 'btc_beta', 'time_decay'];
    const labels = ['Mom', 'Tech', 'Flow', 'MRev', 'XExch', 'Takr', 'Settl', 'XAst', 'CLink', 'Beta', 'TDec'];
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

  const kalshiSlugs = {
    'KXBTC15M': 'bitcoin-price-up-down',
    'KXETH15M': 'eth-15m-price-up-down',
  };
  function buildKalshiUrl(ticker, eventTicker) {
    if (!ticker) return null;
    // Derive series from ticker (e.g. KXBTC15M-26FEB172215-15 → KXBTC15M)
    const series = ticker.split('-')[0].toUpperCase();
    const slug = kalshiSlugs[series];
    if (!slug) return 'https://kalshi.com/markets/' + series.toLowerCase();
    // Use event_ticker if available, otherwise derive from ticker (drop last segment)
    let evt = eventTicker;
    if (!evt) {
      const parts = ticker.split('-');
      if (parts.length >= 2) evt = parts.slice(0, -1).join('-');
      else evt = ticker;
    }
    return 'https://kalshi.com/markets/' + series.toLowerCase() + '/' + slug + '/' + evt.toLowerCase();
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

  // EST clock with quiet hours coloring
  let quietHoursEST = [];
  const estFmt = new Intl.DateTimeFormat('en-US', {timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  function getESTHour() { return parseInt(new Intl.DateTimeFormat('en-US', {timeZone:'America/New_York',hour:'numeric',hour12:false}).format(new Date()), 10); }
  function tickUTCClock() {
    const now = new Date();
    const estH = getESTHour();
    const el = $('utc-clock');
    el.textContent = estFmt.format(now) + ' EST';
    if (quietHoursEST.length > 0 && quietHoursEST.includes(estH)) {
      el.style.background = '#2a1a1a';
      el.style.color = '#f85149';
      el.style.border = '1px solid #da3633';
    } else {
      el.style.background = '#1a3a1a';
      el.style.color = '#3fb950';
      el.style.border = '1px solid #238636';
    }
  }

  // Trading toggle
  window.toggleTrading = function() {
    fetch('/api/toggle-trading', {method: 'POST'})
      .then(r => {
        if (r.status === 409) return r.json().then(d => { alert(d.error); return null; });
        return r.json();
      })
      .then(d => {
        if (!d) return;
        updateToggleButton(d.trading_paused);
        updateQHToggle(d.quiet_hours_override, d.trading_paused);
      })
      .catch(err => console.error('toggle error', err));
  };

  // Quiet hours override toggle
  window.toggleQuietHours = function() {
    const wrap = $('qh-toggle');
    if (wrap.classList.contains('disabled')) return;
    fetch('/api/toggle-quiet-hours', {method: 'POST'})
      .then(r => r.json())
      .then(d => { if (!d.error) updateQHToggle(d.quiet_hours_override, false); })
      .catch(err => console.error('qh toggle error', err));
  };

  function updateToggleButton(paused) {
    const label = $('toggle-label');
    const track = $('toggle-track');
    if (paused) {
      label.textContent = 'Paused';
      label.className = 'toggle-label paused';
      track.className = 'toggle-track paused';
    } else {
      label.textContent = 'Active';
      label.className = 'toggle-label active';
      track.className = 'toggle-track active';
    }
  }

  // Mode badge + switch
  function updateModeBadge(mode, positions) {
    const badge = $('mode-badge');
    const btn = $('mode-switch-btn');
    const hasPositions = positions.length > 0;
    badge.textContent = mode.toUpperCase();
    badge.className = 'mode-badge ' + mode;
    if (mode === 'live') {
      document.body.classList.add('live-mode');
    } else {
      document.body.classList.remove('live-mode');
    }
    const targetMode = mode === 'paper' ? 'Live' : 'Paper';
    btn.textContent = 'Switch to ' + targetMode;
    if (hasPositions) {
      btn.classList.add('disabled');
      btn.title = 'Close all positions before switching mode';
    } else {
      btn.classList.remove('disabled');
      btn.title = '';
    }
  }

  window.switchMode = function() {
    const btn = $('mode-switch-btn');
    if (btn.classList.contains('disabled')) return;
    const currentMode = $('mode-badge').textContent.toLowerCase();
    const targetMode = currentMode === 'paper' ? 'live' : 'paper';
    if (targetMode === 'live') {
      if (!confirm('Switch to LIVE mode? Real money will be used for trades.')) return;
    }
    btn.textContent = 'Switching...';
    btn.classList.add('disabled');
    fetch('/api/switch-mode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode: targetMode})
    })
      .then(r => r.json())
      .then(d => {
        if (d.error) alert('Mode switch failed: ' + d.error);
      })
      .catch(err => {
        console.error('mode switch error', err);
        alert('Mode switch failed: network error');
      });
  };

  // BTC killswitch toggle
  window.toggleBTC = function() {
    fetch('/api/toggle-btc', {method: 'POST'})
      .then(r => r.json())
      .then(d => updateBTCToggle(d.btc_disabled))
      .catch(err => console.error('btc toggle error', err));
  };

  function updateBTCToggle(disabled) {
    const label = $('btc-label');
    const track = $('btc-track');
    if (disabled) {
      label.textContent = 'BTC Off';
      label.className = 'toggle-label paused';
      track.className = 'toggle-track paused';
    } else {
      label.textContent = 'BTC';
      label.className = 'toggle-label active';
      track.className = 'toggle-track active';
    }
  }

  // ETH killswitch toggle
  window.toggleETH = function() {
    fetch('/api/toggle-eth', {method: 'POST'})
      .then(r => r.json())
      .then(d => updateETHToggle(d.eth_disabled))
      .catch(err => console.error('eth toggle error', err));
  };

  function updateETHToggle(disabled) {
    const label = $('eth-label');
    const track = $('eth-track');
    if (disabled) {
      label.textContent = 'ETH Off';
      label.className = 'toggle-label paused';
      track.className = 'toggle-track paused';
    } else {
      label.textContent = 'ETH';
      label.className = 'toggle-label active';
      track.className = 'toggle-track active';
    }
  }

  // Strategy toggles
  window.toggleStrategy = function(name) {
    fetch('/api/toggle-strategy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(r => r.json())
      .then(d => { if (d.strategy_toggles) updateStrategyToggles(d.strategy_toggles); })
      .catch(err => console.error('strategy toggle error', err));
  };

  function updateStrategyToggles(toggles) {
    if (!toggles) return;
    document.querySelectorAll('.strategy-bar .toggle-wrap[data-strategy]').forEach(wrap => {
      const name = wrap.dataset.strategy;
      const enabled = toggles[name];
      const label = wrap.querySelector('.toggle-label');
      const track = wrap.querySelector('.toggle-track');
      if (enabled) {
        label.className = 'toggle-label active';
        track.className = 'toggle-track active';
      } else {
        label.className = 'toggle-label paused';
        track.className = 'toggle-track paused';
      }
    });
  }

  function updateQHToggle(override, masterPaused) {
    const wrap = $('qh-toggle');
    const label = $('qh-label');
    const track = $('qh-track');
    if (masterPaused) {
      wrap.className = 'toggle-wrap disabled';
      label.className = 'toggle-label disabled';
      track.className = 'toggle-track disabled';
    } else if (override) {
      wrap.className = 'toggle-wrap';
      label.className = 'toggle-label active';
      track.className = 'toggle-track active';
    } else {
      wrap.className = 'toggle-wrap';
      label.className = 'toggle-label paused';
      track.className = 'toggle-track paused';
    }
  }

  // ===== View switching =====
  let currentView = 'dashboard';
  window.switchView = function(view) {
    currentView = view;
    document.querySelectorAll('.nav-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.view === view);
    });
    $('dashboard-view').style.display = view === 'dashboard' ? '' : 'none';
    $('chart-view').style.display = view === 'trades' ? 'block' : 'none';
    $('settings-view').style.display = view === 'settings' ? 'block' : 'none';
    if (view === 'trades' && modeState.paper.trades.length === 0 && modeState.live.trades.length === 0) refreshTrades();
    if (view === 'settings') renderSettings();
  };

  // ===== Settings renderer =====
  const IMPORTANT_SETTINGS = new Set([
    'min_edge_threshold', 'max_edge_threshold', 'confidence_min',
    'min_entry_price', 'max_directional_price', 'min_quality_score',
    'stop_loss_pct', 'stop_loss_max_dollar_loss',
    'directional_stop_loss_pct', 'directional_stop_loss_max_dollar',
    'directional_kelly_fraction', 'yes_side_edge_multiplier',
    'no_side_edge_multiplier', 'edge_confirmation_cycles',
    'kelly_fraction', 'max_position_per_market',
    'max_total_exposure_dollars', 'max_daily_loss_dollars',
    'min_balance_dollars', 'drawdown_limit_dollars',
  ]);

  let settingsTab = 'running';
  window.switchSettingsTab = function(tab) {
    settingsTab = tab;
    document.querySelectorAll('.settings-sub-tab').forEach(b => {
      b.classList.toggle('active', b.textContent.toLowerCase() === tab);
    });
    renderSettings();
  };

  function fmtVal(v) {
    return (typeof v === 'object' && v !== null) ? JSON.stringify(v) : String(v);
  }

  function renderConfigSection(cfg, compareWith) {
    let html = '';
    for (const [section, values] of Object.entries(cfg)) {
      if (section === 'mode') {
        const diffCls = compareWith && compareWith.mode !== values ? ' diff' : ' highlighted';
        html += '<div class="settings-section"><h3>Mode</h3>' +
          '<div class="setting-row' + diffCls + '"><span class="setting-key">mode</span>' +
          '<span class="setting-val">' + values + '</span></div></div>';
        continue;
      }
      if (typeof values !== 'object' || values === null) {
        html += '<div class="settings-section"><h3>' + section + '</h3>' +
          '<div class="setting-row"><span class="setting-key">' + section + '</span>' +
          '<span class="setting-val">' + fmtVal(values) + '</span></div></div>';
        continue;
      }
      html += '<div class="settings-section"><h3>' + section + '</h3><div class="settings-grid">';
      const cmpSection = compareWith && compareWith[section];
      for (const [k, v] of Object.entries(values)) {
        const display = fmtVal(v);
        let cls = IMPORTANT_SETTINGS.has(k) ? ' highlighted' : '';
        if (cmpSection && typeof cmpSection === 'object' && fmtVal(cmpSection[k]) !== display) {
          cls = ' diff';
        }
        html += '<div class="setting-row' + cls + '"><span class="setting-key">' + k +
          '</span><span class="setting-val">' + display + '</span></div>';
      }
      html += '</div></div>';
    }
    return html;
  }

  function renderSettings() {
    const running = latestState && latestState.startup_config;
    const live = latestState && latestState.live_config;
    if (settingsTab === 'running') {
      if (!running || Object.keys(running).length === 0) {
        $('settings-content').innerHTML = '<p style="color:#8b949e">No config data available</p>';
        return;
      }
      $('settings-content').innerHTML = renderConfigSection(running, null);
    } else if (settingsTab === 'live') {
      if (!live || Object.keys(live).length === 0) {
        $('settings-content').innerHTML = '<p style="color:#8b949e">No live config found (config/settings.live.yaml)</p>';
        return;
      }
      $('settings-content').innerHTML = renderConfigSection(live, null);
    } else {
      // Diff: show live config with differences from running highlighted
      if (!running || !live || Object.keys(live).length === 0) {
        $('settings-content').innerHTML = '<p style="color:#8b949e">Both running and live configs needed for diff</p>';
        return;
      }
      $('settings-content').innerHTML = renderConfigSection(live, running);
    }
  }

  // ===== Trade chart (per-mode) =====
  const modeState = {
    paper: { trades: [], chartFilter: 'all', actionFilter: 'all', strategyFilter: 'all', equityChart: null, pnlChart: null, pnlRange: '1d' },
    live:  { trades: [], chartFilter: 'all', actionFilter: 'all', strategyFilter: 'all', equityChart: null, pnlChart: null, pnlRange: '1d' },
  };

  const STRAT_COLORS = {
    directional: '#58a6ff',
    settlement_ride: '#d2a8ff',
    fomo: '#d29922',
    market_making: '#3fb950',
    certainty_scalp: '#56d4dd',
    averaging: '#f0883e',
    trend_continuation: '#79c0ff',
  };
  const STRAT_LABELS = {
    directional: 'Directional',
    settlement_ride: 'Settlement Ride',
    fomo: 'FOMO',
    market_making: 'Market Making',
    certainty_scalp: 'Certainty Scalp',
    averaging: 'Averaging',
    trend_continuation: 'Trend Cont.',
  };

  window.toggleModeSection = function(mode) {
    const body = $('body-' + mode);
    const arrow = $('arrow-' + mode);
    body.classList.toggle('open');
    arrow.classList.toggle('open');
  };

  window.setFilter = function(mode, type, val) {
    const s = modeState[mode];
    if (type === 'chart') s.chartFilter = val;
    else if (type === 'action') s.actionFilter = val;
    else if (type === 'strategy') s.strategyFilter = val;
    const container = $('filters-' + mode);
    if (type === 'chart') container.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b.dataset.filter === val));
    if (type === 'action') container.querySelectorAll('[data-action]').forEach(b => b.classList.toggle('active', b.dataset.action === val));
    if (type === 'strategy') container.querySelectorAll('[data-strategy]').forEach(b => b.classList.toggle('active', b.dataset.strategy === val));
    renderModeSection(mode);
  };

  window.setPnlRange = function(mode, range) {
    modeState[mode].pnlRange = range;
    $('pnl-range-' + mode).querySelectorAll('.pnl-range-btn').forEach(b => b.classList.toggle('active', b.dataset.range === range));
    fetchPnlSummary(mode);
  };

  function fetchPnlSummary(mode) {
    const range = modeState[mode].pnlRange;
    fetch('/api/pnl-summary?mode=' + mode + '&range=' + range)
      .then(r => r.json())
      .then(d => {
        const tp = $('pnl-total-' + mode);
        tp.textContent = (d.total_pnl >= 0 ? '+' : '') + '$' + d.total_pnl.toFixed(2);
        tp.style.color = d.total_pnl >= 0 ? '#3fb950' : '#f85149';
        $('pnl-trades-' + mode).textContent = d.trade_count;
        const wr = $('pnl-winrate-' + mode);
        wr.textContent = d.trade_count > 0 ? d.win_rate.toFixed(1) + '%' : '--';
        wr.style.color = d.win_rate >= 50 ? '#3fb950' : d.trade_count > 0 ? '#f85149' : '#8b949e';
        const fe = $('pnl-fees-' + mode);
        fe.textContent = '$' + d.total_fees.toFixed(2);
        fe.style.color = '#8b949e';
      })
      .catch(err => console.error('pnl summary error', mode, err));
  }

  window.refreshTrades = function() {
    Promise.all([
      fetch('/api/trades?limit=500&mode=paper').then(r => r.json()),
      fetch('/api/trades?limit=500&mode=live').then(r => r.json()),
    ]).then(([paper, live]) => {
      modeState.paper.trades = paper.sort((a, b) => new Date(a.exit_time || a.entry_time) - new Date(b.exit_time || b.entry_time));
      modeState.live.trades = live.sort((a, b) => new Date(a.exit_time || a.entry_time) - new Date(b.exit_time || b.entry_time));
      $('count-paper').textContent = paper.length + ' trades';
      $('count-live').textContent = live.length + ' trades';
      renderModeSection('paper');
      renderModeSection('live');
    }).catch(err => console.error('trades fetch error', err));
    fetchPnlSummary('paper');
    fetchPnlSummary('live');
  };

  function getFilteredTrades(mode) {
    const s = modeState[mode];
    return s.trades.filter(t => {
      if (t.action === 'buy') return false;
      if (s.chartFilter !== 'all') {
        const ticker = (t.market_ticker || '').toUpperCase();
        if (s.chartFilter === 'BTC' && !ticker.includes('BTC')) return false;
        if (s.chartFilter === 'ETH' && !ticker.includes('ETH')) return false;
      }
      if (s.actionFilter !== 'all' && t.action !== s.actionFilter) return false;
      if (s.strategyFilter !== 'all' && (t.strategy_tag || 'directional') !== s.strategyFilter) return false;
      return true;
    });
  }

  function renderModeSection(mode) {
    const trades = getFilteredTrades(mode);
    renderStrategyStats(mode, trades);
    renderEquityChart(mode, trades);
    renderPnlChart(mode, trades);
    renderTradeTable(mode, trades);
  }

  function renderStrategyStats(mode, trades) {
    const container = $('strategy-stats-' + mode);
    const byStrat = {};
    for (const t of trades) {
      const tag = t.strategy_tag || 'directional';
      if (!byStrat[tag]) byStrat[tag] = [];
      byStrat[tag].push(t);
    }
    const entries = Object.entries(byStrat).sort((a, b) => {
      const pa = a[1].reduce((s, t) => s + (t.pnl_dollars || 0), 0);
      const pb = b[1].reduce((s, t) => s + (t.pnl_dollars || 0), 0);
      return pb - pa;
    });
    if (entries.length === 0) { container.innerHTML = ''; return; }
    container.innerHTML = entries.map(([tag, arr]) => {
      const pnl = arr.reduce((s, t) => s + (t.pnl_dollars || 0), 0);
      const wins = arr.filter(t => (t.pnl_dollars || 0) > 0).length;
      const wr = arr.length > 0 ? (wins / arr.length * 100).toFixed(0) : 0;
      const color = STRAT_COLORS[tag] || '#8b949e';
      const label = STRAT_LABELS[tag] || tag.replace('_', ' ');
      const sign = pnl >= 0 ? '+' : '';
      const pnlColor = pnl >= 0 ? '#3fb950' : '#f85149';
      return '<div class="strat-stat" style="border-top-color:' + color + '">' +
        '<div class="ss-name" style="color:' + color + '">' + label + '</div>' +
        '<div class="ss-pnl" style="color:' + pnlColor + '">' + sign + '$' + pnl.toFixed(2) + '</div>' +
        '<div class="ss-detail">' + arr.length + ' trades &middot; ' + wr + '% win</div>' +
        '</div>';
    }).join('');
  }

  function renderEquityChart(mode, trades) {
    const s = modeState[mode];
    const ctx = $('equity-chart-' + mode).getContext('2d');
    if (s.equityChart) s.equityChart.destroy();

    const byStrat = {};
    for (const t of trades) {
      const tag = t.strategy_tag || 'directional';
      if (!byStrat[tag]) byStrat[tag] = [];
      byStrat[tag].push(t);
    }
    const stratKeys = Object.keys(byStrat);
    const multiStrat = stratKeys.length > 1;

    const datasets = [];
    for (const tag of stratKeys) {
      const stTrades = byStrat[tag];
      const color = STRAT_COLORS[tag] || '#8b949e';
      const label = STRAT_LABELS[tag] || tag.replace('_', ' ');
      let cum = 0;
      const points = [{x: stTrades.length > 0 ? new Date(stTrades[0].exit_time || stTrades[0].entry_time).getTime() - 60000 : Date.now(), y: 0}];
      for (const t of stTrades) {
        cum += (t.pnl_dollars || 0);
        points.push({x: new Date(t.exit_time || t.entry_time).getTime(), y: Math.round(cum * 100) / 100});
      }
      const hidden = s.strategyFilter !== 'all' && s.strategyFilter !== tag;
      datasets.push({
        label: label,
        data: points,
        borderColor: color,
        backgroundColor: 'transparent',
        fill: false,
        tension: 0.1,
        borderWidth: 2,
        pointRadius: 0,
        hidden: hidden,
      });
    }

    if (multiStrat) {
      let cumTotal = 0;
      const totalPts = [{x: trades.length > 0 ? new Date(trades[0].exit_time || trades[0].entry_time).getTime() - 60000 : Date.now(), y: 0}];
      for (const t of trades) {
        cumTotal += (t.pnl_dollars || 0);
        totalPts.push({x: new Date(t.exit_time || t.entry_time).getTime(), y: Math.round(cumTotal * 100) / 100});
      }
      datasets.push({
        label: 'Total',
        data: totalPts,
        borderColor: '#f0f6fc',
        backgroundColor: 'transparent',
        fill: false,
        tension: 0.1,
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        hidden: s.strategyFilter !== 'all',
      });
    }

    const markerDatasets = [];
    for (const tag of stratKeys) {
      const stTrades = byStrat[tag];
      const color = STRAT_COLORS[tag] || '#8b949e';
      const label = STRAT_LABELS[tag] || tag.replace('_', ' ');
      let cum = 0;
      const markers = [];
      for (const t of stTrades) {
        cum += (t.pnl_dollars || 0);
        markers.push({
          x: new Date(t.exit_time || t.entry_time).getTime(),
          y: Math.round(cum * 100) / 100,
          pnl: t.pnl_dollars || 0,
          action: t.action,
          ticker: t.market_ticker,
          side: t.side,
          count: t.count,
          strategy: label,
        });
      }
      const hidden = s.strategyFilter !== 'all' && s.strategyFilter !== tag;
      markerDatasets.push({
        label: 'Trades',
        data: markers,
        type: 'scatter',
        pointRadius: 6,
        pointHoverRadius: 9,
        pointBackgroundColor: markers.map(m => color),
        pointBorderColor: markers.map(m => m.pnl >= 0 ? '#238636' : '#da3633'),
        pointBorderWidth: 2,
        hidden: hidden,
      });
    }
    datasets.push(...markerDatasets);

    s.equityChart = new Chart(ctx, {
      type: 'line',
      data: { datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: true },
        plugins: {
          legend: {
            display: true,
            labels: {
              color: '#c9d1d9',
              font: { size: 11 },
              filter: function(item) { return item.text !== 'Trades'; },
              usePointStyle: true,
              pointStyle: 'line',
            },
          },
          tooltip: {
            callbacks: {
              title: function(items) { if (!items.length) return ''; return new Date(items[0].parsed.x).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'}) + ' EST'; },
              label: function(ctx) {
                const raw = ctx.raw;
                if (raw.ticker) {
                  const sign = raw.pnl >= 0 ? '+' : '';
                  return [
                    (raw.strategy ? '[' + raw.strategy + '] ' : '') + raw.ticker,
                    raw.action.replace('_', ' ') + ' ' + raw.side.toUpperCase() + ' x' + raw.count,
                    'P&L: ' + sign + '$' + raw.pnl.toFixed(2),
                    'Cumulative: $' + raw.y.toFixed(2),
                  ];
                }
                return ctx.dataset.label + ': $' + raw.y.toFixed(2);
              },
            },
            backgroundColor: '#161b22',
            titleColor: '#f0f6fc',
            bodyColor: '#c9d1d9',
            borderColor: '#30363d',
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'MMM d, HH:mm', displayFormats: { hour: 'HH:mm', minute: 'HH:mm' } },
            grid: { color: '#21262d' },
            ticks: { color: '#8b949e', font: { size: 11 }, callback: function(val) { return new Date(val).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'}); } },
          },
          y: {
            grid: { color: '#21262d' },
            ticks: {
              color: '#8b949e',
              font: { size: 11 },
              callback: v => '$' + v.toFixed(2),
            },
          },
        },
      },
    });
  }

  function renderPnlChart(mode, trades) {
    const s = modeState[mode];
    const ctx = $('pnl-chart-' + mode).getContext('2d');
    if (s.pnlChart) s.pnlChart.destroy();

    const bars = trades.map(t => ({
      x: new Date(t.exit_time || t.entry_time).getTime(),
      y: t.pnl_dollars || 0,
      ticker: t.market_ticker,
      action: t.action,
      side: t.side,
    }));

    s.pnlChart = new Chart(ctx, {
      type: 'bar',
      data: {
        datasets: [{
          label: 'Trade P&L',
          data: bars,
          backgroundColor: bars.map(b => b.y >= 0 ? 'rgba(63,185,80,0.7)' : 'rgba(248,81,73,0.7)'),
          borderColor: bars.map(b => b.y >= 0 ? '#3fb950' : '#f85149'),
          borderWidth: 1,
          borderRadius: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: function(items) { if (!items.length) return ''; return new Date(items[0].parsed.x).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'}) + ' EST'; },
              label: function(ctx) {
                const r = ctx.raw;
                const sign = r.y >= 0 ? '+' : '';
                return [r.ticker, r.action.replace('_', ' ') + ' ' + r.side.toUpperCase(), sign + '$' + r.y.toFixed(2)];
              },
            },
            backgroundColor: '#161b22',
            titleColor: '#f0f6fc',
            bodyColor: '#c9d1d9',
            borderColor: '#30363d',
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'MMM d, HH:mm', displayFormats: { hour: 'HH:mm', minute: 'HH:mm' } },
            grid: { color: '#21262d' },
            ticks: { color: '#8b949e', font: { size: 11 }, callback: function(val) { return new Date(val).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',timeZone:'America/New_York'}); } },
          },
          y: {
            grid: { color: '#21262d' },
            ticks: {
              color: '#8b949e',
              font: { size: 11 },
              callback: v => '$' + v.toFixed(2),
            },
          },
        },
      },
    });
  }

  function renderTradeTable(mode, trades) {
    const tbody = $('trade-table-body-' + mode);
    const rows = trades.slice().reverse();
    tbody.innerHTML = rows.map(t => {
      const pnl = t.pnl_dollars || 0;
      const pnlCls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      const sign = pnl >= 0 ? '+' : '';
      const sideCls = t.side === 'yes' ? 'side-yes' : 'side-no';
      const time = t.exit_time ? new Date(t.exit_time).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', timeZone:'America/New_York'}) : '--';
      const actionLabel = (t.action || '').replace('_', ' ');
      const actionCls = (t.action || '').replace('_', '-');
      const strat = t.strategy_tag || 'directional';
      const stratLabel = strat.replace('_', ' ');
      return '<tr>' +
        '<td style="color:#8b949e">' + time + '</td>' +
        '<td style="color:#c9d1d9">' + (t.market_ticker || '') + '</td>' +
        '<td class="' + sideCls + '">' + (t.side || '').toUpperCase() + '</td>' +
        '<td><span class="type-tag ' + actionCls + '">' + actionLabel + '</span></td>' +
        '<td><span class="type-tag ' + strat + '">' + stratLabel + '</span></td>' +
        '<td>' + (t.count || 0) + '</td>' +
        '<td>$' + (t.price_dollars || 0).toFixed(2) + '</td>' +
        '<td style="color:#8b949e">$' + (t.fees_dollars || 0).toFixed(2) + '</td>' +
        '<td class="' + pnlCls + '">' + sign + '$' + pnl.toFixed(2) + '</td>' +
        '</tr>';
    }).join('');
  }

  setInterval(tickCountdown, 1000);
  setInterval(tickUTCClock, 1000);
  tickUTCClock();
  connect();
})();
</script>
</body>
</html>
"""
