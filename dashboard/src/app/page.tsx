'use client';

import { useEffect, useState } from 'react';
import { fetchAPI } from '@/lib/api';
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from 'recharts';

// ---- Types matching the live API responses ----

interface BotStatus {
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
    halt_reason: string;
    today: string;
  };
  kpi: {
    sharpe_ratio: number;
    win_rate: number;
    max_drawdown: number;
    profit_factor: number;
    expectancy: number;
  };
  last_update: string;
  // Spanish aliases
  saldo?: number;
  resultado_operaciones?: number;
  operaciones_hoy?: number;
}

interface TradeRecord {
  timestamp: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  stop_loss: number;
  take_profit: number;
  stake: number;
  confidence: number;
  pnl: number;
  exit_reason: string;
  status: string;
}

interface KPI {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
}

const Dashboard = () => {
  const [balance, setBalance] = useState<number>(0);
  const [pnl, setPnl] = useState<number>(0);
  const [winRate, setWinRate] = useState<number>(0);
  const [sharpe, setSharpe] = useState<number>(0);
  const [tradesToday, setTradesToday] = useState<number>(0);
  const [circuitBreakerActive, setCircuitBreakerActive] = useState<boolean>(false);
  const [consecutiveLosses, setConsecutiveLosses] = useState<number>(0);
  const [isHalted, setIsHalted] = useState<boolean>(false);
  const [haltReason, setHaltReason] = useState<string>('');
  const [symbol, setSymbol] = useState<string>('');
  const [mode, setMode] = useState<string>('');
  const [lastUpdate, setLastUpdate] = useState<string>('');
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [equityData, setEquityData] = useState<{ name: string; value: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ maxDrawdown, setMaxDrawdown] = useState<number>(0);
  const [profitFactor, setProfitFactor] = useState<number>(0);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        // Fetch bot status + trades in parallel
        const [status, tradesRes] = await Promise.all([
          fetchAPI<BotStatus>('/api/bot/status'),
          fetchAPI<TradeRecord[]>('/api/bot/trades').catch(() => []),
        ]);

        setBalance(status.balance);
        setPnl(status.pnl);
        setTradesToday(status.trades_today);
        setCircuitBreakerActive(status.circuit_breaker?.is_halted ?? false);
        setConsecutiveLosses(status.circuit_breaker?.consecutive_losses ?? 0);
        setIsHalted(status.is_halted);
        setHaltReason(status.circuit_breaker?.halt_reason ?? '');
        setSymbol(status.symbol);
        setMode(status.mode);
        setLastUpdate(status.last_update);
        setWinRate(status.kpi?.win_rate ?? 0);
        setSharpe(status.kpi?.sharpe_ratio ?? 0);
        setMaxDrawdown(status.kpi?.max_drawdown ?? 0);
        setProfitFactor(status.kpi?.profit_factor ?? 0);

        // Map trades
        const tradeList = Array.isArray(tradesRes) ? tradesRes : [];
        setTrades(tradeList);

        // Build equity curve from trades
        let runningBalance = status.balance - status.pnl; // starting
        const equityPoints = [{ name: 'Inicio', value: runningBalance }];
        for (const t of tradeList) {
          runningBalance += t.pnl;
          equityPoints.push({
            name: new Date(t.timestamp).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' }),
            value: runningBalance,
          });
        }
        // Add current balance as last point
        equityPoints.push({ name: 'Ahora', value: status.balance });
        setEquityData(equityPoints);

        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error desconocido');
        setLoading(false);
      }
    };

    fetchAll();
    // Refresh every 15 seconds
    const interval = setInterval(fetchAll, 15000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-screen">
        <div className="text-gray-400 text-lg">Cargando datos del bot...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 flex flex-col items-center justify-center min-h-screen">
        <div className="text-red-400 text-xl mb-4">Error de conexión</div>
        <div className="text-gray-500 mb-2">{error}</div>
        <div className="text-gray-600 text-sm">
          Verifica que la API esté corriendo en el puerto 8001:
          <br />
          <code className="text-green-400">uvicorn src.api.server:app --port 8001</code>
        </div>
      </div>
    );
  }

  const fmt = (n: number, decimals = 2) =>
    n.toLocaleString('es-ES', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  const kpis: KPI[] = [
    {
      label: 'Balance',
      value: `$${fmt(balance)}`,
      change: pnl >= 0 ? `+${fmt((pnl / (balance - pnl || 1)) * 100)}%` : `${fmt((pnl / (balance - pnl || 1)) * 100)}%`,
      positive: pnl >= 0,
    },
    {
      label: 'Resultado de operaciones',
      value: `${pnl >= 0 ? '+' : ''}$${fmt(pnl)}`,
      positive: pnl >= 0,
    },
    {
      label: 'Tasa de aciertos',
      value: `${(winRate * 100).toFixed(1)}%`,
    },
    {
      label: 'Índice de rendimiento (Sharpe)',
      value: sharpe.toFixed(2),
    },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Synthetic Trader — Panel principal</h1>
          <div className="text-gray-500 text-sm mt-1">
            Símbolo: <span className="text-gray-300">{symbol}</span> · Modo: <span className="text-gray-300">{mode}</span>
          </div>
        </div>
        <div className="text-right">
          {isHalted ? (
            <span className="px-3 py-1 bg-red-900 text-red-200 rounded-full text-sm font-semibold">
              ⛔ Bot detenido
            </span>
          ) : (
            <span className="px-3 py-1 bg-green-900 text-green-200 rounded-full text-sm font-semibold">
              ● En operación
            </span>
          )}
          {tradesToday > 0 && (
            <div className="text-gray-500 text-xs mt-1">Última actualización: {new Date(lastUpdate).toLocaleTimeString('es-ES')}</div>
          )}
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {kpis.map((kpi, i) => (
          <div key={i} className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
            <h3 className="text-sm font-medium text-gray-400 mb-2">{kpi.label}</h3>
            <p className="text-2xl font-bold text-white">{kpi.value}</p>
            {kpi.change && (
              <p className={`text-sm ${kpi.positive ? 'text-green-500' : 'text-red-500'}`}>{kpi.change}</p>
            )}
          </div>
        ))}
      </div>

      {/* Charts + tables */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Equity curve */}
        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700 lg:col-span-2">
          <h2 className="text-xl font-bold mb-4 text-white">Curva de capital</h2>
          {equityData.length > 1 ? (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={equityData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="name" stroke="#6b7280" />
                <YAxis stroke="#6b7280" domain={['auto', 'auto']} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '8px' }}
                  labelStyle={{ color: '#e5e7eb' }}
                />
                <Legend />
                <Line type="monotone" dataKey="value" stroke="#8884d8" activeDot={{ r: 8 }} name="Balance" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-300 flex items-center justify-center text-gray-500">
              Sin datos suficientes para mostrar la curva. El bot está acumulando operaciones.
            </div>
          )}
        </div>

        {/* Risk panel */}
        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700">
          <h2 className="text-xl font-bold mb-4 text-white">Métricas de riesgo</h2>
          <div className="space-y-3">
            <div className="flex justify-between">
              <span className="text-gray-400">Cortacircuitos:</span>
              <span className={circuitBreakerActive ? 'text-red-400 font-bold' : 'text-green-400 font-bold'}>
                {circuitBreakerActive ? 'ACTIVO' : 'INACTIVO'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Pérdidas consecutivas:</span>
              <span className={consecutiveLosses >= 3 ? 'text-red-400 font-bold' : 'text-gray-200'}>
                {consecutiveLosses}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Caída máxima:</span>
              <span className="text-gray-200">{(maxDrawdown * 100).toFixed(2)}%</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Factor de beneficio:</span>
              <span className="text-gray-200">{profitFactor.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Operaciones hoy:</span>
              <span className="text-gray-200">{tradesToday}</span>
            </div>
            {isHalted && haltReason && (
              <div className="mt-4 p-3 bg-red-900 bg-opacity-30 rounded-lg border border-red-800">
                <span className="text-red-400 text-sm">Motivo de parada: {haltReason}</span>
              </div>
            )}
          </div>
        </div>

        {/* Trade log */}
        <div className="bg-gray-800 bg-opacity-50 backdrop-blur-sm rounded-xl p-4 border border-gray-700 lg:col-span-3">
          <h2 className="text-xl font-bold mb-4 text-white">Registro de operaciones</h2>
          {trades.length === 0 ? (
            <div className="text-gray-500 py-8 text-center">
              El bot aún no ha ejecutado operaciones. Esperando señales del mercado...
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-gray-300">
                <thead>
                  <tr className="border-b border-gray-700">
                    <th className="text-left p-2">Hora</th>
                    <th className="text-left p-2">Dirección</th>
                    <th className="text-right p-2">Entrada</th>
                    <th className="text-right p-2">Salida</th>
                    <th className="text-right p-2">Stop de pérdida</th>
                    <th className="text-right p-2">Objetivo de ganancia</th>
                    <th className="text-right p-2">Stake</th>
                    <th className="text-right p-2">Confianza</th>
                    <th className="text-right p-2">Resultado</th>
                    <th className="text-left p-2">Motivo de salida</th>
                    <th className="text-left p-2">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.slice().reverse().map((trade, i) => {
                    const stake = Number(trade.stake) || 0;
                    const confidence = Number(trade.confidence) || 0;
                    const pnlVal = Number(trade.pnl) || 0;
                    const entry = Number(trade.entry_price) || 0;
                    const exit = Number(trade.exit_price) || 0;
                    const sl = Number(trade.stop_loss) || 0;
                    const tp = Number(trade.take_profit) || 0;
                    return (
                    <tr key={i} className="border-t border-gray-700 hover:bg-gray-700 hover:bg-opacity-30">
                      <td className="p-2 text-gray-400">{trade.timestamp ? new Date(trade.timestamp).toLocaleString('es-ES') : '—'}</td>
                      <td className="p-2">
                        <span className={trade.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}>
                          {trade.direction || '—'}
                        </span>
                      </td>
                      <td className="p-2 text-right">{entry ? entry.toFixed(5) : '—'}</td>
                      <td className="p-2 text-right">{exit ? exit.toFixed(5) : '—'}</td>
                      <td className="p-2 text-right text-red-300">{sl ? sl.toFixed(5) : '—'}</td>
                      <td className="p-2 text-right text-green-300">{tp ? tp.toFixed(5) : '—'}</td>
                      <td className="p-2 text-right">{`$${stake.toFixed(2)}`}</td>
                      <td className="p-2 text-right">{`${(confidence * 100).toFixed(0)}%`}</td>
                      <td className={`p-2 text-right font-semibold ${pnlVal >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {`${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(4)}`}
                      </td>
                      <td className="p-2 text-gray-400">{trade.exit_reason || '—'}</td>
                      <td className="p-2">
                        <span className={`px-2 py-1 rounded text-xs ${
                          trade.status === 'WON' ? 'bg-green-900 text-green-300' :
                          trade.status === 'LOST' ? 'bg-red-900 text-red-300' :
                          'bg-gray-700 text-gray-300'
                        }`}>
                          {trade.status || '—'}
                        </span>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
