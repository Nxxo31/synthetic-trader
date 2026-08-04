"""Población automática de ``strategies.db`` desde reportes diarios.

Lee los reportes diarios JSON guardados en ``reports/daily/*.json`` por
``src/trading/daily_reporter.py`` y llama a
``StrategyAttribution.save_performance()`` para persistir una fila de
performance por (estrategia × símbolo × fecha) en la tabla
``strategy_performance`` de ``data/strategies.db``.

Motivación:
  Antes, ``strategies.db`` solo se poblaba al final de un backtest.  Pero
  el paper trading genera reportes diarios constantemente (cada 00:00 UTC),
  y la matriz de rentabilidad del dashboard estaba vacía entre backtests
  por días/semanas.  Este módulo cierra el lazo: después de cada día de
  paper trading, corre este script (vía cron o systemd timer) y la
  attribution del dashboard refleja la performance del paper en vivo.

Mapa de campos (daily report → ``strategy_performance``)::

    daily report                         → strategy_performance
    ---------------------------------------------------------------
    date                                 → backtest_date
    total_pnl                            → total_pnl
    win_rate                             → win_rate
    sharpe_ratio                         → sharpe
    max_drawdown                         → max_dd
    total_trades                         → total_trades
    (calc) profit_factor                 → profit_factor  (≈ wins/losses)
    (calc) expectancy                    → expectancy    (≈ pnl/trades)
    circuit_halted (→bool)               → gate_passed   (inverso: halted = gate failed)

  Daily report NO contiene ``symbol`` ni ``strategy`` a nivel top-level
  (el paper_runner actual solo opera una estrategia × símbolo, Range Break
  en RB100, fijo en el bot).  Por eso ``daily_attribution`` acepta
  parámetros ``default_symbol`` y ``default_strategy`` que se aplican a
  todo daily report que no traiga esas claves (forward-compatible: si el
  DailyReporter evoluciona y añade ``strategy``/``symbol`` al JSON, los
  respetamos).

Idempotencia:
  ``save_performance()`` hace INSERT puro (no upsert) por diseño del
  módulo ``attribution``.  Para evitar duplicar filas al correr este
  script varias veces, hacemos una pre-verificación: si ya existe un
  row con (strategy_name, symbol, backtest_date, total_trades,
  total_pnl) idéntico, **saltamos** la inserción y reportamos
  "skip (already imported)".

Uso::

    # Procesa TODOS los daily reports no importados aún:
    python -m src.analysis.daily_attribution

    # Procesa un reporte específico:
    python -m src.analysis.daily_attribution --report 2026-08-01

    # Sobreescribe los defaults de symbol/strategy:
    python -m src.analysis.daily_attribution --symbol R_100 --strategy volatility

    # Solo reporta qué haría sin escribir en la DB:
    python -m src.analysis.daily_attribution --dry-run

Exit codes:
  0 — éxito (algunos o todos los reports importados o ya estaban).
  1 — error fatal (I/O, esquema, etc.).
  2 — directorio de daily reports vacío o inexistente.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.analysis.attribution import DEFAULT_DB_PATH, StrategyAttribution

logger = logging.getLogger(__name__)

# Defaults — alineados con el único bot del pipeline (PROJECT.md): RangeBreak
# en RB100.  Overrideable vía CLI.
DEFAULT_SYMBOL = "RB100"
DEFAULT_STRATEGY = "RangeBreak"

REPORTS_DAILY_DIR = (
    Path(__file__).resolve().parent.parent.parent / "reports" / "daily"
)
"""Directorio canónico de daily reports (``reports/daily/``)."""


# ----------------------------------------------------------------------
# Lectura de daily reports
# ----------------------------------------------------------------------


def _list_daily_reports(report_dir: Path) -> list[Path]:
    """Lista los archivos ``YYYY-MM-DD.json`` en ``report_dir`` ordenados."""
    if not report_dir.exists():
        return []
    return sorted(
        (
            p
            for p in report_dir.iterdir()
            if p.is_file() and p.suffix == ".json"
        ),
        key=lambda p: p.name,
    )


def _load_report(path: Path) -> dict[str, Any] | None:
    """Carga un daily report JSON.  Retorna ``None`` si está corrupto."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Daily report corrupto o ilegible (%s): %s", path, e)
        return None


