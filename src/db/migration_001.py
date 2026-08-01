"""migration_001 — Initial schema for the Strategy Histórica DB.

Creates six tables (per PROJECT.md) plus optimized indexes and a
``schema_migrations`` bookkeeping table so future migrations can run
idempotently. Idempotent: re-running is a no-op once ``migration_001``
is recorded as applied.

Tables:
    strategies              — strategy metadata + semantic versioning
    strategy_performance    — backtest metrics per (strategy, symbol, date)
    strategy_results        — trade-by-trade detail + equity curve + metrics
    market_regimes           — HMM regime detection history
    strategy_comparisons    — A/B comparison results
    optimization_experiments — walk-forward / genetic / RL experiments

Run:
    python -m src.db.migration_001            # applies to default path
    DB_PATH=/path/to.db python -m src.db.migration_001
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Paths & constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "data" / "strategies.db"
)

MIGRATION_VERSION: Final[str] = "001"
MIGRATION_DESCRIPTION: Final[str] = "Initial schema — 6 tables for BD Histórica de Estrategias"


# ---------------------------------------------------------------------------
#  DDL
# ---------------------------------------------------------------------------

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version         TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT (datetime('now')),
    checksum        TEXT
);
"""

STRATEGIES_DDL = """
CREATE TABLE IF NOT EXISTS strategies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,
    version            TEXT NOT NULL,                 -- semantic MAJOR.MINOR.PATCH
    description        TEXT,
    parameters_json    TEXT,                          -- JSON-encoded parameter set
    lineage_parent_id  INTEGER REFERENCES strategies(id) ON DELETE SET NULL,
    market_type        TEXT DEFAULT 'synthetic',      -- synthetic | forex | crypto | commodity
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    status             TEXT NOT NULL DEFAULT 'active', -- active | archived | deprecated
    UNIQUE(name, version)
);
"""

STRATEGY_PERFORMANCE_DDL = """
CREATE TABLE IF NOT EXISTS strategy_performance (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id      INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    symbol           TEXT NOT NULL,
    backtest_date    TEXT NOT NULL,                  -- ISO date of the backtest run
    win_rate         REAL,
    sharpe           REAL,
    max_dd           REAL,
    total_pnl        REAL,
    profit_factor    REAL,
    expectancy       REAL,
    gate_passed      INTEGER NOT NULL DEFAULT 0,     -- 0 = failed, 1 = passed
    total_trades     INTEGER,
    calmar_ratio     REAL,
    sortino_ratio    REAL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(strategy_id, symbol, backtest_date)
);
"""

STRATEGY_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id       INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    backtest_id       TEXT,                           -- groups results of one backtest run
    trade_data_json   TEXT,                           -- JSON array of trade records
    equity_curve_json TEXT,                           -- JSON array of equity points
    metrics_json      TEXT,                           -- JSON dict of computed metrics
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

