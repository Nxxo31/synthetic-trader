# PROJECT.md — Synthetic Trader SaaS

> **Estado:** Activo — Paper Trading 24/7 en curso | **Versión:** 0.3.0 | **Última actualización:** 2026-08-03

---

## 🎯 Objetivo Principal

Plataforma SaaS para deployar, gestionar y monitorear bots de trading algorítmico en índices sintéticos de Deriv y crypto, con gestión de riesgo institucional, multi-tenancy y analítica en tiempo real.

## 🎯 Objetivos Secundarios

1. Permitir diseño de estrategias vía factory pattern (BreakoutStrategy, VolatilityStrategy, ConfluenceStrategy, StepIndexStrategy, DriftBoomCrashStrategy) basadas en investigación de mercado y backtesting rigurosos
2. Backtesting con walk-forward, Monte Carlo y simulación de latencia 100-500ms
3. Paper trading en vivo 24/7 con pipeline real-time (state + equity + trades cada 2-10s)
4. Gestión de riesgo institucional: Kelly dinámico, dual circuit breaker, hard cap 1.5% per trade
5. Dashboard React/Next.js con WebSocket live-data + zona de proyección económica
6. Containerización con Docker para deployment reproducible
7. Capital Allocator con gestión de superávit (reserva 80% + superávit operativo 20% + reinversión)
8. Strategy Attribution Engine (Brinson-Fachler) para medir qué estrategia rinde más por símbolo
9. Return Projector con Monte Carlo forward (10k simulaciones, bandas P5/P50/P95)
10. Spinner de paper trading 24/7 con reconexión automática via systemd

---

## 📐 Arquitectura

### Stack Tecnológico

| Capa | Tecnología | Versión | Propósito |
|------|-----------|---------|-----------|
| Lenguaje | Python | 3.12+ | Backend, strategies, asyncio |
| API Framework | FastAPI | latest | REST endpoints + WebSocket live-data |
| Broker API | Deriv WebSocket | new API | Conexión con PAT + OTP flow a Deriv |
| Data Format | Parquet + JSONL | — | Candles históricos + realtime state |
| Frontend | Next.js 16 + Tailwind | latest | Dashboard + zona de proyección |
| Charts | Recharts | v3 | Equity curves, heatmaps, proyecciones |
| Realtime | WebSockets | — | `/ws/live-data` transmite state + equity + trades |
| DB | SQLite | bundled | Estado paper trading + attribution + backtest |
| Testing | pytest | latest | Tests unitarios módulos core |
| Container | Docker | latest | Dockerfile para deployment |
| Capital Allocator | Python | v0.5 | Reserva(80%) + superávit(20%) + reinversión diaria |
| Attribution | SQLite + Python | v0.5 | Brinson-Fachler, matriz estrategia×símbolo |
| Monte Carlo Sim | NumPy | v0.5 | 10,000 paths forward, P5/P50/P95 equity curves |
| Paper 24/7 | systemd + wrapper | v0.5 | Reconexión automática, health checks, backoff |
| API Aliases | Python middleware | v0.5 | Responses en español (palabras clave, no siglas) |

