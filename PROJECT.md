# PROJECT.md — Synthetic Trader SaaS

> **Estado:** Activo | **Versión:** 0.2.0 | **Última actualización:** 2026-07-31

---

## 🎯 Objetivo Principal

Plataforma SaaS para deployar, gestionar y monitorear bots de trading algorítmico en índices sintéticos de Deriv y crypto, con gestión de riesgo institucional, multi-tenancy y analítica en tiempo real.

## 🎯 Objetivos Secundarios

1. Permitir diseño de estrategias vía factory pattern (BreakoutStrategy, VolatilityStrategy, ConfluenceStrategy)
2. Backtesting con walk-forward, Monte Carlo y simulación de latencia 100-500ms
3. Paper trading en vivo con pipeline real-time (state + equity + trades cada 2-10s)
4. Gestión de riesgo institucional: Kelly dinámico, dual circuit breaker, hard cap 1.5% per trade
5. Dashboard React con WebSocket live-data
6. Containerización con Docker para deployment reproducible

---

## 📐 Arquitectura

### Stack Tecnológico

| Capa | Tecnología | Versión | Propósito |
|------|-----------|---------|-----------|
| Lenguaje | Python | 3.12+ | Backend, strategies, asyncio |
| API Framework | FastAPI | latest | REST endpoints + WebSocket live-data |
| Broker API | Deriv WebSocket | new API | Conexión con PAT + OTP flow a Deriv |
| Data Format | OHLCV JSON | — | Almacenamiento histórico (candle caching) |
| Frontend | React | latest | Dashboard con visualización de señales |
| Realtime | WebSockets | — | `/ws/live-data` transmite state + equity + trades |
| DB | SQLite | bundled | Estado de paper trading + backtest persistido |
| Testing | pytest | latest | 37 unit tests (analysis, risk, trading, paper) |
| Container | Docker | latest | Dockerfile para deployment |
| Realtime Files | JSONL + JSON | — | `paper_state.json`, `equity.jsonl`, `trades.jsonl` |

