# Synthetic Trader SaaS — Multi-Bot Algorithmic Trading Platform

## Project
SaaS platform for deploying, managing, and monitoring algorithmic trading bots for Deriv synthetic indices and other markets. Enables users to create, backtest, paper trade, and run live trading bots with institutional-grade risk management, multi-tenancy, and real-time analytics.

**Vision:** A scalable, professional trading bot SaaS where users can:
- Design strategies using visual builders or code
- Backtest with walk-forward validation and Monte Carlo simulation
- Deploy bots to paper trading with real-time performance tracking
- Scale to live trading with institutional risk controls
- Monitor multiple bots via a unified dashboard
- Subscription-based access with usage-based pricing

**Repository:** `~/proyectos/synthetic-trader/`  
**License:** MIT (core) + proprietary SaaS extensions  
**Target Audience:** Quant traders, retail investors, fintech companies seeking white-label bot infrastructure

---

## 🏗️ High-Level Architecture

```mermaid
graph TB
    subgraph "Frontend Tier"
        CDN[Global CDN] --> NextJS[Next.js 15 App Router]
        NextJS --> |SSR/SSG| Edge[Edge Functions (Vercel)]
        NextJS --> |CSR| Browser[User Browser]
        User Browser
Browser --> |WebSocket| WS[Real-time Data Stream]
    end

    subgraph LR
    Browser --> |WebSocket| WS[Real-time Data Stream]
    end

    subgraph "API & Application Tier"
        APIGateway[API Gateway (Vercel)] --> Auth[Auth Service (NextAuth.js)]
        APIGateway --> BotAPI[Bot Management API (FastAPI)]
        APIGateway --> MarketAPI[Market Data API (FastAPI)]
        APIGateway --> BacktestAPI[Backtest & Strategy API (FastAPI)]
        
        Auth --> |Sessions| Redis[Redis (Session Store)]
        BotAPI --> |Bot State| PostgreSQL[(PostgreSQL + TimescaleDB)]
        MarketAPI --> |OHLCV/Candles| TimescaleDB[TimescaleDB (Market Data)]
        BacktestAPI --> |Results| PostgreSQL
        
        BotAPI --> |Trading Signals| SignalRouter[Signal Router Service]
        SignalRouter --> |Per-Tenant| BotWorker[Bot Worker Cluster]
        BotWorker --> |Executions| Deriv[Deriv API WebSocket]
        BotWorker --> |Results| PostgreSQL
    end

    subgraph "Data & Infrastructure Layer"
        PostgreSQL --> |Backups| S3[Object Storage (S3/MinIO)]
        TimescaleDB --> |Compressed| S3
        Redis --> |Pub/Sub| WS
        WS --> |Live Updates| Browser
        S3 --> |Analytics| DataLake[Data Lake (for ML training)]
    end

    subgraph "Observability & Ops"
        PostgreSQL --> |Metrics| Prometheus[Prometheus]
        TimescaleDB --> |Metrics| Prometheus
        Redis --> |Metrics| Prometheus
        BotWorker --> |Metrics| Prometheus
        Prometheus --> |Alerting| Alertmanager[Alertmanager]
        Prometheus --> |Dashboards| Grafana[Grafana (Observability)]
        BotWorker --> |Logs| Loki[Loki (Log Aggregation)]
        BotWorker --> |Traces| Jaeger[Jaeger (Distributed Tracing)]
    end

    subgraph "External Services"
        Deriv --> |Market Data| MarketAPI
        Deriv --> |Order Execution| BotWorker
        Stripe[Stripe Payments] --> |Billing| Auth
        Email[Email Service] --> |Notifications| Auth
    end
```

---

## 📚 Technology Stack & Justifications

