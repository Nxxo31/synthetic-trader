# Investigación: Operador de Criptos en Deriv para Índices Sintéticos

**Fecha:** 2026-07-12  
**Autor:** SophIA (research profile)  
**Estado:** Completo  
**Confianza:** Alta (fuentes oficiales Deriv + repos verificados)

---

## 1. Resumen Ejecutivo

Deriv ofrece **dos mercados distintos** accesibles desde la misma API WebSocket:

1. **Índices Sintéticos** — RNG criptográficamente seguro, 24/7, 103 instrumentos, sin order book
2. **Criptomonedas** — 38 pares crypto reales (BTC, ETH, XRP, ADA, BNB, etc.), 24/7, con order book real

La API es **la misma** para ambos. Un operador puede tradear sintéticos y crypto desde una sola conexión WebSocket, con el mismo flujo: `authorize → ticks → proposal → buy → sell`. La diferencia clave es que **en crypto SÍ funcionan los indicadores técnicos** porque hay un mercado real detrás.

### Oportunidad detectada

Deriv ofrece **índices de pares arbitrage** (ej: BTCETH) que combinan crypto con la estructura de índice sintético. Esto es un híbrido único: mercado real con la simplicidad de opciones binarias.

---

## 2. Mercado de Cripto en Deriv

### Instrumentos disponibles (38 pares)

| Categoría | Símbolos | Notas |
|-----------|---------|-------|
| Major coins | BTCUSD, ETHUSD, BTCETH | Mayor liquidez, spreads bajos (0.13%) |
| Altcoins | ADAUSD, BNBUSD, XRPUSD, ALGUSD, APEUSD, APTUSD, AVAUSD, BATUSD, BCHUSD | Leverage 1:50 a 1:400 |
| Pairs Arbitrage | BTCETH | Ratio BTC/ETH, mercado-neutral |

### Características clave

- **Trading 24/7** (Sun 00:00 - Sat 24:00 GMT, break diario 20:00-20:05)
- **Swap-free** — sin overnight fees en sintéticos, swaps negativos en crypto (-15 a -29 pts)
- **Spreads:** 0.13% (BTCUSD) a 1.83% (APEUSD)
- **Leverage:** 1:50 (APEUSD) hasta 1:400 (ADAUSD, BNBUSD)
- **0% comisión** en CFDs
- **Take profit / Stop loss** disponibles

### Plataformas de trading crypto en Deriv

| Plataforma | Tipo | Indicadores |
|-----------|------|------------|
| Deriv MT5 | CFDs | Sí, análisis técnico completo |
| Deriv cTrader | CFDs | Sí, análisis técnico completo |
| TradingView | CFDs | Sí, integración completa |
| Deriv Trader | Opciones | Limitado |
| Deriv Bot | Opciones automatizadas | Limitado |

### Tipos de contrato disponibles en crypto

| Tipo | Plataforma | Notas |
|------|-----------|-------|
| CFDs (CFD) | MT5, cTrader, TradingView | Leverage, stop loss, take profit |
| Opciones (CALL/PUT) | Deriv Trader, Deriv Bot | Duración fija, payout fijo |
| Opciones de barrera | Deriv Trader | Touch/No Touch, etc. |

**Confirmado:** En crypto, los indicadores técnicos SÍ funcionan porque hay un mercado real subyacente con oferta/demanda.

---

## 3. Arquitectura de la API de Deriv — Unificada

### La misma API para todo

La API WebSocket de Deriv es **unificada**. Un solo endpoint, una conexión, acceso a todos los mercados:

```
wss://ws.derivws.com/websockets/v3?app_id=XXXX
```

#### Flujo de trading (aplica a sintéticos Y crypto)

```
1. authorize      → { authorize: 1, token: "API_TOKEN" }
2. active_symbols → { active_symbols: "full" }
   → Retorna TODOS los símbolos: sintéticos + crypto + forex
3. ticks          → { ticks: "BTCUSD", subscribe: 1 }  // o "R_100"
4. proposal       → { proposal: 1, amount: 100, basis: "payout",
                      contract_type: "CALL", currency: "USD",
                      duration: 60, duration_unit: "s", symbol: "BTCUSD" }
5. buy            → { buy: proposal_id, price: 100 }
6. proposal_open_contract → { proposal_open_contract: 1, contract_id: XXX }
7. sell           → { sell: contract_id, price: 0 }
```

