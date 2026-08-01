"""Pair Trading Strategy based on Ornstein-Uhlenbeck process.

Implements pair trading between two cointegrated synthetic indices using the
Ornstein-Uhlenbeck mean-reversion process as described in Leung & Li (2015/2016)
Optimal Mean Reversion Trading.

The spread between two indices is modeled as:
 dX_t = (θ - κ*X_t)dt + σ*dW_t

Where:
- κ = speed of mean reversion (estimated via MLE)
- θ = equilibrium level
- σ = volatility of the process
- W_t = Wiener process
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class OUParameters:
    kappa: float  # speed of mean reversion
    theta: float  # long-term mean
    sigma: float  # volatility
    half_life: float  # ln(2)/kappa

class PairTradingStrategy:
    """
    Pair trading strategy using Ornstein-Uhlenbeck process for spread modeling.
    
    Based on Leung & Li (2015/2016) Optimal Mean Reversion Trading.
    """
    
    def __init__(self, 
                 symbol_a: str, 
                 symbol_b: str,
                 entry_threshold: float = 1.5,
                 exit_threshold: float = 0.5,
                 stop_threshold: float = 3.0,
                 min_half_life: int = 5,
                 max_half_life: int = 500):
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_threshold = stop_threshold
        self.min_half_life = min_half_life
        self.max_half_life = max_half_life
        
    def estimate_ou_params(self, spread_series: pd.Series) -> OUParameters:
        """
        Estimate OU parameters via Maximum Likelihood Estimation.
        
        Based on discretized OU process: X_t = α + β*X_{t-1} + ε_t
        where β = e^(-κ*dt), α = θ*(1-β), σ_ε^2 = σ^2*(1-β^2)/(2κ)
        """
        if len(spread_series) < 2:
            raise ValueError("Need at least 2 observations to estimate OU parameters")
            
        # Lagged regression: X_t = α + β*X_{t-1} + ε_t
        x_lag = spread_series.shift(1).dropna()
        x_current = spread_series[1:]  # Align with lagged values
        
        if len(x_lag) == 0:
            raise ValueError("Insufficient data after lagging")
            
        # Simple linear regression
        x_mean = x_lag.mean()
        y_mean = x_current.mean()
        
        numerator = ((x_lag - x_mean) * (x_current - y_mean)).sum()
        denominator = ((x_lag - x_mean) ** 2).sum()
        
        if denominator == 0:
            beta = 0.0
        else:
            beta = numerator / denominator
            
        alpha = y_mean - beta * x_mean
        
        # Convert to OU parameters (assuming dt = 1 for simplicity)
        # beta = e^(-κ*dt) => κ = -ln(beta)
        if beta <= 0 or beta >= 1:
            # Handle edge cases
            kappa = max(0.001, abs(beta))  # fallback
        else:
            kappa = -np.log(beta)
            
        theta = alpha / (1 - beta) if beta != 1.0 else x_mean
        
        # Residual variance
        residuals = x_current - (alpha + beta * x_lag)
        sigma_eps_squared = (residuals ** 2).sum() / (len(residuals) - 2)
        
        # Convert to OU volatility: σ_ε^2 = σ^2*(1-β^2)/(2κ)
        if kappa > 0:
            sigma_squared = sigma_eps_squared * (2.0 / (1.0 - beta ** 2))
            sigma = np.sqrt(max(sigma_squared, 0.0))
        else:
            sigma = np.sqrt(sigma_eps_squared)
        
        half_life = np.log(2.0) / kappa if kappa > 0.0 else float('inf')
        
        return OUParameters(
            kappa=kappa,
            theta=theta,
            sigma=sigma,
            half_life=half_life
        )
    
    def compute_zscore(self, spread: float, lookback: int = 100) -> float:
        """
        Compute z-score of current spread vs recent history.
        """
        # This would need recent spread history - simplified for now
        # In practice, maintain rolling window of spread values
        return 0.0  # Placeholder
    
    def generate_signal(self, price_a: pd.Series, price_b: pd.Series) -> dict:
        """
        Generate trading signal based on current prices.
        Returns dict with action, zscore, confidence, etc.
        """
        # Calculate spread (log price ratio is common for pairs)
        spread = np.log(float(price_a.iloc[-1])) - np.log(float(price_b.iloc[-1]))
        
        # In practice, we'd need historical spread to compute z-score properly
        # For now, return placeholder
        return {
            "action": "HOLD",
            "zscore": 0.0,
            "confidence": 0.0,
            "spread": float(spread),
            "signal_strength": 0.0
        }
    
    def backtest(self, 
                 price_a: pd.Series, 
                 price_b: pd.Series,
                 initial_capital: float = 10000.0) -> dict:
        """
        Run backtest on historical price data.
        Returns dict with trades, equity curve, and performance metrics.
        """
        # Simplified placeholder - full implementation would walk through time
        # generating signals, executing trades, tracking P&L, etc.
        return {
            "trades": [],
            "equity_curve": [float(initial_capital)],
            "total_pnl": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "total_trades": 0
        }
    
    def compute_gate_metrics(self, backtest_result: dict) -> dict:
        """
        Compute metrics for strategy gate evaluation.
        Matches Pradx gate criteria: Sharpe>1.2, DD<12%, WR>52%, Expectancy>0.15R
        """
        return {
            "sharpe_ratio": float(backtest_result.get("sharpe_ratio", 0.0)),
            "max_drawdown": float(backtest_result.get("max_drawdown", 0.0)),
            "win_rate": float(backtest_result.get("win_rate", 0.0)),
            "expectancy": float(backtest_result.get("expectancy", 0.0)),
            "gate_passed": (
                float(backtest_result.get("sharpe_ratio", 0)) > 1.2 and
                float(backtest_result.get("max_drawdown", 1.0)) < 0.12 and
                float(backtest_result.get("win_rate", 0)) > 0.52 and
                float(backtest_result.get("expectancy", 0)) > 0.15
            )
        }