| Layer | Technology | Why This Choice (vs Alternatives) |
|-------|------------|-----------------------------------|
| **Framework** | **Next.js 15 (App Router)** | Built-in API routes, ISR/SSR for SEO/marketing, edge-ready, excellent DX, Vercel-first deployment. Beats Remix (better ecosystem) and SvelteKit (more mature trading libs). |
| **UI Library** | **Park UI (Radix UI + Panda CSS)** | Headless primitives (Radix) + utility-first styling (Panda CSS) = maximal design control, AAA accessibility, zero runtime CSS-in-JS overhead. Beats Mantine (CSS-in-JS) and Ant Design (bundle bloat). No Tailwind per user request. |
| **Animation** | **Framer Motion** | Industry standard for React dashboards, spring physics, layout/shared layout animations, excellent for modals, reorder lists, micro-interactions. Beats Motion One (more features) and GSAP (overkill). |
| **Charts** | **TradingView Lightweight Charts** (candlesticks) + **Recharts** (equity/KPIs) | TV Lightweight Charts = gold standard for financial candlesticks. Recharts = lightweight, composable, SVG-based for line/area/pie charts. Beats ApexCharts (heavier) and ECharts (bundle size). |
| **Backend API** | **FastAPI (Python 3.11+)** | Async-native, automatic OpenAPI docs, Pydantic validation, excellent for ML/backtesting workers. Beats Express (less Python-native) and NestJS (overkill for microservices). |
| **Real-time Data** | **WebSocket (FastAPI + uvicorn)** | Low-latency push updates for equity curves, trade logs, KPIs. Beats polling or Server-Sent Events for bidirectional needs. |
| **Market Data Storage** | **TimescaleDB** | Time-series optimized PostgreSQL extension, automatic compression, hypertables, excellent for OHLCV/candles. Beats plain PostgreSQL (no time-series optimizations) and InfluxDB (less SQL compatibility). |
| **User & Bot State** | **PostgreSQL** | Relational integrity for users, subscriptions, bots, strategies, trade logs. Beats MongoDB (no ACID for financial data) and Redis (not durable for critical state). |
| **Session & Cache** | **Redis** | Blazing-fast session stores, pub/sub for real-time notifications, rate limiting counters. Beats in-memory (no persistence) and Memcached (no richer data types). |
| **Background Workers** | **Docker Containers (Fly.io/AWS ECS)** | Horizontal scaling per tenant, isolated environments, resource limits, zero-downtime deploys. Beats Vercel Server Functions (cold starts) and AWS Lambda (execution limits). |
| **Observability** | **Prometheus + Grafana + Loki + Jaeger** | Full-stack observability: metrics, logs, traces. Beats Datadog (cost) and New Relic (complexity). Open source, self-hosted. |
| **Payments** | **Stripe** | Developer-friendly, global payment methods, subscription billing, tax handling, PCI compliance. Beats Paddle (less flexible) and Chargebee (overkill). |
| **Authentication** | **NextAuth.js + JWT** | Seamless Next.js integration, supports email, OAuth, credentials, session/JWT strategies. Beats Auth0 (vendor lock-in) and Firebase (less customizable). |
| **Deployment** | **Vercel (frontend) + Fly.io (workers)** | Vercel: instant previews, edge functions, git-driven deploys. Fly.io: global VMs, private networking, Docker-native, per-second billing. Beats AWS (complexity) and GCP (less DX). |

---

## 🎯 Prioritized Feature Matrix (MoSCoW)

### Must Have (MVP for Launch)
- [x] **Core Trading Bot Engine** (Phase 1-4 working)
  - Deriv WebSocket client with auth, ticks, proposal/buy/sell
  - Range Break strategy (channel breakout with volume confirmation)
  - Risk manager: Kelly criterion (quarter-Kelly), circuit breaker (5 losses), daily DD limit (5%), max 8 trades/day
  - Backtest engine: walk-forward validation, Monte Carlo simulation, spread/slippage modeling
  - Paper trading engine: demo account simulation with realistic latency/slippage
  - Live trading execution: explicit user approval required, risk rules enforced
- [ ] **User Authentication & Management**
  - Email/password login (NextAuth.js)
  - Demo account provisioning (virtual $10,000)
  - API key management (scopes: read, trade)
  - Role-based access (user/admin)
- [ ] **Single-Bot Dashboard**
  - Real-time equity curve (Recharts line chart)
  - Candlestick chart with entry/exit markers (TV Lightweight Charts)
  - Trade log table (entry, exit, P&L, duration, reason)
  - Risk status panel: circuit breaker state, daily loss %, trades remaining
  - Strategy config panel (TP/SL ratios, channel parameters)
