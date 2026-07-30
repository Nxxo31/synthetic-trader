"""Telegram notifier — envía reportes diarios y alertas del bot.

Configuración (.env):
    TELEGRAM_BOT_TOKEN=tu_bot_token_de_botfather
    TELEGRAM_CHAT_ID=tu_chat_id

Uso:
    from src.notifications.telegram import TelegramNotifier
    notifier = TelegramNotifier.from_env()
    notifier.send_daily_report(report_dict)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Telegram Bot API base URL
TG_API_BASE = "https://api.telegram.org/bot"


class TelegramNotifier:
    """Notificador por Telegram para reportes diarios y alertas.

    Args:
        bot_token: Token del bot (de @BotFather)
        chat_id: Chat ID destino (tuyo o grupo)
    """

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"{TG_API_BASE}{bot_token}/sendMessage"

    @classmethod
    def from_env(cls) -> "TelegramNotifier | None":
        """Crea el notificador desde variables de entorno.

        Retorna None si las variables no están configuradas.
        """
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning(
                "Telegram no configurado: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID faltan en .env"
            )
            return None
        logger.info("Telegram notifier configurado (chat_id=%s)", chat_id)
        return cls(token, chat_id)

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Envía un mensaje de texto al chat configurado.

        Args:
            text: Mensaje a enviar (soporta Markdown)
            parse_mode: "Markdown" o "HTML"

        Returns:
            True si se envió correctamente
        """
        try:
            response = requests.post(
                self.api_url,
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=15,
            )
            if response.status_code == 200:
                return True
            logger.error(
                "Telegram API error: %d %s",
                response.status_code,
                response.text[:200],
            )
            return False
        except requests.Timeout:
            logger.error("Telegram: timeout enviando mensaje")
            return False
        except Exception as e:
            logger.error("Telegram error: %s", e)
            return False

    def send_daily_report(self, report: dict[str, Any]) -> bool:
        """Formatea y envía el reporte diario de trading.

        Args:
            report: Diccionario con keys: date, starting_balance, ending_balance,
                    total_pnl, win_rate, total_trades, wins, losses, max_drawdown,
                    sharpe_ratio, circuit_halted, halt_reason, consecutive_losses

        Returns:
            True si se envió correctamente
        """
        date = report.get("date", "N/A")
        pnl = float(report.get("total_pnl", 0))
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        win_rate = float(report.get("win_rate", 0)) * 100
        trades = int(report.get("total_trades", 0))
        wins = int(report.get("wins", 0))
        losses = int(report.get("losses", 0))
        max_dd = float(report.get("max_drawdown", 0)) * 100
        sharpe = float(report.get("sharpe_ratio", 0))
        starting = float(report.get("starting_balance", 0))
        ending = float(report.get("ending_balance", 0))
        halted = bool(report.get("circuit_halted", False))
        halt_reason = report.get("halt_reason", "")
        consec_losses = int(report.get("consecutive_losses", 0))

        status_emoji = "🔴" if halted else "🟢"
        status_text = "DETENIDO" if halted else "ACTIVO"

        message = (
            f"📊 *Reporte Diario — Synthetic Trader*\n"
            f"📅 Fecha: `{date}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{pnl_emoji} *P&L del día*: `{pnl_str}`\n"
            f"💰 *Balance*: `${starting:.2f}` → `${ending:.2f}`\n"
            f"📈 *Win Rate*: `{win_rate:.1f}%` ({wins}G/{losses}P)\n"
            f"🔢 *Trades*: `{trades}`\n"
            f"🔻 *Max Drawdown*: `{max_dd:.2f}%`\n"
            f"⚡ *Sharpe*: `{sharpe:.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_emoji} *Circuit Breaker*: `{status_text}`\n"
        )
        if halted and halt_reason:
            message += f"🛑 *Razón*: {halt_reason}\n"
        if consec_losses > 0:
            message += f"⚠️ *Pérdidas consec.*: `{consec_losses}`\n"
        message += f"━━━━━━━━━━━━━━━━━━━━\n"
        message += f"🤖 _Synthetic Trader v0.2.0_"

        return self.send_message(message)

    def send_alert(self, title: str, message: str) -> bool:
        """Envía una alerta inmediata (no espera al reporte diario).

        Args:
            title: Título corto de la alerta
            message: Detalle del alerta

        Returns:
            True si se envió correctamente
        """
        text = f"🚨 *ALERTA — Synthetic Trader*\n\n*{title}*\n\n{message}"
        return self.send_message(text)

    def send_trade_notification(self, trade: dict[str, Any]) -> bool:
        """Envía notificación de un trade ejecutado.

        Args:
            trade: Diccionario con datos del trade

        Returns:
            True si se envió correctamente
        """
        direction = trade.get("direction", "?")
        symbol = trade.get("symbol", "?")
        entry = float(trade.get("entry_price", 0))
        sl = float(trade.get("stop_loss", 0))
        tp = float(trade.get("take_profit", 0))
        stake = float(trade.get("stake", 0))
        confidence = float(trade.get("confidence", 0))
        score = float(trade.get("score", 0))

        dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"

        message = (
            f"🔔 *Nuevo Trade Ejecutado*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{dir_emoji} `{symbol}`\n"
            f"📍 Entrada: `{entry:.2f}`\n"
            f"🛑 SL: `{sl:.2f}`\n"
            f"🎯 TP: `{tp:.2f}`\n"
            f"💵 Stake: `${stake:.2f}`\n"
            f"📊 Score: `{score:.2f}` | Confianza: `{confidence:.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        return self.send_message(message)