#### Diferencias entre sintéticos y crypto en la API

| Aspecto | Sintéticos | Crypto |
|---------|-----------|--------|
| Símbolos | R_10, R_25, R_50, BOOM1000, etc. | BTCUSD, ETHUSD, ADAUSD, etc. |
| Datos | RNG | Mercado real (aggregated feeds) |
| Indicadores técnicos | NO funcionan | SÍ funcionan |
| Order book | No existe | Sí existe |
| Spreads | Mínimos | Variables (0.13%-1.83%) |
| Trading horas | 24/7 real | Sun 00:00 - Sat 24:00 (break 20:00-20:05) |
| Swaps | Zero | Negativos (-15 a -29 pts) |
| Volatilidad | Constante (10%-100%) | Variable (mercado) |
| Contract types | Opciones (CALL/PUT, digits, asian) | Opciones + CFDs |

---

## 4. Deriv MCP Server — Estado Actual

### Servidor MCP existente

- **Repo:** mcpmarket.com/server/deriv-api
- **Funciones expuestas:** `active_symbols`, `balance`
- **Limitación crítica:** NO expone trading endpoints (proposal, buy, sell)
- **Instalación:**

```yaml
mcp_servers:
  deriv:
    command: npx
    args: ["-y", "deriv-mcp-server"]
    env:
      DERIV_API_TOKEN: ${DERIV_API_TOKEN}
      DERIV_APP_ID: ${DERIV_APP_ID}
```

### Enfoque recomendado: Híbrido

| Componente | Herramienta | Uso |
|-----------|-------------|-----|
| Queries rápidos | Deriv MCP Server | Balance, símbolos activos |
| Strategy + backtest | python_deriv_api | Backtest engine, data collection |
| Live trading | Direct WebSocket | Ejecución real, reconnect handling |

---

## 5. Arquitecturas de Referencia (GitHub)

### 5.1 OmashelCap (iamMashel) — MeÌtodo de referencia maÌs completo

**Stack:** Python 3.12 + FastAPI + React 19 + TypeScript + Tailwind + SQLite  
**Arquitectura:**

```
Browser Dashboard (React/Tailwind)
    ↓ WebSocket + REST
FastAPI (REST + WS)
    ↓
Bot Engine (DerivClient + SignalEngine + RiskManager + OrderManager)
    ↓
Deriv WebSocket API
```

**Características implementadas:**
- WebSocket client con reconnect
- Signal engine: Market structure + EMA + RSI + Bollinger + S&D zones
- Confluence scoring (0-5, dispara con ≥3)
- Risk manager: Fixed fractional (1% per trade), max stake cap, circuit breaker
- Backtest engine: Walk-forward bar-by-bar en datos reales de Deriv
- Mock mode: Funciona sin API key
- Telegram alerts
- Docker ready
- CI: ruff + mypy + pytest

**Lecciones aplicables:**
- Circuit breaker es esencial (auto-halt tras N pérdidas o DD limÌite)
- Mock mode agiliza desarrollo sin riesgo
- Confluence scoring > estrategia de un solo indicador
- Dashboard en tiempo real es factible y Ãotil

### 5.2 Trading-Pipeline (stephen-njiu)

**Stack:** Python puro  
**Estrategia:** Bollinger Bands + Rejection candlestick patterns  
**LimitaciÃ³n:** Usa indicadores tÃ©cnicos en sintÃ©ticos (problema conocido)

### 5.3 kairos-trade y hilo-fast-trade (dinethlive)

**Stack:** TypeScript + Bun + Ink (CLI)  
**Enfoque:** Tick-based adaptive trading, time-block paired trading  
**Lecciones:** CLI-first es vÃ¡lido para bots de trading, no siempre necesitas dashboard

---

## 6. Estrategias para un Operador de Criptos en Deriv

### 6.1 Estrategia para Crypto (indicadores SÃ funcionan)

Dado que crypto en Deriv tiene mercado real subyacente:

