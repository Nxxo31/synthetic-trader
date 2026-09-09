"""ConfigManager — Centralized configuration for the trading system.

Loads from:
- config/deriv.yaml (structure)
- .env (secrets)
- Environment variables (override)

Environments: BACKTEST | SANDBOX | LIVE
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Trading environment modes."""

    BACKTEST = "backtest"
    SANDBOX = "sandbox"  # Paper trading with real market data
    LIVE = "live"  # Real money


class DerivConfig(BaseSettings):
    """Deriv API configuration."""

    api_base_url: str = "https://api.derivws.com"
    ws_demo_path: str = "/trading/v1/options/ws/demo"
    ws_real_path: str = "/trading/v1/options/ws/real"
    ws_public_path: str = "/trading/v1/options/ws/public"
    otp_path: str = "/trading/v1/options/accounts"
    app_id: str = ""
    demo_account: str = ""
    real_account: str = ""
    is_demo: bool = True
    otp_lifetime: int = 120

    model_config = SettingsConfigDict(
        env_prefix="DERIV_",
        extra="ignore",
    )


class InstrumentConfig(BaseSettings):
    """Instrument configuration."""

    symbol: str
    display_name: str
    enabled: bool = False


class InstrumentsConfig(BaseSettings):
    """All instruments configuration."""

    range_break: list[InstrumentConfig] = Field(default_factory=list)
    volatility: list[InstrumentConfig] = Field(default_factory=list)
    step_index: list[InstrumentConfig] = Field(default_factory=list)
    jump: list[InstrumentConfig] = Field(default_factory=list)
    boom_crash: list[InstrumentConfig] = Field(default_factory=list)

    def get_enabled(self) -> list[InstrumentConfig]:
        """Get all enabled instruments."""
        all_instruments = []
        for category in [
            self.range_break,
            self.volatility,
            self.step_index,
            self.jump,
            self.boom_crash,
        ]:
            all_instruments.extend([i for i in category if i.enabled])
        return all_instruments

    def get_by_symbol(self, symbol: str) -> InstrumentConfig | None:
        """Find instrument by symbol."""
        for inst in self.get_enabled():
            if inst.symbol == symbol:
                return inst
        return None


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    max_risk_per_trade: Decimal = Field(default=Decimal("0.02"))
    max_daily_drawdown: Decimal = Field(default=Decimal("0.05"))
    max_trades_per_day: int = 10
    circuit_breaker_losses: int = 5
    kelly_fraction: Decimal = Field(default=Decimal("0.25"))
    initial_capital: Decimal = Field(default=Decimal("10000.0"))
    tradable_balance_ratio: Decimal = Field(default=Decimal("0.99"))
    max_open_positions: int = 5

    @field_validator(
        "max_risk_per_trade",
        "max_daily_drawdown",
        "kelly_fraction",
        "tradable_balance_ratio",
        mode="before",
    )
    @classmethod
    def parse_decimal(cls, v: Any) -> Decimal:
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))

    @field_validator("initial_capital", mode="before")
    @classmethod
    def parse_capital(cls, v: Any) -> Decimal:
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))


class StrategyConfig(BaseSettings):
    """Strategy-specific configuration."""

    range_break: dict[str, Any] = Field(default_factory=dict)
    step_index: dict[str, Any] = Field(default_factory=dict)
    volatility: dict[str, Any] = Field(default_factory=dict)
    confluence: dict[str, Any] = Field(default_factory=dict)
    drift_boom_crash: dict[str, Any] = Field(default_factory=dict)


class BacktestConfig(BaseSettings):
    """Backtest configuration."""

    granularity: int = 60
    lookback_days: int = 180
    train_ratio: float = 0.67
    mc_simulations: int = 10000
    gates: dict[str, float] = Field(default_factory=dict)


class PaperConfig(BaseSettings):
    """Paper trading configuration."""

    min_days: int = 15
    min_trades: int = 30
    gates: dict[str, Any] = Field(default_factory=dict)


class StorageConfig(BaseSettings):
    """Storage paths."""

    ticks_dir: str = "data/ticks"
    candles_dir: str = "data/candles"
    reports_dir: str = "reports"


