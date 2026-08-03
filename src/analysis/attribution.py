"""Strategy attribution — performance por estrategia×símbolo para dashboard.

Responsabilidades (R-17 — Strategy Attribution):
  (a) Persistir performance por estrategia×símbolo en la tabla
      ``strategy_performance`` de ``data/strategies.db``.
  (b) Generar una matriz de rentabilidad (estrategias × símbolos → P&L)
      lista para serializar y consumir desde el dashboard React.
  (c) Detectar la mejor estrategia para cada símbolo, basada en una
      métrica configurable (default: ``total_pnl``, alternativa: Sharpe).

Diseño:
  - Reutiliza el schema existente (migration 001 — tablas ``strategies`` y
    ``strategy_performance`` ya creadas por el inicializador de BD).
  - Resuelve el nombre de estrategia → ``strategy_id`` consultando
    ``strategies`` (inserta un row si la estrategia no existe todavía).
  - Acepta ``BacktestResult`` del engine y/o dicts sueltos para máxima
    flexibilidad — el backtest engine y el paper runner pueden llamarlo.
  - Todas las queries son idempotentes: ``save_performance`` hace
    upsert lógico (INSERT nuevo, no sobrescribe histórico). Para un
    snapshot "último" se filtra por ``backtest_date`` DESC.
  - Usa sqlite3 puro (yyvsp con el resto del proyecto, sin ORM).

Usage::

    from src.analysis.attribution import StrategyAttribution
    from src.backtest.engine import BacktestResult

    attr = StrategyAttribution()  # usa data/strategies.db del proyecto
    attr.save_performance(
        strategy_name="breakout",
        symbol="RB100",
        result=backtest_result,      # BacktestResult o dict
    )
    matrix = attr.profitability_matrix()   # {"R_100": {"breakout": 250.0, ...}}
    best = attr.best_strategy_per_symbol() # {"R_100": ("breakout", 250.0)}
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default DB path — relative to project root (synthetic-trader/).
# Tests override this with a tmp_path.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "strategies.db"

# Minimum trades for "best strategy" detection — avoids declaring a winner
# on statistically insignificant samples (e.g. 1-2 lucky trades).
_MIN_TRADES_FOR_BEST = 10


class StrategyAttribution:
    """Persiste y consulta performance por estrategia × símbolo.

    Args:
        db_path: Ruta al SQLite con las tablas ``strategies`` y
            ``strategy_performance``. Default: ``data/strategies.db``.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # ------------------------------------------------------------------ #
    #  Internal — connection helpers                                     #
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with row factory for dict-like access."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Ensure tables exist — safe for fresh/test DBs and no-op for
        # the initialised project DB (migration 001 already ran).
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create the two required tables if they don't exist yet.

        This is a safety net: in the initialised project DB these tables
        already exist (migration 001).  We guard for fresh/test DBs where
        the migration hasn't run.
        """
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                version TEXT NOT NULL DEFAULT '1.0.0',
                description TEXT,
                parameters_json TEXT,
                lineage_parent_id INTEGER,
                market_type TEXT DEFAULT 'synthetic',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                status TEXT NOT NULL DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                backtest_date TEXT NOT NULL,
                win_rate REAL,
                sharpe REAL,
                max_dd REAL,
                total_pnl REAL,
                profit_factor REAL,
                expectancy REAL,
                gate_passed INTEGER NOT NULL DEFAULT 0,
                total_trades INTEGER,
                calmar_ratio REAL,
                sortino_ratio REAL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            );
            """
        )
        conn.commit()

    def _resolve_strategy_id(self, conn: sqlite3.Connection, name: str) -> int:
        """Return the ``strategies.id`` for *name*, inserting a stub row if absent.

        The strategy table holds registered strategies (from the factory).
        If a caller passes a name not yet persisted, we insert a minimal
        row so the FK on ``strategy_performance`` is satisfied.
        """
        row = conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        conn.execute(
            "INSERT INTO strategies (name, version, description) VALUES (?, ?, ?)",
            (name, "1.0.0", f"Auto-registered by StrategyAttribution"),
        )
        conn.commit()
        return int(conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()["id"])

    # ------------------------------------------------------------------ #
    #  (a)  Save performance                                              #
    # ------------------------------------------------------------------ #

    def save_performance(
        self,
        strategy_name: str,
        symbol: str,
        result: Any,
        backtest_date: str | None = None,
        total_trades: int | None = None,
        calmar_ratio: float | None = None,
        sortino_ratio: float | None = None,
    ) -> int:
        """Persist una fila de performance estrategia×símbolo.

        Acepta ``BacktestResult`` (del backtest engine) o un ``dict`` plano
        con las claves relevantes.  Extrae las métricas y las inserta en
        ``strategy_performance``.

        Args:
            strategy_name: Nombre canónico de la estrategia (ej: "breakout",
                "volatility", "confluence").
            symbol: Símbolo tradear (ej: "R_100", "RB100").
            result: ``BacktestResult`` o ``dict`` con win_rate, sharpe_ratio,
                max_drawdown, total_pnl, profit_factor, expectancy,
                gate_passed, total_trades.
            backtest_date: Fecha del backtest (ISO ``YYYY-MM-DD``). Si es
                ``None``, usa la fecha UTC actual.
            total_trades: Override del número de trades (opcional).
            calmar_ratio: Calmar ratio (opcional, si no está en result).
            sortino_ratio: Sortino ratio (opcional, si no está en result).

        Returns:
            El ``id`` (rowid) de la fila insertada en ``strategy_performance``.
        """
        metrics = self._extract_metrics(result)
        date = backtest_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Allow override of trades if caller provides it explicitly
        if total_trades is not None:
            metrics["total_trades"] = total_trades
        if calmar_ratio is not None:
            metrics["calmar_ratio"] = calmar_ratio
        if sortino_ratio is not None:
            metrics["sortino_ratio"] = sortino_ratio

        with self._connect() as conn:
            strategy_id = self._resolve_strategy_id(conn, strategy_name)

            cursor = conn.execute(
                """
                INSERT INTO strategy_performance (
                    strategy_id, symbol, backtest_date,
                    win_rate, sharpe, max_dd, total_pnl,
                    profit_factor, expectancy, gate_passed,
                    total_trades, calmar_ratio, sortino_ratio
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    strategy_id,
                    symbol,
                    date,
                    metrics["win_rate"],
                    metrics["sharpe"],
                    metrics["max_dd"],
                    metrics["total_pnl"],
                    metrics["profit_factor"],
                    metrics["expectancy"],
                    metrics["gate_passed"],
                    metrics["total_trades"],
                    metrics["calmar_ratio"],
                    metrics["sortino_ratio"],
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid or 0

        logger.info(
            "StrategyPerformance saved: id=%d, strategy='%s' (id=%d), symbol='%s', "
            "pnl=%.2f, sharpe=%.3f, win_rate=%.2f, trades=%d",
            row_id, strategy_name, strategy_id, symbol,
            metrics["total_pnl"], metrics["sharpe"],
            metrics["win_rate"], metrics["total_trades"],
        )
        return row_id

    @staticmethod
    def _extract_metrics(result: Any) -> dict[str, Any]:
        """Normalise ``BacktestResult`` or ``dict`` into a flat metrics dict.

        Handles the naming differences between BacktestResult fields
        (``sharpe_ratio``, ``max_drawdown``) and DB columns
        (``sharpe``, ``max_dd``).
        """
        if isinstance(result, dict):
            d = result
        elif hasattr(result, "to_dict"):
            d = result.to_dict()
        elif hasattr(result, "__dict__"):
            # Dataclass / object — convert to dict
            d = {k: v for k, v in vars(result).items()
                 if not k.startswith("_") and not callable(v)}
        else:
            raise TypeError(
                f"result must be BacktestResult, dict, or dataclass, "
                f"got {type(result).__name__}"
            )

        def _key(*candidates: str) -> Any:
            for c in candidates:
                if c in d and d[c] is not None:
                    return d[c]
            return None

        return {
            "win_rate": _key("win_rate") or 0.0,
            "sharpe": _key("sharpe_ratio", "sharpe") or 0.0,
            "max_dd": _key("max_drawdown", "max_dd") or 0.0,
            "total_pnl": _key("total_pnl") or 0.0,
            "profit_factor": _key("profit_factor") or 0.0,
            "expectancy": _key("expectancy") or 0.0,
            "gate_passed": int(bool(_key("gate_passed"))),
            "total_trades": _key("total_trades") or 0,
            "calmar_ratio": _key("calmar_ratio") or None,
            "sortino_ratio": _key("sortino_ratio") or None,
        }

    # ------------------------------------------------------------------ #
    #  (b)  Profitability matrix                                          #
    # ------------------------------------------------------------------ #

    def profitability_matrix(
        self,
        metric: str = "total_pnl",
        latest_only: bool = True,
    ) -> dict[str, dict[str, float]]:
        """Genera matriz de rentabilidad {símbolo: {estrategia: métrica}}.

        Para cada combinación estrategia×símbolo, retorna el valor de la
        métrica elegida (default: P&L total).  Pensado para serializar a
        JSON y renderizar como heatmap en el dashboard React.

        Args:
            metric: Métrica a reportar. Valores válidos:
                ``total_pnl`` (default), ``sharpe``, ``win_rate``,
                ``max_dd``, ``profit_factor``, ``expectancy``,
                ``total_trades``.
            latest_only: Si ``True``, toma solo la performance más reciente
                por estrategia×símbolo (filtrando por ``backtest_date``
                DESC).  Si ``False``, agrega (suma para ``total_pnl``,
                promedio para ratios).

        Returns:
            ``{symbol: {strategy_name: metric_value}}`` — nested dict.
            Símbolos o estrategias sin datos quedan ausentes del dict.
        """
        valid_metrics = {
            "total_pnl", "sharpe", "win_rate", "max_dd",
            "profit_factor", "expectancy", "total_trades",
        }
        if metric not in valid_metrics:
            raise ValueError(
                f"metric='{metric}' is invalid. Valid: {sorted(valid_metrics)}"
            )

        with self._connect() as conn:
            if latest_only:
                query = """
                    SELECT s.name AS strategy, sp.symbol, sp.backtest_date,
                           sp.{metric}
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    WHERE sp.id IN (
                        SELECT MAX(id) FROM strategy_performance
                        GROUP BY strategy_id, symbol
                    )
                """.format(metric=metric)
                rows = conn.execute(query).fetchall()
            else:
                if metric in ("total_pnl", "total_trades"):
                    agg_fn = "SUM"
                else:
                    agg_fn = "AVG"
                query = """
                    SELECT s.name AS strategy, sp.symbol,
                           {agg}(sp.{metric}) AS {metric}
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    GROUP BY s.name, sp.symbol
                """.format(agg=agg_fn, metric=metric)
                rows = conn.execute(query).fetchall()

        matrix: dict[str, dict[str, float]] = {}
        for row in rows:
            symbol = row["symbol"]
            strategy = row["strategy"]
            value = row[metric]
            if value is None:
                continue
            matrix.setdefault(symbol, {})[strategy] = float(value)

        return matrix

    # ------------------------------------------------------------------ #
    #  (c)  Best strategy per symbol                                      #
    # ------------------------------------------------------------------ #

    def best_strategy_per_symbol(
        self,
        metric: str = "total_pnl",
        min_trades: int = _MIN_TRADES_FOR_BEST,
        latest_only: bool = True,
    ) -> dict[str, tuple[str, float]]:
        """Detecta la mejor estrategia para cada símbolo.

        Para cada símbolo, selecciona la estrategia con el valor más alto
        en la métrica elegida (default: P&L total), filtrando las que
        tienen menos de ``min_trades`` trades (para significancia
        estadística).

        Args:
            metric: Métrica para ranking (default ``total_pnl``). También
                soporta ``sharpe``, ``profit_factor``, ``expectancy``,
                ``win_rate``.  Para ``max_dd`` (donde menor es mejor),
                se invierte la selección automáticamente.
            min_trades: Trades mínimos para considerar una estrategia.
                Default 10.  Estrategias con menos trades se descartan.
            latest_only: Si ``True``, usa solo la performance más reciente
                por estrategia×símbolo.

        Returns:
            ``{symbol: (best_strategy_name, metric_value)}`` — solo
            símbolos con al menos una estrategia que cumple ``min_trades``.
        """
        lower_is_better = (metric == "max_dd")

        with self._connect() as conn:
            if latest_only:
                query = """
                    SELECT s.name AS strategy, sp.symbol, sp.{metric}, sp.total_trades
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    WHERE sp.id IN (
                        SELECT MAX(id) FROM strategy_performance
                        GROUP BY strategy_id, symbol
                    )
                """.format(metric=metric)
            else:
                agg_fn = "SUM" if metric in ("total_pnl", "total_trades") else "AVG"
                query = """
                    SELECT s.name AS strategy, sp.symbol,
                           {agg}(sp.{metric}) AS {metric},
                           SUM(sp.total_trades) AS total_trades
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    GROUP BY s.name, sp.symbol
                """.format(agg=agg_fn, metric=metric)

            rows = conn.execute(query).fetchall()

        # Group by symbol
        per_symbol: dict[str, list[tuple[str, float, int]]] = {}
        for row in rows:
            value = row[metric]
            trades = row["total_trades"]
            if value is None or trades is None:
                continue
            symbol = row["symbol"]
            strategy = row["strategy"]
            per_symbol.setdefault(symbol, []).append(
                (strategy, float(value), int(trades))
            )

        best: dict[str, tuple[str, float]] = {}
        for symbol, entries in per_symbol.items():
            # Filter by min_trades
            qualified = [e for e in entries if e[2] >= min_trades]
            if not qualified:
                continue
            # Select by metric (min or max depending on direction)
            if lower_is_better:
                winner = min(qualified, key=lambda e: e[1])
            else:
                winner = max(qualified, key=lambda e: e[1])
            best[symbol] = (winner[0], winner[1])

        return best

    # ------------------------------------------------------------------ #
    #  Convenience — full dashboard payload                               #
    # ------------------------------------------------------------------ #

    def dashboard_payload(
        self,
        metric: str = "total_pnl",
        min_trades: int = _MIN_TRADES_FOR_BEST,
    ) -> dict[str, Any]:
        """Bundle matrix + best-per-symbol + metadata in one dict for the API.

        Returns a JSON-serialisable dict ready for ``GET /api/attribution``
        or the WebSocket live-data stream::

            {
                "matrix": {"R_100": {"breakout": 250.0, "volatility": -30.0}},
                "best_per_symbol": {"R_100": ["breakout", 250.0]},
                "metric": "total_pnl",
                "min_trades": 10,
                "generated_at": "2026-08-02T12:00:00+00:00",
            }
        """
        return {
            "matrix": self.profitability_matrix(metric=metric),
            "best_per_symbol": self.best_strategy_per_symbol(
                metric=metric, min_trades=min_trades
            ),
            "metric": metric,
            "min_trades": min_trades,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
