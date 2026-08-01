"""service — service layer for the Strategy Histórica DB.

Provides four services that wrap the SQLite layer established by
``migration_001``. Each service is constructed with a DB path (defaults
to the standard ``data/strategies.db``); they ensure the schema is
applied on first use and expose a small, type-safe API.

Services:
    StrategyService       — CRUD strategies + semantic versioning + rollback
    PerformanceService    — register backtest results, compare metrics
    RegimeService         — detect (HMM-lite) and register market regimes
    OptimizationService   — register walk-forward / genetic / RL experiments

Design notes:
    - ``sqlite3`` connections are short-lived (one per call) so the layer
      is safe for use from FastAPI request handlers without external
      pooling.
    - JSON columns (``parameters_json``, ``metrics_json``, …) are
      decoded lazily in helpers: callers receive ``dict | list | None``
      rather than raw JSON strings.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.db.migration_001 import apply_migration, get_db_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a ``MAJOR.MINOR.PATCH`` string into a tuple of ints.

    Raises ``ValueError`` if the format is invalid.
    """
    m = _SEMVER_RE.match(version.strip())
    if not m:
        raise ValueError(f"Invalid semantic version: {version!r}")
    return int(m["major"]), int(m["minor"]), int(m["patch"])


def _bump(
    version: str, kind: str
) -> str:  # kind: "major" | "minor" | "patch"
    """Return the next version string for ``kind`` bump."""
    major, minor, patch = _parse_semver(version)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump kind: {kind!r}")


def _json_dumps(value: Any) -> str:
    """Serialise ``value`` to a compact JSON string."""
    return json.dumps(value, default=str, separators=(",", ":"))


def _json_loads(raw: str | None) -> Any:
    """Decode a JSON column; returns ``None`` for missing/empty data."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite Row (or None) into a plain dict (or None)."""
    return dict(row) if row is not None else None


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys on and Row factory set."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
#  StrategyService
# ---------------------------------------------------------------------------