class ConfigManager:
    """Centralized configuration manager.

    Loads configuration from:
    1. config/deriv.yaml (base structure)
    2. .env file (secrets, environment-specific)
    3. Environment variables (highest priority)

    Supports three environments:
    - BACKTEST: Deterministic simulation, TestClock
    - SANDBOX: Paper trading with real market data
    - LIVE: Real money trading (requires explicit approval)
    """

    _instance: ConfigManager | None = None

    def __new__(cls) -> ConfigManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._project_root = self._find_project_root()
        self._config_path = self._project_root / "config" / "deriv.yaml"
        self._env = Environment(os.getenv("TRADING_ENV", "backtest"))
        self._load_config()

    def _find_project_root(self) -> Path:
        """Find project root by looking for config/deriv.yaml."""
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "config" / "deriv.yaml").exists():
                return parent
        # Fallback to cwd
        return Path.cwd()

    def _load_config(self) -> None:
        """Load configuration from YAML and environment."""
        # Load YAML config
        yaml_config = {}
        if self._config_path.exists():
            with open(self._config_path) as f:
                yaml_config = yaml.safe_load(f) or {}

        # Load .env file manually if exists
        self._load_dotenv()

        # Build configurations
        self.deriv = DerivConfig(
            **yaml_config.get("deriv", {}),
            _env_file=self._project_root / ".env",
            _env_file_encoding="utf-8",
        )

        self.instruments = self._parse_instruments(yaml_config.get("instruments", {}))
        self.risk = RiskConfig(**yaml_config.get("risk", {}))
        self.strategy = StrategyConfig(**yaml_config.get("strategy", {}))
        self.backtest = BacktestConfig(**yaml_config.get("backtest", {}))
        self.paper = PaperConfig(**yaml_config.get("paper", {}))
        self.storage = StorageConfig(**yaml_config.get("storage", {}))

        # Override with environment-specific settings
        self._apply_env_overrides()

    def _load_dotenv(self) -> None:
        """Load .env file into os.environ."""
        env_path = self._project_root / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        # Don't override already set env vars
                        if key not in os.environ:
                            os.environ[key] = value

    def _parse_instruments(self, data: dict) -> InstrumentsConfig:
        """Parse instruments from YAML."""
        return InstrumentsConfig(
            range_break=[
                InstrumentConfig(**i) for i in data.get("range_break", [])
            ],
            volatility=[
                InstrumentConfig(**i) for i in data.get("volatility", [])
            ],
            step_index=[
                InstrumentConfig(**i) for i in data.get("step_index", [])
            ],
            jump=[InstrumentConfig(**i) for i in data.get("jump", [])],
            boom_crash=[
                InstrumentConfig(**i) for i in data.get("boom_crash", [])
            ],
        )

    def _apply_env_overrides(self) -> None:
        """Apply environment-specific overrides."""
        if self._env == Environment.BACKTEST:
            self.risk.initial_capital = Decimal("10000.0")
            self.deriv.is_demo = True
        elif self._env == Environment.SANDBOX:
            self.risk.initial_capital = Decimal("10000.0")
            self.deriv.is_demo = True
        elif self._env == Environment.LIVE:
            # LIVE requires explicit env var
            if os.getenv("ALLOW_LIVE") != "true":
                raise RuntimeError(
                    "LIVE trading requires ALLOW_LIVE=true environment variable"
                )

    @property
    def environment(self) -> Environment:
        """Get current trading environment."""
        return self._env

    @environment.setter
    def environment(self, value: Environment | str) -> None:
        if isinstance(value, str):
            value = Environment(value.lower())
        self._env = value
        self._apply_env_overrides()

    def get_risk_config(self) -> RiskConfig:
        """Get risk configuration for RiskEngine."""
        return self.risk

    def get_enabled_symbols(self) -> list[str]:
        """Get list of enabled trading symbols."""
        return [i.symbol for i in self.instruments.get_enabled()]

    def get_instrument(self, symbol: str) -> InstrumentConfig | None:
        """Get instrument config by symbol."""
        return self.instruments.get_by_symbol(symbol)

    def get_strategy_config(self, strategy_name: str) -> dict:
        """Get strategy-specific configuration."""
        return getattr(self.strategy, strategy_name, {})

    def get_storage_paths(self) -> StorageConfig:
        """Get storage paths."""
        return self.storage

    def resolve_storage_path(self, path: str) -> Path:
        """Resolve storage path relative to project root."""
        return self._project_root / path

    def to_dict(self) -> dict:
        """Export configuration as dictionary (without secrets)."""
        return {
            "environment": self._env.value,
            "deriv": {
                "api_base_url": self.deriv.api_base_url,
                "is_demo": self.deriv.is_demo,
                "app_id": "***REDACTED***" if self.deriv.app_id else "",
            },
            "instruments": {
                cat: [i.model_dump() for i in getattr(self.instruments, cat)]
                for cat in [
                    "range_break",
                    "volatility",
                    "step_index",
                    "jump",
                    "boom_crash",
                ]
            },
            "risk": {
                "max_risk_per_trade": float(self.risk.max_risk_per_trade),
                "max_daily_drawdown": float(self.risk.max_daily_drawdown),
                "max_trades_per_day": self.risk.max_trades_per_day,
                "circuit_breaker_losses": self.risk.circuit_breaker_losses,
                "kelly_fraction": float(self.risk.kelly_fraction),
                "initial_capital": float(self.risk.initial_capital),
            },
            "strategy": self.strategy.model_dump(),
            "backtest": self.backtest.model_dump(),
            "paper": self.paper.model_dump(),
        }


# Global instance getter
def get_config() -> ConfigManager:
    """Get the global ConfigManager instance."""
    return ConfigManager()


# Convenience functions
def get_risk_config() -> RiskConfig:
    """Get risk configuration."""
    return get_config().get_risk_config()


def get_enabled_symbols() -> list[str]:
    """Get enabled symbols."""
    return get_config().get_enabled_symbols()


def is_live() -> bool:
    """Check if running in LIVE mode."""
    return get_config().environment == Environment.LIVE


def is_backtest() -> bool:
    """Check if running in BACKTEST mode."""
    return get_config().environment == Environment.BACKTEST


def is_sandbox() -> bool:
    """Check if running in SANDBOX mode."""
    return get_config().environment == Environment.SANDBOX


if __name__ == "__main__":
    # Test configuration loading
    config = get_config()
    print(f"Environment: {config.environment.value}")
    print(f"Project root: {config._project_root}")
    print(f"Enabled symbols: {config.get_enabled_symbols()}")
    print(f"Risk config: {config.risk}")
    print(f"Deriv is_demo: {config.deriv.is_demo}")
    print(f"Storage paths: {config.storage}")
    import json
    print(json.dumps(config.to_dict(), indent=2))