# ----------------------------------------------------------------------
# Normalización de métricas (ad-hoc para daily report)
# ----------------------------------------------------------------------

# Métricas que el DailyReporter ya produce y que mapean directo a la
# tabla ``strategy_performance``.
_DAILY_METRIC_KEYS = (
    "win_rate",
    "sharpe_ratio",
    "max_drawdown",
    "total_pnl",
    "total_trades",
    "pnl_pct",
    "wins",
    "losses",
)


def _build_attribution_payload(
    daily: dict[str, Any],
    symbol: str,
    strategy: str,
) -> dict[str, Any]:
    """Construye el dict de métricas que se pasa a ``save_performance``.

    ``StrategyAttribution._extract_metrics`` ya normaliza las claves del
    dict (``win_rate``, ``sharpe_ratio`` o ``sharpe``, ``max_drawdown`` o
    ``max_dd``, ``total_pnl``, etc.).  Por eso aquí simplemente exten-
    dente el daily report con ``symbol``, ``strategy`` y derivados
    (``profit_factor`` y ``expectancy`` que el daily report no calcula)
    y opcionalmente ``calmar_ratio``/``sortino_ratio`` si hay info.

    Metric derivation:
      profit_factor = wins / max(losses, 1)   — ratio bruto en ausencia
        de profit_factor explícito en el daily.
      expectancy    = total_pnl / max(total_trades, 1)   — P&L por trade.
      gate_passed   = not circuit_halted  — diario con halt ⇒ "gate failed"
        (equivalencia razonable: el circuit breaker detuvo a causa de
        pérdidas o drawdown, i.e. incumplió reglas).
    """
    payload: dict[str, Any] = dict(daily)  # copia

    # Asegura claves de identidad (sobreescribe defaults solo si el
    # daily report aún no las contiene — forward-compatible).
    payload.setdefault("symbol", symbol)
    payload.setdefault("strategy", strategy)
    # alias para _extract_metrics (que busca ``sharpe`` o ``sharpe_ratio``):
    # ya está ``sharpe_ratio`` en el daily; lo dejamos.

    # profit_factor derivado si el daily no lo trae.
    if "profit_factor" not in daily or daily.get("profit_factor") in (None, 0.0):
        wins = int(daily.get("wins", 0) or 0)
        losses = int(daily.get("losses", 0) or 0)
        if losses > 0:
            payload["profit_factor"] = round(wins / losses, 4)
        else:
            # Sin pérdidas → factor de beneficio tendiendo a ∞ —
            # representamos como total_pnl positivo simple y PF alto.
            payload["profit_factor"] = (
                9.99 if profits_positive(daily) else 0.0
            )
    # expectancy derivado si el daily no lo trae.
    if "expectancy" not in daily or daily.get("expectancy") in (None, 0.0):
        pnl = float(daily.get("total_pnl", 0) or 0)
        trades = int(daily.get("total_trades", 0) or 0)
        payload["expectancy"] = round(pnl / trades, 6) if trades > 0 else 0.0
    # gate_passed: el daily report tiene ``circuit_halted`` (bool).
    # Inversión: halted ⇒ gate failed.  Esto es una aproximación de
    # equivalencia y permite mostrar el estado en la matriz.
    if "gate_passed" not in daily:
        # circuit_halted puede faltar — default True (sin halt = gate OK).
        payload["gate_passed"] = not bool(daily.get("circuit_halted", False))

    # total_trades y las métricas directas ya están con sus claves.
    # calmar/sortino no se derivan aquí (necesitan series de equity);
    # se dejan None para que ``save_performance`` las acepte como NULL.
    payload.pop("calmar_ratio", None)
    payload.pop("sortino_ratio", None)
    return payload


def profits_positive(daily: dict[str, Any]) -> bool:
    """True si el daily report tiene P&L total positivo."""
    return float(daily.get("total_pnl", 0) or 0) > 0


# ----------------------------------------------------------------------
# Idempotencia — skip si ya importado
# ----------------------------------------------------------------------


