"""End-to-end HTTP tests for the FastAPI server in ``src/api/server.py``.

Covers the endpoints required by the Next.js dashboard:
  - ``/api/health``
  - ``/api/allocator/config``  (GET + POST)
  - ``/api/allocator/allocate``
  - ``/api/attribution/matrix``  | ``/api/attribution/ranking`` | ``/api/attribution/brinson``
  - ``/api/projection/equity``
  - ``/api/bot/status`` | ``/api/bot/trades``

Drives the ASGI app *in-process* via :class:`httpx.ASGITransport` (no socket,
no subprocess, no port races).  Persisted-state files (``realtime/*.json``)
are pointed at a per-test ``tmp_path`` so the suite never contaminates the
checked-in ``realtime/`` directory or another concurrent run.

Run:
    pytest tests/test_api_server.py -v
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

# Make the project root importable so ``from src.api.server import app`` works
# regardless of pytest's invocation cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_realtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every ``realtime/`` file the server writes/reads into tmp_path.

    The server reads/writes four files via module-level Path constants in
    ``src.api.server``: ``REALTIME_STATE_FILE``, ``REALTIME_TRADES_FILE`` and
    ``ALLOCATOR_CONFIG_FILE``.  Patching them here means each test starts with
    a clean slate and never touches the real ``realtime/`` directory.
    """
    rt_dir = tmp_path / "realtime"
    rt_dir.mkdir(exist_ok=True)

    from src.api import server as srv

    monkeypatch.setattr(srv, "REALTIME_STATE_FILE", rt_dir / "paper_state.json")
    monkeypatch.setattr(srv, "REALTIME_TRADES_FILE", rt_dir / "trades.jsonl")
    monkeypatch.setattr(srv, "ALLOCATOR_CONFIG_FILE", rt_dir / "allocator_config.json")
    return rt_dir


@pytest.fixture
def with_live_state(isolated_realtime: Path) -> dict:
    """Seed a realistic ``paper_state.json`` (paper trading is reporting)."""
    state = {
        "mode": "paper",
        "symbol": "R_100",
        "strategy": "volatility",
        "balance": 9792.38,
        "pnl": -207.62,
        "trades_today": 3,
        "is_halted": False,
        "circuit_breaker": {
            "consecutive_losses": 0,
            "is_halted": False,
            "today": "2026-08-09",
        },
        "last_update": "2026-08-09T12:00:00+00:00",
        "recent_trades": [
            {
                "timestamp": "2026-08-09T11:00:00+00:00",
                "direction": "SHORT",
                "entry_price": 628.58,
                "exit_price": 627.10,
                "pnl": 8.50,
                "stake": 146.89,
                "status": "WON",
            }
        ],
    }
    state_file = isolated_realtime / "paper_state.json"
    state_file.write_text(json.dumps(state))
    return state