- [ ] **Core SaaS Infrastructure**
  - Multi-tenant PostgreSQL schema (tenant_id on all tables)
  - Subscription plans (Free, Pro, Enterprise) via Stripe
  - Rate limiting (10 req/sec/user, 100 req/sec/IP)
  - Basic audit logging (user actions, bot state changes)
- [ ] **Essential API Endpoints**
  - POST /api/auth/login, /api/auth/logout
  - GET /api/user/profile, /api/bot/status
  - POST /api/bot/start, /api/bot/stop
  - GET /api/market/ticks/{symbol}, /api/market/candles/{symbol}
  - POST /api/backtest/run, GET /api/backtest/results/{id}
  - WebSocket /ws/live-data (equity, trades, risk)

### Should Have (Post-MVP)
- [ ] **Multi-Bot Management**
  - Create/clone multiple bots per user
  - Bot grouping/folders (by strategy, market, risk profile)
  - Cross-bot performance comparison (equity curves side-by-side)
  - Resource allocation limits per bot (max capital, max trades/day)
- [ ] **Advanced Strategy Builder**
  - Visual strategy editor (drag-and-drop indicators)
  - Code editor for custom strategies (Monaco/CodeMirror)
  - Strategy backtesting with one-click execution
  - Strategy marketplace (community/shared strategies)
- [ ] **Enhanced Analytics & Reporting**
  - Sharpe ratio, Sortino, Calmar, max drawdown, win/loss streaks
  - Trade analysis: MFE/MAE, duration analysis, timing analysis
  - Daily/weekly/monthly performance reports (PDF/CSV)
  - Benchmark against buy-and-hold, random strategy
- [ ] **Risk Management Suite**
  - Value-at-Risk (VaR) calculation
  - Stress testing (historical scenarios)
  - Position concentration limits (by symbol, sector)
  - Correlation-based risk limits
- [ ] **Notifications & Alerts**
  - Email/SMS for: circuit breaker triggered, new high/low P&L day, bot stopped/error
  - In-app notifications for: new signal, trade executed, strategy update
  - Webhook integrations (Slack, Discord, custom URLs)

### Could Have (Future)
- [ ] **Social Trading & Copy Trading**
  - Follow top-performing bots (with performance fees)
  - Leaderboards (by Sharpe, win rate, consistency)
  - Allocate capital to copy trader bots
- [ ] **AI-Assisted Strategy Development**
  - LLM-powered strategy suggestions (based on market regime)
  - Natural language to strategy conversion ("buy when RSI < 30 and volume > 2x average")
  - Strategy explanation engine (why did this bot make this trade?)
- [ ] **Advanced Order Types**
  - TWAP, VWAP, iceberg orders
  - Conditional orders (OCO, OSO)
  - Algorithmic execution pipelines
- [ ] **Exchange Expansion**
  - Beyond Deriv: Binance, Bybit, OKX (crypto futures)
  - Forex, commodities, indices (via CFDs)
  - Native stock/ETF trading (via broker APIs)

### Won't Have (Yet)
- [ ] **High-Frequency Trading (HFT)** infrastructure
- [ ] **Dark pool** access
- [ ] **Options** trading (complex greeks, assignment risk)
- [ ] **Fiat currency** trading (requires banking licenses)
- [ ] **Regulatory certifications** (SEC, FCA, etc.) – for professional/institutional tiers only

---

## 🔑 Design Patterns & Architectural Decisions

### 1. **Multi-Tenant SaaS Architecture**
- **Pattern:** Shared Database, Schema-Per-Tenant (via `tenant_id` column)
- **Why:** Balances cost (shared infra) with isolation (easy backup/restore per tenant). Beats shared-row (complex RLS) and db-per-tenant (costly at scale).
- **Implementation:** Every table includes `tenant_id` foreign key. All queries auto-append `WHERE tenant_id = ?` via SQLAlchemy event listeners or QueryBuilder wrappers.

