"""Inline HTML/CSS/JS for the dashboard — no external files needed."""

HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi BTC Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:'SF Mono',SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace;font-size:13px}
a{color:#58a6ff}
.header{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;background:#161b22;border-bottom:1px solid #30363d}
.header h1{font-size:16px;font-weight:600;color:#f0f6fc}
.header .meta{display:flex;gap:16px;font-size:12px;color:#8b949e}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:middle}
.status-dot.live{background:#3fb950} .status-dot.disconnected{background:#f85149}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:#30363d;padding:1px}
.grid>.panel{background:#0d1117;padding:12px}
.wide{grid-column:span 2} .full{grid-column:span 3}
.panel h2{font-size:11px;text-transform:uppercase;color:#8b949e;margin-bottom:8px;letter-spacing:0.5px}
.val{font-size:22px;font-weight:700;color:#f0f6fc}
.sub{font-size:11px;color:#8b949e;margin-top:2px}
.bar-row{display:flex;align-items:center;gap:6px;margin:3px 0}
.bar-label{width:72px;text-align:right;font-size:11px;color:#8b949e;flex-shrink:0}
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
#log{max-height:220px;overflow-y:auto;font-size:11px;line-height:1.8}
.log-entry{padding:2px 6px;border-radius:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-entry.trade-log{background:#1a3a1a;color:#3fb950}
.log-entry.reject-log{background:#2a1a1a;color:#f85149}
.log-entry.no-market-log{background:#161b22;color:#8b949e}
.pos-row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #21262d;font-size:12px}
#reconnect-banner{display:none;position:fixed;top:0;left:0;right:0;background:#da3633;color:#fff;text-align:center;padding:6px;font-size:12px;font-weight:600;z-index:999}
.countdown{font-size:20px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.countdown.urgent{color:#f85149;animation:pulse 1s ease-in-out infinite}
.countdown.warning{color:#ffa657}
.countdown.ok{color:#3fb950}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
</style>
</head>
<body>
<div id="reconnect-banner">Disconnected — reconnecting&hellip;</div>

<div class="header">
  <h1>KALSHI BTC BOT</h1>
  <div class="meta">
    <span><span class="status-dot live" id="status-dot"></span><span id="conn-status">Live</span></span>
    <span>Cycle #<span id="cycle">0</span></span>
    <span>Uptime <span id="uptime">0s</span></span>
    <span>Mode: <span id="mode">--</span></span>
  </div>
</div>

<div class="grid">
  <!-- Row 1: Market | BTC Price | Prediction -->
  <div class="panel" id="p-market">
    <h2>Market</h2>
    <div class="val" id="market-ticker">--</div>
    <div class="sub" id="market-title" style="color:#f0f6fc;font-size:13px;font-weight:600;margin:4px 0">--</div>
    <div class="countdown ok" id="market-countdown">--:--</div>
    <div class="sub" id="market-volume">--</div>
  </div>

  <div class="panel" id="p-btc">
    <h2>BTC Price</h2>
    <div class="val" id="btc-price">--</div>
    <div class="sub" id="btc-implied">Implied P(YES): --</div>
  </div>

  <div class="panel" id="p-prediction">
    <h2>Prediction</h2>
    <div class="val" id="pred-prob">--</div>
    <div class="sub" id="pred-conf">Confidence: --</div>
    <div style="margin-top:8px" id="signal-bars"></div>
  </div>

  <!-- Row 2: Edge Analysis | Orderbook -->
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
      </div>
    </div>
  </div>

  <div class="panel" id="p-orderbook">
    <h2>Orderbook</h2>
    <div class="kv"><span class="k">YES bid</span><span class="v" id="ob-yes-bid">--</span></div>
    <div class="kv"><span class="k">NO bid</span><span class="v" id="ob-no-bid">--</span></div>
    <div class="kv"><span class="k">Spread</span><span class="v" id="ob-spread">--</span></div>
    <div class="kv"><span class="k">YES depth</span><span class="v" id="ob-yes-depth">--</span></div>
    <div class="kv"><span class="k">NO depth</span><span class="v" id="ob-no-depth">--</span></div>
    <div class="kv"><span class="k">Implied prob</span><span class="v" id="ob-implied">--</span></div>
  </div>

  <!-- Row 3: Features -->
  <div class="panel full" id="p-features">
    <h2>Features</h2>
    <div class="feat-grid" id="feat-grid"></div>
  </div>

  <!-- Row 4: Positions | Risk -->
  <div class="panel" id="p-positions">
    <h2>Positions &amp; Orders</h2>
    <div id="positions-list"><span class="sub">No open positions</span></div>
  </div>

  <div class="panel wide" id="p-risk">
    <h2>Risk Status</h2>
    <div style="display:flex;gap:24px">
      <div style="flex:1">
        <div class="kv"><span class="k">Balance</span><span class="v" id="risk-balance">--</span></div>
        <div class="kv"><span class="k">Daily P&amp;L</span><span class="v" id="risk-pnl">--</span></div>
        <div class="kv"><span class="k">Trades today</span><span class="v" id="risk-trades">--</span></div>
      </div>
      <div style="flex:1">
        <div class="kv"><span class="k">Consec. losses</span><span class="v" id="risk-losses">--</span></div>
        <div class="kv"><span class="k">Vol regime</span><span class="v" id="risk-vol">--</span></div>
        <div class="kv"><span class="k">Exposure</span><span class="v" id="risk-exposure">--</span></div>
      </div>
    </div>
  </div>

  <!-- Row 5: Decision log -->
  <div class="panel full" id="p-log">
    <h2>Decision Log</h2>
    <div id="log"></div>
  </div>
</div>

<script>
(function(){
  const $ = id => document.getElementById(id);

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
      try { render(JSON.parse(e.data)); } catch(err) { console.error('render error', err); }
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

    // Market
    const m = s.market || {};
    $('market-ticker').textContent = m.ticker || '--';
    $('market-title').textContent = m.yes_sub_title || m.title || '--';
    $('market-volume').textContent = m.volume != null ? 'Vol: ' + fmtVol(m.volume) : '--';
    // Store close_time for the live countdown
    if (m.close_time) { window._closeTime = new Date(m.close_time).getTime(); }
    else { window._closeTime = null; }

    // BTC Price
    const snap = s.snapshot || {};
    $('btc-price').textContent = snap.btc_price ? '$' + Number(snap.btc_price).toLocaleString(undefined, {maximumFractionDigits:0}) : '--';
    $('btc-implied').textContent = 'Implied P(YES): ' + pct(snap.implied_prob);

    // Prediction
    const pred = s.prediction || {};
    $('pred-prob').textContent = pred.probability != null ? 'P(YES) = ' + pct(pred.probability) : '--';
    $('pred-conf').textContent = 'Confidence: ' + pct(pred.confidence);
    renderSignalBars(pred.signals || {});

    // Edge
    const edge = s.edge || {};
    $('edge-side').textContent = edge.side || '--';
    $('edge-raw').textContent = edge.raw_edge != null ? edge.raw_edge.toFixed(4) : '--';
    $('edge-fee').textContent = edge.fee_drag != null ? edge.fee_drag.toFixed(4) : '--';
    $('edge-net').textContent = edge.net_edge != null ? edge.net_edge.toFixed(4) : '--';
    $('edge-thresh').textContent = edge.min_threshold != null ? edge.min_threshold.toFixed(4) : '--';

    // Edge bar: show net_edge relative to max_edge scale of 0.10
    const maxScale = 0.10;
    const netE = edge.net_edge || 0;
    const thresh = edge.min_threshold || 0;
    const fillPct = Math.min(100, (netE / maxScale) * 100);
    const threshPct = Math.min(100, (thresh / maxScale) * 100);
    const ef = $('edge-fill');
    ef.style.width = fillPct + '%';
    ef.style.background = netE >= thresh && thresh > 0 ? '#3fb950' : '#f85149';
    $('edge-marker').style.left = threshPct + '%';

    // Verdict
    const vd = $('edge-verdict');
    vd.textContent = edge.decision || 'Waiting for data...';
    if (edge.passed) { vd.className = 'verdict trade'; }
    else if (edge.decision) { vd.className = 'verdict no-trade'; }
    else { vd.className = 'verdict no-market'; }

    // Orderbook
    const ob = snap.orderbook || {};
    $('ob-yes-bid').textContent = ob.best_yes_bid || '--';
    $('ob-no-bid').textContent = ob.best_no_bid || '--';
    $('ob-spread').textContent = ob.spread || '--';
    $('ob-yes-depth').textContent = ob.yes_depth != null ? ob.yes_depth : '--';
    $('ob-no-depth').textContent = ob.no_depth != null ? ob.no_depth : '--';
    $('ob-implied').textContent = pct(ob.implied_prob);

    // Features
    const feats = s.features || {};
    const fg = $('feat-grid');
    fg.innerHTML = '';
    for (const [k,v] of Object.entries(feats)) {
      const cell = document.createElement('div');
      cell.className = 'feat-cell';
      const cls = valClass(v);
      cell.innerHTML = '<span class="fn">' + k + '</span><span class="fv ' + cls + '">' + fmtNum(v) + '</span>';
      fg.appendChild(cell);
    }

    // Positions
    const positions = s.positions || [];
    const pl = $('positions-list');
    if (positions.length === 0) {
      pl.innerHTML = '<span class="sub">No open positions</span>';
    } else {
      pl.innerHTML = positions.map(p =>
        '<div class="pos-row"><span>' + p.ticker + '</span><span>' +
        p.side + ' x' + p.count + ' @ ' + p.avg_price + '</span></div>'
      ).join('');
    }

    // Risk
    const risk = s.risk || {};
    $('risk-balance').textContent = risk.balance != null ? '$' + Number(risk.balance).toFixed(2) : '--';
    $('risk-pnl').textContent = risk.daily_pnl != null ? '$' + Number(risk.daily_pnl).toFixed(2) : '--';
    $('risk-trades').textContent = risk.trades_today != null ? risk.trades_today : '--';
    $('risk-losses').textContent = risk.consecutive_losses != null ? risk.consecutive_losses : '--';
    $('risk-vol').textContent = risk.vol_regime || '--';
    $('risk-exposure').textContent = risk.exposure != null ? '$' + Number(risk.exposure).toFixed(2) : '--';

    if (risk.daily_pnl != null) {
      $('risk-pnl').style.color = risk.daily_pnl >= 0 ? '#3fb950' : '#f85149';
    }

    // Decision log
    const decisions = s.recent_decisions || [];
    const logDiv = $('log');
    logDiv.innerHTML = '';
    for (let i = decisions.length - 1; i >= 0; i--) {
      const d = decisions[i];
      const el = document.createElement('div');
      const cls = d.type === 'trade' ? 'trade-log' : d.type === 'no_market' ? 'no-market-log' : 'reject-log';
      el.className = 'log-entry ' + cls;
      el.textContent = d.time + '  #' + d.cycle + '  ' + d.summary;
      logDiv.appendChild(el);
    }
  }

  function renderSignalBars(signals) {
    const names = ['momentum', 'technical', 'flow', 'mean_reversion', 'funding', 'time_decay'];
    const labels = ['Momentum', 'Technical', 'Flow', 'MeanRev', 'Funding', 'TimeDec'];
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