| Estrategia | Aplicable a |ImplementaciÃ³n |
|-----------|------------|---------------|
| EMA crossover (9/21/50) | BTCUSD, ETHUSD | clÃ¡sico, probado |
| RSI + Bollinger confluence | Todos | like OmashelCap |
| Breakout con volumen | BTCUSD, ETHUSD | requiere datos de volumen |
| Mean reversion con bands | Altcoins baja liquidez | Bollinger + RSI |
| Pairs arbitrage (BTCETH) | BTCETH ratio | mercado-neutral, menor riesgo |
| Momentum + trend follow | Momentum natural crypto | EMA + MACD |

### 6.2 Estrategia para Sinteticos (estadistica pura)

| Estrategia |Instrumento | Fundamento |
|-----------|-----------|------------|
| Volatility breakout | R_50, R_75 | Movimientos estad               ilis |
| Digit analysis | R_100 | AnÃ¡lisis del Ãºltimo dÃ­gito del precio |
| Kelly criterion sizing | Todos | Edge estadÃ­stico + position sizing |
| Boom/Crash directional bias | BOOM1000, CRASH1000 | Tendencia estructural sesgada |
| Range Break channel | RangeBrk100 | Ãnico donde S/R real funciona |

### 6.3 Kelly Criterion — Consideraciones crÃ­ticas

ArtÃ­culo oficial de Deriv (experts.deriv.com) confirma:

**FÃ³rmula:** Kelly % = W - (1-W)/R
- W = win rate histÃ³rico
- R = avg gain / avg loss

**Pautas del artÃ­culo oficial:**
- Full Kelly es demasiado agresido → usar Quarter-Kelly (25%)
- Para retail, cap duro: 1-2% de capital por trade
- Recalcular quarterly (edge cambia)
- 50-100 trades mÃ­nimos antes de confiar en los inputs
- Negative Kelly = no trade

**Nuestra config:** Risk rules ya definidas: 2% max per trade, 5% daily, 10 trades max. Kelly se usa para escalar dentro del 2% cap.

---

## 7. Arquitectura Propuesta â Operador Unificado

### Concepto

Un solo bot que puede operar **tanto sinteticos como crypto** en Deriv, usando la misma API WebSocket, con mÃ³dulos de estrategia intercambiables segÃºn el tipo de instrumento.

```
âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
â              SophIA Trader (Unified Bot)                       â
â                                                              â
â  ââââââââââââââââââââââââââââââââââââââââââââââ        â
â  â  Module: Indicador Selector                                 â        â
â  â  ââ Sinteticos â estrategia estadÃ­stica (stats + risk)    â        â
â  â  ââ Crypto â estrategia tÃ©cnica (EMA + RSI + BB)          â        â
â  ââââââââââââââââââââââââââââââââââââââââââââââ        â
â                                                              â
â  ââââââââââââââââââââââââââââââââââââââââââââââ        â
â  â  Strategy Engine (pluggable)                               â        â
â  â  ââ TechnicalStrategy (crypto): EMA, RSI, BB, confluence  â        â
â  â  ââ StatisticalStrategy (sinteticos): vol, digits, bias   â        â
â  â  ââ RangeBreakStrategy (RangeBrk): channel analysis       â        â
â  ââââââââââââââââââââââââââââââââââââââââââââââââ        â
â                                                              â
â  Risk Manager (unified): 2% trade | 5% daily | 10 trades      â
â  Ã¢ââ Kelly sizing within 2% cap                              â
â  Ã¢ââ Circuit breaker: N losses or DD threshold                â
â                                                              â
â  Data Collector: ticks + candles â Parquet                    â
â  Backtest Engine: walk-forward replay                         â
â  Monitor: daily report + Telegram alerts                      â
â                                                              â
âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
                           â
                    âââââââ´ââââââ
                    â Deriv API    â
                    â (WebSocket)  â
                    â              â
                    â Sinteticos   â
                    â + Crypto      â
                    â + Forex       â
                    âââââââââââââ
```

### Estructura de directorios actualizada