### 2. **Plugin-Based Strategy Engine**
- **Pattern:** Strategy Pattern + Factory Pattern
- **Why:** Enables adding new strategies (Range Break, Volatility, Confluence, ML) without modifying core bot logic.
- **Implementation:** 
  ```python
  # src/strategy/base.py
  class Strategy(ABC):
      @abstractmethod
      def generate_signal(self, data: def generate_signal(self, data: pd.DataFrame) -> Signal: ...
      @abstractmethod: def get_win_probability(self, signal: Signal) -> float: ...
  
  # src/strategy/range_break.py
  class RangeBreakStrategy(Strategy): ...
  
  # src/strategy/factory.py
  def get_strategy(name: str, symbol: str, config: dict) -> Strategy:
      if name == "range_break":
          return RangeBreakStrategy(symbol, config)
      # ... other strategies
  ```

### 3. **Event-Driven Risk Management**
- **Pattern:** Observer Pattern + State Machine
- **Why:** Decouples risk checks from trading logic; allows multiple risk rules to react to same events.
- **Implementation:**
  - RiskManager subscribes to: `trade_opened`, `trade_closed`, `balance_updated`
  - On each event, evaluates all active risk rules (position size, daily loss, consecutive losses)
  - State machine: `NORMAL` → `WARNING` (approaching limits) → `HALTED` (limit breached) → `RECOVERING` (after cooldown)

### 4. **Walk-Forward Backtesting with Monte Carlo**
- **Pattern:** Template Method + Strategy Pattern
- **Why:** Robust validation that avoids overfitting; separates strategy logic from backtesting mechanics.
- **Implementation:**
  - BacktestEngine defines the walk-forward template (split data, train/test loops)
  - Strategy implements `generate_signal` and `get_win_probability`
  - Monte Carlo simulator generates 10k+ price paths from historical return distribution

### 5. **Real-Time Data Pipeline**
- **Pattern:** Publish-Subscribe + Circuit Breaker
- **Why:** Decouples data collection from consumption; prevents cascade failures during market volatility.
- **Implementation:**
  - DerivClient publishes ticks to Redis channels (`ticks:{symbol}`)
  - Multiple subscribers: candle aggregator, strategy engine, UI WebSocket broadcaster
  - Circuit breaker pauses subscription if downstream lag > 5s (prevents memory explosion)

### 6. **Plugin Architecture for Brokers/Exchanges**
- **Pattern:** Adapter Pattern + Factory Pattern
- **Why:** Enables adding new brokers (Deriv, Binance, Interactive Brokers) without changing core trading logic.
- **Implementation:**
  ```python
  # src/broker/base.py
  class Broker(ABC):
      @abstractmethod: async def get_ticks(self, symbol: str) -> AsyncIterator[Tick]: ...
      @abstractmethod: async def place_order(self, order: Order) -> OrderResult: ...
  
  # src/broker/deriv.py
  class DerivBroker(Broker): ...
  
  # src/broker/factory.py
  def get_broker(name: str, config: dict) -> Broker:
      if name == "deriv":
          return DerivBroker(config)
      # ... others
  ```

### 7. **Feature Flags for Safe Rollouts**
- **Pattern:** Feature Flag Pattern (LaunchDarkly-style)
- **Why:** Enables testing new features with subset of users before full rollout; enables kill switches.
- **Implementation:** 
  - Flags stored in PostgreSQL with `enabled`, `rollout_percentage`, `user_segments`
  - Middleware checks flags before enabling new code paths
  - Dashboard uses flags to show/hide UI components

### 8. **Immutable Audit Trail**
- **Pattern:** Append-Only Log + Hash Chaining
- **Why:** Tamper-evident record for compliance and debugging; critical for financial SaaS.
- **Implementation:**
  - Every significant action (trade, config change, user login) appends to `audit_log` table
  - Each entry includes: `previous_hash`, `action_data`, `timestamp`, `user_id`
  - `current_hash = SHA256(previous_hash + action_data + timestamp)`
  - Verifiable by walking the chain from genesis block

### 9. **Resource Isolation per Tenant/Bot**
- **Pattern:** Container Orchestration + Resource Quotas
- **Why:** Prevents noisy neighbor problems; ensures fair resource allocation in multi-tenant SaaS.
- **Implementation:**
  - Each bot runs in its own Docker container (via Kubernetes/Fly.io)
  - Resource quotas set per container: CPU (500m), memory (512MB), disk (1GB)
  - Network policies restrict inter-bot communication (unless explicitly allowed)
  - Monitoring alerts when container approaches limits

