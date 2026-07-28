'use client';

import { useEffect, useState } from 'react';
import { fetchAPI } from '@/lib/api';
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from 'recharts';

interface KPI {
  label: string;
  value: string | number;
  change?: string;
}

interface Trade {
  id: number;
  symbol: string;
  side: string;
  entry: number;
  exit: number;
  pnl: number;
  time: string;
}

interface RiskMetrics {
  circuitBreaker: boolean;
  dailyLoss: number;
  tradesToday: number;
}

interface BacktestResult {
  filename: string;
  strategy: string;
  symbol: string;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_pnl: number;
  profit_factor: number;
  expectancy: number;
  gate_passed: boolean;
  gate_failures: string[];
  trades: Array<{
    entry_price: number;
    exit_price: number;
    pnl: number;
    direction: string;
    timestamp: string;
  }>;
  equity_curve: number[];
  initial_capital: number;
}

const Dashboard = () => {
  const [kpis, setKpis] = useState<KPI[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [risk, setRisk] = useState<RiskMetrics>({
    circuitBreaker: false,
    dailyLoss: 0,
    tradesToday: 0,
  });
  const [equityData, setEquityData] = useArray<{ name: string; value: number }>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const result = await fetchAPI<BacktestResult[]>('/api/backtest/results');
        if (result.length === 0) {
          setError('No backtest results available');
          setLoading(false);
          return;
        }
        const data = result[0];

        // Map KPIs
        const balance = data.initial_capital + data.total_pnl;
        const formattedBalance = `$${balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const formattedPnL = `$${data.total_pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        const winRatePercent = (data.win_rate * 100).toFixed(1) + '%';
        const sharpe = data.sharpe_ratio.toFixed(2);

        setKpis([
          { label: 'Balance', value: formattedBalance, change: data.total_pnl >= 0 ? `+${((data.total_pnl / data.initial_capital) * 100).toFixed(2)}%` : `${((data.total_pnl / data.initial_capital) * 100).toFixed(2)}%` },
          { label: 'P&L Today', value: formattedPnL, change: data.total_pnl >= 0 ? `+${((data.total_pnl / data.initial_capital) * 100).toFixed(2)}%` : `${((data.total_pnl / data.initial_capital) * 100).toFixed(2)}%` },
          { label: 'Win Rate', value: winRatePercent, change: '+' }, // Placeholder change
          { label: 'Sharpe', value: sharpe, change: '+' }, // Placeholder change
        ]);

        // Map trades
        const mappedTrades = data.trades.map((trade, index) => ({
          id: index + 1,
          symbol: trade.symbol,
          side: trade.direction,
          entry: trade.entry_price,
          exit: trade.exit_price,
          pnl: trade.pnl,
          time: trade.timestamp,
        }));
        setTrades(mappedTrades);

        // Map risk metrics
        setRisk({
          circuitBreaker: !data.gate_passed,
          dailyLoss: 0, // Placeholder - not available in API
          tradesToday: data.total_trades,
        });

        // Map equity curve
        const mappedEquity = data.equity_curve.map((point, index) => ({
          name: String(index),
          value: point,
        }));
        setEquityData(mappedEquity);

        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) return <div className="p-6">Loading...</div>;
  if (error) return <div className="p-6 text-red-500">Error: {error}</div>;

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Synthetic Trader Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {kpis.map((kpi, index) => (
          <div key={index} className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
            <h3 className="text-sm font-medium text-gray-400 mb-2">{kpi.label}</h3>
            <p className="text-2xl font-bold text-white">{typeof kpi.value === 'string' ? kpi.value : `$${kpi.value}`}</p>
            {kpi.change && <p className="text-sm">{kpi.change.startsWith('+') ? 'text-green-500' : 'text-red-500'}>{kpi.change}</p>}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
          <h2 className="text-xl font-bold mb-4">Equity Curve</h2>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={equityData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="value" stroke="#8884d8" activeDot={{ r: 8 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
          <h2 className="text-xl font-bold mb-4">Trade Log</h2>
          <div className="h-96 overflow-y-auto">
            <table className="w-full text-sm text-gray-300">
              <thead>
                <tr className="border-b">
                  <th className="text-left p-2">ID</th>
                  <th className="text-left p-2">Symbol</th>
                  <th className="text-left p-2">Side</th>
                  <th className="text-left p-2">Entry</th>
                  <th className="text-left p-2">Exit</th>
                  <th className="text-left p-2">P&L</th>
                  <th className="text-left p-2">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => (
                  <tr key={trade.id} className="border-t">
                    <td className="p-2">{trade.id}</td>
                    <td className="p-2">{trade.symbol}</td>
                    <td className="p-2">{trade.side}</td>
                    <td className="p-2">{trade.entry.toFixed(2)}</td>
                    <td className="p-2">{trade.exit.toFixed(2)}</td>
                    <td className="p-2">{trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>{trade.pnl.toFixed(2)}</td>
                    <td className="p-2">{trade.time}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
          <h2 className="text-xl font-bold mb-4">Risk Metrics</h2>
          <div className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-400">Circuit Breaker:</span>
              <span className={risk.circuitBreaker ? 'text-red-400 font-bold' : 'text-green-400 font-bold'}>
                {risk.circuitBreaker ? 'ACTIVE' : 'INACTIVE'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Daily Loss:</span>
              <span className="text-gray-200">$${risk.dailyLoss.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Trades Today:</span>
              <span className="text-gray-200">{risk.tradesToday}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;