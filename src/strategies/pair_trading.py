"""Pair Trading Strategy based on Ornstein-Uhlenbeck process.

Implements pair trading between two cointegrated synthetic indices using the
Ornstein-Uhlenbeck mean-reversion process as described in Leung & Li (2015/2016)
Optimal Mean Reversion Trading.

The spread between two indices is modeled as:
    dX_t = (theta - kappa*X_t)*dt + sigma*dW_t

Where:
  - kappa  = speed of mean reversion (estimated via MLE)
  - theta  = equilibrium level (long-term mean)
  - sigma  = volatility of the process
  - W_t    = Wiener process (Brownian motion)

Trading signals are generated based on Z-score thresholds:
  - LONG  spread when Z < -entry_threshold   (expect spread to revert upward)
  - SHORT spread when Z > +entry_threshold   (expect spread to revert downward)
  - Exit  when abs(Z) < exit_threshold       (spread reverts to mean)
  - Stop  loss when abs(Z) > stop_threshold   (extreme deviation)

Position sizing is based on the OU process half-life:
    T_1/2 = ln(2) / kappa

Pairs with too-short half-life (< min_half_life periods) are noise and are
skipped. Pairs with too-long half-life (> max_half_life periods) may not be
practically mean-reverting and are also skipped.

Cointegration is checked via a simplified Engle-Granger two-step procedure:
  (1) Fit OLS: symbol_a ~ intercept + slope * symbol_b
  (2) Test spread residuals for stationarity (ADF-like heuristic)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Configuration dataclass
# --------------------------------------------------------------------------- #

@dataclass
class PairTradingConfig:
    """Configuration for the Pair Trading (OU process) strategy.

    Attributes:
        entry_threshold:  Z-score threshold for entering trades (default 1.5).
        exit_threshold:   Z-score threshold for exiting trades (default 0.5).
        stop_threshold:   Z-score threshold for stop-loss (default 3.0).
        min_half_life:    Minimum half-life in periods to consider trading (default 5).
        max_half_life:    Maximum half-life in periods to consider trading (default 500).
        lookback_window:  Rolling window for statistics / hedge-ratio estimation.
    """
    entry_threshold: float = 1.5
    exit_threshold: float = 0.5
    stop_threshold: float = 3.0
    min_half_life: int = 5
    max_half_life: int = 500
    lookback_window: int = 100


# ------------------------------------------------------------------------- #
#  OU parameter estimation helper
# ------------------------------------------------------------------------- #

def _estimate_ou_params_ols(spread: pd.Series) -> dict[str, float]:
    """Estimate Ornstein-Uhlenbeck parameters via OLS regression.

    Discretised OU  :  X_t - X_{t-1} = alpha + beta * X_{t-1} + epsilon_t
    Recovery formulas:
        kappa = -ln(1 + beta) / dt
        theta = alpha / (1 - e^{-kappa * dt})
        sigma^2 = Var(epsilon) * (2 * kappa) / (1 - e^{-2 * kappa * dt})

    Returns dict with keys ``kappa``, ``theta``, ``sigma``, ``half_life``.
    """
    if len(spread) < 2:
        return {"kappa": 0.0, "theta": 0.0, "sigma": 0.0, "half_life": float("inf")}

    x_lag = spread.shift(1).dropna().values   # X_{t-1}
    x_diff = spread.diff().dropna().values     # ΔX_t = X_t - X_{t-1}

    min_len = min(len(x_lag), len(x_diff))
    if min_len < 2:
        return {"kappa": 0.0, "theta": 0.0, "sigma": 0.0, "half_life": float("inf")}

    x_lag = x_lag[-min_len:]
    x_diff = x_diff[-min_len:]

    slope, intercept, _r, _p, _stderr = stats.linregress(x_lag, x_diff)
    beta = slope
    alpha = intercept
    dt = 1.0  # assumes observations are equally spaced

    # kappa = -ln(1 + beta) / dt
    if 1.0 + beta <= 0:
        kappa = 0.0
    else:
        kappa = -math.log(1.0 + beta) / dt

    # theta = alpha / (1 - e^{-kappa * dt})
    if kappa == 0.0:
        theta = float(spread.mean())
    else:
        theta = alpha / (1.0 - math.exp(-kappa * dt))

    # sigma
    residuals = x_diff - (alpha + beta * x_lag)
    residual_var = float(np.var(residuals)) if len(residuals) > 1 else 0.0
    if kappa == 0.0:
        sigma = math.sqrt(residual_var) if residual_var > 0 else 0.0
    else:
        denom = (1.0 - math.exp(-2.0 * kappa * dt))
        if denom <= 0:
            sigma = 0.0
        else:
            sigma = math.sqrt(residual_var * (2.0 * kappa) / denom)

    # half-life
    half_life = math.log(2.0) / kappa if kappa > 0 else float("inf")

    return {"kappa": kappa, "theta": theta, "sigma": sigma, "half_life": half_life}


# ------------------------------------------------------------------------- #
#  Cointegration test  (simplified Engle-Granger)
# ------------------------------------------------------------------------- #

def cointegration_test(
    series_a: pd.Series,
    series_b: pd.Series,
    significance: float = 0.05,
    maxlag: int | None = None,
) -> dict[str, Any]:
    """Simplified Engle-Granger cointegration test.

    1. Regress A on B (OLS) to obtain hedge ratio and residuals.
    2. Test residuals for stationarity with an Augmented Dickey-Fuller test.
    3. Return test statistics and pass/fail verdict.

    Args:
        series_a: Price series for the first instrument.
        series_b: Price series for the second instrument.
        significance: p-value threshold for the ADF test on residuals.
        maxlag:      Maximum lags for the ADF test (see ``stats.adfuller``).

    Returns:
        ``{"cointegrated": bool, "hedge_ratio": float, "adf_stat": float,
           "adf_pvalue": float, "residuals": pd.Series}``
    """
    common = pd.concat([series_a, series_b], axis=1).dropna()
    if len(common) < 10:
        return {
            "cointegrated": False,
            "hedge_ratio": 0.0,
            "adf_stat": -999.0,
            "adf_pvalue": 1.0,
            "residuals": pd.Series(dtype=float),
        }

    a = common.iloc[:, 0]
    b = common.iloc[:, 1]

    slope, intercept, r_value, p_value, std_err = stats.linregress(b, a)
    hedge_ratio = slope
    residuals = a - (intercept + hedge_ratio * b)

    from statsmodels.tsa.stattools import adfuller
    adf_result = adfuller(residuals.dropna(), maxlag=maxlag, autolag="AIC")
    adf_stat = float(adf_result[0])
    adf_pvalue = float(adf_result[1])

    cointegrated = adf_pvalue < significance

    logger.debug(
        "EG cointegration test: hedge=%.4f  ADF=%.3f  p=%.4f  cointegrated=%s",
        hedge_ratio, adf_stat, adf_pvalue, cointegrated,
    )

    return {
        "cointegrated": cointegrated,
        "hedge_ratio": hedge_ratio,
        "adf_stat": adf_stat,
        "adf_pvalue": adf_pvalue,
        "residuals": residuals,
    }


# ------------------------------------------------------------------------- #
#  Pair Trading Strategy
# ------------------------------------------------------------------------- #

class PairTradingStrategy:
    """Pair Trading strategy based on Ornstein-Uhlenbeck process.

    Models the spread between two cointegrated indices as an OU process and
    generates trading signals based on Z-score deviations from the mean.

    This strategy does **not** inherit from ``Strategy`` (the base expects a
    single ``generate_signal(data)`` signature) because pair trading requires
    two input series.

    Parameters match the gate system in ``BacktestResult`` and PROJECT.md:
    Sharpe > 1.2, MaxDD < 12 %, WR > 52 %, Expectancy > 0.0.
    """

    def __init__(
        self,
        symbol_a: str,
        symbol_b: str,
        entry_threshold: float = 1.5,
        exit_threshold: float = 0.5,
        stop_threshold: float = 3.0,
        min_half_life: int = 5,
        max_half_life: int = 500,
    ) -> None:
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b
        self.config = PairTradingConfig(
            entry_threshold=entry_threshold,
            exit_threshold=exit_threshold,
            stop_threshold=stop_threshold,
            min_half_life=min_half_life,
            max_half_life=max_half_life,
        )

    def estimate_ou_params(self, spread_series: pd.Series) -> dict[str, float]:
        """Estimate Ornstein-Uhlenbeck parameters via MLE.

        Delegates to the module-level ``_estimate_ou_params_ols``.
        Returns ``{"kappa", "theta", "sigma", "half_life"}``.
        """
        return _estimate_ou_params_ols(spread_series)

    def compute_zscore(
        self,
        spread: float,
        window_mean: float,
        window_std: float,
    ) -> float:
        """Compute Z-score of a spread value.

        Z = (spread - rolling_mean) / rolling_std

        The caller is expected to supply the rolling statistics from the
        historical window; this avoids keeping stale state on the strategy.
        """
        if window_std == 0.0:
            return 0.0
        return (spread - window_mean) / window_std

    def generate_signal(
        self, data_a: pd.DataFrame, data_b: pd.DataFrame
    ) -> dict[str, Any]:
        """Generate a trading signal for the pair.

        Args:
            data_a: DataFrame for symbol A, must contain a ``'close'`` column.
            data_b: DataFrame for symbol B, must contain a ``'close'`` column.

        Returns:
            ``{"action": str, "zscore": float, "half_life": float,
               "confidence": float, "spread": float, "hedge_ratio": float}``
        """
        min_len_needed = self.config.lookback_window
        if len(data_a) < min_len_needed or len(data_b) < min_len_needed:
            return self._no_signal()

        close_a = data_a["close"].astype(float)
        close_b = data_b["close"].astype(float)

        # Recent window
        lookback = min(min_len_needed, len(data_a), len(data_b))
        recent_a = close_a.iloc[-lookback:]
        recent_b = close_b.iloc[-lookback:]

        if len(recent_a) < 2 or len(recent_b) < 2:
            return self._no_signal()

        # Hedge ratio: ratio of means (robust, avoids full OLS every tick)
        mean_a = float(recent_a.mean())
        mean_b = float(recent_b.mean())
        hedge_ratio = mean_a / mean_b if mean_b != 0 else 1.0

        spread_series = recent_a - hedge_ratio * recent_b
        mean_spread = float(spread_series.mean())
        std_spread = float(spread_series.std(ddof=1))
        current_spread = float(spread_series.iloc[-1])

        if std_spread == 0.0:
            return self._no_signal(spread=current_spread, hedge_ratio=hedge_ratio)

        zscore = (current_spread - mean_spread) / std_spread

        # Estimate OU parameters
        ou_params = self.estimate_ou_params(spread_series)
        half_life = ou_params["half_life"]

        # Gate: half-life must be in [min_half_life, max_half_life]
        if half_life < self.config.min_half_life or half_life > self.config.max_half_life:
            logger.debug(
                "half-life %.1f outside [%d, %d] — no trade",
                half_life, self.config.min_half_life, self.config.max_half_life,
            )
            return {
                "action": "NO_SIGNAL",
                "zscore": zscore,
                "half_life": half_life,
                "confidence": 0.0,
                "spread": current_spread,
                "hedge_ratio": hedge_ratio,
            }

        # --- Signal decision --------------------------------------------------
        action = "NO_SIGNAL"
        confidence = 0.0

        if abs(zscore) > self.config.stop_threshold:
            # Extreme — would close any open position
            confidence = min(1.0, abs(zscore) / (2.0 * self.config.stop_threshold))
        elif zscore < -self.config.entry_threshold:
            action = "LONG"
            # Confidence: how far beyond the threshold we are
            confidence = min(
                1.0,
                (abs(zscore) - self.config.entry_threshold) / self.config.entry_threshold,
            )
        elif zscore > self.config.entry_threshold:
            action = "SHORT"
            confidence = min(
                1.0,
                (zscore - self.config.entry_threshold) / self.config.entry_threshold,
            )
        elif abs(zscore) < self.config.exit_threshold:
            # Near mean — would trigger exit
            action = "EXIT"
            confidence = 1.0 - abs(zscore) / self.config.exit_threshold

        logger.debug(
            "[pairTrading %s/%s] spread=%.5f  z=%.3f  action=%s  conf=%.3f  hl=%.1f",
            self.symbol_a, self.symbol_b, current_spread,
            zscore, action, confidence, half_life,
        )

        return {
            "action": action,
            "zscore": zscore,
            "half_life": half_life,
            "confidence": confidence,
            "spread": current_spread,
            "hedge_ratio": hedge_ratio,
        }

    def backtest(
        self,
        data_a: pd.DataFrame,
        data_b: pd.DataFrame,
        initial_capital: float = 10000.0,
    ) -> dict[str, Any]:
        """Run a historical backtest of the pair-trading strategy.

        Uses closing prices only. A rolling window of ``lookback_window``
        candles provides the statistics for Z-score and OU estimation.

        Position sizing is simplified to 1% of initial capital per trade.

        Returns:
            ``{"trades": [...], "equity_curve": [...], "metrics": {...}}``
        """
        if len(data_a) < self.config.lookback_window or len(data_b) < self.config.lookback_window:
            return {
                "trades": [],
                "equity_curve": [initial_capital],
                "metrics": self._empty_metrics(),
            }

        close_a = data_a["close"].astype(float).values
        close_b = data_b["close"].astype(float).values
        n = min(len(close_a), len(close_b))

        # Global hedge ratio from the last lookback period
        lookback = self.config.lookback_window
        global_hr = float(np.mean(np.asarray(close_a[-lookback:], dtype=float))
                          / np.mean(np.asarray(close_b[-lookback:], dtype=float)))

        capital = initial_capital
        equity_curve = [capital]
        trades: list[dict[str, Any]] = []
        position = 0  # +1=long spread, -1=short spread, 0=flat
        entry_spread = 0.0
        entry_time: int | None = None

        start = lookback

        for i in range(start, n):
            win_a = close_a[i - lookback + 1 : i + 1]
            win_b = close_b[i - lookback + 1 : i + 1]

            # Hedge ratio from the window
            ma = float(np.mean(np.asarray(win_a, dtype=float)))
            mb = float(np.mean(np.asarray(win_b, dtype=float)))
            hr = ma / mb if mb != 0 else 1.0

            spreads = win_a - hr * win_b
            mu = float(np.mean(np.asarray(spreads, dtype=float)))
            sigma_val = float(np.std(np.asarray(spreads, dtype=float), ddof=1))
            current_spread = float(np.asarray(spreads, dtype=float)[-1])

            if sigma_val == 0.0:
                zscore = 0.0
            else:
                zscore = (current_spread - mu) / sigma_val

            ou = _estimate_ou_params_ols(pd.Series(spreads))
            half_life = ou["half_life"]

            # Skip if half-life is noise / non-mean-reverting
            if half_life < self.config.min_half_life or half_life > self.config.max_half_life:
                if position != 0:
                    # Force-close
                    trade = self._close_trade(
                        position, close_a[i], close_b[i], global_hr,
                        entry_spread, initial_capital, capital, i, entry_time,
                    )
                    capital += trade["pnl"]
                    trades.append(trade)
                    position = 0
                equity_curve.append(capital)
                continue

            # Signal
            action = self._signal_from_zscore(zscore)

            if position == 0 and action in ("LONG", "SHORT"):
                position = 1 if action == "LONG" else -1
                entry_spread = current_spread
                entry_time = i
            elif position != 0:
                should_close = False
                if action == "EXIT":
                    should_close = True
                elif abs(zscore) > self.config.stop_threshold:
                    should_close = True
                elif (position == 1 and action == "SHORT") or (position == -1 and action == "LONG"):
                    should_close = True  # signal reversal

                if should_close:
                    trade = self._close_trade(
                        position, close_a[i], close_b[i], global_hr,
                        entry_spread, initial_capital, capital, i, entry_time,
                    )
                    capital += trade["pnl"]
                    trades.append(trade)
                    position = 0

            equity_curve.append(capital)

        # End-of-data: close any open position
        if position != 0:
            trade = self._close_trade(
                position, close_a[-1], close_b[-1], global_hr,
                entry_spread, initial_capital, capital, n - 1, entry_time,
            )
            capital += trade["pnl"]
            trades.append(trade)

        # --- Metrics ----------------------------------------------------------
        metrics = self._backtest_metrics(trades, equity_curve, initial_capital)

        return {"trades": trades, "equity_curve": equity_curve, "metrics": metrics}

    # ------------------------------------------------------------------ #
    #  Backtest helpers
    # ------------------------------------------------------------------ #

    def _signal_from_zscore(self, zscore: float) -> str:
        if zscore < -self.config.entry_threshold:
            return "LONG"
        if zscore > self.config.entry_threshold:
            return "SHORT"
        if abs(zscore) < self.config.exit_threshold:
            return "EXIT"
        return "info"  # neither entry nor exit — do nothing

    @staticmethod
    def _close_trade(
        direction: int,
        close_a: float,
        close_b: float,
        hedge_ratio: float,
        entry_spread: float,
        initial_capital: float,
        capital: float,
        idx: int,
        entry_idx,  # type: ignore[no-untyped-def]
    ) -> dict[str, Any]:
        exit_spread = close_a - hedge_ratio * close_b
        raw = (exit_spread - entry_spread) if direction == 1 else (entry_spread - exit_spread)
        pnl = raw * initial_capital * 0.01  # 1% of initial per trade
        entry_time = entry_idx or idx
        return {
            "entry_time": entry_time,
            "exit_time": idx,
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry_spread": entry_spread,
            "exit_spread": exit_spread,
            "pnl": pnl,
            "pnl_pct": pnl / initial_capital,
            "duration": idx - entry_time,
            "win": pnl > 0,
            "equity": capital + pnl,
        }

    @staticmethod
    def _empty_metrics() -> dict[str, float]:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
        }

    @staticmethod
    def _backtest_metrics(
        trades: list[dict[str, Any]],
        equity: list[float],
        initial: float,
    ) -> dict[str, float]:
        n = len(trades)
        wins = sum(1 for t in trades if t["win"])
        win_rate = wins / n if n > 0 else 0.0
        total_pnl = sum(t["pnl"] for t in trades)

        peak = initial
        max_dd = 0.0
        for e in equity:
            if e > peak:
                peak = e
            dd = (peak - e) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        sharpe = 0.0
        if len(equity) > 1:
            rets = np.diff(equity) / np.array(equity[:-1])
            rets = rets[np.isfinite(rets)]
            if len(rets) > 1 and np.std(rets) > 0:
                sharpe = float(np.mean(rets) / np.std(rets) * math.sqrt(252))

        return {
            "total_trades": n,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
        }

    # ------------------------------------------------------------------ #
    #  Gate evaluation
    # ------------------------------------------------------------------ #

    def compute_gate_metrics(self, backtest_result: dict[str, Any]) -> dict[str, Any]:
        """Evaluate whether the backtest passes Pradx quality gates.

        Gates:
          - Sharpe > 1.2
          - MaxDD < 0.12
          - Win Rate > 52 %
          - Expectancy > 0.0

        Returns:
            ``{"sharpe": float, "max_dd": float, "win_rate": float,
               "expectancy": float, "gate_passed": bool, "gate_failures": [...]}``
        """
        metrics = backtest_result.get("metrics", {})
        sharpe = float(metrics.get("sharpe_ratio", 0.0))
        max_dd = float(metrics.get("max_drawdown", 0.0))
        wr = float(metrics.get("win_rate", 0.0))
        total_pnl = float(metrics.get("total_pnl", 0.0))
        total_trades = int(metrics.get("total_trades", 0))
        expectancy = total_pnl / total_trades if total_trades > 0 else 0.0

        gate_passed = True
        failures: list[str] = []

        if sharpe <= 1.2:
            gate_passed = False
            failures.append(f"sharpe={sharpe:.3f} ≤ 1.2")
        if max_dd >= 0.12:
            gate_passed = False
            failures.append(f"max_dd={max_dd:.3f} ≥ 0.12")
        if wr <= 0.52:
            gate_passed = False
            failures.append(f"win_rate={wr:.3f} ≤ 0.52")
        if expectancy <= 0.0:
            gate_passed = False
            failures.append(f"expectancy={expectancy:.3f} ≤ 0.0")

        return {
            "sharpe": sharpe,
            "max_dd": max_dd,
            "win_rate": wr,
            "expectancy": expectancy,
            "gate_passed": gate_passed,
            "gate_failures": failures,
        }

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _no_signal(
        self,
        spread: float = 0.0,
        hedge_ratio: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "action": "NO_SIGNAL",
            "zscore": 0.0,
            "half_life": float("inf"),
            "confidence": 0.0,
            "spread": spread,
            "hedge_ratio": hedge_ratio,
        }