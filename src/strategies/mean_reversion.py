"""Mean Reversion Strategy using Bollinger Bands + RSI.

Optimized for volatility indices R_25, R_50, R_75 which follow Ornstein-Uhlenbeck
(mean-reverting) process.

Uses 3-confirmation entry logic:
LONG: Price touches lower BB AND RSI < 30 AND price closes above lower BB
SHORT: Price touches upper BB AND RSI > 70 AND price closes below upper BB
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class BBands:
    upper: float
    middle: float  # SMA
    lower: float
    bandwidth: float  # (upper - lower) / middle

@dataclass
class Signal:
    action: str  # "LONG", "SHORT", "HOLD"
    confidence: float  # 0.0 to 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    metadata: dict

class MeanReversionStrategy:
    """
    Mean reversion strategy using Bollinger Bands + RSI with 3-confirmation entry.
    
    Optimized for R_25, R_50, R_75 volatility indices (mean-reverting/OU process).
    """
    
    def __init__(self, 
                 symbol: str,
                 bb_period: int = 20,
                 bb_std: float = 2.0,
                 rsi_period: int = 14,
                 rsi_oversold: float = 30.0,
                 rsi_overbought: float = 70.0,
                 risk_per_trade: float = 0.015):
        self.symbol = symbol
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.risk_per_trade = risk_per_trade  # 1.5% risk per trade
        
    def _calculate_bollinger_bands(self, prices: pd.Series, period: int, num_std: float) -> BBands:
        """
        Calculate Bollinger Bands for price series.
        """
        if len(prices) < period:
            # Not enough data - return NaNs
            return BBands(float('nan'), float('nan'), float('nan'), float('nan'))
            
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        
        upper = sma + (std * num_std)
        lower = sma - (std * num_std)
        bandwidth = (upper - lower) / sma
        
        # Return latest values as Python floats
        return BBands(
            upper=float(upper.iloc[-1]),
            middle=float(sma.iloc[-1]),
            lower=float(lower.iloc[-1]),
            bandwidth=float(bandwidth.iloc[-1])
        )
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """
        Calculate Relative Strength Index (RSI).
        """
        if len(prices) < period + 1:
            return 50.0  # Neutral when insufficient data
            
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # Avoid division by zero
        if loss.iloc[-1] == 0:
            return 100.0 if gain.iloc[-1] > 0 else 50.0
            
        rs = gain.iloc[-1] / loss.iloc[-1]
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """
        Generate trading signal based on OHLCV data.
        Assumes DataFrame has columns: ['open', 'high', 'low', 'close', 'volume']
        """
        if len(data) < max(self.bb_period, self.rsi_period) + 1:
            return Signal(
                action="HOLD",
                confidence=0.0,
                entry_price=float(data['close'].iloc[-1]) if len(data) > 0 else 0.0,
                stop_loss=0.0,
                take_profit=0.0,
                metadata={"reason": "insufficient_data"}
            )
            
        # Get latest price data
        close_prices = data['close']
        high_prices = data['high']
        low_prices = data['low']
        current_price = float(close_prices.iloc[-1])
        prev_close = float(close_prices.iloc[-2]) if len(close_prices) > 1 else current_price
        
        # Calculate Bollinger Bands
        bb = self._calculate_bollinger_bands(close_prices, self.bb_period, self.bb_std)
        
        # Calculate RSI
        current_rsi = self._calculate_rsi(close_prices, self.rsi_period)
        
        # Check if Bollinger Bands are valid (not too tight)
        if bb.bandwidth < 0.001:  # Bands too tight - low volatility environment
            return Signal(
                action="HOLD",
                confidence=0.0,
                entry_price=current_price,
                stop_loss=current_price,
                take_profit=current_price,
                metadata={"reason": "bands_too_tight", "bandwidth": bb.bandwidth}
            )
        
        # Price touching bands (with small tolerance)
        touches_lower = float(low_prices.iloc[-1]) <= bb.lower * 1.001  # Allow 0.1% tolerance
        touches_upper = float(high_prices.iloc[-1]) >= bb.upper * 0.999
        
        # Initialize signal as HOLD
        signal = Signal(
            action="HOLD",
            confidence=0.0,
            entry_price=current_price,
            stop_loss=current_price,
            take_profit=current_price,
            metadata={
                "bb_upper": bb.upper,
                "bb_middle": bb.middle,
                "bb_lower": bb.lower,
                "bb_bandwidth": bb.bandwidth,
                "rsi": current_rsi,
                "price": current_price,
                "touches_lower": touches_lower,
                "touches_upper": touches_upper
            }
        )
        
        # LONG signal: mean reversion up (price expected to rise from lower band)
        if touches_lower and current_rsi < self.rsi_oversold and prev_close > bb.lower:
            # 3 confirmations:
            # 1. Price touched/broke below lower Bollinger Band
            # 2. RSI is oversold (< 30)
            # 3. Previous close was above lower band (confirming bounce/reversal)
            
            # Calculate confidence based on how extreme conditions are
            rsi_factor = (self.rsi_oversold - current_rsi) / self.rsi_oversold  # 0 to 1+
            price_position = (current_price - bb.lower) / (bb.upper - bb.lower)  # 0 to 1
            bb_width_factor = min(bb.bandwidth * 100.0, 1.0)  # Wider bands = higher confidence
            
            confidence = min(0.95, 0.5 + rsi_factor * 0.3 + price_position * 0.1 + bb_width_factor * 0.1)
            
            # Position sizing based on ATR-like volatility (simplified)
            atr_estimate = (bb.upper - bb.lower) / 2  # Rough volatility estimate
            stop_loss = bb.lower * 0.995  # Slightly below lower band
            take_profit = bb.middle  # Target middle band (mean reversion to SMA)
            
            # Ensure stop loss is reasonable
            if stop_loss >= current_price:
                stop_loss = current_price * 0.99
                
            signal = Signal(
                action="LONG",
                confidence=confidence,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    **signal.metadata,
                    "signal_type": "mean_reversion_long",
                    "rsi_condition": current_rsi < self.rsi_oversold,
                    "price_touch_lower": touches_lower,
                    "prev_close_above_lower": prev_close > bb.lower
                }
            )
        
        # SHORT signal: mean reversion down (price expected to fall from upper band)
        elif touches_upper and current_rsi > self.rsi_overbought and prev_close < bb.upper:
            # 3 confirmations:
            # 1. Price touched/broke above upper Bollinger Band
            # 2. RSI is overbought (> 70)
            # 3. Previous close was below upper band (confirming rejection/reversal)
            
            # Calculate confidence
            rsi_factor = (current_rsi - self.rsi_overbought) / (100.0 - self.rsi_overbought)  # 0 to 1+
            price_position = (bb.upper - current_price) / (bb.upper - bb.lower)  # 0 to 1
            bb_width_factor = min(bb.bandwidth * 100.0, 1.0)
            
            confidence = min(0.95, 0.5 + rsi_factor * 0.3 + (1.0 - price_position) * 0.1 + bb_width_factor * 0.1)
            
            # Stop loss slightly above upper band, target middle band
            stop_loss = bb.upper * 1.005
            take_profit = bb.middle
            
            if stop_loss <= current_price:
                stop_loss = current_price * 1.01
                
            signal = Signal(
                action="SHORT",
                confidence=confidence,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    **signal.metadata,
                    "signal_type": "mean_reversion_short",
                    "rsi_condition": current_rsi > self.rsi_overbought,
                    "price_touch_upper": touches_upper,
                    "prev_close_below_upper": prev_close < bb.upper
                }
            )
            
        return signal
    
    def backtest(self, data: pd.DataFrame, initial_capital: float = 10000.0) -> dict:
        """
        Run backtest on historical OHLCV data.
        Simplified implementation - production version would be more complex.
        """
        # Placeholder - full implementation would walk through data bar by bar
        # generating signals, managing positions, calculating P&L, etc.
        return {
            "trades": [],
            "equity_curve": [float(initial_capital)],
            "total_pnl": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "expectancy": 0.0
        }
    
    def compute_gate_metrics(self, backtest_result: dict) -> dict:
        """
        Compute metrics for strategy gate evaluation.
        Matches Pradx gate criteria: Sharpe>1.2, DD<12%, WR>52%, Expectancy>0.15R
        """
        sharpe = float(backtest_result.get("sharpe_ratio", 0.0))
        max_dd = min(float(backtest_result.get("max_drawdown", 1.0)), 1.0)  # Cap at 100%
        win_rate = min(float(backtest_result.get("win_rate", 0.0)), 1.0)
        expectancy = max(float(backtest_result.get("expectancy", 0.0)), -10.0)  # Floor at -10R
        
        return {
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "expectancy": expectancy,
            "gate_passed": (
                sharpe > 1.2 and
                max_dd < 0.12 and
                win_rate > 0.52 and
                expectancy > 0.15
            )
        }