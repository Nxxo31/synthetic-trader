# Synthetic Trader — Bot de Indices Sinteticos en Deriv

## Project
Bot de trading algorithmico para indices sinteticos en Deriv.
Pipeline demo-to-live: strategy design → backtest → paper trading demo → live.
Repository: ~/proyectos/synthetic-trader/
Broker: Deriv (unico broker de indices sinteticos 24/7)

## Stack
- **SDK principal**: requests + websockets (REST OTP + WebSocket hybrid, nueva API Deriv)
- **Backtest**: Python + pandas + numpy
- **Storage**: SQLite (metadatos) + Parquet (ticks/OHLCV)
- **Broker**: Deriv API nueva (developers.deriv.com)
- **Auth**: PAT (Personal Access Token) + OTP flow (Bearer token REST → WebSocket upgrade)

## Critical rules — NEVER violate
- Paper trading ONLY hasta que Phase 3 gate pase
- Sin confirmacion explicita de Sebastian: NO live trading
- API token NUNCA en codigo — siempre env vars (.env del proyecto)
- Risk rules: 2% per trade, 5% daily loss, 10 trades max
- No estrategia va a live sin backtest + paper trading
- No ejecutar trade que no ha pasado backtesting

## API de Deriv (NUEVA — developers.deriv.com)
```
REST Base URL: https://api.derivws.com
Auth: Bearer token (PAT) en headers + Deriv-App-ID header

Flujo de conexión:
  1. POST /trading/v1/options/accounts/{accountId}/otp
     Headers: Deriv-App-ID, Authorization: Bearer {PAT}
     → Response: { data: { url: "wss://api.derivws.com/.../ws/demo?otp=xxx" } }
  2. Conectar WebSocket a la URL devuelta (OTP válido 120s, un solo uso)
  3. Enviar JSON: ticks, proposal, buy, sell, etc.

Public WebSocket (sin auth, market data only):
  wss://api.derivws.com/trading/v1/options/ws/public

Cuentas:
  Demo: DOT93744719 ($10,000)
  Real: ROT92215439 ($0.00)
```

## Pipeline (4 fases)
```
Phase 1: Strategy Design → hipotesis, entry/exit, risk params
Phase 2: Backtest → replay, metricas (Sharpe>1.0, DD<15%, WR>50%)
Phase 3: Paper Trading Demo → 30+ trades, profitable, no day >-5%
Phase 4: Live Trading → solo con aprobacion explicita
```

## Instrumentos sinteticos
- Volatility: R_10, R_25, R_50, R_75, R_100
- Boom/Crash: BOOM1000, CRASH1000, etc.
- Step: STEPT10, etc.
- Range Break: UNICO donde indicadores tecnicos funcionan
- 103 instrumentos totales disponibles

## Limitacion critica
> Los indices sinteticos NO responden a indicadores tecnicos tradicionales.
> Patrones historicos son coincidencia. Estrategia debe basarse en:
> - Estadistica pura (distribucion de retornos, volatilidad)
> - Gestion de riesgo cuantitativa (Kelly, position sizing)
> - Range Break Index como excepcion (canal SI funciona)

## PROJECT.md
`~/proyectos/synthetic-trader/PROJECT.md` es la fuente de verdad.

## Development loop for this project

1. Read PROJECT.md → check active phase and known limitations
2. `git status` → ver estado del repo
3. Verificar LSP activo: `hermes lsp status` — si no hay clientes: `hermes lsp restart`
   **Nota en WSL**: El servidor LSP de TypeScript está instalado, pero el cliente solo se conecta cuando un editor (VS Code, etc.) abre un archivo `.ts` o `.tsx`. Mientras no haya un archivo abierto, `hermes lsp status` mostrará `active clients: none`; esto es esperado y no indica un problema. La fuente de verdad para tipos es `mcp__lsp_intelligence__live_diagnostics`, que debe dar 0 errores antes de hacer commit.
4. Skills loaded automatically by the agent before writing code: based on the project's stack (e.g., for Electron projects: electron-desktop-dev; for Next.js: nextjs-best-practices; for Go: go-environment-setup, etc.). The agent loads the appropriate stack-specific skills without needing a file reminder.
5. For tasks >1 archivo or UI work: the agent thinks first about what it's going to build, shows mockups if UI, and only then writes code. No intermediate .md files — design lives inline in PROJECT.md if needed.
6. **LSP gate**: `mcp__lsp_intelligence__live_diagnostics` en archivos modificados — 0 errores
7. **Code review gate**: `delegate_task` con skill `code-review-and-quality` — todos los findings addressados
8. **Secret scan gate**: run `gitleaks detect --staged` (via terminal tool) — no secrets detected
9. Update PROJECT.md with results BEFORE commit (only project doc allowed)
10. **Commit gate**: use GitHub MCP tools to create a commit with conventional message (type(scope): description) and push to the current branch.
11. Next task immediately.

NO vitest, NO jest, NO playwright, NO `tsc --noEmit` directo. Los gates son determinísticos: LSP live_diagnostics + delegate_task review + gitleaks + GitHub commit.
NO separate spec files, drift reports, docs/specs/, architecture overviews, or any .md outside PROJECT.md. Everything goes in PROJECT.md.

## Editing code files
- Leer archivo completo antes de editar
- Sin sed multilinea en Python
- Validar con typecheck/lint despues de cambios
- Si un edit falla 2 veces: parar y reportar
