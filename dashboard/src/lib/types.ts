// Domain types for the Synthetic Trader dashboard.

export interface KPI {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
}

export interface Trade {
  id: number;
  symbol: string;
  side: string;
  entry: number;
  exit: number;
  pnl: number;
  time: string;
}

export interface RiskMetrics {
  circuitBreaker: boolean;
  dailyLoss: number;
  consecutiveLosses: number;
  tradesToday: number;
  maxDrawdown: number;
}

export interface BotStatus {
  running: boolean;
  strategy: string;
  symbol: string;
  balance: number;
  pnlToday: number;
  winRate: number;
  sharpe: number;
  tradesToday: number;
  maxDrawdown: number;
  circuitBreaker: boolean;
  dailyLoss: number;
  consecutiveLosses: number;
}

export interface DailyStat {
  date: string;
  pnl: number;
  trades: number;
  winRate: number;
  sharpe: number;
  maxDrawdown: number;
}

export interface EquityPoint {
  time: string;
  value: number;
}

export interface StrategyInfo {
  name: string;
  label: string;
  description: string;
}

export const STRATEGIES: StrategyInfo[] = [
  { name: 'RangeBreak', label: 'Range Break', description: 'Range Break Index — canal técnico válido' },
  { name: 'Volatility', label: 'Volatility', description: 'Volatility indices — estadística pura' },
  { name: 'Gems', label: 'Gems', description: 'Gems — oportunidades altas convicción' },
];

export const SYMBOLS = [
  'R_10', 'R_25', 'R_50', 'R_75', 'R_100',
  'RB100', 'BOOM1000', 'CRASH1000',
] as const;

export type SymbolName = (typeof SYMBOLS)[number];

export const SYMBOL_LABELS: Record<SymbolName, string> = {
  R_10: 'R_10',
  R_25: 'R_25',
  R_50: 'R_50',
  R_75: 'R_75',
  R_100: 'R_100',
  RB100: 'RB100',
  BOOM1000: 'BOOM1000',
  CRASH1000: 'CRASH1000',
};
