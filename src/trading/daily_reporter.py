"""Daily reporter — genera reportes diarios de trading a las 00:00 UTC.

Genera un JSON con:
    - Balance inicial y final del día
    - P&L total, win rate, trades
    - Max drawdown, Sharpe
    - Estado del circuit breaker
    - Lista de trades del día

Guarda en reports/daily/YYYY-MM-DD.json
Envía a Telegram si está configurado.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


class DailyReporter:
    """Genera y persiste reportes diarios de trading.

    Args:
        report_dir: Directorio para guardar JSONs (default reports/daily)
        telegram: Notificador Telegram opcional
    """

    def __init__(
        self,
        report_dir: str = "reports/daily",
        telegram: TelegramNotifier | None = None,
    ) -> None:
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.telegram = telegram

    def generate_report(
        self,
        date: str,
        starting_balance: float,
        ending_balance: float,
        trades: list[dict[str, Any]],
        circuit_breaker_status: dict[str, Any] | None = None,
        risk_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Genera el reporte diario completo.

        Args:
            date: Fecha en formato YYYY-MM-DD
            starting_balance: Balance al inicio del día (00:00 UTC)
            ending_balance: Balance al final del día
            trades: Lista de trades del día (dicts con pnl, direction, etc.)
            circuit_breaker_status: Estado del circuit breaker
            risk_report: Reporte del risk manager

        Returns:
            Diccionario con el reporte completo
        """
        wins = sum(1 for t in trades if float(t.get("pnl", 0)) > 0)
        losses = len(trades) - wins
        total_pnl = sum(float(t.get("pnl", 0)) for t in trades)
        win_rate = wins / len(trades) if trades else 0.0

        # Max drawdown calculation
        max_dd = 0.0
        if starting_balance > 0 and len(trades) > 0:
            peak = starting_balance
            running_balance = starting_balance
            for t in trades:
                pnl = float(t.get("pnl", 0))
                running_balance += pnl
                if running_balance > peak:
                    peak = running_balance
                dd = (peak - running_balance) / peak if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd

        # Sharpe ratio (simplified)
        sharpe = 0.0
        if len(trades) > 1:
            import numpy as np
            pnls = np.array([float(t.get("pnl", 0)) for t in trades])
            if np.std(pnls) > 0:
                sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252))

        report: dict[str, Any] = {
            "date": date,
            "starting_balance": round(starting_balance, 2),
            "ending_balance": round(ending_balance, 2),
            "total_pnl": round(total_pnl, 4),
            "pnl_pct": round((total_pnl / starting_balance) * 100, 4) if starting_balance > 0 else 0,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_dd, 6),
            "sharpe_ratio": round(sharpe, 2),
            "circuit_halted": bool(circuit_breaker_status.get("is_halted", False)) if circuit_breaker_status else False,
            "halt_reason": circuit_breaker_status.get("halt_reason", "") if circuit_breaker_status else "",
            "consecutive_losses": circuit_breaker_status.get("consecutive_losses", 0) if circuit_breaker_status else 0,
            "trades": trades,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save to JSON
        report_file = self.report_dir / f"{date}.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("Reporte diario guardado: %s (%d trades, P&L=$%.2f)", report_file, len(trades), total_pnl)

        # Send to Telegram
        if self.telegram is not None:
            success = self.telegram.send_daily_report(report)
            if success:
                logger.info("Reporte diario enviado a Telegram para %s", date)
            else:
                logger.warning("Falló envío a Telegram para %s", date)

        return report

    def load_report(self, date: str) -> dict[str, Any] | None:
        """Carga un reporte diario desde JSON.

        Args:
            date: Fecha en formato YYYY-MM-DD

        Returns:
            Reporte o None si no existe
        """
        report_file = self.report_dir / f"{date}.json"
        if not report_file.exists():
            return None
        return json.loads(report_file.read_text())

    def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        """Lista todos los reportes diarios disponibles.

        Args:
            limit: Máximo número de reportes a retornar

        Returns:
            Lista de reportes (sin trades para reducir tamaño)
        """
        reports: list[dict[str, Any]] = []
        for report_file in sorted(self.report_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(report_file.read_text())
                # Sin trades para el list (se cargan individualmente si se necesitan)
                data.pop("trades", None)
                reports.append(data)
            except Exception:
                continue
        return reports