```
~/proyectos/synthetic-trader/
âââ PROJECT.md
âââ config/
â   âââ deriv.yaml
â   âââ strategies.yaml
âââ src/
â   âââ connection/
â   â   âââ deriv_client.py     â WebSocket client unificado
â   âââ data/
â   â   âââ collector.py        â Tick/candle collector (sint + crypto)
â   â   âââ storage.py           â SQLite/Parquet
â   âââ strategy/
â   â   âââ base.py               â Abstract strategy interface
â   â   âââ technical.py         â EMA/RSI/BB para crypto
â   â   âââ statistical.py        â Stats para sinteticos
â   â   âââ range_break.py        â Range Break (Ãºnico hÃ­brido)
â   âââ backtest/
â   â   âââ engine.py
â   â   âââ metrics.py
â   â   âââ report.py
â   âââ execution/
â   â   âââ paper.py
â   â   âââ live.py
â   âââ risk/
â   â   âââ manager.py           â 2% trade, 5% daily, circuit breaker
â   â   âââ position_sizing.py    â Kelly criterion (Quarter-Kelly)
â   âââ monitor/
â   â   âââ daily_report.py
â   âââ main.py
âââ data/
â   âââ ticks/
â   âââ candles/
âââ reports/
âââ strategies/
âââ tests/
âââ pyproject.toml
```

### Consideraciones de diseÃ±o

1. **Selector de estrategia automÃ¡tico:** El bot detecta el tipo de instrumento (sintÃ©tico vs crypto) vÃ­a `active_symbols` y carga el mÃ³dulo de estrategia apropiado.

2. **Risk manager unificado:** Las mismas reglas (2% trade, 5% daily, 10 trades) aplican a ambos mercados. Kelly sizing escala dentro del 2% cap.

3. **Multi-symbol:** OmashelCap tiene esto en roadmap. Nuestro bot puede empezar con un sÃ­mbolo pero el diseÃ±o debe permitir multi-symbol desde el dÃ­a 1.

4. **Datos diferenciados:**
   - SintÃ©ticos: ticks son suficientes (no hay volumen)
   - Crypto: candles + volumen si disponible
   - Storage: Parquet para ambos, schema ligeramente distinto

5. **Simbolos iniciales recomendados:**

| Mercado | SÃ­mbolo | RazÃ³n |
|---------|--------|-------|
| SintÃ©ticos | R_50 | Volatilidad equilibrada, estadÃ­sticas estables |
| Crypto | BTCUSD | Mayor liquidez, spread mÃ­nimo, edge tÃ©cnico real |
| HÃ­brido | BTCETH | Pairs arbitrage, menor riesgo direccional |

---

## 8. Riesgos y Consideraciones

### 8.1 Riesgos ya identificados en PROJECT.md

1. SintÃ©ticos no responden a indicadores tÃ©cnicos
2. Volatilidad extrema en R_100+ y Boom/Crash
3. API rate limits no documentados
4. Demo puede diferir de real
5. No order book en sintÃ©ticos

### 8.2 Riesgos nuevos detectados para crypto

6. **Spreads variables en crypto** (0.13% a 1.83%) â impacta profitabilidad de estrategias de corto plazo
7. **Swaps negativos en crypto** (-15 a -29 pts) â cost SignUp para posiciones overnight
8. **Break diario en crypto** (20:00-20:05, 21:05-21:10) â no aplica a sintÃ©ticos
9. **Leverage variable por crypto** (1:50 a 1:400) â sizing debe adaptarse
10. **RegulaciÃ³n crypto** â oferta puede variar por jurisdicciÃ³n
11. **House edge en opciones binarias** â a 85% payout necesitas >54% win rate para break even

### 8.3 Riesgos de seguridad

12. **API token Ãºnico para demo y real** â verificar siempre el account_list despuÃ©s de authorize
13. **Una conexión WebSocket por app_id** â no abrir mÃºltiples conexiones simultÃ¡neas

---

## 9. Modelo de Caso de Uso Actualizado

### UC-07: Operar cripto en Deriv (nuevo)