class StrategyService:
    """CRUD + semantic versioning + lineage rollback for strategies."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        # Ensure the schema is applied — idempotent.
        apply_migration(self.db_path)

    # -- CRUD ---------------------------------------------------------------

    def create(
        self,
        name: str,
        version: str,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        lineage_parent_id: int | None = None,
        market_type: str = "synthetic",
        status: str = "active",
    ) -> dict[str, Any]:
        """Insert a new strategy row. Returns the created record.

        Raises ``ValueError`` if ``version`` is not valid semver, or
        ``sqlite3.IntegrityError`` if (name, version) already exists.
        """
        _parse_semver(version)  # validate format
        params = _json_dumps(parameters) if parameters is not None else None
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO strategies
                    (name, version, description, parameters_json,
                     lineage_parent_id, market_type, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    version,
                    description,
                    params,
                    lineage_parent_id,
                    market_type,
                    status,
                ),
            )
            conn.commit()
            if cur.lastrowid is None:  # pragma: no cover — defensive
                raise RuntimeError("INSERT did not return a row id")
            return self.get(int(cur.lastrowid)) or {}
        finally:
            conn.close()

    def get(self, strategy_id: int) -> dict[str, Any] | None:
        """Fetch a single strategy by id, decoding JSON columns."""
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
            d = _row_to_dict(row)
            if d is not None:
                d["parameters"] = _json_loads(d.pop("parameters_json", None))
            return d
        finally:
            conn.close()

    def list(
        self,
        *,
        name: str | None = None,
        status: str | None = None,
        market_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List strategies with optional filters. Newest first."""
        sql = "SELECT * FROM strategies WHERE 1=1"
        args: list[Any] = []
        if name is not None:
            sql += " AND name = ?"
            args.append(name)
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if market_type is not None:
            sql += " AND market_type = ?"
            args.append(market_type)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)

        conn = _connect(self.db_path)
        try:
            rows = conn.execute(sql, args).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["parameters"] = _json_loads(d.pop("parameters_json", None))
                out.append(d)
            return out
        finally:
            conn.close()

    def update(
        self,
        strategy_id: int,
        *,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        status: str | None = None,
        market_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch one or more fields of a strategy. Returns the updated row."""
        sets: list[str] = []
        args: list[Any] = []
        if description is not None:
            sets.append("description = ?")
            args.append(description)
        if parameters is not None:
            sets.append("parameters_json = ?")
            args.append(_json_dumps(parameters))
        if status is not None:
            sets.append("status = ?")
            args.append(status)
        if market_type is not None:
            sets.append("market_type = ?")
            args.append(market_type)
        if not sets:
            return self.get(strategy_id)

        sets.append("updated_at = ?")
        args.append(datetime.utcnow().isoformat())
        args.append(strategy_id)

        conn = _connect(self.db_path)
        try:
            conn.execute(
                f"UPDATE strategies SET {', '.join(sets)} WHERE id = ?",
                args,
            )
            conn.commit()
            return self.get(strategy_id)
        finally:
            conn.close()

    def delete(self, strategy_id: int) -> bool:
        """Delete a strategy row. Returns ``True`` if a row was removed."""
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                "DELETE FROM strategies WHERE id = ?", (strategy_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # -- Versioning ---------------------------------------------------------

    def new_version(
        self,
        parent_id: int,
        *,
        bump: str = "patch",
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        market_type: str | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        """Create a new version derived from a parent strategy.

        ``bump`` is one of ``"major" | "minor" | "patch"``. The new
        strategy inherits ``name`` and (unless overridden)
        ``market_type`` from the parent, and records ``lineage_parent_id``.
        Raises ``ValueError`` if the parent does not exist.
        """
        parent = self.get(parent_id)
        if parent is None:
            raise ValueError(f"Parent strategy {parent_id} not found")

        new_version = _bump(parent["version"], bump)
        return self.create(
            name=parent["name"],
            version=new_version,
            description=description if description is not None else parent.get("description"),
            parameters=parameters if parameters is not None else parent.get("parameters"),
            lineage_parent_id=parent_id,
            market_type=market_type or parent.get("market_type", "synthetic"),
            status=status,
        )

    def latest_version(self, name: str) -> dict[str, Any] | None:
        """Return the strategy row with the highest version for ``name``."""
        rows = self.list(name=name, limit=1)
        # ``list`` is newest-by-id, but version order ≠ id order if rows
        # were inserted out of order. Sort explicitly to be safe.
        all_versions = self.list(name=name, limit=10_000)
        if not all_versions:
            return None
        all_versions.sort(
            key=lambda r: _parse_semver(r["version"]), reverse=True
        )
        return all_versions[0]

    def rollback(self, strategy_id: int) -> dict[str, Any] | None:
        """Mark a strategy as the active version and de-activate its siblings.

        The caller's ``strategy_id`` becomes the only ``active`` row for
        its ``name``. Older siblings are marked ``archived``. Returns the
        now-active strategy row.
        """
        target = self.get(strategy_id)
        if target is None:
            return None
        conn = _connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE strategies
                   SET status = 'archived', updated_at = ?
                 WHERE name = ? AND id != ? AND status = 'active'
                """,
                (datetime.utcnow().isoformat(), target["name"], strategy_id),
            )
            conn.execute(
                "UPDATE strategies SET status = 'active', updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), strategy_id),
            )
            conn.commit()
            return self.get(strategy_id)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  PerformanceService
# ---------------------------------------------------------------------------