def _already_imported(
    db_path: Path,
    strategy_name: str,
    symbol: str,
    backtest_date: str,
    total_trades: int,
    total_pnl: float,
) -> bool:
    """Verifica si ya existe un row idéntico en ``strategy_performance``.

    Criterio de identidad (suficientemente estricto para evitar duplicar):
      same strategy_id (resolved via strategies.name)
      same symbol
      same backtest_date
      same total_trades
      same total_pnl (rounded to 4 dec)

    Comparar ``total_trades + total_pnl + date`` (en vez de solo date)
    permite re-importar el mismo día si el daily report se regeneró con
    más trades (caso: bot termina a las 23:59:59, genera reporte
    parcial, luego al siguiente reset produce el definitivo).
    """
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT sp.id FROM strategy_performance sp
            JOIN strategies s ON s.id = sp.strategy_id
            WHERE s.name = ? AND sp.symbol = ? AND sp.backtest_date = ?
              AND sp.total_trades = ? AND
                  ABS(COALESCE(sp.total_pnl,0) - ?) < 0.0001
            LIMIT 1
            """,
            (
                strategy_name,
                symbol,
                backtest_date,
                total_trades,
                round(total_pnl, 4),
            ),
        ).fetchone()
        exists = row is not None
        conn.close()
        return exists
    except sqlite3.Error as e:
        logger.warning(
            "Fallo verificación de importación previa (%s); asumo no "
            "importado para no bloquear el pipeline.",
            e,
        )
        return False


# ----------------------------------------------------------------------
# Pipelining
# ----------------------------------------------------------------------


def process_report(
    report_path: Path,
    attribution: StrategyAttribution,
    default_symbol: str = DEFAULT_SYMBOL,
    default_strategy: str = DEFAULT_STRATEGY,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Procesa un archivo JSON de daily report y retorna el resultado.

    Returns:
        ``{"status": "imported" | "skipped" | "error", "report": ..., "detail": ...}``
    """
    daily = _load_report(report_path)
    if daily is None:
        return {
            "status": "error",
            "report": str(report_path),
            "detail": "JSON corrupto o inválido",
        }
    if not isinstance(daily, dict):
        return {
            "status": "error",
            "report": str(report_path),
            "detail": f"JSON no es objeto dict (tipo {type(daily).__name__})",
        }
    if "date" not in daily:
        return {
            "status": "error",
            "report": str(report_path),
            "detail": "Falta campo 'date' en el reporte",
        }

    # Identidad (con override CLI / defaults forward-compatible)
    symbol = daily.get("symbol", default_symbol)
    strategy = daily.get("strategy", default_strategy)
    backtest_date = str(daily["date"])

    payload = _build_attribution_payload(daily, symbol, strategy)
    total_trades = int(payload.get("total_trades", 0) or 0)
    total_pnl = float(payload.get("total_pnl", 0) or 0)

    # Idempotencia
    if (not dry_run) and _already_imported(
        attribution.db_path,
        strategy,
        symbol,
        backtest_date,
        total_trades,
        total_pnl,
    ):
        return {
            "status": "skipped",
            "report": str(report_path),
            "detail": (
                f"Ya existe fila para strategy='{strategy}' symbol='{symbol}' "
                f"date='{backtest_date}' trades={total_trades} pnl={total_pnl:.4f}"
            ),
        }

    if dry_run:
        return {
            "status": "imported (dry-run)",
            "report": str(report_path),
            "detail": (
                f"[dry-run] save_performance(strategy='{strategy}', "
                f"symbol='{symbol}', date='{backtest_date}', "
                f"trades={total_trades}, pnl={total_pnl:.4f})"
            ),
        }

    try:
        row_id = attribution.save_performance(
            strategy_name=strategy,
            symbol=symbol,
            result=payload,
            backtest_date=backtest_date,
            total_trades=total_trades,
        )
    except Exception as e:
        logger.exception("save_performance falló para %s", report_path)
        return {
            "status": "error",
            "report": str(report_path),
            "detail": f"save_performance: {e}",
        }
    return {
        "status": "imported",
        "report": str(report_path),
        "row_id": row_id,
        "detail": (
            f"strategy='{strategy}' symbol='{symbol}' date='{backtest_date}' "
            f"trades={total_trades} pnl={total_pnl:.4f}"
        ),
    }


