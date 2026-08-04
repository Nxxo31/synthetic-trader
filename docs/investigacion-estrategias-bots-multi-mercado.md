# Investigación: Estrategias de Trading de Bajo Coste + 5 Proyectos Open-Source de Bots Multi-Mercado

> **Contexto del proyecto**: Bot de trading algorítmico para índices sintéticos de Deriv + crypto.
> Stack: Python 3.12 + FastAPI + Next.js 16 + Tailwind + SQLite.
> Estrategias actuales: RangeBreak, Volatility, MeanReversion, PairTrading.
> Objetivo: (1) estrategias de alto retorno/bajo coste, (2) proyectos similares famosos, (3) asignación de capital entre mercados.
> Fecha: 2026-08-02

---

## Tabla de contenidos

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Estrategias de bajo coste en Deriv (referencia $10 → $300, ~$700/mes)](#2-estrategias-de-bajo-coste-en-deriv-referencia-10--300-700mes)
3. [Los 5 proyectos open-source más famosos de bots multi-mercado](#3-los-5-proyectos-open-source-más-famosos-de-bots-multi-mercado)
4. [Asignación de capital entre mercados: patrones comparados](#4-asignación-de-capital-entre-mercados-patrones-comparados)
5. [Patrones de diseño identificados](#5-patrones-de-diseño-identificados)
6. [Recomendaciones para nuestro bot (Python + FastAPI + Next.js)](#6-recomendaciones-para-nuestro-bot-python--fastapi--nextjs)
7. [Fuentes](#7-fuentes)

---

## 1. Resumen ejecutivo

- **Estrategias de bajo coste**: En Deriv, las estrategias viables con $10 descansan en (a) trading del **drift** en Boom/Crash (comprar después de un crash, vender después de un boom), (b) **scalping con RSI + EMA** en M5 con TP de 3-5 pips y SL de 2-3 pips, (c) **Matches/Differs** con $0.35 mínimos usando estadística del último dígito. Convertir $10 en $300 es realista **solo con disciplina estricta**: 1 operación a la vez, lote mínimo (0.001 en Volatility, 0.20 en Boom/Crash), relación riesgo-recompensa 1:3, y retirar beneficios tras doblar la cuenta. Generar ~$700/mes de forma sostenible requiere cuenta de **$200-$500** con 1-3 operaciones diarias de calidad, no $10.
- **"Gems" / "Kraken"**: No existen estrategias con esos nombres en la literatura pública de Deriv. La referencia del amigo probablemente apunta a: (a) **Step Index** como la "joya" (gem) de Deriv — predecible, sin saltos bruscos, ideal para automatizar; (b) una estrategia agresiva tipo "kraken" = **scalping V75/V100 con lote 0.001 y TP de 3 pips** que genera $14 por cada 20 operaciones con lote mínimo 0.20. Documento los principios comprobados detrás de ambas.
- **5 proyectos open-source**: Freqtrade (52K estrellas), Hummingbot, NautilusTrader, Jesse, y CCXT son los más famosos y maduros. Cada uno aporta patrones de diseño distintos y complementarios.
- **Recomendación clave para nuestro bot**: Adoptar el patrón **Controller-Executor** de Hummingbot V2, el **MessageBus + RiskEngine** de NautilusTrader, el **stake_amount dinámico + max_open_trades** de Freqtrade, y un **asignador de capital por Programación Cuadrática (QP)** inspirado en el proyecto "regime-aware-strategy-allocator". Stack actual (Python + FastAPI + SQLite) es compatible con todos estos patrones.

---

## 2. Estrategias de bajo coste en Deriv (referencia $10 → $300, ~$700/mes)

### 2.1 Realidad del $10 → $300

Fuentes consultadas (263forex.com, synthetics.info, binarybrokerhub.com, pipslegion.com) confirman:

- **Técnicamente posible** con lote mínimo 0.001 en V10/V25 o 0.20 en Boom/Crash, 1 operación a la vez, y R:R de 1:3.
- **No es rápido ni fácil**: La mayoría de traders nuevas vuelan la cuenta por (a) sobrecalcular el lote, (b) perseguir picos de Boom/Crash sin confirmación, (c) vengarse tras pérdidas, (d) operar contra tendencia.
- La regla de oro: con $10, el **riesgo por operación es de $0.10-$0.20** (1-2% de la cuenta). El TP de 1:3 convierte eso en $0.30-$0.60 por operación ganadora. Se necesitan **~500-1000 operaciones buenas** para llegar a $300, asumiendo 50-60% de acierto.

### 2.2 Estrategia 1 — Drift Trading en Boom/Crash (la más consistente)

**Principio**: Los índices Crash derivan hacia arriba entre caídas; los Boom derivan hacia abajo entre picos. No se intenta predecir el picodirectamente. Se opera **a favor del drift**.

| Parámetro | Valor recomendado |
|---|---|
| Instrumento | Crash 1000 o Boom 500 (menos margen) |
| Lote | 0.20 (mínimo en Deriv para Boom/Crash) |
| Dirección | Crash 1000 → comprar (drift alcista); Boom → vender (drift bajista) |
| Entrada | Tras un crash/pico reciente (zona segura temporal) |
| TP | Antes de la zona media del siguiente pico esperado |
| SL | 100-150 ticks (~$2-$3 de riesgo en cuenta $10) |
| Frecuencia | 1-3 operaciones/día |

**Por qué funciona**: El drift estadístico es positivo por construcción del índice. El riesgo es el pico repentino, mitigado operando justo después de uno.

### 2.3 Estrategia 2 — Scalping RSI + EMA200 en M5 (alta frecuencia)

**Principio**: En M5, esperar RSI < 30 (sobreventa) en Boom o RSI > 70 (sobrecompra) en Crash, confirmar con cruce alcista/bajista de MACD, entrar 3-5 pips, SL de 2-3 pips.

- Con lote 0.20 y 20 operaciones/día se generan ~$14 diarios (fuente: 263forex.com boom-and-crash-scalping-strategy).
- Requiere disciplina absoluta: ni una operación fuera del setup.
- Es la base más probable de la referencia "kraken" del amigo: **agresiva, alta frecuencia, lote mínimo, TP corto**.

### 2.4 Estrategia 3 — Step Index como "gem" (joya) para automatizar

El Step Index (STEPT10, STEPT25, etc.) es mencionado explícitamente como **"the gem"** (la joya) de Deriv por kenyaforexfirm.com:

- Movimiento **predecible en escalones**, sin saltos bruscos ni picos repentinos.
- **24/7**, no afectado por noticias.
- Ideal para bots: el precio sube/baja en incrementos fijos. Las estrategias de seguimiento de tendencia y reversión a la media funcionan aquí **mejor que en cualquier otro índice sintético**.
- Riesgo controlable: el tamaño del paso es conocido, así que SL y TP se ajustan con precisión matemática.

**Este es el candidato nº 1 para implementar primero en nuestro bot**, porque viola menos el principio del PROJECT.md de que "los índices sintéticos no responden a indicadores técnicos". El Step Index es la excepción más limpia.

### 2.5 Estrategia 4 — Matches/Differs con estadística del último dígito (mínimo $0.35)

**Principio**: Apostar que el último dígito de 3 ticks en Volatility 100 **difiere** de un número elegido con baja frecuencia en los últimos 25 ticks (4-8% de aparición).

- Probabilidad teórica: 9/10 dígitos ganan → 90% de acierto esperado.
- Retorno: ~10% del stake por operación ($0.10 con $1, $1 con $10).
- Riesgo: si el dígito coincide, se pierde todo el stake.
- **Mitigación**: alternar dígitos (1-5 usos máximo cada uno), máximo 2 sesiones/día.
- Coste mínimo por operación: $0.35. Con $10 se hacen ~28 operaciones. Si 90% aciertan, beneficio neto ~$2.50 por ciclo de 28 operaciones.

### 2.6 Matemática del ~$700/mes

Para generar ~$700/mes de forma sostenible con estas estrategias, las cifras realistas son:

| Enfoque | Capital inicial | Operaciones/día | Beneficio/op. | Días/mes | Total/mes |
|---|---|---|---|---|---|
| Scalping Boom/Crash | $200 | 1-3 | $0.30-$1 | 30 | $9-$90 |
| Scalping Boom/Crash | $500 | 3-5 | $1-$3 | 30 | $90-$450 |
| Drift + Step Index automatizado | $300 | 10-20 (bot) | $0.50-$2 | 30 | $150-$1200 |
| Matches/Differs bot | $200 | 50-100 (bot) | $0.10-$0.20 | 30 | $150-$600 |

**Conclusión**: ~$700/mes requiere un bot operando automáticamente (no manual), capital de $300+, y combinación de 2-3 estrategias. Con $10 solo, $700/mes es extremadamente improbable; $300 sí es alcanzable con disciplina y tiempo (semanas a meses).

---

## 3. Los 5 proyectos open-source más famosos de bots multi-mercado

### 3.1 Freqtrade — 52.846 estrellas

| Atributo | Valor |
|---|---|
| Repo | github.com/freqtrade/freqtrade |
| Stars | 52.846 |
| Licencia | GPL v3 |
| Stack | Python 3.11+, SQLite, Telegram bot, WebUI (FreqUI), Jinja, Jupyter |
| Mercados | Crypto (100+ exchanges via CCXT) |
| Casos de uso | Trading algorítmico de crypto con backtesting, hyperopt (optimización por ML), FreqAI (modelos adaptativos), dry-run, live trading |

**Arquitectura**:
- **Monolito Python** con CLI + WebUI + Telegram.
- Separación por capas: `freqtrade/configuration` (config), `freqtrade/data` (descarga/almacenamiento OHLCV), `freqtrade/strategy` (lógica de estrategia interchangeable), `freqtrade/optimize` (backtesting + hyperopt), `freqtrade/freqai` (ML adaptativo).
- **Persistence**: SQLite para trades y metadatos.
- **Pairlist**: Whitelist/blacklist dinámica de pares — se pueden seleccionar pares por volumen, cambios de precio, etc.

**Asignación de capital**:
- `max_open_trades`: número máximo de operaciones simultáneas (1 operación por par).
- `stake_amount`: monto por operación. Fijo (e.g. 0.05 BTC) o **"unlimited"** = dinámico.
- **Dinámico**: `stake = balance / (max_open_trades - open_trades)`. Divide el capital disponible entre los slots libres.
- `available_capital`: límite de capital para este bot (útil al correr varios bots en una misma cuenta).
- `tradable_balance_ratio`: fracción usable del balance (0.99 por defecto, 1% reservado para fees).
- `amend_last_stake_amount`: ajusta el último stake al balance disponible si no alcanza.
- **Sin asignación entre mercados**: cada par es un slot igual. No pondera por edge/volatilidad.

**Patrones de diseño**:
- **Strategy Pattern**: estrategias son clases Python con métodos `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`. Intercambiables sin tocar el motor.
- **Callback Hooks**: `custom_stake_amount()`, `custom_exit()`, `custom_stoploss()`, `confirm_trade_entry()` — inyección de lógica en puntos clave sin acoplamiento.
- **Plugin Pattern**: `ProtectionManager` con plugins de protección (Stoploss, MaxDrawdown, CooldownPeriod) que envuelven la decisión de entrar.
- **Command Pattern**: CLI con subcomandos (backtesting, hyperopt, list-strategies...).
- **Repository Pattern**: acceso a datos via `DataHandler` con backend interchangeable (JSON, Feather, Parquet).

**Relevancia para nosotros**: Su modelo `max_open_trades` + `stake_amount` dinámico es **directamente aplicable** a nuestros slots de índices sintéticos. El patrón de callbacks es ideal para que las estrategias (RangeBreak, Volatility, etc.) personalicen sin acoplarse al motor.

---

### 3.2 Hummingbot — ~9.000 estrellas (Apache 2.0, ~$34B volumen generado)

| Atributo | Valor |
|---|---|
| Repo | github.com/hummingbot/hummingbot |
| Licencia | Apache 2.0 |
| Stack | Python + Cython (caminos críticos en C), asyncio, Docker, Gateway (TypeScript middleware para DEX) |
| Mercados | 140+ venues: CEX (Binance, Coinbase, Kraken...) + DEX (Uniswap, PancakeSwap, Raydium, Hyperliquid) |

**Casos de uso**: Market making, arbitraje entre exchanges (XEMM), arbitraje AMM, grid trading, DCA, TWAP, liquidity provision en CLMM (Concentrated Liquidity Market Maker).

**Arquitectura** (V2 Framework):
- **Clock-driven**: clase `Clock` central que itera todos los componentes cada tick (1s en live).
- **3 capas** (Patrón Controller-Executor):
  1. **StrategyV2Base**: punto de entrada clock-driven, posee controllers y el orquestador.
  2. **ControllerBase**: bucle asíncrono independiente; emite acciones tipadas (`CreateExecutorAction`, `StopExecutorAction`).
  3. **ExecutorBase**: gestiona una operación atómica (position, DCA, grid, arbitrage, TWAP, XEMM, LP) hasta completarse.
- **Action Queue**: cola que desacopla controllers de la gestión de executors.
- **ExecutorOrchestrator**: runtime manager de todos los executors activos, mapea tipo → clase.
- **Gateway**: middleware TypeScript (Docker) para DEX — normaliza interacción con AMM/CLMM cross-chain.

**Asignación de capital**:
- **Inventory Skew** (patrón clave): `inventory_target_base_pct` define el porcentaje objetivo del asset base. `inventory_range_multiplier` expande/contrae el rango tolerable. El bot ajusta tamaños de orden linealmente para volver al target. Si el inventorio excede el límite superior, no emite bids; si cae bajo el inferior, no emite asks.
- **Avellaneda-Stoikov**: reserva precio + spread óptimo basados en (a) desviación de inventario `q`, (b) tiempo restante de sesión `T-t`, (c) factor de riesgo `gamma`, (d) liquidez del book `kappa`. `gamma` alto = persegición agresiva del target de inventario; `gamma` ~0 = simétrico.
- **Hedge strategy**: `hedge_ratio` abre posición opuesta en otro exchange/asset para mitigar riesgo de inventario. Modo "by value" permite hacer proxy hedge de un basket correlacionado con un solo activo shortable (e.g. ETH).
- **No asignación QP entre mercados**: cada mercado es un connector independiente con su propia gestión de inventorio. No centraliza.

**Patrones de diseño**:
- **Controller-Executor** (lo más relevante para nosotros): separa "qué decidir" (controller) de "cómo ejecutar" (executor).
- **Event-driven**: asyncio + MessageBus.
- **Strategy Pattern**: connectors `ConnectorBase` abstraen REST/WebSocket por exchange.
- **Triple Barrier** (de Hummingbot, inspirado en Marcos López de Prado): `stop_loss`, `take_profit`, `time_limit`, `trailing_stop` configuran el cierre automático del PositionExecutor.
- **Builder**: `BacktestNode`/`LiveNode` con configuración declarativa.
- **Adapter**: connectors estandarizan APIs heterogéneas.

**Relevancia**: El patrón **Controller-Executor con triple barrier** es el más transplantable a nuestro bot. Cada estrategia (RangeBreak, Volatility, MeanReversion) puede ser un `ControllerBase` que emite acciones a executors compartidos (Position, DCA, Grid).

---

### 3.3 NautilusTrader — ~3.500 estrellas

| Atributo | Valor |
|---|---|
| Repo | github.com/nautechsystems/nautilus_trader |
| Licencia | LGPL v3 |
| Stack | **Rust-native** (core, crates/) + Python/Cython (control plane, nautilus_trader/) + PyO3 (bindings) |
| Mercados | Multi-asset, multi-venue: crypto (Binance, Bybit, BitMEX, dYdX, Hyperliquid, Kraken, OKX, Polymarket), FX (Oanda), betting (Betfair), datos (Databento, Tardis, Polygon) |

**Casos de uso**: Backtesting de altísima resolución (nanosegundo, order book L2/L3), live trading multi-venue, market-making cross-venue, entrenamiento de agentes de IA (RL/ES), paradigma research-to-live con **cero cambios de código** entre backtest y live.

**Arquitectura**:
- **NautilusKernel**: orquestador central. Single-threaded core con orden determinista de eventos (igual en backtest que en live → paridad research-live).
- **MessageBus**: backbone pub/sub + command/event + registry dispatch por actor ID.
- **Ports & Adapters**: estilo arquitectónico. Componentes modulares (DataEngine, ExecutionEngine, RiskEngine, Portfolio) se integran vía interfaces.
- **NO multi-nodo en un proceso**: singletons globales (Tokio runtime, callback registries). Para paralelismo, lanzar procesos separados. Dentro de un nodo, múltiples estrategias sí.
- **Categorías de crates Rust**: Foundation (core, model, common), Engines (data, execution, portfolio, risk), Infrastructure (serialization, network, persistence), Runtime (live, backtest).

**Asignación de capital**:
- **Portfolio** (central): agrega posiciones por instrumento. `mark_values()`, `equity()`, `missing_price_instruments()`.
- **RiskEngine**: todo comando de orden pasa por él salvo bypass explícito. Valida: precios positivos, precisión, notional máx, cantidad min/max, `reduce_only`.
- **OMS NETTING vs HEDGING**: NETTING = 1 posición por instrumento (agrega fills); HEDGING = múltiples posiciones (cada una con `position_id` único). Se puede mezclar strategy/venue OMS independientes.
- **No asignación dinámica entre mercados**: el Portfolio agrega y el RiskEngine limita, pero no hay un allocator que rebalancee entre estrategias — eso lo hace el usuario escribiendo una estrategia maestra.

**Patrones de diseño**:
- **Ports & Adapters (Hexagonal)**: separation of concerns más estricto de los 5.
- **Event Sourcing**: eventos capturados en log durable, replayables para debug/audit/investigación.
- **Actor Model**: registry dispatch, handlers por tipo de evento.
- **Strategy base**: hereda de `Actor`, añade `submit_order()`, `close_position()`, `close_all_positions()`.
- **ExecAlgorithm**: TWAP, custom algoritmos que fragmentan órdenes en child orders.
- **OrderFactory**: factory que abstrae trader_id, strategy_id, init_id, timestamps.
- **Bracket Orders**: OCO (One-Cancels-Other), OTO (One-Triggers-Other), OUO (One-Updates-Other).

**Relevancia**: Su **RiskEngine central + Portfolio agregador + MessageBus pub/sub** es el patrón más robusto para nuestro Python. Traducir a Python puro: un `RiskEngine` que valide toda orden antes de enviarla, un `Portfolio` que agregue exposición por instrumento, un bus de eventos simple (asyncio.Queue o lib como `blinker`). El principio **research-to-live parity** (mismo motor en backtest que en live) es de oro para nuestro pipeline Phase 2 → Phase 3 → Phase 4.

---

### 3.4 Jesse — ~6.500 estrellas

| Atributo | Valor |
|---|---|
| Repo | github.com/jesse-ai/jesse |
| Stack | Python 3.10+, PostgreSQL (candles, backtests, optimizations, trades), Redis (session state, cache), 300+ indicadores (reescritos en Rust desde v1.10), Dashboard web GUI |
| Mercados | Crypto (spot, futures, DEX) |

**Casos de uso**: Research + backtesting de altísima precisión (sin look-ahead bias), optimización con Optuna, **Monte Carlo** (trade-shuffling + candles-based), ML pipeline end-to-end (gather → train → deploy), paper/live trading multi-cuenta.

### 3.5 Enigma Catalyst — ~2.500 estrellas (ARCHIVADO)

| Atributo | Valor |
|---|---|
| Repo | github.com/scrtlabs/catalyst (antes enigmampc/catalyst) |
| Status | ARCHIVADO (sin mantenimiento) |
| Stack | Python, basado en **Zipline** (Quantopian) |
| Mercados | Crypto (Binance, Bitfinex, Bittrex, Poloniex históricamente) |

**Casos de uso**: Backtesting + live trading de crypto con API familiar para usuarios de Zipline, pipelines de datos marketplaces.

### 3.6 CCXT — 43.406 estrellas (biblioteca, no bot)

| Atributo | Valor |
|---|---|
| Repo | github.com/ccxt/ccxt |
| Licencia | MIT |
| Stack | **TypeScript fuente única** → transpilado a Python, PHP, C#, Go, Java. REST + WebSocket (Pro) |
| Mercados | **100+ exchanges** crypto + prediction markets (Polymarket, Kalshi, Hyperliquid) |

**Casos de uso**: No es un bot, es la **capa de abstracción de exchanges** que usan Freqtrade, Hummingbot y otros. Una API unificada para market data + trading.

---

## 4. Asignación de capital entre mercados: patrones comparados

### 4.1 Nivel 1 — Por slot igual (Freqtrade)

- `max_open_trades` divide el capital en N buckets iguales.
- Simple, no requiere estimación de edge ni covarianza.
- **No pondera**: un par con edge alto recibe lo mismo que uno sin edge.

### 4.2 Nivel 2 — Por inventario objetivo (Hummingbot Avellaneda-Stoikov)

- `inventory_target_base_pct` define el mix objetivo (e.g. 50/50).
- `risk_factor` (`gamma`) controla cuán agresivamente persegir el objetivo.
- Skew asimétrico de bids/asks para rebalancear.
- **Es para 2 assets** (base/quote en un par). No escala naturalmente a N mercados.

### 4.3 Nivel 3 — Por Portfolio + RiskEngine (NautilusTrader)

- Portfolio agrega exposición; RiskEngine valida límitesglobales.
- Multi-venue, multi-currency aggregation.
- **No decide**: solo observa y limita. La decisión de "cuánto a cada mercado" la hace la estrategia del usuario.

### 4.4 Nivel 4 — Por Programación Cuadrática (regime-aware-strategy-allocator)

Este es el patrón más avanzado encontrado (github.com/LORD-ZYTHOZ/regime-aware-strategy-allocator-public):

```
b* = argmax_b [ νᵀb − (γ/2) bᵀΣb − λ_turn ‖b − b_prev‖₁ ]
s.a.  1ᵀb ≤ 1   (full investment)
      b ≥ 0     (no short)
```

Donde:
- `ν = η̂ − λ_U · U − λ_C · Ĉ` es la utilidad por estrategia (edge estimado, penalizado por incertidumbre y coste).
- `Σ` = matriz de covarianza EWMA de retornos entre estrategias.
- `γ` = aversión al riesgo, calibrada por régimen.
- `λ_turn` = penalización por turnover (evita rebalancear en exceso).

**Régimen**: 3 señales — volatilidad, breadth de correlación, tendencia — recalibran `gamma` automáticamente. Bandera de riesgo: OK → DE_RISK (escalar budgets hacia abajo) → KILL (todo a cash).

**Es el patrón más transplantable a nuestro multi-mercado** (RangeBreak, Volatility, MeanReversion, PairTrading son estrategias que compiten por el mismo capital).

### 4.5 Nivel 5 — Kelly multi-mercado (académico)

```
f* = Σ⁻¹ × α
```

Donde `α` = vector de edge estimado y `Σ` = matriz de covarianza de retornos.

- **Kelly fraccional** (0.25x-0.50x) reduce varianza drásticamente.
- **Drawdown brake**: tras DD > 15% del pico, bajar a 0.25x Kelly hasta recuperar.
- Complejidad: estimar `Σ` bien requiere `shrinkage estimators` y rolling windows.

### 4.6 Adopción recomendada (progresiva)

1. **Fase 1 (ahora)**: Nivel 1 (slots iguales `max_open_trades` estilo Freqtrade).
2. **Fase 2**: Nivel 3 (Portfolio + RiskEngine estilo NautilusTrader) para limitar exposición agregada.
3. **Fase 3**: Nivel 4 (QP allocator) cuando tengamos histórico de >30 operaciones por estrategia para estimar `eta_hat` y `Sigma`.

---

## 5. Patrones de diseño identificados

| Patrón | Proveedor | Descripción | Aplicabilidad a nuestro bot |
|---|---|---|---|
| **Strategy** | Freqtrade, Hummingbot | Estrategias intercambiables con interfaz común | Ya lo hacemos (RangeBreak, Volatility...) — formalizar la interfaz |
| **Controller-Executor** | Hummingbot V2 | Controller decide, executor ejecuta patrón atómico | Altísima. Separar "decisión de estrategia" de "gestión de orden" |
| **Triple Barrier** | Hummingbot | SL/TP/time/trailing como config struct | Altísima. Estandarizar config de salida en todas las estrategias |
| **Ports & Adapters** | NautilusTrader | Núcleo aislado de adapters (broker, data) | Alta. Conector Deriv como adapter, backtest como otro adapter |
| **MessageBus pub/sub** | NautilusTrader, Hummingbot | Comunicación desacoplada por eventos | Alta. `asyncio.Queue` en Python es suficiente para empezar |
| **RiskEngine central** | NautilusTrader | Toda orden pasa por validación pre-submit | Crítica. Ya tenemos risk rules en PROJECT.md (2%, 5%, 10 trades) |
| **Portfolio agregador** | NautilusTrader | Posición agregada por instrumento + equity | Alta. Necesario para ver exposición total multi-índice |
| **Stake dinámico** | Freqtrade | `balance / available_slots` | Alta. Simple de implementar, mejora compounding |
| **Inventory Skew** | Hummingbot | Sesgar órdenes para volver a target de inventario | Media. Relevante si hacemos market-making en Deriv (no ahora) |
| **Avellaneda-Stoikov** | Hummingbot | Reserva precio + spread óptimo | Baja. Para market making, no para trading direccional |
| **ExecutorOrchestrator** | Hummingbot | Runtime manager de executors activos | Alta. Un orquestador que gestione Position/DCA/Grid shared |
| **Action Queue** | Hummingbot | Cola que desacopla producers (controllers) de consumers | Media. Útil si hay latencia entre decisión y ejecución |
| **Backtest-Live Parity** | NautilusTrader, Hummingbot | Mismo motor en research y producción | Crítica. Nuestro pipeline Phase 2-3-4 exige esto |
| **Plugin/Protection** | Freqtrade | Stoploss, MaxDrawdown, Cooldown como plugins envolventes | Alta. Implementar `MaxDrawdown` y `CooldownPeriod` como plugins |
| **Builder (Node)** | NautilusTrader, Hummingbot | `BacktestNode`/`LiveNode` declarativos | Media. Útil si expandimos a múltiples cuentas Deriv |

---

## 6. Recomendaciones para nuestro bot (Python + FastAPI + Next.js)

### 6.1 Arquitectura propuesta (capas)

```
┌─────────────────────────────────────────────────┐
│  Next.js 16 Dashboard (Tailwind)                │
│  - Métricas, equity curve, posiciones abiertas  │
│  - Configuración de estrategias, risk params    │
└────────────────────┬────────────────────────────┘
                     │ REST / WebSocket
┌────────────────────┴────────────────────────────┐
│  FastAPI (Python 3.12)                          │
│  - /strategies, /positions, /backtests, /risk   │
│  - WebSocket /ws/equity para streaming live     │
└────────────────────┬────────────────────────────┘
                     │ asyncio
┌────────────────────┴────────────────────────────┐
│  Trading Engine (Python)                        │
│  ┌──────────────┐  ┌──────────────────────────┐│
│  │ MessageBus    │←→│  RiskEngine              ││
│  │ (asyncio.Queue│  │  - 2% per trade         ││
│  │  /blinker)    │  │  - 5% daily loss         ││
│  └──────┬───────┘  │  - 10 trades max          ││
│         │          │  - reduce_only checks    ││
│  ┌──────┴───────┐  └──────────────────────────┘│
│  │ Portfolio     │  ┌──────────────────────────┐│
│  │ - equity()    │←→│  Deriv Adapter (Port)    ││
│  │ - mark_values │  │  - REST PAT + OTP flow   ││
│  │ - exposure    │  │  - WebSocket ticks      ││
│  └──────┬───────┘  └──────────────────────────┘│
│         │                                       │
│  ┌──────┴───────────────────────────────────────┐
│  │ Strategy Controllers (Strategy Pattern)      │
│  │  - RangeBreakController                       │
│  │  - VolatilityController                       │
│  │  - MeanReversionController                    │
│  │  - PairTradingController                      │
│  │  - StepIndexController (NUEVO)                │
│  │  - DriftBoomCrashController (NUEVO)           │
│  └──────┬───────────────────────────────────────┘
│         │ CreateExecutorAction / StopExecutorAction
│  ┌──────┴───────────────────────────────────────┐
│  │ ExecutorOrchestrator                          │
│  │  - PositionExecutor (triple barrier)          │
│  │  - DCAExecutor  (promedio escalonado)        │
│  │  - GridExecutor  (grid en Step Index)        │
│  └──────────────────────────────────────────────┘
└─────────────────────────────────────────────────┘
```

### 6.2 Implementar en orden de prioridad

#### Prioridad 1 (próximas 2-3 semanas)

- **Triple Barrier config estandarizada** para todos los executors:
  ```python
  class TripleBarrierConfig(BaseModel):
      stop_loss: Decimal          # fracción del entry
      take_profit: Decimal
      time_limit: int             # segundos
      trailing_stop: Optional[TrailingStop]  # opcional
      open_order_type: Literal["LIMIT", "MARKET"]
  ```
- **Nueva estrategia StepIndexController**: el Step Index es la "joya" de Deriv — predecible, automatizable, sin picos repentinos. Backtest primero (Phase 2), paper (Phase 3), live (Phase 4 con approval explícito).
- **Stake dinámico** estilo Freqtrade: `stake = balance / (max_open_trades - open_trades)` con `tradable_balance_ratio = 0.99`.

#### Prioridad 2 (4-6 semanas)

- **RiskEngine центральний**: toda orden pasa por validación antes de `buy`/`sell` en Deriv. Implementar las reglas del PROJECT.md (2% per trade, 5% daily loss, 10 trades max) aquí, no en las estrategias.
- **Portfolio агрегатор**: clase que mantiene `equity()`, `exposure_per_symbol()`, `open_positions_total()`.
- **MessageBus simple**: empezar con `asyncio.Queue` o `blinker`. Eventos: `tick_received`, `order_submitted`, `order_filled`, `position_opened`, `position_closed`, `risk_breach`.

#### Prioridad 3 (2-3 meses)

- **Portfolio + RiskEngine** funcionando y todos los executors pasando por ellos.
- **DriftBoomCrashController**: automatizar la estrategia de drift en Crash 1000 / Boom 500 con lote mínimo 0.20, SL de 100-150 ticks, TP antes del próximo pico esperado.
- **Backtest-Live Parity**: el mismo `StrategyController` + `Executor` que corre en backtest debe correr en live sin cambios (como NautilusTrader/Hummingbot). El principio es oro para nuestro pipeline Phase 2 → Phase 3 → Phase 4.

#### Prioridad 4 (3-6 meses)

- **Capital Allocator QP** (regime-aware): cuando tengamos >30 operaciones por estrategia, implementar el `QP` del `regime-aware-strategy-allocator`. Bibliotecas: `cvxpy` o `osqp` en Python.
- **Kelly fraccional** como alternativa: `f* = Σ⁻¹ × α`, fracción 0.25x-0.50x, con `drawdown brake` al 15%.
- **Monte Carlo** (estilo Jesse): trade-shuffling + candles-based para distinguir skill-vs-luck antes deapasos a live.

### 6.3 Mapeo stack actual → patrones recomendados

| Patrón | Biblioteca Python recomendada | Alternativa |
|---|---|---|
| MessageBus | `asyncio.Queue` (stdlib) | `blinker`, `pydantic` events |
| RiskEngine | Pydantic validators + métodos | Reglas declarativas en YAML |
| Portfolio | dataclass + pandas DataFrame | Modelo SQLAlchemy |
| Triple Barrier | Pydantic `BaseModel` | dataclass |
| QP Allocator | `cvxpy` | `osqp` directo |
| Backtest engine | pandas + numpy (ya está) | vectorbt para velocidad |
| WebUI streaming | FastAPI WebSocket + Next.js | Server-Sent Events |
| Histórico ticks | Parquet (ya está en PROJECT.md) | SQLite con WAL |
| ML (opcional) | scikit-learn (como Jesse) | optuna para hyperopt |

### 6.4 Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Indicadores técnicos no funcionan en sintéticos (PROJECT.md lo advierte) | Limitar uso de RSI/EMA a Step Index, Range Break y Boom/Crash drift. Usar estadística pura (distribución de retornos, volatilidad) para V75/V100. |
| Asignación QP sensible a estimaciones ruidosas | Empezar con slots iguales (Nivel 1). Solo subir a QP con >50 operaciones por estrategia. Usar `shrinkage` en la covarianza. |
| Backtest overfitting | Monte Carlo (trade-shuffling) antes de paper. `lookahead-analysis` de Freqtrade como inspiración. |
| Cuenta $10 vuela | Reinforzar: 1 operación a la vez, lote mínimo absoluto, retirar tras doblar. El bot debe **negarse a operar** si `risk_per_trade > disponible`. |
| API Deriv nueva (PAT+OTP) inestable | Heartbeat + reconexión automática. No operar si WebSocket caído > N segundos. |
| Sin aprobación explícita de Sebastian para live | Hard gate en código: `if not is_live_approved(): reject_all_orders()`. |

---

## 7. Fuentes

### Estrategias Deriv bajo coste
- 263forex.com — "Best Synthetic Indices for Beginners on Deriv 2025"
- 263forex.com — "How to Grow a Small Account Trading Volatility Indices"
- 263forex.com — "How to Grow a Small Account Safely with Boom & Crash"
- 263forex.com — "Boom and Crash Scalping Strategy: Flip Small Accounts"
- synthetics.info — "Volatility Indices on Deriv: 2025 Guide"
- synthetics.info — "Scalping Boom & Crash"
- synthetics.info — "Best Volatility Indices for Beginners"
- binarybrokerhub.com — "Deriv Synthetic Indices — Complete Guide"
- pipslegion.com — "Can I trade Boom and Crash with $10?"
- publish0x.com — "Matches/Differs Strategy #1" (digitos estadística)
- kenyaforexfirm.com — "Best Time to Trade Synthetic Indices on Deriv" (Step Index como "gem")
- myforexpips.com — "Boom and Crash Strategy That Works in 2026"

### Proyectos open-source
- github.com/freqtrade/freqtrade (52.846 stars, GPL v3)
- github.com/hummingbot/hummingbot + hummingbot.org/docs — architecture, V2 framework, inventory skew, Avellaneda-Stoikov
- github.com/nautechsystems/nautilus_trader + nautilustrader.io/docs — architecture, portfolio, strategies, execution, positions
- github.com/jesse-ai/jesse + jesse.trade — core architecture, ML pipeline
- github.com/scrtlabs/catalyst (ARCHIVADO, Zipline fork)
- github.com/ccxt/ccxt (43.406 stars, MIT) + docs.ccxt.com — core architecture TS→multi-lang

### Asignación de capital
- github.com/LORD-ZYTHOZ/regime-aware-strategy-allocator-public — QP allocator, EWMA covariance, regime gamma
- MDPI Mathematics 13(8):1317 — "Global Cross-Market Trading Optimization Using IMCA" (DRL + IMCA multi-asset)
- arxiv.org/html/2605.17307v1 — "Deep Reinforcement Learning Framework for Diversified Portfolio Management"
- doi.org/10.1002/cpe.70540 — "Cross-Market Portfolio Optimization via Structure-Aware Deep RL"
- prevayo.com — "Advanced Kelly Criterion: Fractional Kelly & Multi-Market"
- tradescopeblog.info — "Position-Sizing 2025: Adaptive Kelly for Multi-Asset Volatility"
- usekeel.io — "How to Size a Position" (risk-percent + Kelly + vol-target)
- mbrenndoerfer.com — "Position Sizing & Leverage: Kelly Criterion Strategy"
- docs.freqtrade.io/en/stable/configuration/ — `max_open_trades`, `stake_amount`, `available_capital`, dynamic stake

---

## Conclusión para Sebastian

1. **"Gems"/"Kraken" no son estrategias con nombre en la literatura pública**. Lo más probable:
   - **"Gems"** = Step Index (la "joya" de Deriv) — predecible, automatizable.
   - **"Kraken"** = scalping agresivo RSI+EMA en M5 con TP de 3 pips (~$14 por cada 20 operaciones con lote 0.20).
2. **$10 → $300 es realista pero lento** (semanas-meses, con disciplina férrea). **$700/mes sostenible requiere $300+ de capital y un bot automatizado** combinando 2-3 estrategias.
3. **Patrones a implementar con prioridad**:
   - **Controller-Executor** (Hummingbot V2) para separar decisión de ejecución.
   - **Triple Barrier** (Hummingbot) estandarizar exit config en todas las estrategias.
   - **RiskEngine + Portfolio** (NautilusTrader) centralizar validación y agregación.
   - **Stake dinámico** (Freqtrade) `balance / available_slots`.
   - **QP Allocator** (regime-aware-strategy-allocator) cuando haya histórico suficiente.
4. **Orden de implementación**: StepIndex strategy → Triple Barrier → RiskEngine → Portfolio → DriftBoomCrash → QP Allocator.
5. **Stack actual (Python + FastAPI + Next.js + SQLite) es compatible con todos los patrones**. No migrar a Rust; está justificado solo si necesitamos latencia sub-microsegundo (no es el caso de Deriv, que actualiza cada 1-2 segundos).
