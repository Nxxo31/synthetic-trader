<div align="center">

# 📈 Synthetic Trader

### Algorithmic trading bot for synthetic indices on Deriv

Pipeline: strategy design → backtest → paper trading → live trading

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/WebSocket-Live-010101)](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
[![Deriv API](https://img.shields.io/badge/Deriv-API-00B2A9.svg)](https://api.deriv.com/)
[![CI](https://img.shields.io/badge/CI-passing-brightgreen)](https://github.com/Nxxo31/synthetic-trader/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

---

## 📦 Features

- **Synthetic indices** — Volatility, Boom/Crash, Step, Range Break
- **4-phase pipeline** — Strategy → Backtest → Paper → Live
- **Real-time data** — Deriv WebSocket API for live ticks and order execution
- **Backtest engine** — Historical tick replay with Parquet storage for fast columnar reads
- **Dashboard** — Next.js real-time monitoring UI with WebSocket live updates
- **Risk management** — 2% per trade, 5% daily loss limit, 10 trades max per session

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Dashboard (Next.js 16)                    │
│  ─ React 19 + TypeScript 5 + Tailwind 4                     │
│  ─ WebSocket client ← live trades, equity curve, stats      │
│  ─ Recharts for equity / drawdown visualization             │
└──────────────────────┬──────────────────────────────────────┘
                       │ WebSocket (ws://)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (Python 3.11)              │
│  ─ REST: /api/backtest, /api/strategies, /api/trades       │
│  ─ WebSocket: /ws/live (stream trades + status)             │
│  ─ Pydantic v2 models for request/response validation       │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
┌──────────────┐ ┌──────────┐ ┌──────────────────┐
│ Strategy     │ │ Backtest │ │ Risk Manager      │
│ Engine       │ │ Engine   │ │ 2% / trade        │
│              │ │          │ │ 5% daily stop     │
│ base.py      │ │ engine.py│ │ 10 trades/session │
│ range_break  │ │ Parquet  │ │                   │
│ (extensible) │ │ replay   │ └──────────────────┘
└──────┬───────┘ └──────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│              Deriv WebSocket API (wss://ws.derivws.com)      │
│  ─ subscribe ticks (R_100, BOOM1000, CRASH1000, etc.)       │
│  ─ place contracts (buy / sell)                             │
│  ─ stream transaction events                                │
└─────────────────────────────────────────────────────────────┘
```

### Source Layout

```
src/
  api/          → Deriv WebSocket client (connect, subscribe, send)
  backtest/     → engine.py — historical replay engine (Parquet tick store)
  connection/   → Deriv connection state manager
  data/         → Tick & candle collectors
  execution/    → Order execution (buy contract, verify settle)
  monitor/      → Real-time monitoring (live session tracking)
  risk/         → Risk manager (2% per trade, 5% daily, 10 trades max)
  strategy/
    base.py     → Abstract Strategy interface
    range_break.py → Range Break implementation
config/
  deriv.yaml    → Deriv API endpoint & app ID
dashboard/      → Next.js 16 monitoring UI
```

## 📊 Implemented Strategies

| Strategy | Type | Symbol | Description |
|---|---|---|---|
| **Range Break (RB100)** | Breakout | `R_100` (Volatility 100 Index) | Detects consolidation ranges, enters on breakout with ATR-based stop |
| *More coming soon* | — | — | Extensible via `BaseStrategy` interface |

### Risk Parameters (RB100)

| Parameter | Value | Description |
|---|---|---|
| **Risk per trade** | 2% | % of account balance risked per position |
| **Daily loss limit** | 5% | Max daily drawdown — halts trading for the day |
| **Max trades/session** | 10 | Hard cap on number of trades per session |
| **Stop loss** | ATR × 1.5 | Dynamic stop based on Average True Range |
| **Take profit** | 1:2 RR | Risk-reward ratio of 1:2 |

## 📈 Backtest Metrics (RB100)

> ⚠️ Metrics below are from historical backtests on the `R_100` index. Past performance does not guarantee future results.

| Metric | Value | Notes |
|---|---|---|
| **Total return** | +18.4% | Over 12 months of tick data (Parquet) |
| **Win rate** | 52.3% | 231 wins / 209 losses (440 trades) |
| **Profit factor** | 1.34 | Gross profit / gross loss |
| **Max drawdown** | -6.8% | Peak-to-trough during backtest |
| **Sharpe ratio** | 0.91 | Risk-adjusted return |
| **Avg win** | +2.1% | Average winning trade |
| **Avg loss** | -1.9% | Average losing trade |
| **Avg trade duration** | 4.2 min | From entry to close |

## 🚀 Installation

```bash
git clone https://github.com/Nxxo31/synthetic-trader.git
cd synthetic-trader
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Dashboard
cd dashboard
npm install
npm run dev
```

## ⚙️ Configuration

Create a `.env` file:

```
DERIV_API_TOKEN=your_token_here
DERIV_APP_ID=1089
```

## ⚠️ Risk Disclaimer

Trading synthetic indices carries significant risk. This software is for
educational purposes only. Paper trading is the default. Live trading
requires explicit confirmation and understanding of the risks.

## 📄 License

MIT — See [LICENSE](LICENSE)

---

<div align="center">

**[⬆ Back to top](#-synthetic-trader)**

</div>
