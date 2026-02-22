"""Tests for dashboard state and toggle endpoints."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.dashboard.server import DashboardServer, DashboardState


@pytest.fixture
def dashboard_state() -> DashboardState:
    return DashboardState()


class TestDashboardState:
    def test_eth_disabled_default_false(self, dashboard_state: DashboardState):
        """ETH killswitch is off by default."""
        assert dashboard_state.eth_disabled is False

    def test_eth_disabled_in_json(self, dashboard_state: DashboardState):
        """eth_disabled field is serialized in the SSE payload."""
        data = json.loads(dashboard_state.to_json())
        assert "eth_disabled" in data
        assert data["eth_disabled"] is False

    def test_eth_disabled_true_in_json(self, dashboard_state: DashboardState):
        """eth_disabled=True is reflected in JSON."""
        dashboard_state.eth_disabled = True
        data = json.loads(dashboard_state.to_json())
        assert data["eth_disabled"] is True

    def test_trading_paused_in_json(self, dashboard_state: DashboardState):
        """Verify other toggles still present alongside eth_disabled."""
        data = json.loads(dashboard_state.to_json())
        assert "trading_paused" in data
        assert "quiet_hours_override" in data
        assert "eth_disabled" in data


    def test_strategy_toggles_in_json(self, dashboard_state: DashboardState):
        """All 6 strategy toggles are present in JSON output."""
        data = json.loads(dashboard_state.to_json())
        assert "strategy_toggles" in data
        toggles = data["strategy_toggles"]
        expected_keys = {"directional", "fomo", "certainty_scalp", "settlement_ride", "market_making", "trend_guard", "mm_vol_filter"}
        assert set(toggles.keys()) == expected_keys
        assert toggles["directional"] is True

    def test_strategy_toggles_reflect_changes(self, dashboard_state: DashboardState):
        """Mutating strategy_toggles dict is reflected in JSON."""
        dashboard_state.strategy_toggles["fomo"] = False
        data = json.loads(dashboard_state.to_json())
        assert data["strategy_toggles"]["fomo"] is False


def _build_app(state: DashboardState) -> web.Application:
    """Build a minimal aiohttp app with dashboard toggle routes."""
    server = DashboardServer(state, "127.0.0.1", 0)
    app = web.Application()
    app.router.add_post("/api/toggle-eth", server._handle_toggle_eth)
    app.router.add_post("/api/toggle-trading", server._handle_toggle_trading)
    app.router.add_post("/api/toggle-strategy", server._handle_toggle_strategy)
    return app


@pytest.mark.asyncio
async def test_toggle_eth_endpoint(dashboard_state: DashboardState):
    """POST /api/toggle-eth flips eth_disabled."""
    app = _build_app(dashboard_state)
    async with TestClient(TestServer(app)) as client:
        # First toggle: off → on
        resp = await client.post("/api/toggle-eth")
        assert resp.status == 200
        data = await resp.json()
        assert data["eth_disabled"] is True
        assert dashboard_state.eth_disabled is True

        # Second toggle: on → off
        resp = await client.post("/api/toggle-eth")
        assert resp.status == 200
        data = await resp.json()
        assert data["eth_disabled"] is False
        assert dashboard_state.eth_disabled is False


@pytest.mark.asyncio
async def test_eth_toggle_independent_of_master(dashboard_state: DashboardState):
    """ETH toggle works regardless of master trading toggle state."""
    app = _build_app(dashboard_state)
    async with TestClient(TestServer(app)) as client:
        # Pause master trading
        await client.post("/api/toggle-trading")
        assert dashboard_state.trading_paused is True

        # ETH toggle should still work
        resp = await client.post("/api/toggle-eth")
        data = await resp.json()
        assert data["eth_disabled"] is True
        assert dashboard_state.eth_disabled is True


@pytest.mark.asyncio
async def test_toggle_strategy_endpoint(dashboard_state: DashboardState):
    """POST /api/toggle-strategy flips the boolean and returns updated dict."""
    app = _build_app(dashboard_state)
    async with TestClient(TestServer(app)) as client:
        # directional starts True, toggle off
        resp = await client.post(
            "/api/toggle-strategy",
            json={"name": "directional"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["strategy_toggles"]["directional"] is False
        assert dashboard_state.strategy_toggles["directional"] is False

        # Toggle back on
        resp = await client.post(
            "/api/toggle-strategy",
            json={"name": "directional"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["strategy_toggles"]["directional"] is True


@pytest.mark.asyncio
async def test_toggle_unknown_strategy(dashboard_state: DashboardState):
    """POST /api/toggle-strategy returns 400 for unknown strategy name."""
    app = _build_app(dashboard_state)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/toggle-strategy",
            json={"name": "nonexistent"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data
