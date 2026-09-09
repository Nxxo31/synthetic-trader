"""Kelly Criterion position sizing module — quarter-Kelly with confidence and volatility adjustments.

Implements the position sizing formula from architectural synthesis findings:
- D17: Quarter-Kelly (fraction=0.25) with confidence × volatility adjustments
- D4: Confidence-gated signal scoring integration
- D16: Regime-aware risk adjustments

Formula:
  raw_kelly = (p * win_amount - (1-p) * loss_amount) / win_amount
  fractional_kelly = raw_kelly * 0.25          # quarter-Kelly
  confidence_adjusted = fractional_kelly * confidence
  final_size = confidence_adjusted / vol_multiplier  # volatility reduction

All sizes capped at max_position_pct of available capital.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VolatilityRegime(Enum):
    """Volatility regime states for regime-aware risk adjustments."""

    CALM = "calm"
    NORMAL = "normal"
    STRESS = "stress"
    PANIC = "panic"


@dataclass
class KellyResult:
    """Result from Kelly fraction calculation."""

    raw_kelly: float          # Pure Kelly fraction (unadjusted)
    fractional_kelly: float  # Quarter-Kelly fraction (0.25 × raw)
    confidence_adjusted: float  # After confidence multiplication
    final_fraction: float     # After volatility division, ready for capital multiplication
    edge: float              # Expected edge (p * w - q * l)
    win_probability: float   # Input win probability
    confidence: float        # Input confidence score (0-1)
    volatility_multiplier: float  # Input volatility multiplier


def kelly_fraction(
    win_probability: float,
    win_amount: float,
    loss_amount: float,
    confidence: float = 1.0,
    volatility_multiplier: float = 1.0,
    regime: VolatilityRegime | None = None,
) -> KellyResult:
    """Calculate quarter-Kelly position fraction with confidence and volatility adjustments.

    Core formula from architectural synthesis finding #17:
      raw_kelly = (p × w - q × l) / w   where q = 1-p
      fractional_kelly = raw_kelly × 0.25   # quarter-Kelly risk mitigation
      confidence_adjusted = fractional_kelly × confidence
      final_fraction = confidence_adjusted / vol_multiplier  # vol reduction

    The result's final_fraction is multiplied by available capital to get the
    position size in USD.

    Args:
        win_probability: Probability of win (p), range [0, 1]
        win_amount: Win amount in units of risk (e.g., 1.5 for 1:1.5 RR)
        loss_amount: Loss amount in units of risk (always 1.0 for symmetric bets)
        confidence: Signal confidence score (0-1), defaults to 1.0
        volatility_multiplier: Volatility reduction factor (>=1.0), defaults to 1.0
            Higher values reduce position size. Regime-based values:
            CALM: 1.0, NORMAL: 1.5, STRESS: 2.0, PANIC: N/A (no position)
        regime: Volatility regime for per-regime volatility multiplier.

    Returns:
        KellyResult with all intermediate calculations and final fraction.

    Raises:
        ValueError: If win_probability outside [0,1], or win_amount/loss_amount <= 0.
    """
    # Validate inputs
    if not 0.0 <= win_probability <= 1.0:
        raise ValueError(f"win_probability={win_probability} must be in [0, 1]")

    if win_amount <= 0 or loss_amount <= 0:
        raise ValueError("win_amount and loss_amount must be > 0")

    p = win_probability
    q = 1.0 - p

    # --- Step 1: Calculate expected edge ---
    # edge = p * win_amount - q * loss_amount
    edge = p * win_amount - q * loss_amount

    # --- Step 2: Raw Kelly fraction ---
    # f = edge / win_amount for a single-period bet
    # But we use the more general: f = edge / win_amount when loss_amount = win_amount / RR
    # Actually the standard Kelly for p win, q loss, b = win_amount/loss_amount:
    # f = (p*b - q) / b  where b = win_amount / loss_amount
    # Since we have win_amount and loss_amount in R-multiples:
    b = win_amount / loss_amount  # payoff ratio
    raw_kelly = (p * b - q) / b  # = p - q / b = p - (1-p)/b

    # Alternative: raw_kelly = edge / win_amount
    # edge = p * win_amount - q * loss_amount
    # raw_kelly = (p * win_amount - q * loss_amount) / win_amount
    # = p - q * (loss_amount / win_amount)
    # = p - (1-p) / b   where b = win_amount / loss_amount
    # These are equivalent. We'll use the edge formulation.

    # --- Step 3: Apply quarter-Kelly fraction ---
    # Quarter-Kelly: use 25% of full Kelly to reduce volatility risk
    fractional_kelly = raw_kelly * 0.25

    # --- Step 4: Apply confidence adjustment ---
    # Confidence gating: scale position by signal confidence
    confidence_adjusted = fractional_kelly * confidence

    # --- Step 5: Apply volatility multiplier ---
    # Volatility reduction: divide by vol_multiplier (>=1.0 reduces size)
    # Regime-based defaults if no explicit multiplier provided
    if volatility_multiplier <= 0:
        raise ValueError("volatility_multiplier must be > 0")

    # Apply regime-based volatility multiplier if specified
    if regime is not None:
        regime_multipliers = {
            VolatilityRegime.CALM: 1.0,
            VolatilityRegime.NORMAL: 1.5,
            VolatilityRegime.STRESS: 2.0,
            # PANIC: no position (handled at higher level)
        }
        # Use regime default if no explicit multiplier given
        if volatility_multiplier == 1.0:  # default, apply regime
            volatility_multiplier = regime_multipliers.get(regime, 1.0)

    final_fraction = confidence_adjusted / volatility_multiplier

    # Clamp to [0, 1] — never short or leveraged beyond fraction 1.0
    final_fraction = max(0.0, min(1.0, final_fraction))

    return KellyResult(
        raw_kelly=round(raw_kelly, 6),
        fractional_kelly=round(fractional_kelly, 4),
        confidence_adjusted=round(confidence_adjusted, 4),
        final_fraction=round(final_fraction, 4),
        edge=round(edge, 6),
        win_probability=p,
        confidence=confidence,
        volatility_multiplier=volatility_multiplier,
    )


def quarter_kelly_fraction(
    win_probability: float,
    confidence: float = 1.0,
    volatility_multiplier: float = 1.0,
    regime: VolatilityRegime | None = None,
) -> KellyResult:
    """Convenience: quarter-Kelly with default win/loss amounts (symmetric bet, RR=1:1).

    For symmetric bets where win_amount = loss_amount = 1.0 (b = 1.0):
      raw_kelly = p - q = p - (1-p) = 2p - 1
      fractional_kelly = (2p - 1) × 0.25

    This is the special case used when only win probability is known and
    the payoff ratio is 1:1.

    Args:
        win_probability: Probability of win (p), range [0, 1]
        confidence: Signal confidence score (0-1), defaults to 1.0
        volatility_multiplier: Volatility reduction factor (>=1.0), defaults to 1.0
        regime: Volatility regime for per-regime volatility multiplier.

    Returns:
        KellyResult with final fraction ready for capital multiplication.
    """
    # Symmetric bet: win_amount = loss_amount = 1.0, so b = 1.0
    # raw_kelly = p - q = 2p - 1
    # fractional_kelly = raw_kelly * 0.25 = (2p - 1) * 0.25

    # But we should use the full kelly_fraction for consistency and extendability
    # Use edge = p*1 - q*1 = p - q = 2p - 1, win_amount = 1.0
    return kelly_fraction(
        win_probability=win_probability,
        win_amount=1.0,
        loss_amount=1.0,
        confidence=confidence,
        volatility_multiplier=volatility_multiplier,
        regime=regime,
    )


def regime_volatility_multiplier(regime: VolatilityRegime) -> float:
    """Return the volatility multiplier for a given regime.

    Per architectural synthesis finding #18 (PROBABLE) and D3 (CONFIRMED):
    - CALM: RR 1:1 → vol_multiplier = 1.0 (full size, mean reversion)
    - NORMAL: RR 1:1.5 → vol_multiplier = 1.5 (reduce size, follow trend)
    - STRESS: RR 1:2 → vol_multiplier = 2.0 (much reduce, wider targets)
    - PANIC: NO_SIGNAL → handled at strategy level, no position

    Returns:
        Volatility multiplier >= 1.0 (always reduce or maintain size, never increase).
    """
    multipliers = {
        VolatilityRegime.CALM: 1.0,
        VolatilityRegime.NORMAL: 1.5,
        VolatilityRegime.STRESS: 2.0,
        VolatilityRegime.PANIC: float("inf"),  # No position — caller should check
    }
    return multipliers[regime]


def calculate_position_size(
    capital: float,
    win_probability: float,
    win_amount: float,
    loss_amount: float,
    confidence: float = 1.0,
    volatility_multiplier: float = 1.0,
    regime: VolatilityRegime | None = None,
    max_position_pct: float = 0.02,
) -> dict:
    """Calculate final position size in USD with all adjustments.

    End-to-end calculation integrating:
    1. Kelly fraction calculation (quarter-Kelly, confidence, volatility)
    2. Max position percentage cap (hard cap from config)
    3. Returns dict with breakdown for transparency

    Args:
        capital: Available capital (typically daily surplus)
        win_probability: Win probability p (0-1)
        win_amount: Win amount in R-multiples
        loss_amount: Loss amount in R-multiples
        confidence: Signal confidence (0-1)
        volatility_multiplier: Volatility reduction factor (>=1)
        regime: Volatility regime for regime-aware adjustments
        max_position_pct: Maximum % of capital per trade (default 2% from config)

    Returns:
        dict with: size_usd, pct_capital, kelly_fraction, edge, confidence_applied
    """
    # Get Kelly result with all adjustments
    kelly_result = kelly_fraction(
        win_probability=win_probability,
        win_amount=win_amount,
        loss_amount=loss_amount,
        confidence=confidence,
        volatility_multiplier=volatility_multiplier,
        regime=regime,
    )

    # Apply max position percentage cap
    max_size_usd = round(capital * max_position_pct, 2)
    size_usd = min(kelly_result.final_fraction * capital, max_size_usd)
    # Recalculate pct_capital based on actual size
    pct_capital = round(size_usd / capital * 100, 2) if capital > 0 else 0.0

    # If capped, log the adjustment
    if size_usd < kelly_result.final_fraction * capital:
        logger_debug = __import__("logging").getLogger(__name__).debug
        logger_debug(
            "Position size capped: kelly=%.2f USD, capped=%.2f USD (max %.2f%%)",
            kelly_result.final_fraction * capital,
            size_usd,
            max_position_pct * 100,
        )

    return {
        "size_usd": size_usd,
        "pct_capital": pct_capital,
        "kelly_fraction": kelly_result.final_fraction,
        "raw_kelly": kelly_result.raw_kelly,
        "edge": kelly_result.edge,
        "win_probability": kelly_result.win_probability,
        "confidence": kelly_result.confidence,
        "volatility_multiplier": kelly_result.volatility_multiplier,
        "regime": regime.value if regime else None,
        "regime_vol_multiplier": regime_volatility_multiplier(regime) if regime else 1.0,
    }


# For backward compatibility with RiskManager.position_size_dynamic signature
def legacy_position_size_dynamic(
    capital: float,
    win_probability: float,
    win_amount: float,
    loss_amount: float,
    confidence: float = 1.0,
    volatility_multiplier: float = 1.0,
    config: object | None = None,
) -> dict:
    """Legacy wrapper compatible with RiskManager.position_size_dynamic.

    Integrates the new Kelly criterion module into the existing RiskManager API.
    This allows gradual migration without breaking existing callers.

    Args:
        capital: Available capital
        win_probability: Win probability (p)
        win_amount: Win amount in risk units
        loss_amount: Loss amount in risk units
        confidence: Signal confidence (0-1)
        volatility_multiplier: Volatility factor (>=1.0)
        config: Optional RiskConfig for max_position_pct cap

    Returns:
        dict with position_size_result keys compatible with PositionSizeResult
    """
    # Use max_position_pct from config if provided, else default 2%
    max_pct = 0.02
    if config is not None:
        # Try to get max_position_pct from config
        try:
            if hasattr(config, "max_position_pct"):
                max_pct = config.max_position_pct
            elif hasattr(config, "validate"):
                config.validate()
        except Exception:
            pass  # Use default if config reading fails

    result = calculate_position_size(
        capital=capital,
        win_probability=win_probability,
        win_amount=win_amount,
        loss_amount=loss_amount,
        confidence=confidence,
        volatility_multiplier=volatility_multiplier,
        regime=None,  # Legacy mode: no regime awareness by default
        max_position_pct=max_pct,
    )

    # Return compatible format
    return {
        "size_usd": result["size_usd"],
        "pct_capital": result["pct_capital"],
        "kelly_fraction": result["kelly_fraction"],
        "edge": result["edge"],
    }


# Module-level logger
import logging

logger = logging.getLogger(__name__)

# Example usage (commented out for module import safety):
# if __name__ == "__main__":
#     result = kelly_fraction(0.62, win_amount=1.5, loss_amount=1.0, confidence=0.8)
#     print(f"Kelly result: {result}")
#     print(f"Position size for $10000 capital: ${result.final_fraction * 10000:.2f}")
