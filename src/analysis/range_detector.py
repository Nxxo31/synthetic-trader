"""Range detector — dynamic channel detection via rolling high/low.

Identifies support/resistance channels using a rolling window of recent
candles. Used by the multi-factor signal scorer to measure breakout
penetration depth.

RB100 (Range Break Index) is the only synthetic where technical
channel analysis works — price oscillates between boundaries and breaks
out periodically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelResult:
    """Resultado de la detección de canal."""
    support: float           # nivel de soporte (mínimo del window)
    resistance: float        # nivel de resistencia (máximo del window)
    mid_price: float         # precio medio del canal
    channel_width: float    # ancho absoluto del canal
    width_pct: float         # ancho como fracción del precio medio
    window_size: int        # número de candles usadas
    valid: bool             # True si el canal es suficientemente ancho


class RangeDetector:
    """Detecta canales dinámicos con rolling high/low.

    Args:
        window_size: número de candles para el rolling window (default 20)
        min_width_pct: ancho mínimo del canal como fracción del precio medio
                       (default 0.0001 = 0.01% — RB100 channels are tight)
    """

    def __init__(self, window_size: int = 20, min_width_pct: float = 0.0001) -> None:
        self.window_size = window_size
        self.min_width_pct = min_width_pct

    def detect(self, data: pd.DataFrame, lookback: int | None = None) -> ChannelResult:
        """Detecta el canal a partir de las candles anteriores a la última.

        Usa las `lookback` candles (o `window_size` por defecto) ANTES de la
        última candle para definir soporte/resistencia. La última candle es
        la que se evalúa para breakout.

        Args:
            data: DataFrame con columnas high, low, close (y opcionalmente epoch)
            lookback: override del window_size

        Returns:
            ChannelResult con soporte, resistencia, ancho y validez
        """
        n = lookback if lookback is not None else self.window_size
        total_needed = n + 1  # +1 para la candle de breakout

        if len(data) < total_needed:
            logger.debug(
                "Datos insuficientes para canal: %d < %d", len(data), total_needed
            )
            return ChannelResult(
                support=0.0, resistance=0.0, mid_price=0.0,
                channel_width=0.0, width_pct=0.0, window_size=n, valid=False,
            )

        # Canal desde candles ANTES de la última
        window = data.iloc[-(n + 1):-1]

        support = float(window["low"].min())
        resistance = float(window["high"].max())
        mid_price = (support + resistance) / 2.0
        channel_width = resistance - support
        width_pct = channel_width / mid_price if mid_price > 0 else 0.0

        valid = width_pct >= self.min_width_pct and channel_width > 0

        logger.debug(
            "Canal detectado: support=%.5f resistance=%.5f width=%.5f (%.4f%%) valid=%s",
            support, resistance, channel_width, width_pct * 100, valid,
        )

        return ChannelResult(
            support=support,
            resistance=resistance,
            mid_price=mid_price,
            channel_width=channel_width,
            width_pct=width_pct,
            window_size=n,
            valid=valid,
        )

    def penetration_depth(
        self,
        last_close: float,
        channel: ChannelResult,
    ) -> tuple[float, str]:
        """Calcula la profundidad de penetración del breakout.

        Args:
            last_close: precio de cierre de la candle de breakout
            channel: resultado de detect()

        Returns:
            Tupla (penetration_score 0-1, dirección "LONG"/"SHORT"/"NONE")
        """
        if not channel.valid or channel.channel_width <= 0:
            return 0.0, "NONE"

        # LONG: close above resistance
        if last_close > channel.resistance:
            depth = abs(last_close - channel.resistance) / channel.channel_width
            # Normalizar: 1x channel_width = score máximo (0.4 cap)
            score = min(0.4, depth * 0.4)
            return round(score, 4), "LONG"

        # SHORT: close below support
        if last_close < channel.support:
            depth = abs(channel.support - last_close) / channel.channel_width
            score = min(0.4, depth * 0.4)
            return round(score, 4), "SHORT"

        return 0.0, "NONE"
