"""Dashboard server: SSE-based real-time bot state viewer."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from typing import Any

import structlog
from aiohttp import web

from src.dashboard.page import HTML_PAGE

logger = structlog.get_logger()


class DashboardState:
    """Mutable snapshot of every pipeline stage.

    Written by the trading loop (single asyncio writer, no locks needed).
    Read by the SSE handler to push to clients.
    """

    def __init__(self) -> None:
        self.cycle: int = 0
        self.mode: str = ""
        self.start_time: datetime | None = None

        # Pipeline stages (last-written, kept for backward compat)
        self.market: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {}
        self.features: dict[str, float] = {}
        self.prediction: dict[str, Any] = {}
        self.edge: dict[str, Any] = {}
        self.fomo: dict[str, Any] = {}
        self.signals: list[dict[str, Any]] = []
        self.sizing: dict[str, Any] = {}
        self.last_trade: dict[str, Any] = {}

        # Per-asset pipeline state: {"BTC": {market, snapshot, ...}, "ETH": {...}}
        self.per_asset: dict[str, dict[str, Any]] = {}

        # Health / risk
        self.risk: dict[str, Any] = {}
        self.positions: list[dict[str, Any]] = []
        self.health: dict[str, Any] = {}

        # Ring buffer of last 50 cycle outcomes
        self.recent_decisions: deque[dict[str, Any]] = deque(maxlen=50)

        # Per-asset trade history (last 5 results per asset)
        self.trade_history: dict[str, deque[dict[str, Any]]] = {}

        # Cumulative realized P&L per asset (e.g. {"BTC": 12.50, "ETH": -3.20})
        self.per_asset_pnl: dict[str, float] = {}

        # Kalshi settlement history (last 5 settled markets per asset)
        self.settlement_history: dict[str, list[dict[str, Any]]] = {}

        # Quiet hours config (passed to frontend for clock coloring)
        self.quiet_hours_utc: list[int] = []

        # Cumulative seconds where at least one position was open
        self.active_trading_seconds: float = 0.0

        # Trading pause toggle (controlled from dashboard UI)
        self.trading_paused: bool = False

        # Quiet hours override (skip quiet hours when master toggle is active)
        self.quiet_hours_override: bool = False

        # ETH killswitch (disable all ETH trading from dashboard)
        self.eth_disabled: bool = False

    def add_trade_result(
        self, asset: str, action: str, side: str, pnl: float, ticker: str,
        size_dollars: float = 0.0,
        signal_type: str = "",
    ) -> None:
        """Record a completed trade result for the trade history panel."""
        if asset not in self.trade_history:
            self.trade_history[asset] = deque(maxlen=10)
        self.trade_history[asset].append(
            {
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "action": action,
                "side": side,
                "pnl": round(pnl, 2),
                "size": round(size_dollars, 2),
                "ticker": ticker,
                "signal_type": signal_type,
            }
        )
        # Accumulate per-asset realized P&L
        self.per_asset_pnl[asset] = round(
            self.per_asset_pnl.get(asset, 0.0) + pnl, 2
        )

    def add_decision(
        self, cycle: int, decision_type: str, summary: str
    ) -> None:
        """Append a decision entry to the ring buffer.

        Args:
            cycle: Current cycle number.
            decision_type: One of 'trade', 'reject', 'no_market'.
            summary: Human-readable one-liner, e.g.
                     "NO TRADE: net edge 0.021 < threshold 0.030"
        """
        self.recent_decisions.append(
            {
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "cycle": cycle,
                "type": decision_type,
                "summary": summary,
            }
        )

    def to_json(self) -> str:
        """Serialize full state as JSON for SSE push."""
        uptime = 0.0
        if self.start_time:
            uptime = (
                datetime.now(timezone.utc) - self.start_time
            ).total_seconds()

        payload: dict[str, Any] = {
            "cycle": self.cycle,
            "mode": self.mode,
            "uptime_seconds": uptime,
            "market": self.market,
            "snapshot": self.snapshot,
            "features": self.features,
            "prediction": self.prediction,
            "edge": self.edge,
            "fomo": self.fomo,
            "signals": self.signals,
            "sizing": self.sizing,
            "last_trade": self.last_trade,
            "per_asset": self.per_asset,
            "risk": self.risk,
            "positions": self.positions,
            "recent_decisions": list(self.recent_decisions),
            "trade_history": {
                asset: list(trades)
                for asset, trades in self.trade_history.items()
            },
            "settlement_history": self.settlement_history,
            "per_asset_pnl": self.per_asset_pnl,
            "quiet_hours_utc": self.quiet_hours_utc,
            "active_trading_seconds": self.active_trading_seconds,
            "trading_paused": self.trading_paused,
            "quiet_hours_override": self.quiet_hours_override,
            "eth_disabled": self.eth_disabled,
        }
        return json.dumps(payload, default=str)


class DashboardServer:
    """aiohttp web server serving the dashboard UI and SSE state stream."""

    def __init__(
        self, state: DashboardState, host: str, port: int, db=None
    ) -> None:
        self._state = state
        self._host = host
        self._port = port
        self._db = db
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Start the web server (non-blocking)."""
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/events", self._handle_sse)
        self._app.router.add_get("/api/state", self._handle_api_state)
        self._app.router.add_post("/api/toggle-trading", self._handle_toggle_trading)
        self._app.router.add_post("/api/toggle-quiet-hours", self._handle_toggle_quiet_hours)
        self._app.router.add_post("/api/toggle-eth", self._handle_toggle_eth)
        self._app.router.add_get("/api/trades", self._handle_trades)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(
            "dashboard_started",
            url=f"http://{self._host}:{self._port}",
        )

    async def stop(self) -> None:
        """Gracefully shut down the web server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("dashboard_stopped")

    # -- Route handlers -------------------------------------------------------

    async def _handle_index(self, _request: web.Request) -> web.Response:
        return web.Response(text=HTML_PAGE, content_type="text/html")

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        try:
            while True:
                data = self._state.to_json()
                await response.write(
                    f"data: {data}\n\n".encode("utf-8")
                )
                await asyncio.sleep(1)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return response

    async def _handle_api_state(self, _request: web.Request) -> web.Response:
        return web.Response(
            text=self._state.to_json(),
            content_type="application/json",
        )

    async def _handle_toggle_trading(self, _request: web.Request) -> web.Response:
        self._state.trading_paused = not self._state.trading_paused
        # Master off → force quiet hours override off
        if self._state.trading_paused:
            self._state.quiet_hours_override = False
        status = "paused" if self._state.trading_paused else "active"
        logger.info("trading_toggled", trading_status=status)
        return web.Response(
            text=json.dumps({
                "trading_paused": self._state.trading_paused,
                "quiet_hours_override": self._state.quiet_hours_override,
            }),
            content_type="application/json",
        )

    async def _handle_trades(self, request: web.Request) -> web.Response:
        """Return recent trades from the database for the chart."""
        if not self._db:
            return web.Response(
                text=json.dumps([]),
                content_type="application/json",
            )
        try:
            limit = int(request.query.get("limit", "200"))
            trades = await self._db.get_recent_trades(limit=limit)
            # Convert Decimal/datetime to JSON-safe types
            for t in trades:
                for k, v in t.items():
                    if hasattr(v, "as_integer_ratio"):  # Decimal
                        t[k] = float(v)
            return web.Response(
                text=json.dumps(trades, default=str),
                content_type="application/json",
            )
        except Exception:
            logger.warning("trades_api_error", exc_info=True)
            return web.Response(
                text=json.dumps([]),
                content_type="application/json",
            )

    async def _handle_toggle_eth(self, _request: web.Request) -> web.Response:
        self._state.eth_disabled = not self._state.eth_disabled
        status = "disabled" if self._state.eth_disabled else "enabled"
        logger.info("eth_trading_toggled", eth_status=status)
        return web.Response(
            text=json.dumps({"eth_disabled": self._state.eth_disabled}),
            content_type="application/json",
        )

    async def _handle_toggle_quiet_hours(self, _request: web.Request) -> web.Response:
        # Only allow enabling if master toggle is active
        if self._state.trading_paused:
            return web.Response(
                text=json.dumps({
                    "quiet_hours_override": False,
                    "error": "Cannot override quiet hours while trading is paused",
                }),
                content_type="application/json",
            )
        self._state.quiet_hours_override = not self._state.quiet_hours_override
        logger.info(
            "quiet_hours_override_toggled",
            override=self._state.quiet_hours_override,
        )
        return web.Response(
            text=json.dumps({"quiet_hours_override": self._state.quiet_hours_override}),
            content_type="application/json",
        )