MARKET_REGIMES_DDL = """
CREATE TABLE IF NOT EXISTS market_regimes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    regime_type     TEXT NOT NULL,                   -- bull | bear | sideways | high_vol_trend | crisis | ...
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    volatility      REAL,                            -- volatility estimate (e.g. annualized σ)
    trend           REAL,                            -- trend strength (e.g. |slope| normalized)
    metadata_json   TEXT,                            -- confidence, HMM state, extra context
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

STRATEGY_COMPARISONS_DDL = """
CREATE TABLE IF NOT EXISTS strategy_comparisons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_a_id   INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    strategy_b_id   INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    metric          TEXT NOT NULL,                   -- e.g. 'sharpe', 'max_dd', 'win_rate'
    value           REAL,                            -- value of the winning metric (A - B)
    winner          TEXT,                            -- 'A' | 'B' | 'tie' | 'none'
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    start_date      TEXT,
    end_date        TEXT,
    metrics_json    TEXT                             -- full comparison breakdown
);
"""

OPTIMIZATION_EXPERIMENTS_DDL = """
CREATE TABLE IF NOT EXISTS optimization_experiments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id      INTEGER NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    experiment_type  TEXT NOT NULL,                  -- walk_forward | genetic | rl | ...
    parameters_json  TEXT,                            -- explored parameter space
    results_json     TEXT,                            -- best params, OOS perf, overfitting score
    status           TEXT NOT NULL DEFAULT 'pending', -- pending | running | completed | failed
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    notes            TEXT
);
"""

# Indexes (per PROJECT.md — "Índices Optimizados")
INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS idx_strategies_name_version
    ON strategies(name, version);

CREATE INDEX IF NOT EXISTS idx_strategies_status
    ON strategies(status);

CREATE INDEX IF NOT EXISTS idx_strategies_lineage
    ON strategies(lineage_parent_id);

CREATE INDEX IF NOT EXISTS idx_performance_strategy_id_period
    ON strategy_performance(strategy_id, backtest_date);

CREATE INDEX IF NOT EXISTS idx_performance_symbol
    ON strategy_performance(symbol);

CREATE INDEX IF NOT EXISTS idx_results_strategy_id_timestamp
    ON strategy_results(strategy_id, created_at);

CREATE INDEX IF NOT EXISTS idx_regimes_timestamp
    ON market_regimes(start_time);

CREATE INDEX IF NOT EXISTS idx_regimes_regime_type
    ON market_regimes(regime_type);

CREATE INDEX IF NOT EXISTS idx_comparisons_a_b
    ON strategy_comparisons(strategy_a_id, strategy_b_id);

CREATE INDEX IF NOT EXISTS idx_experiments_strategy_id
    ON optimization_experiments(strategy_id);

CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON optimization_experiments(status);
"""


ALL_DDL: Final[list[str]] = [
    SCHEMA_MIGRATIONS_DDL,
    STRATEGIES_DDL,
    STRATEGY_PERFORMANCE_DDL,
    STRATEGY_RESULTS_DDL,
    MARKET_REGIMES_DDL,
    STRATEGY_COMPARISONS_DDL,
    OPTIMIZATION_EXPERIMENTS_DDL,
    INDEXES_DDL,
]


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    """Return the configured SQLite path (env override supported)."""
    env = os.environ.get("DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def apply_migration(db_path: Path | None = None) -> None:
    """Apply migration_001 — idempotent.

    Re-running is a no-op once ``migration_001`` is recorded.
    Logs which statements executed; failures abort the transaction.
    """
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Applying migration_001 to %s", path)

    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row

        # Ensure the bookkeeping table exists first (idempotent CREATE),
        # so the idempotency check below can run on a fresh DB.
        conn.executescript(SCHEMA_MIGRATIONS_DDL)
        conn.commit()

        # Idempotency check
        cur = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        )
        if cur.fetchone() is not None:
            logger.info("migration_001 already applied — skipping")
            return

        # Apply the remaining DDL inside one transaction. We exclude the
        # migrations table since it was created above.
        conn.execute("BEGIN;")
        for ddl in ALL_DDL[1:]:
            conn.executescript(ddl)
        conn.execute(
            "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
            (MIGRATION_VERSION, MIGRATION_DESCRIPTION),
        )
        conn.commit()
        logger.info("migration_001 applied successfully")
    except Exception:
        conn.rollback()
        logger.exception("migration_001 failed — rolled back")
        raise
    finally:
        conn.close()


def verify_schema(db_path: Path | None = None) -> dict[str, object]:
    """Return a quick schema sanity-check report.

    Useful for tests and API health endpoints.
    """
    path = db_path or get_db_path()
    if not path.exists():
        return {"exists": False, "tables": [], "migration_version": None}

    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = [r["name"] for r in rows]

        mig = conn.execute(
            "SELECT version, applied_at FROM schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        ).fetchone()
        return {
            "exists": True,
            "tables": tables,
            "migration_version": mig["version"] if mig else None,
            "applied_at": mig["applied_at"] if mig else None,
            "table_count": len(tables),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    apply_migration()
    report = verify_schema()
    print(report)