```
UC-07: Trading de criptomonedas
  Actor: SophIA (trader profile)
  Pre: UC-01 completo (cuenta demo + API token)
  Post: Estrategia tÃ©cnica ejecutÃ¡ndose en cuenta demo

  Flujo:
    1. SophIA obtiene active_symbols â filtra market = "cryptocurrencies"
    2. SophIA selecciona BTCUSD (mayor liquidez, menor spread)
    3. Suscribe a ticks_history con style=candles (1m, 5m)
    4. Strategy Engine selecciona TechnicalStrategy automÃ¡ticamente
    5. Calcula EMA(9), EMA(21), EMA(50), RSI(14), Bollinger(20,2)
    6. Confluence score â¥3 â genera seÃ±al CALL/PUT
    7. Risk Manager calcula stake (Quarter-Kelly within 2% cap)
    8. Proposal â Buy â Monitor â Sell (o expira)
    9. Log trade en SQLite con contexto completo
    10. Daily report via cron

  Diferencias con sintÃ©ticos:
    - Indicadores tÃ©cnicos SÃ funcionan
    - Spread variable impacta entry/exit
    - Break diario 20:00-20:05 (no operar en ese window)
    - Swaps negativos si hold overnight (no aplica a opciones de duracin corta)
```

---

## 10. Roadmap Actualizado

### Sprint T1 â Setup y conexiÃ³n (sin cambios)
### Sprint T2 â Data collection (actualizado)

| Tarea | Descripcion | Entrega |
|-------|-------------|---------|
| T2.1 Tick collector sintÃ©ticos | R_50 ticks | collector.py |
| T2.2 Tick collector crypto | BTCUSD candles (1m, 5m) | collector.py |
| T2.3 Historical download | 5000+ candles ambos mercados | data/ |
| T2.4 Data validation | Verificar gaps, breaks, spread | report |

### Sprint T3 â Strategy design + backtest (actualizado)

| Tarea | Descripcion | Entrega |
|-------|-------------|---------|
| T3.1 Statistical profile R_50 | Vol, distribucin, ACF | stats_r50.md |
| T3.2 Technical profile BTCUSD | EMA, RSI, BB effectiveness | stats_btcusd.md |
| T3.3 Strategy v1 sintÃ©ticos | Strategy_ stock v1 | strategy_r50_v1.md |
| T3.4 Strategy v1 crypto | EMA confluence + RSI | strategy_btcusd_v1.md |
| T3.5 Backtest engine | Replay para ambos tipos | engine.py |
| T3.6 Run backtests | Gate eval ambos | backtest_reports |

### Sprint T4-5 â Paper trading y live (sin cambios estructurales)

---

## 11. Conclusiones

1. **Deriv permite operar cripto y sintÃ©ticos desde la misma API** â no requiere infraestructura separada
2. **Crypto en Deriv tiene mercado real** â los indicadores tÃ©cnicos SÃ funcionan, a diferencia de los sintÃ©ticos
3. **La arquitectura del bot puede ser unificada** â un solo WebSocket client, strategy engine intercambiable
4. **OmashelCap es la referencia mÃ¡s completa** â arquitectura production-grade, stack similar al nuestro
5. **Kelly Criterion con cap duro de 2%** es la estrategia de position sizing correcta (confirmado por Deriv)
6. **BTCETH pairs arbitrage** es una oportunidad Ãºnica de mercado-neutral disponible solo en Deriv
7. **El Deriv MCP Server actual es limitado** â solo balance y symbols. Para trading completo necesitamos SDK directo

### PrÃ³ximos pasos recomendados

1. **Crear cuenta demo en Deriv** â bloqueador actual
2. **Configurar Deriv MCP Server** en trader profile â para queries rÃ¡pidos
3. **Empezar con python_deriv_api** â backtest y strategy development
4. **SÃ­mbolos iniciales:** R_50 (sintÃ©ticos) + BTCUSD (crypto)
5. **Strategy v1 crypto:** EMA(9/21/50) + RSI(14) + Bollinger(20,2) confluence scoring

---

## Referencias

- Deriv API docs: https://developers.deriv.com
- Deriv crypto markets: https://deriv.com/markets/cryptocurrencies
- Deriv Kelly Criterion: https://experts.deriv.com/insights/kelly-criterion-position-sizing
- Deriv MCP Server: https://mcpmarket.com/server/deriv-api
- python_deriv_api: https://pypi.org/project/python-deriv-api/
- @deriv/deriv-api (TS): https://deriv-com.github.io/deriv-api/
- OmashelCap (ref architecture): https://github.com/iamMashel/OmashelCap
- Trading-Pipeline (ref): https://github.com/stephen-njiu/Trading-Pipeline
- kairos-trade (ref): https://github.com/dinethlive/kairos-trade
- Deriv bot platform: https://deriv.com/trading-platforms/deriv-bot
