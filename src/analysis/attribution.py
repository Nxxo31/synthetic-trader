"""Strategy attribution — performance por estrategia×símbolo para dashboard.

Responsabilities (R-20 — Strategy Attribution + Brinson-Fachler):
  (a) Persistir performance por estrategia×símbolo en la tabla
      ``strategy_performance`` de ``data/strategies.db``.
  (b) Generar una matriz de rentabilidad (estrategias × símbolos → P&L)
      lista para serializar y consumir desde el dashboard React.
  (c) Detectar la mejor estrategia para cada símbolo, basada en una
      métrica configurable (default: ``total_pnl``, alternativa: Sharpe).
  (d) **Brinson-Fachler decomposition** — descompone el exceso de retorno
      del portfolio activo vs benchmark en tres efectos ortogonales:
      Allocation (qué símbolos peso), Selection (qué estrategia elijo
      dentro de cada símbolo) e Interaction (sinergia).  Ver
      ``BrinsonFachlerResult`` y ``brinson_fachler_decomposition()``.

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

Brinson-Fachler math (industry-standard performance attribution):

  For each symbol ``i`` and strategy ``j`` within that symbol:

    w_p(i,j) = portfolio weight of cell (i,j)        [actual allocation]
    w_b(i,j) = benchmark weight of cell (i,j)        [passive/equal-weight]
    r_p(i,j) = portfolio return of cell (i,j)         [observed return]
    r_b(i,j) = benchmark return of cell (i,j)         [= r_p for self-attribution]
    R_b(i)   = benchmark return for symbol i
             = Σ_j w_b(i,j)·r_b(i,j)  (weighted within symbol)
    R_b      = overall benchmark return = Σ_i w_b(i)·R_b(i)

  Effects (all expressed in return units):

    Allocation     = Σ_i (w_p(i) − w_b(i)) · (R_b(i) − R_b)
    Selection      = Σ_i w_b(i) · Σ_j (r_p(i,j) − r_b(i,j))·w_b(i,j)
                   = 0  when r_p = r_b  (self-attribution, single period)
    Interaction    = Σ_i (w_p(i) − w_b(i)) · (R_p(i) − R_b(i))
    Total Excess   = Allocation + Selection + Interaction

  Trading-context adaptation:
    - Cells (i,j) are (symbol, strategy) pairs from the performance table.
    - Weights default to trade counts (``total_trades``) which proxy for
      how much capital/time each cell consumed; callers can override with
      ``weight_column='total_pnl_abs'`` for absolute-capital weighting.
    - Returns default to per-trade average P&L (``total_pnl / total_trades``)
      normalised as a percentage; callers can override with
      ``return_column='win_rate'`` or ``'sharpe'``.
    - Benchmark is equal-weight across all active cells by default — the
      passive alternative "what if we'd treated every strategy-symbol
      pair identically".  ``benchmark_strategy`` lets you choose one
      strategy as the benchmark within each symbol instead.
    - Because each cell is observed only in its own (strategy, symbol)
      pairing, r_p(i,j) = r_b(i,j) by construction and Selection = 0.
      The **meaningful** effects are Allocation (overweighting a
      high-return symbol) and Interaction (the product of that
      overweight with the symbol's outperformance).  See
      ``BrinsonFachlerResult.selection_note``.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "StrategyAttribution",
    "BrinsonFachlerResult",
    "BrinsonFachlerRow",
]

# Default DB path — relative to project root (synthetic-trader/).
# Tests override this with a tmp_path.
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "strategies.db"

# Minimum trades for "best strategy" detection — avoids declaring a winner
# on statistically insignificant samples (e.g. 1-2 lucky trades).
_MIN_TRADES_FOR_BEST = 10


# ─── Brinson-Fachler result dataclasses ──────────────────────────────────────────


@dataclass
class BrinsonFachlerRow:
    """Per-symbol contribution to the Brinson-Fachler decomposition.

    Each row corresponds to one symbol ``i`` and shows how that symbol
    contributed to the allocation, selection, and interaction effects.
    """

    symbol: str
    portfolio_weight: float          # w_p(i) — actual allocation weight
    benchmark_weight: float          # w_b(i) — benchmark allocation weight
    portfolio_return: float          # R_p(i) — weighted return of the symbol in portfolio
    benchmark_return: float          # R_b(i) — weighted return of the symbol in benchmark
    allocation_contribution: float   # (w_p − w_b)·(R_b(i) − R_b)
    selection_contribution: float    # w_b · Σ_j (r_p − r_b)·w_b   (0 in self-attribution)
    interaction_contribution: float  # (w_p − w_b)·(R_p(i) − R_b(i))


@dataclass
class BrinsonFachlerResult:
    """Full Brinson-Fachler decomposition across the portfolio.

    Attributes:
        allocation_effect: Σ_i allocation_contribution.
            Positive ⇒ the portfolio overweighted symbols whose benchmark
            return exceeded the overall benchmark (good allocation).
        selection_effect: Σ_i selection_contribution.
            Positive ⇒ the portfolio picked above-benchmark strategies
            within symbols.  In single-period self-attribution (r_p = r_b
            for each cell) this is zero by construction — see
            ``selection_note``.
        interaction_effect: Σ_i interaction_contribution.
            Positive ⇒ overweight + outperformance synergised.
        total_excess_return: Sum of the three effects.  Equals
            ``portfolio_return − benchmark_return`` (identity).
        portfolio_return: Weighted return of the actual portfolio.
        benchmark_return: Weighted return of the benchmark portfolio.
        rows: Per-symbol breakdown (list of :class:`BrinsonFachlerRow`).
        weight_column: Column used to derive allocation weights.
        return_column: Column used to derive cell returns.
        benchmark_strategy: Strategy used as benchmark within each symbol
            (or ``None`` for equal-weight across all strategies).
        selection_note: Human-readable explanation of the selection effect
            (always zero in self-attribution).
    """

    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_excess_return: float
    portfolio_return: float
    benchmark_return: float
    rows: list[BrinsonFachlerRow]
    weight_column: str
    return_column: str
    benchmark_strategy: str | None
    selection_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict for API / dashboard consumption."""
        return {
            "allocation_effect": round(self.allocation_effect, 6),
            "selection_effect": round(self.selection_effect, 6),
            "interaction_effect": round(self.interaction_effect, 6),
            "total_excess_return": round(self.total_excess_return, 6),
            "portfolio_return": round(self.portfolio_return, 6),
            "benchmark_return": round(self.benchmark_return, 6),
            "weight_column": self.weight_column,
            "return_column": self.return_column,
            "benchmark_strategy": self.benchmark_strategy,
            "selection_note": self.selection_note,
            "rows": [
                {
                    "symbol": r.symbol,
                    "portfolio_weight": round(r.portfolio_weight, 6),
                    "benchmark_weight": round(r.benchmark_weight, 6),
                    "portfolio_return": round(r.portfolio_return, 6),
                    "benchmark_return": round(r.benchmark_return, 6),
                    "allocation_contribution": round(r.allocation_contribution, 6),
                    "selection_contribution": round(r.selection_contribution, 6),
                    "interaction_contribution": round(r.interaction_contribution, 6),
                }
                for r in self.rows
            ],
        }



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

    # ------------------------------------------------------------------ #
    #  (d)  Brinson-Fachler decomposition                                 #
    # ------------------------------------------------------------------ #

    # Valid weight columns (proxying capital allocation across cells).
    _BF_WEIGHT_COLUMNS: dict[str, str] = {
        "total_trades": "total_trades",
        "total_pnl_abs": "_pnl_abs_for_weight",   # computed below
    }
    # Valid return columns for the per-cell return metric.
    _BF_RETURN_COLUMNS: frozenset[str] = frozenset(
        {"avg_pnl_pct", "win_rate", "sharpe", "expectancy"}
    )

    def brinson_fachler_decomposition(
        self,
        *,
        weight_column: str = "total_trades",
        return_column: str = "avg_pnl_pct",
        benchmark_strategy: str | None = None,
        latest_only: bool = True,
        min_trades: int = 1,
    ) -> BrinsonFachlerResult:
        """Brinson-Fachler decomposition of portfolio vs benchmark excess return.

        Decomposes the excess return of the actively-allocated portfolio
        over a benchmark allocation into three additive effects:

          - **Allocation**: overweighting symbols whose benchmark return
            exceeds the overall benchmark.
          - **Selection**: picking strategies within a symbol that beat the
            symbol's benchmark strategy.  Zero in self-attribution (r_p = r_b).
          - **Interaction**: the cross term.

        Cells (i,j) = (symbol, strategy) with performance in the DB.

        Args:
            weight_column: Column used to derive allocation weights.
                ``"total_trades"`` (default) weights each cell by its trade
                count — a proxy for capital/time commitment.
                ``"total_pnl_abs"`` weights by absolute P&L magnitude.
            return_column: Column used as the per-cell return metric.
                ``"avg_pnl_pct"`` (default) = ``total_pnl / total_trades`` as
                a fraction.  Also: ``"win_rate"``, ``"sharpe"``, ``"expectancy"``.
            benchmark_strategy: If set, the benchmark within each symbol
                uses only this strategy's return (a single-strategy
                baseline).  If ``None`` (default), the benchmark is
                equal-weight across all strategies within each symbol.
            latest_only: If ``True`` (default), use only the latest
                performance row per (strategy, symbol).
            min_trades: Cells with fewer than this many trades are excluded
                from the decomposition (statistical significance floor).

        Returns:
            :class:`BrinsonFachlerResult` with the three effects, total
            excess return, per-symbol rows, and metadata.

        Raises:
            ValueError: If ``weight_column`` or ``return_column`` is invalid,
                or if no cells survive the ``min_trades`` filter.
        """
        if weight_column not in self._BF_WEIGHT_COLUMNS:
            raise ValueError(
                f"weight_column='{weight_column}' is invalid. "
                f"Valid: {sorted(self._BF_WEIGHT_COLUMNS)}"
            )
        if return_column not in self._BF_RETURN_COLUMNS:
            raise ValueError(
                f"return_column='{return_column}' is invalid. "
                f"Valid: {sorted(self._BF_RETURN_COLUMNS)}"
            )

        # ---- 1. Fetch all cells (strategy, symbol, metrics) ----
        with self._connect() as conn:
            if latest_only:
                query = """
                    SELECT s.name AS strategy, sp.symbol, sp.backtest_date,
                           sp.total_trades, sp.total_pnl, sp.win_rate,
                           sp.sharpe, sp.expectancy, sp.profit_factor
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    WHERE sp.id IN (
                        SELECT MAX(id) FROM strategy_performance
                        GROUP BY strategy_id, symbol
                    )
                """
            else:
                query = """
                    SELECT s.name AS strategy, sp.symbol, sp.backtest_date,
                           sp.total_trades, sp.total_pnl, sp.win_rate,
                           sp.sharpe, sp.expectancy, sp.profit_factor
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                """
            rows = conn.execute(query).fetchall()

        # ---- 2. Build per-cell records, applying min_trades filter ----
        cells: list[dict[str, Any]] = []
        for row in rows:
            tt = int(row["total_trades"] or 0)
            if tt < min_trades:
                continue
            pnl = float(row["total_pnl"] or 0.0)
            # Compute per-cell return metric
            if return_column == "avg_pnl_pct":
                # Per-trade average P&L as a fraction of capital (use absolute
                # scale proxy: pnl / trades gives $/trade, divide by a nominal
                # capital of 100 to normalise to a percentage-like fraction).
                # This keeps the units consistent across cells.
                r_val = pnl / tt if tt > 0 else 0.0
                # Normalise by dividing by a notional 100 so we get small
                # percent-like numbers (consistent scale for the BF math):
                r = r_val / 100.0
            elif return_column == "win_rate":
                r = float(row["win_rate"] or 0.0)
            elif return_column == "sharpe":
                r = float(row["sharpe"] or 0.0)
            elif return_column == "expectancy":
                r = float(row["expectancy"] or 0.0)
            else:
                r = 0.0  # unreachable (validated above)

            # Compute cell weight (raw)
            if weight_column == "total_trades":
                w_raw = float(tt)
            elif weight_column == "total_pnl_abs":
                w_raw = abs(pnl)
            else:
                w_raw = float(tt)  # unreachable

            cells.append({
                "symbol": row["symbol"],
                "strategy": row["strategy"],
                "w_raw": w_raw,
                "r": r,
                "total_trades": tt,
                "total_pnl": pnl,
            })

        if not cells:
            raise ValueError(
                "No cells survive the min_trades filter — "
                "cannot compute Brinson-Fachler decomposition."
            )

        # ---- 3. Normalise weights to get portfolio weights w_p(i,j) ----
        total_weight_all = sum(c["w_raw"] for c in cells)
        if total_weight_all <= 0:
            raise ValueError(
                "Total portfolio weight is zero — cannot normalise."
            )
        for c in cells:
            c["w_p"] = c["w_raw"] / total_weight_all

        # ---- 4. Compute per-cell benchmark weights and returns ----
        # Benchmark is either the same strategy across symbols
        # (benchmark_strategy) or equal-weight across all cells globally.
        symbols = sorted({c["symbol"] for c in cells})
        # Group cells by symbol
        by_symbol: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
        for c in cells:
            by_symbol[c["symbol"]].append(c)

        if benchmark_strategy is not None:
            # Benchmarks allocated across ALL symbols, weights normalised globally
            # among the benchmark-strategy's available cells.
            bm_weight_global = sum(
                c["w_raw"]
                for c in cells
                if c["strategy"] == benchmark_strategy
            )
            for sym, sym_cells in by_symbol.items():
                for c in sym_cells:
                    if c["strategy"] == benchmark_strategy and bm_weight_global > 0:
                        c["w_b"] = c["w_raw"] / bm_weight_global
                        c["r_b"] = c["r"]
                    else:
                        c["w_b"] = 0.0
                        c["r_b"] = 0.0  # not held in benchmark
            # If no cells match the benchmark strategy at all, fall back to
            # equal-weight so the decomposition doesn't blow up.
            if bm_weight_global <= 0:
                n_total = len(cells)
                for c in cells:
                    c["w_b"] = c["w_raw"] / total_weight_all if total_weight_all > 0 else 0.0
                    c["r_b"] = c["r"]
        else:
            # Equal-weight across all cells globally: w_b = 1 / n_total per cell
            n_total = len(cells)
            for c in cells:
                c["w_b"] = 1.0 / n_total if n_total > 0 else 0.0
                c["r_b"] = c["r"]  # self-attribution

        # ---- 5. Aggregate to symbol-level portfolio & benchmark weights ----
        # w_p(i)  = Σ_j w_p(i,j)
        # R_p(i)  = Σ_j w_p(i,j)·r(i,j) / w_p(i)   (weighted avg within symbol)
        # R_b(i)  = Σ_j w_b(i,j)·r(i,j) / w_b(i)   (weighted avg within symbol)
        sym_portfolio_weight: dict[str, float] = {}
        sym_benchmark_weight: dict[str, float] = {}
        sym_portfolio_return_avg: dict[str, float] = {}
        sym_benchmark_return_avg: dict[str, float] = {}

        for sym, sym_cells in by_symbol.items():
            wp_i = sum(c["w_p"] for c in sym_cells)
            wb_i = sum(c["w_b"] for c in sym_cells)
            sym_portfolio_weight[sym] = wp_i
            sym_benchmark_weight[sym] = wb_i
            # Weighted average return within the symbol
            if wp_i > 0:
                rp_i = sum(c["w_p"] * c["r"] for c in sym_cells) / wp_i
            else:
                rp_i = 0.0
            if wb_i > 0:
                rb_i = sum(c["w_b"] * c["r_b"] for c in sym_cells) / wb_i
            else:
                rb_i = 0.0
            sym_portfolio_return_avg[sym] = rp_i
            sym_benchmark_return_avg[sym] = rb_i

        # Overall portfolio return R_p = Σ_i w_p(i)·R_p(i)
        # This equals Σ_{i,j} w_p(i,j)·r(i,j) since w_p(i) normalises the
        # within-symbol weighted average back to a raw sum.
        total_wp = sum(sym_portfolio_weight.values())
        R_p = sum(
            sym_portfolio_weight[s] * sym_portfolio_return_avg[s]
            for s in symbols
        ) / total_wp if total_wp > 0 else 0.0

        # Overall benchmark return R_b = Σ_i w_b(i)·R_b(i)
        total_wb = sum(sym_benchmark_weight.values())
        if total_wb <= 0:
            raise ValueError(
                "Total benchmark weight is zero — check benchmark_strategy."
            )
        R_b = sum(
            sym_benchmark_weight[s] * sym_benchmark_return_avg[s]
            for s in symbols
        ) / total_wb

        # ---- 6. Compute the three effects per symbol ----
        # Brinson-Fachler 1985 (aggregate form):
        #   Alloc_i    = (w_p(i) − w_b(i)) · (R_b(i) − R_b)
        #   Selec_i    = w_b(i) · (R_p(i) − R_b(i))
        #   Inter_i    = (w_p(i) − w_b(i)) · (R_p(i) − R_b(i))
        # where:
        #   R_p(i) = Σ_j w_p(i,j)·r(i,j) / w_p(i)   (weighted avg using portfolio wts)
        #   R_b(i) = Σ_j w_b(i,j)·r(i,j) / w_b(i)   (weighted avg using benchmark wts)
        # In single-period self-attribution (r_p == r_b per cell), R_p(i) and R_b(i)
        # can still differ because the *within-symbol weight distributions* differ.
        bf_rows: list[BrinsonFachlerRow] = []
        alloc_total = 0.0
        selec_total = 0.0
        inter_total = 0.0

        for sym in symbols:
            wp_i = sym_portfolio_weight[sym]
            wb_i = sym_benchmark_weight[sym]
            rp_i = sym_portfolio_return_avg[sym]
            rb_i = sym_benchmark_return_avg[sym]

            # Allocation: (w_p(i) − w_b(i)) · (R_b(i) − R_b)
            alloc_i = (wp_i - wb_i) * (rb_i - R_b)
            # Selection: w_b(i) · (R_p(i) − R_b(i))
            selec_i = wb_i * (rp_i - rb_i)
            # Interaction: (w_p(i) − w_b(i)) · (R_p(i) − R_b(i))
            inter_i = (wp_i - wb_i) * (rp_i - rb_i)

            alloc_total += alloc_i
            selec_total += selec_i
            inter_total += inter_i

            bf_rows.append(BrinsonFachlerRow(
                symbol=sym,
                portfolio_weight=wp_i,
                benchmark_weight=wb_i,
                portfolio_return=rp_i,
                benchmark_return=rb_i,
                allocation_contribution=alloc_i,
                selection_contribution=selec_i,
                interaction_contribution=inter_i,
            ))

        total_excess = alloc_total + selec_total + inter_total

        selection_note = (
            "Selection effect measures within-symbol strategy-picking skill: "
            "w_b(i)·(R_p(i)−R_b(i)). In self-attribution, R_p(i) and R_b(i) "
            "differ because the portfolio's within-symbol strategy weights "
            "differ from the benchmark's — so a positive selection means "
            "the portfolio tilted toward above-average strategies within each "
            "symbol. Supply benchmark_strategy='X' to measure against a "
            "single named baseline."
            if benchmark_strategy is None
            else f"Benchmark strategy='{benchmark_strategy}'; selection "
                 f"measures deviations from that strategy's returns."
        )

        return BrinsonFachlerResult(
            allocation_effect=alloc_total,
            selection_effect=selec_total,
            interaction_effect=inter_total,
            total_excess_return=total_excess,
            portfolio_return=R_p,
            benchmark_return=R_b,
            rows=bf_rows,
            weight_column=weight_column,
            return_column=return_column,
            benchmark_strategy=benchmark_strategy,
            selection_note=selection_note,
        )