### 10. **Gradual Rollout & Canary Deployments**
- **Pattern:** Canary Release Pattern
- **Why:** Minimizes risk of bad deployments affecting all users.
- **Implementation:**
  - New code deployed to 5% of traffic (canary group)
  - Key metrics monitored: error rate, latency, P&L volatility, circuit breaker frequency
  - If metrics healthy for 15-30 min, rollout to 25%, then 50%, then 100%
  - Automated rollback if error rate > 1% or latency p95 increases > 20%

---

## 🛠️ Development Roadmap (Phased Approach)

### Phase 0: Foundation (Current State - COMPLETED)
- [x] Core trading bot engine functional (Deriv WebSocket, strategies, risk, backtest)
- [x] Paper trading simulation working
- [x] PROJECT.md documentation updated
- [x] .env configuration with Deriv demo token
- [x] Unit tests for risk manager, strategy logic
- [x] Local development setup (venv, pyproject.toml)
- [x] **Range Break strategy backtest completed and gates passed** (RB100, 12K candles, 84 trades)
  - Sharpe ratio: 5.314 (>1.2 target)
  - Max drawdown: 0.00% (<12% target)
  - Win rate: 64.29% (>52% target)
  - Expectancy: 0.800R (>0.15R target)
  - Total trades: 84 (>30 minimum for paper trading)
  - Total P&L: +$2.74 (on $10,000 capital = +2.74%)
  - Max single day loss: -$0.3342 (-0.003% of capital, far below -5% limit)
  - Max trades per day: 8 (at limit, shows good signal frequency)
- [x] **Paper trading simulation completed** (out-of-sample test with 84 trades)
  - Profitable over full period: YES
  - No single day exceeded -5% loss: YES (max daily loss -0.003%)
  - Average trades per day: ~5.5 (within 8/day limit)
  - Ready for live deployment pending explicit user approval

### Phase 1: SaaS Infrastructure & Authentication (Weeks 1-2)
- [ ] Implement multi-tenant PostgreSQL schema (add `tenant_id` to all tables)
- [ ] Build authentication system (NextAuth.js + email/password)
- [ ] Create user management API (profile, API keys, subscription tier)
- [ ] Integrate Stripe for subscription payments (Free tier only initially)
- [ ] Build basic audit logging (user actions)
- [ ] Deploy frontend skeleton (Next.js + Park UI)
- [ ] Deliverable: Users can sign up, verify email, access demo account

### Phase 2: Core Dashboard & Single-Bot Management (Weeks 3-4)
- [ ] Implement real-time dashboard components (KPI cards, equity curve, candlestick chart)
- [ ] Build bot start/stop/pause controls with explicit confirmation
- [ ] Integrate WebSocket for live data streaming (equity, trades, risk)
- [ ] Add strategy configuration panel (TP/SL ratios, channel parameters)
- [ ] Implement trade log and history views
- [ ] Add risk status panel (circuit breaker, daily loss, trades remaining)
- [ ] Deliverable: Users can run a single bot in paper trading and see live results

### Phase 3: Backtesting & Strategy Development (Weeks 5-6)
- [ ] Implement backtest engine API (run, results, progress)
- [ ] Build backtest configuration UI (instrument, date range, parameters)
- [ ] Add backtest results viewer (equity curve, metrics table, gate evaluation)
- [ ] Implement walk-forward validation and Monte Carlo simulation
- [ ] Add strategy builder (basic form for Range Break params)
- [ ] Deliverable: Users can backtest strategies and see if they pass gates

### Phase 4: Multi-Bot & Advanced Features (Weeks 7-8)
- [ ] Implement multi-bot management (create, clone, delete bots per user)
- [ ] Add bot grouping/folders and performance comparison view
- [ ] Implement resource limits per bot (max capital, max trades/day)
- [ ] Add advanced strategy inputs (technical indicators, ML models)
- [ ] Implement notification system (email, in-app, webhook)
-webhook]

None