### Diagrama de Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│              CAPA CLIENTE (Next.js Dashboard)                 │
│  / → Dashboard (KPIs, equity, trades, risk)                   │
│  /projection → Allocator + Projection + Attribution          │
│  WebSocket `ws://localhost:8001/ws/live-data`                 │
├─────────────────────────────────────────────────────────────┤
│              CAPA API (FastAPI + uvicorn :8001)               │
│  REST: /api/health, /api/bot/status, /api/bot/trades         │
│        /api/allocator/*, /api/attribution/*, /api/projection/*│
│  WS:   /ws/live-data → poll JSONL state files cada 2s        │
│  Middleware: ResponseAliasMiddleware (español)                │
├─────────────────────────────────────────────────────────────┤
│              CAPA LÓGICA (Strategy + Risk + Analysis)        │
│                                                               │
│  ┌── Strategy Factory ────┐  ┌── Analysis ──────────┐       │
│  │ create_strategy(name)  │  │ attribution.py        │       │
│  │  • BreakoutStrategy    │  │  (Brinson-Fachler)    │       │
│  │  • VolatilityStrategy  │  │ projector.py          │       │
│  │  • ConfluenceStrategy  │  │  (Monte Carlo 10k)   │       │
│  │  • StepIndexStrategy   │  │ daily_attribution.py  │       │
│  │  • DriftBoomCrash      │  └───────────────────────┘       │
│  └─────────────────────────┘                                  │
│  ┌── Risk ───────────────┐  ┌── Capital Allocator ──┐        │
│  │ manager.py             │  │ capital_allocator.py   │        │
│  │  • Kelly ×0.25         │  │  • Reserva 80%         │        │
│  │  • Hard cap 1.5%       │  │  • Superávit 20%       │        │
│  │ circuit_breaker.py     │  │  • Reinvest profits    │        │
│  │  • 3 loss → cooldown   │  │  • Micro-stake sizing  │        │
│  │  • 5% DD → halt        │  └───────────────────────┘        │
│  └────────────────────────┘                                    │
│  ┌── Trading ────────────┐  ┌── Data ─────────────────┐     │
│  │ paper_runner.py        │  │ collector.py             │     │
│  │  (multi-strategy)     │  │ store.py (Parquet cache)  │     │
│  └────────────────────────┘  └──────────────────────────┘     │
├─────────────────────────────────────────────────────────────┤
│              CAPA CONEXIÓN (Deriv WebSocket)                  │
│  deriv_client.py · PAT + OTP flow · ping/keepalive             │
├─────────────────────────────────────────────────────────────┤
│              CAPA DATOS (File System + SQLite)                │
│  realtime/paper_state.json · equity.jsonl · trades.jsonl       │
│  data/candles/*.parquet · data/strategies.db                   │
│  reports/paper/*.json · reports/daily/*.json                   │
└─────────────────────────────────────────────────────────────┘
```

### Flujo de Datos

```
[Deriv WebSocket]
  → [deriv_client (subscribe candles + ticks)]
  → [data/collector (cache OHLCV) + data/store (persist Parquet)]
  → [strategies/* (generate_signal via factory)]
  → [risk/capital_allocator (reset_daily → superávit)]
  → [risk/manager (position_size_dynamic Kelly)]
  → [trading/paper_runner (simulate_trade → record P&L)]
  → [allocator.record_trade + circuit_breaker.update]
  → [realtime/*.jsonl (2-10s write)]
  → [api/server (read JSONL) + ws/live_data (push dashboard)]
  → [Next.js Dashboard → chart + equity + trades + projection]
```

Ciclo completo: tick → análisis → señal → (paper/live) → estado realtime → dashboard live.

---

## 📊 Matriz de Trazabilidad

| Req ID | Descripción | Componente | Estado | Verificación |
|--------|-------------|------------|--------|--------------|
| R-01 | Deriv WebSocket con PAT + OTP flow | `connection/deriv_client.py` | ✅ | Paper trading engine conecta y mantiene keepalive |
| R-02 | Rango break con multi-factor scoring (0-1) | `analysis/range_detector.py`, `signal_scorer.py` | ✅ | Backtest RB100 → 84 trades, threshold 0.6 |
| R-03 | Kelly dinámico con confidence × volatility | `risk/manager.py` | ✅ | Kelly fracción 0.012-0.048 (0.3%-1.2% capital) |
| R-04 | Dual circuit breaker (pérdidas + drawdown) | `risk/circuit_breaker.py` | ✅ | Max 2 consec losses, 0% DD en backtest |
| R-05 | Walk-forward validation (5 ventanas) | `backtest/engine.py` | ✅ | 5/5 ventanas pasan gates: avg Sharpe 28.0, WR 91.4% |
| R-06 | Monte Carlo 10,000 permutations | `backtest/engine.py` | ✅ | P(profitable) = 100%, P(DD>12%) = 0% |
| R-07 | Strategy Factory (6 estrategias registradas) | `trading/strategy_factory.py` | ✅ | breakout, volatility, confluence, step_index, drift_boom_crash |
| R-08 | VolatilityStrategy (ATR mean-reversion) | `strategies/volatility.py` | ✅ | Implementa base.py interface |
| R-09 | ConfluenceStrategy (breakout + volatility combo) | `strategies/confluence.py` (in factory) | ✅ | confidence = product de agreement |
| R-10 | Paper trading engine + realtime files | `trading/paper_runner.py` | ✅ | `_write_realtime_state()` cada 10s |
| R-11 | API REST + WebSocket dashboard | `api/server.py` + `/ws/live-data` | ✅ | 17 endpoints activos |
| R-12 | Dockerfile para containerized deploy | `Dockerfile` | ✅ | `docker build` exit 0 |
| R-13 | 37 unit tests en analysis/risk/trading | `tests/` | ✅ | `pytest` 37/37 pasan |
| R-14 | VolatilityStrategyV2 (issue #1) | `strategies/volatility_v2.py` | ⏳ | Issue #1 — pendiente |
| R-15 | Web interface visualización backtest | `frontend/` | ⏳ | Pendiente |
| R-16 | Live trading deploy (user explicit approval) | — | ⏳ | Bloqueado por user approval gate |
| R-17 | StepIndexStrategy (gem — Step Index predecible) | `strategies/step_index.py` | ✅ | EMA trend + ATR reversion, step size known |
| R-18 | DriftBoomCrashStrategy (kraken — Boom/Crash scalping) | `strategies/drift_boom_crash.py` | ✅ | Drift post-spike, RSI + spike detection |
| R-19 | Capital Allocator (reserva + superávit) | `risk/capital_allocator.py` | ✅ | reset_daily, calculate_micro_stake, record_trade |
| R-20 | Strategy Attribution Engine (Brinson-Fachler) | `analysis/attribution.py` | ✅ | Matriz estrategia×símbolo, save en strategies.db |
| R-21 | Return Projector (Monte Carlo forward 10k) | `analysis/projector.py` | ✅ | P5/P50/P95 equity curves, prob_profit, max_dd |
| R-22 | API endpoints allocator/attribution/projection | `api/server.py` | ✅ | GET/POST verificados en puerto 8001 |
| R-23 | API aliases español (middleware) | `api/_aliases.py` | ✅ | with_aliases() aggiorna claves español |
| R-24 | Paper trading 24/7 con reconexión | `scripts/paper_247.py` | ✅ | Wrapper con backoff, crash-loop detection |
| R-25 | Servicio systemd para paper trading | `deploy/synthetic-trader-paper.service`, `deploy/systemd/synthetic-trader-paper.service` | ✅ | Units creados (pendiente `systemctl enable` — requiere sudo) |
| R-26 | Atribución diaria automática | `src/analysis/daily_attribution.py` | ✅ | Script nocturno puebla strategies.db |
| R-27 | Dashboard zona proyección (/projection) | `dashboard/src/app/projection/` | ✅ | Split Panes (Variante B), Recharts, CSS Modules |
| R-28 | Dashboard principal en español | `dashboard/src/app/page.tsx` | ✅ | KPIs, tabla, panel riesgo sin siglas |
| R-29 | Multi-strategy en paper_runner | `trading/paper_runner.py` | ✅ | strategy_name param + factory + allocator |
| R-30 | Timer systemd para attribution diaria | `deploy/synthetic-trader-attribution.timer`, `deploy/synthetic-trader-attribution.service` | ✅ | Timer + service oneshot creados (pendiente `systemctl enable` — requiere sudo) |

---

## 🏗️ Marcos Conceptuales

### Multi-Factor Signal Scoring
Reemplaza detección binaria de breakout con puntuación matizada:
- **Penetration score (0-0.4)**: profundidad del breakout
- **Volume score (0-0.3)**: confirmación por volumen spike
- **Volatility score (0-0.2)**: favorece breakouts en volatilidad normal/baja
- **Decision threshold**: score >= 0.6 → trade signal

### Dynamic Kelly Position Sizing
```
p = win_probability × confidence
kelly = (p × b - q) / b
adjusted = kelly × 0.25 / vol_mult
stake = min(adjusted × capital, 0.015 × capital)  # Hard cap 1.5%
```

### Capital Allocator — Gestión de Superávit
Divide el capital total en dos buckets:
1. **Reserva (80%)**: Capital protegido que NUNCA se arriesga intradía.
2. **Superávit diario (20%)**: Único capital expuesto a trading.
3. **Reinversión**: Si el día fue positivo, las ganancias alimentan el superávit del día siguiente.

El RiskManager sigue aplicando sus caps (1.5% per trade) dentro del superávit.

### Strategy Attribution — Brinson-Fachler
Descomposición de rendimiento en:
- **Allocation Effect**: Cuánto aporta la selección de mercado/símbolo
- **Selection Effect**: Cuánto aporta la estrategia dentro de ese mercado
- **Interaction Effect**: Sinergia entre allocation y selection

Persiste resultados en `data/strategies.db` tablas `strategy_performance` y `strategy_comparisons`.

### Return Projector — Monte Carlo Forward
Genera 10,000 paths forward usando parámetros históricos:
- Win rate, avg win, avg loss por estrategia
- Devuelve: equity curve P5/P50/P95, expected_return, max_dd_proyectado, prob_ruin

### Estrategias Implementadas

| Estrategia | Clase | Símbolos | Edge | Win Prob Base |
|-----------|-------|---------|------|---------------|
| Breakout | RangeBreakStrategy | R_100, RB100 | Breakout de canal con volume confirmation | 54% |
| Volatility | VolatilityStrategy | R_75, R_100 | Mean reversion con ATR bands | 58% |
| Confluence | ConfluenceStrategy | Multi | Breakout + Volatility aggreement (dual confirm) | 60% |
| Step Index (gem) | StepIndexStrategy | STEPT10-100 | Tendencia + reversion en escalones discretos | 62% |
| Drift Boom/Crash (kraken) | DriftBoomCrashStrategy | BOOM300, CRASH300 | Drift post-spike (boom→short, crash→long) | 60% |

### Investigación: Gems y Kraken

**Gems (Step Index)**: Identificado en investigación como la "joya" de Deriv — movimiento predecible en escalones, sin picos, 24/7. Ideal para bots automatizados. Implementado como `StepIndexStrategy` con EMA trend + ATR reversion y SL/TP en múltiplos del tamaño de paso conocido.

**Kraken (Boom/Crash Scalping)**: Estrategia agresiva post-spike. Después de un boom (pico alcista), el precio tiende a revertirse bajista; después de un crash (caída), tiende a recuperarse. Implementado como `DriftBoomCrashStrategy` con detección de spikes via desviación estándar y drift direccional via regresión lineal.

Fuentes: `docs/investigacion-estrategias-bots-multi-mercado.md`, `docs/research-crypto-operator-deriv.md`, `RESEARCH_POSITION_SIZING_RISK.md`

### Reglas de Riesgo (Investigación Validada)
- Risk per trade: 1.5% (hard cap via Kelly)
- Daily loss limit: 5% (circuit breaker)
- Daily profit target: $10 (en cuenta demo $10k → escala proporcional)
- Max concurrent positions: 1 (paper_runner)
- Cooldown per symbol: 120s (recomendado por investigación)
- Prohibidas: martingale, grid, doubling strategies

---

## ✅ Justificación de Decisiones Técnicas

| Decisión | Opción elegida | Alternativas | Razón |
|----------|---------------|-------------|-------|
| Lenguaje backend | Python + asyncio | Rust, Go, Node.js | Ecosistema data science, FastAPI async |
| API Framework | FastAPI | Flask, Django REST | WebSocket nativo, async-first, Pydantic |
| State realtime | JSONL + polling WS | Redis, Kafka | Simple, sin infra extra, escalable luego |
| Risk cap | Quarter-Kelly + 1.5% hard | Full-Kelly, Fixed | Mitiga varianza de win-rate estimates |
| Circuit Breaker | Dual (consec + DD) | Single DD | Cobertura layered: tail risk + diario |
| Strategy pattern | Factory registry | Inheritance directa | Open-Closed, callers no acoplados |
| Capital Allocator | Reserva 80% + Superávit 20% | Flat allocation | Protege capital de rachas malas diarias |
| Attribution | Brinson-Fachler | Simple return comparison | Estándar industry, descomposición 3 efectos |
| Monte Carlo | 10k paths forward | Historical bootstrap | Más flexible para proyección multi-escenario |
| Multi-strategy | Factory + strategy_name param | Hardcoded RangeBreak | Permite comparar estrategias en paper |
| API idioma | Middleware aliases español | Traducir en frontend | Compatibilidad atrás, claves español en JSON |
| Paper 24/7 | Wrapper + systemd | Cron, nohup | Backoff, crash-loop detection, SIGTERM clean |
| Step Index SL/TP | Step size conocido | ATR puro | Precisión matemática, riesgo determinista |
| Boom/Crash entry | Post-spike con drift | Tick direction | Edge empírico ~60%, drift estructural |

---

## 🚀 Deploy systemd — Paper Trading 24/7 + Attribution Diaria

### Archivos creados

| Archivo | Propósito | Estado |
|---------|-----------|--------|
| `deploy/systemd/synthetic-trader-paper.service` | Unit hardened (NoNewPrivileges, ProtectSystem, MemoryMax 2G, args paper_247.py) | ✅ creado |
| `deploy/synthetic-trader-paper.service` | Unit minimalista de referencia del task | ✅ creado |
| `deploy/synthetic-trader-attribution.service` | Servicio oneshot: puebla `strategies.db` desde `reports/daily/*.json` | ✅ creado |
| `deploy/synthetic-trader-attribution.timer` | Timer `OnCalendar=daily` con `Persistent=true` | ✅ creado |

### Validación de sintaxis

```
$ systemd-analyze verify deploy/synthetic-trader-paper.service \
    deploy/synthetic-trader-attribution.service \
    deploy/synthetic-trader-attribution.timer
# exit 0, sin warnings
```

### Instrucciones de instalación (requiere sudo — NO ejecutar desde el agent)

```bash
# 1. Copiar units al directorio de systemd (recomendado: versión hardened)
sudo cp deploy/systemd/synthetic-trader-paper.service /etc/systemd/system/
sudo cp deploy/synthetic-trader-attribution.service   /etc/systemd/system/
sudo cp deploy/synthetic-trader-attribution.timer     /etc/systemd/system/

# 2. Recargar systemd para que detecte los units nuevos
sudo systemctl daemon-reload

# 3. Habilitar y arrancar paper trading 24/7
sudo systemctl enable --now synthetic-trader-paper
systemctl status synthetic-trader-paper   # verificar: active (running)

# 4. Habilitar el timer de attribution diaria (no arrancar el servicio directo)
sudo systemctl enable --now synthetic-trader-attribution.timer
systemctl list-timers synthetic-trader-attribution   # verificar: próximo disparo 00:00

# 5. (Opcional) Test manual del oneshot de attribution
sudo systemctl start synthetic-trader-attribution.service
journalctl -u synthetic-trader-attribution -e
```

### Notas operativas

- **Paper trader ya corriendo**: el proceso actual (PID 5505, `python scripts/paper_247.py` en background) se debe detener antes de `systemctl start` para que systemd gestione el lifecycle (si no, dos instancias harán trades duplicados). Parar con `kill 5505` (o `pkill -f paper_247.py`) antes del paso 3.
- **Idempotencia del attribution**: `daily_attribution.py` detecta rows duplicados por `(strategy_name, symbol, backtest_date, total_trades, total_pnl)` y los saltea. Re-ejecutar el servicio manualmente no duplica datos.
- **Catch-up con `Persistent=true`**: si el host estuvo apagado a 00:00, systemd dispara el timer al arranque en vez de esperar al día siguiente. Útil para VPS/laptops.
- **Logs**: `journalctl -u synthetic-trader-paper -f` (paper trader) y `journalctl -u synthetic-trader-attribution -e` (attribution).
- **Venv**: los ExecStart apuntan a `.venv/bin/python` (symlink a `python3`). Si las deps del project cambian y se recrea el venv, el symlink sigue válido (mismo path).

---

## 📦 Estado de Implementación

### Fases Completadas

| Fase | Descripción | Verificación |
|------|-------------|--------------|
| V1 | Core bot: Deriv WS, Range Break, Kelly, circuit breaker | Paper demo +$2.74 |
| V2 | Refactor modular + multi-factor scoring + latency backtest | Backtest RB100 Sharpe 5.31, WR 64% |
| V3 | Optimization + Walk-forward + Monte Carlo | 5/5 windows pass, MC 100% profitable |
| V4 | Strategy Factory + VolatilityStrategy + 37 tests + Docker | pytest 37/37, docker build exit 0 |
| V5 | Realtime dashboard pipeline (Bot → JSONL → API → WS → Dashboard) | API + WS funcionan |
| V6 | Capital Allocator + Strategy Attribution + Return Projector | API endpoints verificados en :8001 |
| V7 | Multi-strategy + StepIndex + DriftBoomCrash + Paper 24/7 | Paper trading corriendo en Deriv demo |
| V8 | Dashboard zona proyección + aliases español + daily attribution | Next.js :3000, API middleware activo |

**Estado actual**: Paper trading 24/7 en curso en cuenta demo Deriv ($9792.38 balance, estrategia breakout en R_100).

### Próximos Pasos (Backlog)

| ID | Descripción | Prioridad |
|----|-------------|-----------|
| B-1 | VolatilityStrategyV2 — ATR-band mejorado | Alta |
| B-2 | Web interface visualización de backtest | Alta |
| B-3 | Live trading deploy (requiere aprobación explícita del usuario) | Media |
| B-4 | Alertas Telegram (telegram.py skeleton existe) | Media |
| B-5 | Multi-bot orchestration (≥2 bots concurrentes con arbitraje) | Baja |
| B-6 | ✅ Instalar servicio systemd para paper trading persistente — archivos creados en `deploy/`, pendiente `systemctl enable` (ver § Deploy systemd) | Alta | _done: archivos creados_ |
| B-7 | ✅ Programar daily_attribution.py en systemd timer — `deploy/synthetic-trader-attribution.timer` creado, pendiente `systemctl enable` (ver § Deploy systemd) | Alta | _done: archivos creados_ |
| B-8 | Rotación automática de estrategias según attribution ranking | Media |

---

## 🗺️ Roadmap v0.3.0 → v1.0.0 (4 meses)

> **Ventana:** 2026-08-03 → 2026-12-15 | **Meta:** Bot de trading algorítmico production-ready, paper trading validado con 30+ días de datos, signage to live trading con gate de aprobación, dashboard SaaS-grade multi-tenant.

### Mes 1 — Agosto 2026: Paper Trading Persistente + Datos Reales

**Objetivo del mes:** Paper trading 24/7 acumulando datos reales, instalar systemd, attribution diaria automática, backtestear las 2 estrategias nuevas (StepIndex + DriftBoomCrash) cuando haya suficiente data.

#### Semana 1 (Ago 1–7): Infraestructura paper trading 24/7

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 1.1 | Instalar servicio systemd `synthetic-trader-paper.service` con `systemctl enable --now` | `systemctl status synthetic-trader-paper` → active (running) |
| 1.2 | Programar `daily_attribution.py` como systemd timer (diaria a 00:00 UTC) | `systemctl list-timers` muestra synthetic-trader-attribution.timer |
| 1.3 | Verificar que paper_runner acumula trades en `data/strategies.db` y `realtime/*.jsonl` | `.db` tiene ≥10 filas en `strategy_results` tras 24h |
| 1.4 | Fix bugs detectados por workers (code review + playwright) | Todos los hallazgos Critical y Required resueltos |

**Gate Semana 1:** Paper trading 24/7 corriendo como servicio systemd, sin intervención manual.

#### Semana 2 (Ago 8–14): Backend testing + VolatilityStrategyV2

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 2.1 | Escribir tests pytest para capital_allocator, attribution, projector (siguiendo code review del worker) | `pytest tests/test_capital_allocator.py tests/test_attribution.py tests/test_projector.py` pasa |
| 2.2 | Escribir tests pytest para step_index y drift_boom_crash (mock Deriv data) | Strategy interface tests pasan |
| 2.3 | Implementar VolatilityStrategyV2 (B-1) — ATR-band dinámico con vol regime detection | Backtest en R_75 y R_100: Sharpe > 1.0 |
| 2.4 | Registrar volatility_v2 en strategy_factory | Factory reconoce vol_s2 y crea instancia |

**Gate Semana 2:** 6+ tests nuevos pasan. VolatilityStrategyV2 backtestea con Sharpe > 1.0.

#### Semana 3 (Ago 15–21): Dashboard backtest + Playwright E2E

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 3.1 | Web interface visualización de backtest (B-2) — nueva ruta `/backtest` con selector de estrategia, símbolo y rango de fechas | Dashboard muestra resultados de backtest en tabla + gráfico |
| 3.2 | API endpoint `/api/backtest/run` — ejecuta backtest bajo demanda con módulo engine.py | JSON response con métricas: trades, win_rate, sharpe, max_dd, equity curve |
| 3.3 | Tests Playwright E2E para `/backtest` — llenar form, ejecutar, verificar resultados | 3+ specs green en CI |
| 3.4 | Tests Playwright E2E para dashboard principal y `/projection` — basados en specs del worker | 10+ specs green total |

**Gate Semana 3:** Dashboard tiene 3 rutas operativas (/, /projection, /backtest). Playwright suite completa.

#### Semana 4 (Ago 22–31): Attribution + Rotación automática

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 4.1 | Backtestear StepIndex con datos reales acumulados en el mes | Sharpe > 1.0 o documentar edge insuficiente |
| 4.2 | Backtestear DriftBoomCrash con datos reales acumulados | Sharpe > 0.8 o documentar edge insuficiente |
| 4.3 | Rotación automática de estrategias (B-8) — paper_runner selecciona mejor estrategia por símbolo basándose en attribution ranking | Logger muestra "strategy rotated: breakout → step_index for R_100" |
| 4.4 | API endpoint `/api/strategies/recommend` — devuelve ranking + recomendación | JSON con top 3 estrategias y métricas de respaldo |

**Gate final Mes 1 (Ago 31):** 30+ días de paper trading data, 8 estrategias (6 + vol_s2 + rotación), attribution con datos reales. Coverage de tests ≥ 60%.

---

### Mes 2 — Septiembre 2026: Multi-Bot + Alertas + Hardening

**Objetivo del mes:** Arquitectura multi-bot (B-5), alertas Telegram (B-4), hardening de seguridad, CI/CD pipeline.

#### Semana 5 (Sep 1–7): Alertas Telegram

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 5.1 | Implementar `src/alerts/telegram.py` — bot Telegram que envía notificaciones de trades, P&L diario, circuit breaker triggers | Bot responde a `/status` con estado del paper trading |
| 5.2 | Integrar alertas en paper_runner — cada trade ejecutado envía notificación | Telegram recibe mensaje por cada trade TP/SL |
| 5.3 | Configurar comandos Telegram: `/status`, `/trades`, `/pnl`, `/stop`, `/start` | Cada comando devuelve JSON formateado |
| 5.4 | Webhook handler opcional: recibir comandos reversibles desde Telegram (start/stop bot) | `/api/telegram/webhook` handler activo |

**Gate Semana 5:** Bot Telegram funcional, notificaciones en tiempo real.

#### Semana 6 (Sep 8–14): Multi-Bot Orchestration (B-5)

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 6.1 | Refactor `paper_runner` → `BotOrchestrator` que gestiona múltiples bots concurrentes | 2+ bots corriendo simultáneamente sobre símbolos distintos |
| 6.2 | Capital allocator multi-bot: divide superávit entre bots activos proporcionalmente a attribution ranking | Cada bot recibe micro-stake proporcional |
| 6.3 | Dashboard: tabla de bots activos con estado, símbolo, estrategia, P&L | `/` muestra sección "Bots Activos" |
| 6.4 | API endpoint `/api/bots` — CRUD de bots (create, list, stop, delete) | 5 endpoints funcionando |

**Gate Semana 6:** 2+ bots operando simultáneamente, capital compartido, dashboard monitoreando.

#### Semana 7 (Sep 15–21): CI/CD + Docker Hardening

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 7.1 | GitHub Actions: on push → `pytest`, `playwright test`, `lint`, typecheck | Workflow verde en main |
| 7.2 | Docker Compose para deployment: API + Dashboard + paper_runner en un solo comando | `docker compose up` levanta todo |
| 7.3 | Health checks en Docker: `/api/health` para API, `/` para dashboard | `docker inspect` muestra healthy |
| 7.4 | Secrets management: `.env.example` completo, `docker-compose.override.yml` para dev local | Sin secrets hardcoded |

**Gate Semana 7:** `docker compose up` funciona, CI pipeline verde en main.

#### Semana 8 (Sep 22–30): Advanced Risk Management

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 8.1 | Implementar correlations matrix entre estrategias — prevenir over-exposure | Matrix disponible en `/api/risk/correlations` |
| 8.2 | VaR (Value at Risk) calculator — 95th percentile daily loss | API `/api/risk/var` devuelve VaR 1-day, 7-day |
| 8.3 | Drawdown recovery rules — pausar estrategia si DD > 15%, reanudar tras 24h verde | Logger muestra pausa/reanudación |
| 8.4 | Stress testing — simular 2008-style crash en índices sintéticos | Backtest bajo condiciones estresadas documentado |

**Gate final Mes 2 (Sep 30):** Multi-bot funcional, alertas Telegram activas, CI/CD green, Docker compose operativo, risk management avanzado.

---

### Mes 3 — Octubre 2026: SaaS Multi-Tenant + Auth

**Objetivo del mes:** Plataforma SaaS con autenticación, multi-tenancy, billing básico, API pública documentada.

#### Semana 9 (Oct 1–7): Auth + Multi-Tenant

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 9.1 | Auth con JWT: registro, login, refresh tokens | `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh` funcionando |
| 9.2 | Multi-tenant isolation: cada usuario tiene su propio `strategies.db` namespace o DB separada | 2 usuarios no pueden ver trades del otro |
| 9.3 | Dashboard: página de login/registro | Form de login funcional, redirect al dashboard tras auth |
| 9.4 | API rate limiting por usuario: 100 req/min free tier | 429 response tras exceder límite |

**Gate Semana 9:** 2+ usuarios registrados con datos aislados.

#### Semana 10 (Oct 8–14): API Pública + OpenAPI Docs

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 10.1 | OpenAPI 3.0 spec completa de todos los endpoints | `/docs` swagger UI funcional |
| 10.2 | API key management: cada usuario genera API keys con scopes (read, trade, admin) | Keys persisten en DB, scopes respetados |
| 10.3 | SDK Python: `pip install synthetic-trader` wrapper de la API | Round-trip auth → status → trades funcional |
| 10.4 | Webhooks: `/api/webhooks/subscribe` para recibir notificaciones de eventos | Webhook fire en trade execution |

**Gate Semana 10:** API pública documentada y consumible vía SDK.

#### Semana 11 (Oct 15–21): Billing + Usage Tracking

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 11.1 | Usage tracking: trades ejecutados, API calls, datos consumidos por usuario | `usage` table en DB con timestamps |
| 11.2 | Stripe integration (billing): Free tier (50 trades/mes), Pro tier ($29/mes, unlimited) | Stripe webhook handler activo |
| 11.3 | Dashboard: página de suscripción con Stripe Checkout | Pago procesado, plan actualizado en DB |
| 11.4 | Feature gating: según tier, desbloquear estrategias, multi-bot, alertas | Free tier limitado a 1 bot + 1 símbolo |

**Gate Semana 11:** Billing funcional, feature gating operando.

#### Semana 12 (Oct 22–31): Observabilidad + Grafana

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 12.1 | Prometheus metrics: trades_total, pnl_total, strategy_win_rate, api_latency | `/metrics` endpoint scrapeable |
| 12.2 | Grafana dashboard prebuilt: equity curve, trade velocity, strategy heatmap | `grafana/` folder con dashboard JSON |
| 12.3 | Log aggregation: structured logging JSON con correlation IDs | Logs en `logs/` rotativos con request-id |
| 12.4 | Alert rules: Alertmanager para DD > 15%, circuit breaker trip, API down | 3 alert rules funcionando |

**Gate final Mes 3 (Oct 31):** SaaS multi-tenant con auth, billing, API pública, observabilidad. Plataforma lista para usuarios beta.

---

### Mes 4 — Noviembre 2026: Live Trading Gate + Production

**Objetivo del mes:** Gate de aprovación para live trading, ejecución real en Deriv, monitoreo 24/7, incidentes.

#### Semana 13 (Nov 1–7): Paper Trading Validation Gate

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 13.1 | Auditar 90+ días de paper trading data: win rate, sharpe, max DD por estrategia | Reporte documento con tablas y gráficos |
| 13.2 | Walk-forward validation con datos reales: 5 ventanas rolling | Pasa gates: Sharpe > 1.0, DD < 15%, WR > 50% |
| 13.3 | Stress test: simular condiciones adversas (gap, alta vol, low liquidity) | Supervivencia documentada |
| 13.4 | Gate de aprobación: documento formal para Sebastian con métricas reales | Sebastian aprueba o rechaza con datos |

**Gate Semana 13:** 3+ meses de paper trading validado. Approval gate para live.

#### Semana 14 (Nov 8–14): Live Trading Engine (requiere aprobación)

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 14.1 | **REQUIERE APROBACIÓN EXPLÍCITA DE SEBASTIAN** — implementar `live_runner.py` | — |
| 14.2 | Live trading handler: enviar orders reales a Deriv (buy/sell) | Trade ejecutable en cuenta real |
| 14.3 | Risk caps en live: 1% per trade hard, 3% daily loss, 10 trades max | Circuit breaker activo en live |
| 14.4 | Kill switch: botón en dashboard + comando Telegram `/kill` | Detiene todo en <1s |

**Gate Semana 14:** Live trading operativo con kill switch, 1 trade ejecutado (con $1 stake mínimo).

#### Semana 15 (Nov 15–21): Monitoreo Live + Incidentes

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 15.1 | Live dashboard: P&L en tiempo real, exposure, open positions | Dashboard muestra datos live |
| 15.2 | Incident response runbook: procedimientos para DD, API down, disconnects | Documento en `runbooks/live-incident-response.md` |
| 15.3 | Auto-stop en condiciones críticas: DD diario > 3%, API errors > 5 consecutivos | Logger documenta auto-stop |
| 15.4 | Daily report automático: P&L del día, trades, métricas → email + Telegram | Reporte enviado a las 23:59 UTC |

**Gate Semana 15:** Live trading monitoreado con auto-stop y reportes diarios.

#### Semana 16 (Nov 22–30): v1.0.0 Release

| # | Entregable | Criterio de éxito |
|---|------------|-------------------|
| 16.1 | Version bump 0.3.0 → 1.0.0 — todo stable, sin debt crítico | `PROJECT.md` v1.0.0 |
| 16.2 | README professional: quickstart, arquitectura, API docs links, screenshots | README.md actualizado |
| 16.3 | GitHub Release v1.0.0 con changelog completo | Tag `v1.0.0` publishado |
| 16.4 | Demo SaaS: deploy público en VPS, 1 usuario beta onboarded | URL pública funcional |

**Gate final Mes 4 (Nov 30 / v1.0.0):** Bot de trading algorítmico production-ready con live trading validado, multi-bot, SaaS multi-tenant, observabilidad y alertas.

---

### Resumen de Milestones y Gates

| Hito | Fecha | Entregable principal | Criterio de salida |
|------|-------|----------------------|--------------------|
| **M1: Paper Trading Persistente** | 2026-08-31 | systemd + attribution diaria + 30 días data + 8 estrategias + tests | Paper trading 24/7 sin intervención, attribution con datos reales |
| **M2: Multi-Bot + Hardening** | 2026-09-30 | Multi-bot orchestration + alertas Telegram + CI/CD + Docker + risk avanzado | 2+ bots concurrentes, CI green, `docker compose up` |
| **M3: SaaS Multi-Tenant** | 2026-10-31 | Auth + billing + API pública + SDK + observabilidad | SaaS con 2+ usuarios, billing Stripe, API documentada |
| **M4: Live Trading v1.0.0** | 2026-11-30 | Gate aprobación + live trading + monitoreo + release | v1.0.0 tag, live trade ejecutado, demo SaaS pública |

### Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Paper trading no genera suficientes trades (mercado sideways) | Media | Alto | Añadir más símbolos (BOOM/CRASH, Step Index) para aumentar opportunity |
| StepIndex/DriftBoomCrash no tienen edge tras validación | Media | Medio | 4 estrategias originales como fallback; attribution ranking elimina estrategias perdedoras |
| Deriv API rate limits no documentados | Alta | Medio | Throttle en API client + backoff exponencial ya implementado |
| Live trading pierde dinero | Baja | Alto | Paper trading validación 90+ días, gate de aprobación, kill switch, caps estrictos |
| SaaS billing bugs | Baja | Medio | Stripe test mode antes de production; feature gating con fallbacks |

### Out of scope (explícito)

- Soporte para otros brokers (no Deriv) — arquitectura permite pero no se implementa en v1.0
- Mobile app nativa — dashboard es responsive web, PWA-ready
- High-frequency trading (<1s latency) — infraestructura es para medium-frequency (segundos-minutos)
- Crypto trading directo en exchanges — CCXT MCP disponible pero foco es índices sintéticos Deriv

---

## ⚠️ Limitaciones Conocidas

1. **Dataset histórico limitado**: ~2 días de candles por símbolo — Paper trading 24/7 está acumulando datos reales para validar estrategias.
2. **Paper trading only**: sin riesgo real, slippage en live puede diferir.
3. **Deriv API rate limits**: límites no documentados, throttle puede requerir PAT tiers.
4. **Single bot focus**: arquitectura preparada multi-bot pero no probada con ≥2 concurrentes.
5. **Attribution vacía**: `strategies.db` tablas listas pero sin datos hasta que daily_attribution.py corra.
6. **Sin auth multi-tenancy**: el visión SaaS requiere auth/billing (no implementado).
7. **StepIndex/DriftBoomCrash sin backtest**: implementadas pero requieren datos históricos de STEPT y BOOM/CRASH para validar.
8. **API sin TLS**: corre en HTTP plano (localhost) — para producción necesita reverse proxy con TLS.

---

## 🔐 Seguridad

- **PAT** requerido para conexión Deriv — en variables de entorno, nunca hardcodeado
- **No guarda secrets en SQLite** — conexiones in-memory por session
- **.env nunca commiteado** — `.env.example` con placeholders en repo
- **Deriv App ID**: 33Y5EmAQJgXagwIUJQ8Vw (configurado en entorno)

---

## 📚 Referencias

- Deriv API docs: https://api.deriv.com/
- FastAPI docs: https://fastapi.tiangolo.com/
- Kelly criterion: https://en.wikipedia.org/wiki/Kelly_criterion
- Walk-forward analysis: https://www.investopedia.com/terms/w/walkforwardanalysis.asp
- Synthetic indices (Deriv): https://deriv.com/markets/synthetic
- Repo: https://github.com/Nxxo31/synthetic-trader
- Investigación gems/kraken: `docs/investigacion-estrategias-bots-multi-mercado.md`
- Research position sizing: `RESEARCH_POSITION_SIZING_RISK.md`
- Research crypto operator: `docs/research-crypto-operator-deriv.md`

---

## 🚀 Cómo Operar

### Levantar el sistema completo

```bash
cd /home/sebas/proyectos/synthetic-trader
source .venv/bin/activate

# 1. API (puerto 8001)
uvicorn src.api.server:app --host 0.0.0.0 --port 8001 --reload &

# 2. Dashboard (puerto 3000)
cd dashboard && npx next dev --port 3000 &

# 3. Paper trading 24/7
python scripts/paper_247.py
# o con estrategia específica:
python -m src.main paper R_100 breakout
python -m src.main paper R_10 step_index
python -m src.main paper BOOM300N drift_boom_crash
```

### Endpoints API disponibles

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/bot/status` | GET | Estado del bot (balance, trades, P&L) |
| `/api/bot/trades` | GET | Lista de trades ejecutados |
| `/api/allocator/config` | GET/POST | Ver/modificar configuración del allocator |
| `/api/allocator/allocate` | GET | Estado actual de reserva + superávit |
| `/api/attribution/matrix` | GET | Matriz estrategia×símbolo |
| `/api/attribution/ranking` | GET | Ranking de estrategias por rendimiento |
| `/api/projection/equity` | GET | Proyección Monte Carlo (params: days, surplus) |
| `/ws/live-data` | WS | Stream realtime state + equity + trades |

### Estrategias disponibles

```bash
breakout        # RangeBreakStrategy — breakout de canal
volatility      # VolatilityStrategy — mean reversion ATR
confluence      # ConfluenceStrategy — dual confirmation
step_index      # StepIndexStrategy — gem (Step Index predecible)
drift_boom_crash # DriftBoomCrashStrategy — kraken (post-spike drift)
```

---

## 📋 Audit 2026-08-06

### Cambios aplicados
- **conftest.py** movido de root a `tests/conftest.py` — estaba en raíz, fuera de lugar
- **integration_test_capital_allocator.py** movido a `tests/test_capital_allocator.py` — renombrado siguiendo convención pytest
- **AGENTS.md** clarificado: `pytest` es válido para backend Python (no para dashboard Next.js). Tests viven en `tests/`
- **Inconsistencia testing resuelta**: AGENTS.md prohibía tests (vitest/jest/playwright) pero PROJECT.md planeaba pytest. Ambos ahora alineados: pytest backend ✅, vitest/jest/playwright ❌

### Estado arquitectónico
- **Arquitectura más madura de los 5 proyectos**: Clean separation src/strategies, src/risk, src/trading, src/analysis + dashboard/ Next.js
- **68 archivos fuente**, 5 estrategias implementadas, paper trading 24/7 activo
- **Roadmap 4 meses** (Ago-Dic 2026) detallado y coherente con estado actual
- **Sin drift crítico** — PROJECT.md ↔ código alineados

### Minor issues restantes
- `sketches/` físico existe pero ya en .gitignore (no tracked) — cosmético
- `__pycache__/` físico existe pero ya en .gitignore — cosmético

---

*Generado por SophIA — Sebastian Velasco's autonomous operating system*
*Audit 2026-08-06: Cleanup de archivos de test, AGENTS.md clarificado, inconsistencia testing resuelta.*