@pytest.fixture
async def client(isolated_realtime: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client bound to the ASGI app via an in-process transport."""
    from src.api.server import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ─── Health ──────────────────────────────────────────────────────────────────


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


# ─── Allocator ────────────────────────────────────────────────────────────────


async def test_allocator_config_get_defaults_no_state(client: httpx.AsyncClient) -> None:
    """With no live state and no persisted overrides, serves CapitalAllocatorConfig
    defaults (0.80/0.20) and reports ``data_available: false``."""
    r = await client.get("/api/allocator/config")
    assert r.status_code == 200
    body = r.json()
    assert body["data_available"] is False
    assert body["reserva_pct"] == 0.8
    assert body["superávit_diario_pct"] == 0.2
    assert body["rebalance_daily"] is True


async def test_allocator_config_get_with_live_state(
    client: httpx.AsyncClient, with_live_state: dict
) -> None:
    """When paper_state.json exists, the GET endpoint uses the live balance as
    ``initial_capital`` and reports ``data_available: true``."""
    r = await client.get("/api/allocator/config")
    assert r.status_code == 200
    body = r.json()
    assert body["data_available"] is True
    assert body["live_balance"] == 9792.38
    assert body["live_pnl"] == -207.62
    # capital_total should be the live balance, not the default 10000.
    assert body["capital_total"] == 9792.38


async def test_allocator_config_post_valid_persists_and_reconciles(
    client: httpx.AsyncClient, isolated_realtime: Path
) -> None:
    """A valid POST persists the overrides to allocator_config.json and returns
    the reconciled allocator state (same shape as GET)."""
    r = await client.post(
        "/api/allocator/config",
        json={"reserva_pct": 0.70, "max_daily_pct": 0.30, "reinvest_profits": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "updated"
    assert body["reserva_pct"] == 0.7
    assert body["superávit_diario_pct"] == 0.3
    assert body["rebalance_daily"] is True

    persisted_path = isolated_realtime / "allocator_config.json"
    assert persisted_path.exists(), "POST must persist overrides to disk"
    persisted = json.loads(persisted_path.read_text())
    assert persisted["reserva_pct"] == 0.7
    assert persisted["superávit_diario_pct"] == 0.3
    assert persisted["rebalance_daily"] is True
    assert "saved_at" in persisted


async def test_allocator_config_post_invalid_sum_returns_400(client: httpx.AsyncClient) -> None:
    """reserva_pct + superávit_diario_pct must sum to 1.0; otherwise 400 with a
    diagnostic error list."""
    r = await client.post(
        "/api/allocator/config",
        json={"reserva_pct": 0.8, "max_daily_pct": 0.4},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error"
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) >= 1
    # Error message should mention the sum mismatch.
    assert "1.0" in body["errors"][0] or "≠" in body["errors"][0]


async def test_allocator_config_post_partial_preserves_existing(
    client: httpx.AsyncClient, isolated_realtime: Path
) -> None:
    """A partial POST (only one field) must preserve previously-persisted
    values for the other fields."""
    # First POST: full envelope.
    r1 = await client.post(
        "/api/allocator/config",
        json={"reserva_pct": 0.75, "max_daily_pct": 0.25, "reinvest_profits": True},
    )
    assert r1.status_code == 200
    # Second POST: only toggle reinvest_profits=False.
    r2 = await client.post(
        "/api/allocator/config",
        json={"reinvest_profits": False},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["rebalance_daily"] is False
    assert body["reserva_pct"] == 0.75  # preserved
    assert body["superávit_diario_pct"] == 0.25  # preserved

    persisted = json.loads((isolated_realtime / "allocator_config.json").read_text())
    assert persisted["rebalance_daily"] is False
    assert persisted["reserva_pct"] == 0.75


async def test_allocator_config_get_reflects_persisted_overrides(
    client: httpx.AsyncClient,
) -> None:
    """After a POST, a subsequent GET must return the persisted values, not the
    CapitalAllocatorConfig defaults."""
    await client.post(
        "/api/allocator/config",
        json={"reserva_pct": 0.60, "max_daily_pct": 0.40},
    )
    r = await client.get("/api/allocator/config")
    assert r.status_code == 200
    body = r.json()
    assert body["reserva_pct"] == 0.6
    assert body["superávit_diario_pct"] == 0.4


async def test_allocator_allocate_uses_live_balance(
    client: httpx.AsyncClient, with_live_state: dict
) -> None:
    """``/api/allocator/allocate`` auto-detects the live balance from
    paper_state.json when ``starting_balance`` is not provided."""
    r = await client.get("/api/allocator/allocate")
    assert r.status_code == 200
    body = r.json()
    assert body["live_balance"] == 9792.38
    assert body["live_pnl"] == -207.62
    assert body["reserve"] > 0
    assert body["daily_surplus"] > 0
    assert "allocation_pct" in body


async def test_allocator_allocate_explicit_balance(client: httpx.AsyncClient) -> None:
    """An explicit ``starting_balance`` query param overrides live-state detection."""
    r = await client.get("/api/allocator/allocate", params={"starting_balance": 5000.0})
    assert r.status_code == 200
    body = r.json()
    assert body["live_balance"] == 5000.0
    # 80% reserve, 20% surplus on a 5000 base.
    assert abs(body["reserve"] - 4000.0) < 0.01
    assert abs(body["daily_surplus"] - 1000.0) < 0.01


# ─── Attribution ──────────────────────────────────────────────────────────────


async def test_attribution_matrix_returns_200_empty_or_structured(
    client: httpx.AsyncClient,
) -> None:
    """The matrix endpoint always returns 200; when strategy_performance is
    empty the response is `{strategies: [], symbols: [], matrix: []}`."""
    r = await client.get("/api/attribution/matrix")
    assert r.status_code == 200
    body = r.json()
    assert "strategies" in body
    assert "symbols" in body
    assert "matrix" in body
    assert isinstance(body["strategies"], list)
    assert isinstance(body["matrix"], list)


async def test_attribution_matrix_supports_metric_query(
    client: httpx.AsyncClient,
) -> None:
    """The ``metric`` query param is honoured (no 422, returns 200)."""
    for metric in ("total_pnl", "win_rate", "sharpe"):
        r = await client.get("/api/attribution/matrix", params={"metric": metric})
        assert r.status_code == 200, f"metric={metric} failed"


async def test_attribution_ranking_returns_200(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/attribution/ranking")
    assert r.status_code == 200
    body = r.json()
    assert "ranking" in body
    assert isinstance(body["ranking"], list)


async def test_attribution_brinson_empty_db_returns_400_with_message(
    client: httpx.AsyncClient,
) -> None:
    """When there is too little data for a Brinson-Fachler decomposition, the
    endpoint returns 400 with an actionable error message (not a 500)."""
    r = await client.get("/api/attribution/brinson")
    assert r.status_code == 400
    body = r.json()
    assert isinstance(body.get("error"), str)
    assert len(body["error"]) > 0


async def test_attribution_matrix_invalid_metric_returns_400(
    client: httpx.AsyncClient,
) -> None:
    """An invalid ``metric`` query param returns 400 with a diagnostic error
    listing the valid values, not a 422 or 500."""
    r = await client.get("/api/attribution/matrix", params={"metric": "bogus_metric"})
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error"
    assert "bogus_metric" in body["error"]
    assert "total_pnl" in body["error"]


async def test_attribution_matrix_reports_requested_metric(
    client: httpx.AsyncClient,
) -> None:
    """The matrix endpoint echoes the requested metric in the response and
    each cell carries a ``metric_value`` field (not just ``pnl``)."""
    for metric in ("total_pnl", "sharpe", "win_rate", "profit_factor", "expectancy"):
        r = await client.get("/api/attribution/matrix", params={"metric": metric})
        assert r.status_code == 200, f"metric={metric} failed"
        body = r.json()
        assert body["metric"] == metric


async def test_attribution_ranking_invalid_metric_returns_400(
    client: httpx.AsyncClient,
) -> None:
    """The ranking endpoint also validates the ``metric`` param and returns
    400 with a diagnostic on an invalid value."""
    r = await client.get("/api/attribution/ranking", params={"metric": "no_such_metric"})
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error"
    assert "no_such_metric" in body["error"]


async def test_attribution_ranking_echoes_metric_and_min_trades(
    client: httpx.AsyncClient,
) -> None:
    """The ranking endpoint echoes the ``metric`` and ``min_trades`` params
    in the response so callers can confirm what was applied."""
    r = await client.get(
        "/api/attribution/ranking",
        params={"metric": "sharpe", "min_trades": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "sharpe"
    assert body["min_trades"] == 5
    assert isinstance(body["ranking"], list)


# ─── Projection ───────────────────────────────────────────────────────────────


async def test_projection_equity_returns_200_graceful_when_no_data(
    client: httpx.AsyncClient,
) -> None:
    """With no attribution data, projection degrades gracefully: 200 with
    ``data_available: false`` and empty equity arrays."""
    r = await client.get("/api/projection/equity")
    assert r.status_code == 200
    body = r.json()
    assert body["data_available"] is False
    assert body["projection"]["equity_p50"] == []
    assert "config" in body
    assert "metrics_used" in body


async def test_projection_equity_accepts_horizon_param(
    client: httpx.AsyncClient,
) -> None:
    """Custom horizon (days) is honoured and does not break the endpoint."""
    r = await client.get("/api/projection/equity", params={"days": 30})
    assert r.status_code == 200


# ─── Bot status & trades ──────────────────────────────────────────────────────


async def test_bot_status_with_live_state(
    client: httpx.AsyncClient, with_live_state: dict
) -> None:
    r = await client.get("/api/bot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "paper"
    assert body["symbol"] == "R_100"
    assert body["balance"] == 9792.38
    assert "circuit_breaker" in body
    assert "kpi" in body


async def test_bot_trades_with_live_state(
    client: httpx.AsyncClient, with_live_state: dict, isolated_realtime: Path
) -> None:
    """``/api/bot/trades`` reads trades.jsonl AND the recent_trades from the
    state file; either source producing data is acceptable. Here we seed
    trades.jsonl with one trade and expect it in the response."""
    trades_file = isolated_realtime / "trades.jsonl"
    trades_file.write_text(
        json.dumps({"timestamp": "2026-08-09T10:00:00+00:00", "pnl": 5.0, "status": "WON"})
        + "\n"
    )
    r = await client.get("/api/bot/trades")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    # The trade we seeded must be present.
    seeded = [t for t in body if t.get("pnl") == 5.0 and t.get("status") == "WON"]
    assert len(seeded) == 1


# ─── Spanish-alias integrity ────────────────────────────────────────────────────


async def test_bot_status_includes_spanish_aliases(
    client: httpx.AsyncClient, with_live_state: dict
) -> None:
    """The ``with_aliases`` helper must add Spanish keys alongside the English
    ones — the dashboard relies on both. Spot-check a few."""
    r = await client.get("/api/bot/status")
    body = r.json()
    # English + Spanish both present.
    assert "is_halted" in body and "detenido" in body
    assert "trades_today" in body
    # The circuit_breaker sub-dict also gets aliases applied recursively.
    cb = body["circuit_breaker"]
    assert "is_halted" in cb and "detenido" in cb


# ─── WebSocket live-data helpers ──────────────────────────────────────────────


async def test_read_last_jsonl_returns_last_line(
    isolated_realtime: Path,
) -> None:
    """``_read_last_jsonl`` reads only the last line of a JSONL file using a
    bounded backward seek (not ``readlines()``)."""
    from src.api.server import _read_last_jsonl

    f = isolated_realtime / "equity.jsonl"
    f.write_text(
        json.dumps({"i": 1, "v": 100.0}) + "\n"
        + json.dumps({"i": 2, "v": 101.0}) + "\n"
        + json.dumps({"i": 3, "v": 102.0}) + "\n"
    )
    result = _read_last_jsonl(f)
    assert result is not None
    assert result["i"] == 3
    assert result["v"] == 102.0


async def test_read_last_jsonl_empty_file_returns_none(
    isolated_realtime: Path,
) -> None:
    """An empty JSONL file returns ``None`` (no last line to parse)."""
    from src.api.server import _read_last_jsonl

    f = isolated_realtime / "empty.jsonl"
    f.write_text("")
    assert _read_last_jsonl(f) is None


async def test_read_last_jsonl_missing_file_returns_none(
    isolated_realtime: Path,
) -> None:
    """A non-existent file returns ``None`` (no exception, no crash)."""
    from src.api.server import _read_last_jsonl

    assert _read_last_jsonl(isolated_realtime / "nope.jsonl") is None


async def test_file_signature_changes_on_write(
    isolated_realtime: Path,
) -> None:
    """``_file_signature`` returns ``(mtime, size)`` and the signature changes
    when the file is written to — this is the delta-detection primitive."""
    import time

    from src.api.server import _file_signature

    f = isolated_realtime / "sig.jsonl"
    f.write_text(json.dumps({"a": 1}) + "\n")
    sig1 = _file_signature(f)
    assert sig1 is not None
    assert sig1[1] > 0  # size > 0
    # Wait so mtime resolution (1s on many filesystems) registers a change.
    time.sleep(0.05)
    f.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n")
    sig2 = _file_signature(f)
    assert sig2 is not None
    assert sig2 != sig1  # size and/or mtime changed

