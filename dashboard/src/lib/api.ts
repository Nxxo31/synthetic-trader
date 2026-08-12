/**
 * API client for Synthetic Trader backend.
 *
 * Wraps every endpoint in server.py with a typed fetch helper.
 * The base URL is configurable via NEXT_PUBLIC_API_URL (defaults to
 * http://localhost:8001 to match the project's uvicorn port).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    // Always cache: no-store for live trading data.
    cache: "no-store",
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${path} failed (${res.status}): ${text}`);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Type definitions
// ---------------------------------------------------------------------------

export interface BalanceData {
  balance: number;
  pnl: number;
  equity: number;
  trades_today: number;
  is_halted: boolean;
  symbol: string;
  strategy: string;
  mode: string;
  last_update: string;
}

export interface Trade {
  timestamp?: string;
  entry_time?: number;
  exit_time?: number;
  direction: string;
  entry_price: number;
  exit_price?: number;
  stop_loss?: number;
  take_profit?: number;
  pnl: number;
  pnl_pct?: number;
  status?: string;
  win?: boolean;
  exit_reason?: string;
  duration_seconds?: number;
  duration?: number;
  stake?: number;
  confidence?: number;
  score?: number;
}

export interface StrategyInfo {
  name: string;
  display: string;
  description: string;
  enabled: boolean;
  category: string;
  latest_backtest?: {
    filename: string;
    symbol: string;
    total_trades: number;
    win_rate: number;
    sharpe_ratio: number;
    max_drawdown: number;
    total_pnl: number;
    profit_factor: number;
    expectancy: number;
    gate_passed: boolean;
  };
}

export interface AllocationData {
  capital_total: number;
  reserva: number;
  superavit_diario: number;
  superavit_disponible: number;
  superavit_usado: number;
  reserva_pct: number;
  superavit_diario_pct: number;
  live_balance: number;
  live_pnl: number;
  trades_today: number;
  return_pct: number;
  is_active: boolean;
  data_available: boolean;
}

export interface AllocateResult {
  reserve: number;
  daily_surplus: number;
  reinvestable: number;
  total_available: number;
  micro_stake_size: number;
  micro_stakes_count: number;
  live_balance: number;
  live_pnl: number;
  allocation_pct: {
    reserve: number;
    surplus: number;
  };
}

export interface RankingEntry {
  symbol: string;
  strategy_name: string;
  metric: string;
  metric_value: number;
  pnl: number;
  sharpe: number;
}

export interface AttributionData {
  ranking: RankingEntry[];
  matrix: {
    strategies: string[];
    symbols: string[];
    matrix: (CellData | null)[][];
    metric: string;
  };
  metric: string;
  min_trades: number;
}

export interface CellData {
  strategy_name: string;
  symbol: string;
  metric: string;
  metric_value: number;
  pnl: number;
  sharpe: number;
}

export interface BacktestResult {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl: number;
  max_drawdown: number;
  sharpe_ratio: number;
  profit_factor: number;
  expectancy: number;
  trades: Trade[];
  equity_curve: number[];
  gate_passed: boolean;
  gate_failures: string[];
  strategy: string;
  symbol: string;
  config?: Record<string, unknown>;
  circuit_breaker_status?: Record<string, unknown>;
  error?: string;
}

export interface AllocatorConfig {
  data_available: boolean;
  capital_total: number;
  reserva: number;
  superavit_diario: number;
  superavit_disponible: number;
  superavit_usado: number;
  reserva_pct: number;
  superavit_diario_pct: number;
  live_balance: number;
  live_pnl: number;
  trades_today: number;
  return_pct: number;
  is_active: boolean;
  rebalance_daily: boolean;
  min_surplus: number;
  status?: string;
}

export interface BotStatus {
  bot_id: string;
  strategy: string;
  symbol: string;
  mode: string;
  balance: number;
  pnl: number;
  trades_today: number;
  is_halted: boolean;
  circuit_breaker: {
    consecutive_losses: number;
    is_halted: boolean;
  };
  kpi: {
    sharpe_ratio: number;
    win_rate: number;
    max_drawdown: number;
    profit_factor: number;
    expectancy: number;
  };
  gate_passed: boolean;
  gate_failures: string[];
  last_update: string;
}

// ---------------------------------------------------------------------------
// API methods — one per endpoint
// ---------------------------------------------------------------------------

export const api = {
  health: () => apiFetch<{ status: string; timestamp: string; version: string }>("/api/health"),

  // Balance (Dashboard overview)
  getBalance: () => apiFetch<BalanceData>("/api/balance"),

  // Positions (Dashboard positions table)
  getPositions: () => apiFetch<Trade[]>("/api/positions"),

  // Strategies (list + detail)
  getStrategies: () => apiFetch<StrategyInfo[]>("/api/strategies"),

  // Allocator
  getAllocatorConfig: () => apiFetch<AllocatorConfig>("/api/allocator/config"),
  updateAllocatorConfig: (config: {
    reserva_pct?: number;
    max_daily_pct?: number;
    reinvest_profits?: boolean;
    min_surplus?: number;
  }) =>
    apiFetch<AllocatorConfig>("/api/allocator/config", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  getAllocation: (params?: { starting_balance?: number; daily_pnl?: number }) => {
    const query = new URLSearchParams();
    if (params?.starting_balance != null) query.set("starting_balance", String(params.starting_balance));
    if (params?.daily_pnl != null) query.set("daily_pnl", String(params.daily_pnl));
    const qs = query.toString();
    return apiFetch<AllocateResult>(`/api/allocator/allocate${qs ? `?${qs}` : ""}`);
  },
  postAllocate: (payload: { starting_balance?: number; daily_pnl?: number }) =>
    apiFetch<AllocateResult>("/api/allocator/allocate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Attribution
  getAttribution: (params?: { metric?: string; min_trades?: number }) => {
    const query = new URLSearchParams();
    if (params?.metric) query.set("metric", params.metric);
    if (params?.min_trades != null) query.set("min_trades", String(params.min_trades));
    const qs = query.toString();
    return apiFetch<AttributionData>(`/api/attribution${qs ? `?${qs}` : ""}`);
  },
  getAttributionMatrix: (metric = "total_pnl") =>
    apiFetch<AttributionData["matrix"]>(`/api/attribution/matrix?metric=${metric}`),
  getAttributionRanking: (params?: { metric?: string; min_trades?: number }) => {
    const query = new URLSearchParams();
    if (params?.metric) query.set("metric", params.metric);
    if (params?.min_trades != null) query.set("min_trades", String(params.min_trades));
    const qs = query.toString();
    return apiFetch<{ ranking: RankingEntry[]; metric: string; min_trades: number }>(
      `/api/attribution/ranking${qs ? `?${qs}` : ""}`
    );
  },

  // Backtest
  getBacktestResults: () => apiFetch<any[]>("/api/backtest/results"),
  getLatestBacktest: () => apiFetch<BacktestResult>("/api/backtest/latest"),
  postBacktestReplay: (filename?: string) =>
    apiFetch<BacktestResult>("/api/backtest/replay", {
      method: "POST",
      body: JSON.stringify(filename ? { filename } : {}),
    }),

  // Bot status (full payload for Dashboard)
  getBotStatus: () => apiFetch<BotStatus>("/api/bot/status"),
  getBotTrades: () => apiFetch<Trade[]>("/api/bot/trades"),

  // Daily reports
  getDailyReport: () => apiFetch<any>("/api/daily/report"),
  getDailyHistory: (limit = 30) => apiFetch<any[]>(`/api/daily/history?limit=${limit}`),

  // Projection
  getProjection: (days = 7, surplus = 200) =>
    apiFetch<any>(`/api/projection/equity?days=${days}&surplus=${surplus}`),
};

export { API_BASE };