def process_all(
    report_dir: Path | None = None,
    db_path: Path | None = None,
    default_symbol: str = DEFAULT_SYMBOL,
    default_strategy: str = DEFAULT_STRATEGY,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Procesa todos los daily reports en ``report_dir``.

    Returns:
        ``{"total": N, "imported": n, "skipped": n, "errors": n, "results": [...]}``
    """
    report_dir = report_dir or REPORTS_DAILY_DIR
    attribution = StrategyAttribution(db_path=db_path) if db_path else StrategyAttribution()
    files = _list_daily_reports(report_dir)
    if not files:
        logger.warning("Sin daily reports en %s", report_dir)
        return {
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "results": [],
            "report_dir": str(report_dir),
            "db_path": str(attribution.db_path),
        }

    results: list[dict[str, Any]] = []
    imported = skipped = errors = 0
    for f in files:
        r = process_report(
            f,
            attribution,
            default_symbol=default_symbol,
            default_strategy=default_strategy,
            dry_run=dry_run,
        )
        results.append(r)
        s = r["status"]
        if s in ("imported", "imported (dry-run)"):
            imported += 1
        elif s == "skipped":
            skipped += 1
        else:
            errors += 1
    return {
        "total": len(files),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "results": results,
        "report_dir": str(report_dir),
        "db_path": str(attribution.db_path),
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="daily_attribution",
        description=(
            "Importa reportes diarios (reports/daily/*.json) a "
            "strategy_performance en strategies.db."
        ),
    )
    p.add_argument(
        "--report",
        type=str,
        default=None,
        help=(
            "Procesa un solo reporte (fecha YYYY-MM-DD o path). "
            "Si se omite, procesa todos los daily reports."
        ),
    )
    p.add_argument(
        "--report-dir",
        type=str,
        default=None,
        help=f"Directorio de daily reports (default: {REPORTS_DAILY_DIR}).",
    )
    p.add_argument(
        "--db-path",
        type=str,
        default=None,
        help=f"Ruta absoluta a strategies.db (default: {DEFAULT_DB_PATH}).",
    )
    p.add_argument(
        "--symbol",
        type=str,
        default=DEFAULT_SYMBOL,
        help=(
            f"Símbolo por defecto para reportes que no lo traen "
            f"(default: {DEFAULT_SYMBOL})."
        ),
    )
    p.add_argument(
        "--strategy",
        type=str,
        default=DEFAULT_STRATEGY,
        help=(
            f"Nombre de estrategia por defecto para reportes sin "
            f"estrategia (default: {DEFAULT_STRATEGY})."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Reporta qué importaría sin escribir en la DB.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Logging INFO del proceso.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    report_dir = Path(args.report_dir) if args.report_dir else REPORTS_DAILY_DIR
    db_path = Path(args.db_path) if args.db_path else None
    attribution = StrategyAttribution(db_path=db_path) if db_path else StrategyAttribution()

    # Modo: un reporte específico (--report) o tutti (--all).
    if args.report:
        target = args.report
        if Path(target).is_file():
            report_file = Path(target)
        else:
            # Asume fecha YYYY-MM-DD
            report_file = report_dir / f"{target}.json"
        if not report_file.exists():
            logger.error("Daily report no encontrado: %s", report_file)
            print(f"ERROR: daily report no encontrado: {report_file}", file=sys.stderr)
            return 2
        result = process_report(
            report_file,
            attribution,
            default_symbol=args.symbol,
            default_strategy=args.strategy,
            dry_run=args.dry_run,
        )
        print(
            f"{result['status']:25s} {result['report']:35s} {result.get('detail', '')}"
        )
        return 0 if result["status"] in ("imported", "imported (dry-run)", "skipped") else 1

    # Modo: todos
    if not report_dir.exists() or not report_dir.is_dir():
        logger.error("Directorio de daily reports no existe: %s", report_dir)
        print(f"ERROR: directorio no existe: {report_dir}", file=sys.stderr)
        return 2
    summary = process_all(
        report_dir=report_dir,
        db_path=db_path,
        default_symbol=args.symbol,
        default_strategy=args.strategy,
        dry_run=args.dry_run,
    )
    print(
        f"Daily Attribution — report_dir: {summary['report_dir']}, "
        f"db: {summary['db_path']}"
    )
    print(
        f"Total={summary['total']}  Imported={summary['imported']}  "
        f"Skipped={summary['skipped']}  Errors={summary['errors']}"
    )
    if args.verbose:
        for r in summary["results"]:
            print(f"  {r['status']:25s} {r['report']:35s} {r.get('detail', '')}")
    # Exit codes: 0 si todo bien, 1 si hubo errores, 2 si no había reports.
    if summary["total"] == 0:
        return 2
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
