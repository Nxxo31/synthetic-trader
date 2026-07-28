<div align="center">

# 📈 Synthetic Trader

### Algorithmic trading bot for synthetic indices on Deriv

Pipeline: strategy design → backtest → paper trading → live trading

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg)](https://www.python.org/)
[![Deriv API](https://img.shields.io/badge/Deriv-API-00B2A9.svg)](https://api.deriv.com/)

</div>

---

## 📦 Features

- **Synthetic indices** — Volatility, Boom/Crash, Step, Range Break
- **4-phase pipeline** — Strategy → Backtest → Paper → Live
- **Risk management** — 2% per trade, 5% daily loss limit, 10 trades max
- **Deriv WebSocket API** — Real-time ticks and order execution
- **Backtest engine** — Historical tick replay with Parquet storage
- **Dashboard** — Next.js real-time monitoring UI

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

## 🏗️ Architecture

```
src/
  api/          → Deriv WebSocket client
  backtest/     → Historical replay engine
  connection/   → Deriv client connection
  data/         → Tick/candle collectors
  execution/    → Order execution
  monitor/      → Real-time monitoring
  risk/         → Risk manager (2% per trade, 5% daily)
  strategy/     → Base + Range Break strategy
dashboard/      → Next.js monitoring UI
config/         → Deriv connection config
```

## ⚠️ Risk Disclaimer

Trading synthetic indices carries significant risk. This software is for
educational purposes only. Paper trading is the default. Live trading
requires explicit confirmation and understanding of the risks.

## 📄 License

MIT — See [LICENSE](LICENSE)