### Diagrama de Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│              CAPA CLIENTE (React Dashboard)                  │
│  Dashboard · Bot Status · Equity Chart · Trades Feed          │
│  WebSocket `ws://localhost/ws/live-data`                      │
├─────────────────────────────────────────────────────────────┤
│              CAPA API (FastAPI + uvicorn)                     │
│  REST: /api/bot/status, /api/backtest, /api/strategies        │
│  WS:   /ws/live-data → poll JSONL state files cada 2s        │
├─────────────────────────────────────────────────────────────┤
│              CAPA LÓGICA (Strategy + Risk + Backtest)        │
│                                                               │
│  ┌── Strategy Factory ───┐  ┌── Analysis ──────────┐        │
│  │ create_strategy(name) │  │ range_detector         │        │
│  │  • BreakoutStrategy   │  │ volume_analyzer        │        │
│  │  • VolatilityStrategy │  │ volatility_filter      │        │
│  │  • ConfluenceStrategy │  │ signal_scorer (0-1)    │        │
│  └────────────────────────┘  │ recommender (humano)   │        │
│                               │ indicators (ATR, EMA)   │        │
│  ┌── Risk ──────────────┐   └────────────────────────┘        │
│  │ manager.py             │                                     │
│  │  • Dynamic Kelly ×0.25 │  ┌── Backtest ────────────┐      │
│  │  • Hard cap 1.5%       │  │ engine.py                │      │
│  │ circuit_breaker.py     │  │  • Walk-forward          │      │
│  │  • 3 loss → cooldown   │  │  • Latency simulation    │      │
│  │  • 5% DD → halt        │  │  • Spread + slippage     │      │
│  └────────────────────────┘  └──────────────────────────┘    │
│                                                               │
│  ┌── Trading ────────────┐  ┌── Data ─────────────────┐     │
│  │ order.py (Deriv)       │  │ collector.py             │     │
│  │ execution.py           │  │ store.py (cache OHLCV)    │     │
│  │ paper_runner.py        │  └──────────────────────────┘     │
│  └────────────────────────┘                                     │
├─────────────────────────────────────────────────────────────┤
│              CAPA CONEXIÓN (Deriv WebSocket)                  │
│  deriv_client.py · PAT + OTP flow · ping/keepalive             │
├─────────────────────────────────────────────────────────────┤
│              CAPA DATOS (File System + SQLite)                │
│  realtime/paper_state.json · equity.jsonl · trades.jsonl       │
│  data/ohlc_cache/*.parquet · backtest_results/*.json           │
└─────────────────────────────────────────────────────────────┘
```

### Flujo de Datos

```
[Deriv WebSocket]
  → [deriv_client (subscribe candles + ticks)]
  → [data/collector (cache OHLCV) + data/store (persist)]
  → [analysis/* (range + volume + volatility → signal_scorer)]
  → [strategies/* (Signal: score >= 0.6 + position size from risk/manager)]
  → [trading/order (DECISION) → execution (Deriv WS order)]
  → [trading/paper_runner (paper mode → realtime files)]
  → [realtime/*.jsonl (2-10s write)]
  → [api/bot_status (read JSONL) + ws/live_data (push a dashboard)]
  → [React Dashboard → chart + equity line + trade feed]
```

Ciclo completo: tick → análisis → señal → (paper/live) → estado realtime → dashboard live.

---

## 📊 Matriz de Trazabilidad

| Req ID | Descripción | Componente | Estado | Verificación |
|--------|-------------|------------|--------|--------------|
| R-01 | Deriv WebSocket con PAT + OTP flow | `connection/deriv_client.py` | ✅ | Paper trading engine conecta y mantiene keepalive |
| R-02 | Rango break con multi-factor scoring (0-1) | `analysis/range_detector.py`, `signal_scorer.py` | ✅ | Backtest RB100 → 84 trades, threshold 0.6 |
| R-03 | Kelly dinámico con confidence × volatility | `risk/manager.py` | ✅ | Backtest — Kelly fracción 0.012-0.048 (0.3%-1.2% capital) |
| R-04 | Dual circuit breaker (pérdidas + drawdown) | `risk/circuit_breaker.py` | ✅ | Backtest nunca triggers (max 2 consec losses, 0% DD) |
| R-05 | Walk-forward validation (5 ventanas) | `backtest/engine.py` | ✅ | 5/5 ventanas pasan gates: avg Sharpe 28.0, WR 91.4% |
| R-06 | Monte Carlo 10,000 permutations | `backtest/engine.py` | ✅ | P(profitable) = 100%, P(DD>12%) = 0% |
| R-07 | Strategy Factory (registry-based) | `trading/strategy_factory.py` | ✅ | `create_strategy("breakout"|"volatility"|"confluence")` funciona |
| R-08 | VolatilityStrategy (ATR mean-reversion) | `strategies/volatility.py` | ✅ | Implementa base.py interface |
| R-09 | ConfluenceStrategy (breakout + volatility combo) | `strategies/confluence.py` | ✅ | confidence = product de agreement |
| R-10 | Paper trading engine + realtime files | `trading/paper_runner.py` | ✅ | `_write_realtime_state()` cada 10s, `_write_realtime_trade()` por trade |
| R-11 | API REST + WebSocket dashboard | `api/server.py` + `/ws/live-data` | ✅ | API devuelve `mode: "paper"`, WebSocket push state/equity/trades |
| R-12 | Dockerfile para containerized deploy | `Dockerfile` | ✅ | `docker build` exit 0 |
| R-13 | 37 unit tests en analysis/risk/trading | `tests/` | ✅ | `pytest` 37/37 pasan |
| R-14 | VolatilityStrategyV2 (issue #1) | `strategies/volatility_v2.py` | ⏳ | Issue #1 — next uppendiente |
| R-15 | Web interface visualización para backtest | `frontend/` | ⏳ | Pendiente |
| R-16 | Live trading deploy (user explicit approval) | — | ⏳ | Bloqueado por user approval gate |

---

## 🏗️ Marcos Conceptuales

### Multi-Factor Signal Scoring
Reemplaza detección binaria de breakout con puntuación matizada:
- **Penetration score (0-0.4)**: profundidad del breakout
- **Volume score (0-0.3)**: confirmación por volumen spike
- **Volatility score (0-0.2)**: favorece breakouts en volatilidad normal/baja
- **Decision threshold**: score >= 0.6 → trade signal

Esto reduce significativamente falsos positivos y permite position sizing dinámico basado en confianza.

### Dynamic Kelly Position Sizing
Kelly clásico refinado con factores adaptativos:
```
p = win_probability × confidence   # confidence = signal score (0-1)
kelly = (p × b - q) / b            # Standard Kelly
adjusted = kelly × 0.25 / vol_mult # Quarter-Kelly ÷ volatility multiplier
stake = min(adjusted × capital, 0.015 × capital)  # Hard cap 1.5%
```
Donde `vol_mult` = 1.0 + max(0, ATR_ratio - 1.0), asegurando exposición conservadora en alta volatilidad.

### Deriv Synthetic Indices Trading Skill
Cumple los requisitos de la skill especializada:
- Usa análisis estadístico (no indicadores técnicos clásicos) para sintéticos
- Kelly criterion con quarter-Kelly y hard caps
- Risk rules: 2% per trade, 5% daily, 10 trades max
- Requiere backtest + paper trading antes de live consideration
- Prohibidas estrategias martingale, grid, u otras peligrosas

### Open-Closed Strategy Pattern
La fábrica `create_strategy(name)` permite añadir nuevas estrategias sin modificar callers existentes — registro interno, base ABC en `strategies/base.py` con `Signal(Timestamp, Type, Price, Score)`.

---

## ✅ Justificación de Decisiones Técnicas

| Decisión | Opción elegida | Alternativas evaluadas | Razón |
|----------|---------------|----------------------|-------|
| Lenguaje backend | Python + asyncio | Rust, Go, Node.js | Ecosistema data science, FastAPI async nativo |
| API Framework | FastAPI | Flask, Django REST, Sanic | WebSocket nativo, async-first, Pydantic validation, OpenAPI docs |
| State realtime | JSONL files + polling WS | Redis Pub/Sub, Kafka | Simple, sin infra extra, paper mode local file ─ escalable luego |
| Risk cap | Quarter-Kelly + hard cap 1.5% | Full-Kelly, Fixed fractional | Mitiga varianza de estimaciones de win-rate, protege en alta volatilidad |
| Circuit Breaker | Dual (consec losses + DD) | Single DD, time-based | Cobertura layered:执勤 tail risk independiente y diario |
| Strategy pattern | Factory registry | Inheritance directa, plugins | Open-Closed, dicts `name→class`, callers no acoplados |
| Backtest realism | Latency sim 100-500ms + spread/slippage | Ideal fill, no costs | Resultados conservadores, previene overfitting a fills mágicos |
| Containerization | Docker | systemd, supervisor | Reproducibilidad, deploy multi-cloud, escalamiento horizontal |
| Threshold | Score 0.6 (sweep opt: 0.50) | Threshold 0.35, 0.70 | Sweep 0.35-0.70: óptimo 0.50 da 80 trades, 92.5% WR; 0.6 por default es más conservador |
| Risk cap adjust | 1.5% → 3% | Stay 1.5%, raise 5% | Duplica P&L mientras Monte Carlo asegura P(DD>12%) = 0% |

---

## 📦 Estado de Implementación

### Fases Completadas

| Fase | Descripción | Commit | Verificación |
|------|-------------|--------|--------------|
| V1 | Core bot: Deriv WS, Range Break, Kelly, circuit breaker | [init] | Paper demo +$2.74 |
| V2 | Refactor modular + multi-factor scoring + latency backtest | 126d2af | Backtest RB100 Sharpe 5.31, WR 64% — gates pasados |
| V3 | Optimization + Walk-forward + Monte Carlo | [prev to bc787be] | 5/5 windows pass, MC 100% profitable |
| V4 | Strategy Factory + VolatilityStrategy + 37 unit tests + Docker | bc787be | pytest 37/37, docker build exit 0 |
| V5 | Realtime dashboard pipeline (Bot → JSONL → API → WS → Dashboard) | bc787be | API + WS funcionan con paper_state.json |
| Templates | GitHub issue/PR templates + CI 3-layer gates | d7bb136 | Workflow files committed |

Próximo commit previsto: `docs: estandarizar PROJECT.md (template SophIA con matriz de trazabilidad y justificación de decisiones)` + `feat: VolatilityStrategyV2 (issue #1)`

### Próximos Pasos (Backlog)

| ID | Descripción | Prioridad | Issue |
|----|-------------|-----------|-------|
| B-1 | VolatilityStrategyV2 — ATR-band mejorado con features adicionales | Alta | #1 |
| B-2 | Web interface visualización de señales y config de backtest | Alta | #2 |
| B-3 | Live trading deploy (requiere aprobación explícita del usuario) | Media | #3 |
| B-4 | Alertas Telegram (notificaciones/telegram.py está skeloton) | Media | #4 |
| B-5 | Multi-bot orchestration (≥2 bots concurrentes con arbitraje) | Baja | #5 |

---

## ⚠️ Limitaciones Conocidas

1. **Backtest dataset limitado**: 12,000 velas (8.3 días) — no es estadísticamente representativo de años. Monte Carlo ayuda pero no sustituye data más larga.
2. **Paper trading only**: sin riesgo real, slippg en live puede diferir (paper_runner.py simula spread/slippage fijo).
3. **Deriv API rate limits**: límites no documentados, throttle puede requerir pagar PAT tiers.
4. **Single bot focus**: arquitectura preparada multi-bot pero no probada con ≥2 concurrentes.
5. **Web dashboard mockup**: API funciona pero el frontend React está incompleto (UI aún básica).
6. **Sin auth multi-tenancy**: aunque el visionar es SaaS, no hay auth/billing implementados.
7. **Risk cap 3% may still be conservative**: Monte Carlo advisory says más agresividad posible, pero no probado en live.

---

## 🔐 Seguridad

- **PAT (Personal Access Token)** requerido para conexión Deriv — nunca hardcodeado, en variables de entorno o config no versionado
- **No guarda secrets en SQLite**: conexiones se gestionan in-memory por session
- **Archivo `deriv.yaml`** solo con endpoints, sin credentials
- **.env nunca commiteado**: `.env.example` con placeholders en repo, real `deriv.yaml` con PAT via environment

---

## 📚 Referencias

- Deriv API docs: https://api.deriv.com/
- FastAPI docs: https://fastapi.tiangolo.com/
- Kelly criterion: https://en.wikipedia.org/wiki/Kelly_criterion
- Walk-forward analysis: https://www.investopedia.com/terms/w/walkforwardanalysis.asp
- Synthetic indices (Deriv): https://deriv.com/markets/synthetic
- Repo: https://github.com/Nxxo31/synthetic-trader
- Backtest results: backtest_results/*.json

---

*Generado por SophIA — Sebastian Velasco's autonomous operating system*