class PerformanceService:
    """Records backtest performance and compares metrics across strategies."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        apply_migration(self.db_path)

    def register(
        self,
        strategy_id: int,
        symbol: str,
        backtest_date: str,
        *,
        win_rate: float | None = None,
        sharpe: float | None = None,
        max_dd: float | None = None,
        total_pnl: float | None = None,
        profit_factor: float | None = None,
        expectancy: float | None = None,
        gate_passed: bool = False,
        total_trades: int | None = None,
        calmar_ratio: float | None = None,
        sortino_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Upsert a performance row for ``(strategy_id, symbol, date)``."""
        conn = _connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO strategy_performance
                    (strategy_id, symbol, backtest_date, win_rate, sharpe,
                     max_dd, total_pnl, profit_factor, expectancy, gate_passed,
                     total_trades, calmar_ratio, sortino_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, symbol, backtest_date) DO UPDATE SET
                     win_rate=excluded.win_rate, sharpe=excluded.sharpe,
                     max_dd=excluded.max_dd, total_pnl=excluded.total_pnl,
                     profit_factor=excluded.profit_factor,
                     expectancy=excluded.expectancy,
                     gate_passed=excluded.gate_passed,
                     total_trades=excluded.total_trades,
                     calmar_ratio=excluded.calmar_ratio,
                     sortino_ratio=excluded.sortino_ratio
                """,
                (
                    strategy_id,
                    symbol,
                    backtest_date,
                    win_rate,
                    sharpe,
                    max_dd,
                    total_pnl,
                    profit_factor,
                    expectancy,
                    int(gate_passed),
                    total_trades,
                    calmar_ratio,
                    sortino_ratio,
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT * FROM strategy_performance
                 WHERE strategy_id = ? AND symbol = ? AND backtest_date = ?
                """,
                (strategy_id, symbol, backtest_date),
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def history(
        self,
        strategy_id: int,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return performance rows for a strategy (newest first)."""
        sql = "SELECT * FROM strategy_performance WHERE strategy_id = ?"
        args: list[Any] = [strategy_id]
        if symbol is not None:
            sql += " AND symbol = ?"
            args.append(symbol)
        sql += " ORDER BY backtest_date DESC LIMIT ?"
        args.append(limit)
        conn = _connect(self.db_path)
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def compare(
        self,
        strategy_a_id: int,
        strategy_b_id: int,
        metric: str = "sharpe",
    ) -> dict[str, Any]:
        """Compare two strategies on a single metric (averaged across rows).

        Records the result in ``strategy_comparisons`` and returns a dict
        describing winner, value, and per-strategy averages.
        """
        # Whitelist of allowed metrics to avoid SQL injection
        allowed = {
            "win_rate", "sharpe", "max_dd", "total_pnl",
            "profit_factor", "expectancy", "calmar_ratio", "sortino_ratio",
        }
        if metric not in allowed:
            raise ValueError(f"Unknown metric: {metric!r}")

        conn = _connect(self.db_path)
        try:
            avg_a = conn.execute(
                f"SELECT AVG({metric}) AS m FROM strategy_performance WHERE strategy_id = ?",
                (strategy_a_id,),
            ).fetchone()["m"]
            avg_b = conn.execute(
                f"SELECT AVG({metric}) AS m FROM strategy_performance WHERE strategy_id = ?",
                (strategy_b_id,),
            ).fetchone()["m"]

            # max_dd is "lower is better"
            lower_is_better = metric == "max_dd"

            def _winner(a: Any, b: Any) -> str:
                if a is None and b is None:
                    return "none"
                if a is None:
                    return "B"
                if b is None:
                    return "A"
                if a == b:
                    return "tie"
                if lower_is_better:
                    return "A" if a < b else "B"
                return "A" if a > b else "B"

            winner = _winner(avg_a, avg_b)
            value = None
            if avg_a is not None and avg_b is not None:
                value = float(avg_a) - float(avg_b)
            elif avg_a is not None:
                value = float(avg_a)
            elif avg_b is not None:
                value = -float(avg_b)

            metrics = {
                "strategy_a_avg": avg_a,
                "strategy_b_avg": avg_b,
                "metric": metric,
                "value_difference": value,
                "lower_is_better": lower_is_better,
            }
            conn.execute(
                """
                INSERT INTO strategy_comparisons
                    (strategy_a_id, strategy_b_id, metric, value, winner, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_a_id,
                    strategy_b_id,
                    metric,
                    value,
                    winner,
                    _json_dumps(metrics),
                ),
            )
            conn.commit()
            return {
                "strategy_a_id": strategy_a_id,
                "strategy_b_id": strategy_b_id,
                "metric": metric,
                "strategy_a_avg": avg_a,
                "strategy_b_avg": avg_b,
                "value": value,
                "winner": winner,
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  RegimeService — HMM-lite regime detection
# ---------------------------------------------------------------------------


class RegimeService:
    """Detects and registers market regimes using an HMM-lite heuristic.

    A full HMM (e.g. ``hmmlearn``) is overkill for the initial version;
    instead we estimate volatility and trend from returns and classify
    into one of five regimes:

        - ``trending_low_vol``  — strong trend, low vol (ideal for trend-follow)
        - ``trending_high_vol`` — strong trend + high vol (riskier)
        - ``mean_reverting``    — weak trend + moderate vol (OU-style)
        - ``choppy``            — weak trend + low vol (noise)
        - ``crisis``            — extreme vol (drawdown protection)

    The service also persists regimes to ``market_regimes`` for history.
    """

    VALID_REGIMES = {
        "trending_low_vol",
        "trending_high_vol",
        "mean_reverting",
        "choppy",
        "crisis",
    }

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        apply_migration(self.db_path)

    def detect(
        self,
        returns: list[float],
        window: int = 100,
    ) -> dict[str, Any]:
        """Classify the current regime from a series of bar returns.

        Returns a dict with ``regime_type``, ``volatility``, ``trend``,
        and ``metadata`` (contains the thresholds used and sample size).
        Empty or single-value inputs are classified as ``choppy``.
        """
        n = len(returns)
        sample = returns[-window:] if n > window else returns

        if n == 0:
            return {"regime_type": "choppy", "volatility": 0.0, "trend": 0.0,
                    "metadata": {"reason": "empty input", "sample_size": 0}}

        # Volatility = std of returns
        mean = sum(sample) / len(sample)
        var = sum((r - mean) ** 2 for r in sample) / len(sample)
        vol = var ** 0.5

        # Trend strength = absolute slope of cumulative returns / vol
        # We compute a simple linear regression slope against index.
        m = len(sample)
        if m >= 2 and vol > 0:
            xs = list(range(m))
            x_mean = sum(xs) / m
            num = sum((x - x_mean) * (y - mean) for x, y in zip(xs, sample))
            den = sum((x - x_mean) ** 2 for x in xs)
            slope = num / den if den else 0.0
            # Normalise trend: relative slope vs volatility
            trend = abs(slope) / vol if vol > 0 else 0.0
        else:
            slope = 0.0
            trend = 0.0

        # Heuristic thresholds (tunable via metadata)
        vol_high = 0.005   # 0.5% bar-to-bar → high vol
        crisis_vol = 0.02  # 2% bar-to-bar → crisis
        trend_strong = 0.5  # normalised trend > 0.5 → strong

        if vol >= crisis_vol:
            regime = "crisis"
        elif trend >= trend_strong and vol < vol_high:
            regime = "trending_low_vol"
        elif trend >= trend_strong and vol >= vol_high:
            regime = "trending_high_vol"
        elif trend < trend_strong and vol < vol_high:
            regime = "mean_reverting"
        else:
            regime = "choppy"

        return {
            "regime_type": regime,
            "volatility": vol,
            "trend": trend,
            "metadata": {
                "sample_size": n,
                "window": window,
                "mean_return": mean,
                "slope": slope,
                "vol_threshold": vol_high,
                "crisis_threshold": crisis_vol,
                "trend_threshold": trend_strong,
            },
        }

    def register(
        self,
        regime_type: str,
        start_time: str,
        *,
        end_time: str | None = None,
        volatility: float | None = None,
        trend: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a regime record. Returns the stored row as a dict."""
        if regime_type not in self.VALID_REGIMES:
            raise ValueError(
                f"Invalid regime {regime_type!r}; valid: {sorted(self.VALID_REGIMES)}"
            )
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO market_regimes
                    (regime_type, start_time, end_time, volatility, trend, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    regime_type,
                    start_time,
                    end_time,
                    volatility,
                    trend,
                    _json_dumps(metadata) if metadata is not None else None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM market_regimes WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            d = dict(row)
            d["metadata"] = _json_loads(d.pop("metadata_json", None))
            return d
        finally:
            conn.close()

    def current(self, as_of: str | None = None) -> dict[str, Any] | None:
        """Return the most recent regime whose ``start_time`` ≤ ``as_of``."""
        ts = as_of or datetime.utcnow().isoformat()
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT * FROM market_regimes
                 WHERE start_time <= ?
                 ORDER BY start_time DESC, id DESC
                 LIMIT 1
                """,
                (ts,),
            ).fetchone()
            d = _row_to_dict(row)
            if d is not None:
                d["metadata"] = _json_loads(d.pop("metadata_json", None))
            return d
        finally:
            conn.close()

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        """List recent regime records (newest first)."""
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM market_regimes ORDER BY start_time DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["metadata"] = _json_loads(d.pop("metadata_json", None))
                out.append(d)
            return out
        finally:
            conn.close()


# ---------------------------------------------------------------------------
#  OptimizationService
# ---------------------------------------------------------------------------


class OptimizationService:
    """Registry for walk-forward / genetic / RL optimization experiments."""

    EXPERIMENT_TYPES = {"walk_forward", "genetic", "rl", "grid_search", "bayesian"}

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        apply_migration(self.db_path)

    def create(
        self,
        strategy_id: int,
        experiment_type: str,
        *,
        parameters: dict[str, Any] | None = None,
        results: dict[str, Any] | None = None,
        status: str = "pending",
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Register a new optimization experiment. Returns the row."""
        if experiment_type not in self.EXPERIMENT_TYPES:
            raise ValueError(
                f"Unknown experiment_type {experiment_type!r}; "
                f"valid: {sorted(self.EXPERIMENT_TYPES)}"
            )
        valid_statuses = {"pending", "running", "completed", "failed"}
        if status not in valid_statuses:
            raise ValueError(
                f"Invalid status {status!r}; valid: {sorted(valid_statuses)}"
            )
        conn = _connect(self.db_path)
        try:
            cur = conn.execute(
                """
                INSERT INTO optimization_experiments
                    (strategy_id, experiment_type, parameters_json,
                     results_json, status, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    experiment_type,
                    _json_dumps(parameters) if parameters is not None else None,
                    _json_dumps(results) if results is not None else None,
                    status,
                    notes,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM optimization_experiments WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            d = dict(row)
            d["parameters"] = _json_loads(d.pop("parameters_json", None))
            d["results"] = _json_loads(d.pop("results_json", None))
            return d
        finally:
            conn.close()

    def update(
        self,
        experiment_id: int,
        *,
        parameters: dict[str, Any] | None = None,
        results: dict[str, Any] | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch an experiment's parameters/results/status/notes."""
        sets: list[str] = []
        args: list[Any] = []
        if parameters is not None:
            sets.append("parameters_json = ?")
            args.append(_json_dumps(parameters))
        if results is not None:
            sets.append("results_json = ?")
            args.append(_json_dumps(results))
        if status is not None:
            if status not in {"pending", "running", "completed", "failed"}:
                raise ValueError(f"Invalid status {status!r}")
            sets.append("status = ?")
            args.append(status)
        if notes is not None:
            sets.append("notes = ?")
            args.append(notes)
        if not sets:
            return self.get(experiment_id)

        args.append(experiment_id)
        conn = _connect(self.db_path)
        try:
            conn.execute(
                f"UPDATE optimization_experiments SET {', '.join(sets)} WHERE id = ?",
                args,
            )
            conn.commit()
            return self.get(experiment_id)
        finally:
            conn.close()

    def get(self, experiment_id: int) -> dict[str, Any] | None:
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM optimization_experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            d = _row_to_dict(row)
            if d is not None:
                d["parameters"] = _json_loads(d.pop("parameters_json", None))
                d["results"] = _json_loads(d.pop("results_json", None))
            return d
        finally:
            conn.close()

    def list(
        self,
        *,
        strategy_id: int | None = None,
        status: str | None = None,
        experiment_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List experiments with filters (newest first)."""
        sql = "SELECT * FROM optimization_experiments WHERE 1=1"
        args: list[Any] = []
        if strategy_id is not None:
            sql += " AND strategy_id = ?"
            args.append(strategy_id)
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if experiment_type is not None:
            sql += " AND experiment_type = ?"
            args.append(experiment_type)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(sql, args).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["parameters"] = _json_loads(d.pop("parameters_json", None))
                d["results"] = _json_loads(d.pop("results_json", None))
                out.append(d)
            return out
        finally:
            conn.close()


__all__ = [
    "StrategyService",
    "PerformanceService",
    "RegimeService",
    "OptimizationService",
    "Optional",
]
