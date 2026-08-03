'use client';

import { useEffect, useState } from 'react';
import { fetchAPI } from '@/lib/api';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Area, AreaChart } from 'recharts';

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
  duration_seconds?: number;
  score?: number;
}

interface KPI {
  label: string;
  value: string;
  sub?: string;
  accent: 'cyan' | 'green' | 'red' | 'neutral';
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
  const [maxDrawdown, setMaxDrawdown] = useState<number>(0);
  const [profitFactor, setProfitFactor] = useState<number>(0);

  useEffect(() => {
    const fetchAll = async () => {
      try {
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

        const tradeList = Array.isArray(tradesRes) ? tradesRes : [];
        setTrades(tradeList);

        // Build equity curve from trades
        const runningBalance = status.balance - status.pnl;
        const equityPoints: { name: string; value: number }[] = [{ name: 'Inicio', value: runningBalance }];
        let running = runningBalance;
        for (const t of tradeList) {
          running += Number(t.pnl) || 0;
          const ts = t.timestamp
            ? new Date(t.timestamp).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
            : `T${equityPoints.length}`;
          equityPoints.push({ name: ts, value: running });
        }
        equityPoints.push({ name: 'Ahora', value: status.balance });
        setEquityData(equityPoints);

        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error desconocido');
        setLoading(false);
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 10000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div style={{ padding: '24px', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 'calc(100vh - 52px)' }}>
        <div style={{ color: 'var(--text-muted)', fontSize: '14px' }}>Inicializando SynthIA Terminal...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 'calc(100vh - 52px)' }}>
        <div style={{ color: 'var(--accent-red)', fontSize: '18px', marginBottom: '12px' }}>Error de conexión</div>
        <div style={{ color: 'var(--text-muted)', fontSize: '13px', marginBottom: '8px' }}>{error}</div>
        <div style={{ color: 'var(--text-muted)', fontSize: '12px' }}>
          Verifica que la API esté corriendo en el puerto 8001
        </div>
      </div>
    );
  }

  // Format helpers
  const fmtPrice = (n: number) =>
    n ? n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const fmtUSD = (n: number, decimals = 2) =>
    n ? n.toLocaleString('es-ES', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '0';
  const fmtTime = (ts: string) => {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return '—'; }
  };

  const accentColor = pnl > 0 ? 'var(--accent-green)' : pnl < 0 ? 'var(--accent-red)' : 'var(--accent-cyan)';

  const kpis: KPI[] = [
    {
      label: 'Balance',
      value: `$${fmtUSD(balance)}`,
      sub: pnl !== 0 ? `${pnl >= 0 ? '+' : ''}$${fmtUSD(pnl)}` : undefined,
      accent: pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'cyan',
    },
    {
      label: 'Operaciones hoy',
      value: `${tradesToday}`,
      sub: tradesToday > 0 ? `${trades.length} en registro` : 'Esperando señales',
      accent: 'cyan',
    },
    {
      label: 'Tasa de aciertos',
      value: winRate > 0 ? `${(winRate * 100).toFixed(1)}%` : '—',
      accent: 'neutral',
    },
    {
      label: 'Índice Sharpe',
      value: sharpe !== 0 ? sharpe.toFixed(2) : '—',
      accent: 'neutral',
    },
  ];

  const accentMap = {
    cyan: 'var(--accent-cyan)',
    green: 'var(--accent-green)',
    red: 'var(--accent-red)',
    neutral: 'var(--text-muted)',
  };

  // Circuit breaker progress (0-100%, 3 losses = halt)
  const cbProgress = Math.min((consecutiveLosses / 3) * 100, 100);

  return (
    <div style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--foreground)', letterSpacing: '-0.3px' }}>
            Panel de control
          </h1>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginTop: '4px' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--accent-cyan)' }}>{symbol || 'R_100'}</span>
            <span style={{ color: 'var(--border-hover)', fontSize: '11px' }}>·</span>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{mode || 'paper'}</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '5px 12px',
            borderRadius: '4px',
            border: `1px solid ${isHalted ? 'var(--accent-red)' : 'var(--accent-green)'}`,
            background: isHalted ? 'rgba(255, 56, 96, 0.08)' : 'rgba(0, 255, 157, 0.06)',
          }}>
            <span style={{
   width: '7px',
   height: '7px',
   borderRadius: '50%',
   background: isHalted ? 'var(--accent-red)' : 'var(--accent-green)',
   boxShadow: `0 0 6px ${isHalted ? 'var(--accent-red)' : 'var(--accent-green)'}`,
   animation: isHalted ? 'none' : 'pulse-dot 2s ease-in-out infinite',
 }} />
            <span style={{
              fontSize: '11px',
              fontWeight: 600,
              color: isHalted ? 'var(--accent-red)' : 'var(--accent-green)',
              letterSpacing: '0.5px',
              textTransform: 'uppercase',
            }}>
              {isHalted ? 'Detenido' : 'En operación'}
            </span>
          </div>
          {lastUpdate && (
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '6px', fontFamily: 'var(--font-mono)' }}>
              {fmtTime(lastUpdate)}
            </div>
          )}
        </div>
      </div>

      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px', marginBottom: '20px' }}>
        {kpis.map((kpi, i) => {
          const color = accentMap[kpi.accent];
          return (
            <div key={i} style={{
              background: 'var(--card)',
              border: '1px solid var(--border)',
              borderLeft: `2px solid ${color}`,
              borderRadius: 'var(--radius)',
              padding: '14px 16px',
              transition: 'border-color 0.2s ease',
            }}>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px', letterSpacing: '0.3px', textTransform: 'uppercase' }}>
                {kpi.label}
              </div>
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: '22px',
                fontWeight: 700,
                color: 'var(--foreground)',
                fontVariantNumeric: 'tabular-nums',
              }}>
                {kpi.value}
              </div>
              {kpi.sub && (
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '12px',
                  marginTop: '2px',
                  color: kpi.accent === 'green' ? 'var(--accent-green)' : kpi.accent === 'red' ? 'var(--accent-red)' : 'var(--text-muted)',
                }}>
                  {kpi.sub}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Charts + Risk Panel */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '16px' }}>
        {/* Equity curve */}
        <div style={{
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '16px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <h2 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.3px', textTransform: 'uppercase' }}>
              Curva de capital
            </h2>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '14px',
              fontWeight: 700,
              color: accentColor,
            }}>
              ${fmtUSD(balance)}
            </span>
          </div>
          {equityData.length > 1 ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityData}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="1 0" stroke="#1e2530" vertical={false} />
                <XAxis dataKey="name" stroke="#5a6577" fontSize={10} tickLine={false} axisLine={false} />
                <YAxis stroke="#5a6577" fontSize={10} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={50} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0a0e14',
                    border: '1px solid #1e2530',
                    borderRadius: '4px',
                    fontSize: '12px',
                    fontFamily: 'var(--font-mono)',
                  }}
                  labelStyle={{ color: '#5a6577' }}
                  itemStyle={{ color: '#00d4ff' }}
                />
                <Area type="monotone" dataKey="value" stroke="#00d4ff" strokeWidth={2} fill="url(#equityGrad)" activeDot={{ r: 4, stroke: '#00d4ff', strokeWidth: 1 }} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: '200px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
              El bot está acumulando operaciones...
            </div>
          )}
        </div>

        {/* Risk panel */}
        <div style={{
          background: 'var(--card)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          padding: '16px',
        }}>
          <h2 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.3px', textTransform: 'uppercase', marginBottom: '14px' }}>
            Monitoreo de riesgo
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {/* Circuit breaker bar */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>Cortacircuitos</span>
                <span style={{ fontSize: '11px', fontWeight: 600, color: circuitBreakerActive ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                  {circuitBreakerActive ? 'ACTIVO' : 'OK'}
                </span>
              </div>
              <div style={{ height: '4px', background: 'var(--border)', borderRadius: '2px', overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${cbProgress}%`,
                  background: cbProgress >= 100 ? 'var(--accent-red)' : cbProgress >= 66 ? 'var(--accent-amber)' : 'var(--accent-cyan)',
                  transition: 'width 0.5s ease',
                }} />
              </div>
              <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>
                {consecutiveLosses} / 3 pérdidas consecutivas
              </div>
            </div>

            <div style={{ height: '1px', background: 'var(--border)', margin: '4px 0' }} />

            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Caída máxima</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--foreground)' }}>
                {maxDrawdown > 0 ? `${(maxDrawdown * 100).toFixed(2)}%` : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Factor de beneficio</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--foreground)' }}>
                {profitFactor > 0 ? profitFactor.toFixed(2) : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Operaciones hoy</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'var(--accent-cyan)' }}>{tradesToday}</span>
            </div>

            {isHalted && haltReason && (
              <div style={{
                padding: '10px',
                background: 'rgba(255, 56, 96, 0.08)',
                border: '1px solid var(--accent-red)',
                borderRadius: 'var(--radius)',
                marginTop: '4px',
              }}>
                <span style={{ fontSize: '12px', color: 'var(--accent-red)' }}>{haltReason}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Trade log */}
      <div style={{
        background: 'var(--card)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        padding: '16px',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h2 style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-muted)', letterSpacing: '0.3px', textTransform: 'uppercase' }}>
            Registro de operaciones
          </h2>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--text-muted)' }}>
            {trades.length} {trades.length === 1 ? 'trade' : 'trades'}
          </span>
        </div>
        {trades.length === 0 ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            Esperando señales del mercado...
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: '12px', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Hora', 'Dir', 'Entrada', 'Salida', 'SL', 'TP', 'Stake', 'Score', 'P&L', 'Estado'].map((h, i) => (
                    <th key={i} style={{
                      textAlign: i >= 2 && i <= 8 ? 'right' : 'left',
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--border)',
                      color: 'var(--text-muted)',
                      fontSize: '10px',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.3px',
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.slice().reverse().map((trade, i) => {
                  const pnlVal = Number(trade.pnl) || 0;
                  const entry = Number(trade.entry_price) || 0;
                  const exit = Number(trade.exit_price) || 0;
                  const sl = Number(trade.stop_loss) || 0;
                  const tp = Number(trade.take_profit) || 0;
                  const stake = Number(trade.stake) || 0;
                  const score = Number(trade.score) || Number(trade.confidence) || 0;
                  const isLong = trade.direction === 'LONG';
                  const isWin = trade.status === 'WON' || trade.status === 'GANADA';
                  const isLost = trade.status === 'LOST' || trade.status === 'PERDIDA';
                  return (
                    <tr
                      key={i}
                      style={{ borderBottom: '1px solid var(--border)', transition: 'background 0.15s ease' }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(0, 212, 255, 0.04)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', fontSize: '11px' }}>
                        {fmtTime(trade.timestamp)}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: '11px',
                          fontWeight: 700,
                          padding: '1px 6px',
                          borderRadius: '2px',
                          color: isLong ? 'var(--accent-green)' : 'var(--accent-red)',
                          background: isLong ? 'rgba(0, 255, 157, 0.08)' : 'rgba(255, 56, 96, 0.08)',
                        }}>
                          {isLong ? 'LONG' : 'SHORT'}
                        </span>
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--foreground)' }}>
                        {entry ? fmtPrice(entry) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                        {exit ? fmtPrice(exit) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'rgba(255, 56, 96, 0.7)', fontSize: '11px' }}>
                        {sl ? fmtPrice(sl) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'rgba(0, 255, 157, 0.7)', fontSize: '11px' }}>
                        {tp ? fmtPrice(tp) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                        {stake ? `$${fmtUSD(stake)}` : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', fontSize: '11px' }}>
                        {score ? `${(score * 100).toFixed(0)}%` : '—'}
                      </td>
                      <td style={{
                        padding: '6px 8px',
                        textAlign: 'right',
                        fontFamily: 'var(--font-mono)',
                        fontWeight: 600,
                        color: pnlVal > 0 ? 'var(--accent-green)' : pnlVal < 0 ? 'var(--accent-red)' : 'var(--text-muted)',
                      }}>
                        {pnlVal !== 0 ? `${pnlVal >= 0 ? '+' : ''}$${fmtUSD(pnlVal)}` : '$0.00'}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{
                          display: 'inline-block',
                          padding: '2px 8px',
                          borderRadius: '2px',
                          fontSize: '10px',
                          fontWeight: 600,
                          letterSpacing: '0.3px',
                          ...(isWin
                            ? { color: 'var(--accent-green)', background: 'rgba(0, 255, 157, 0.1)' }
                            : isLost
                              ? { color: 'var(--accent-red)', background: 'rgba(255, 56, 96, 0.1)' }
                              : { color: 'var(--text-muted)', background: 'var(--border)' })
                        }}>
                          {isWin ? 'GANADA' : isLost ? 'PERDIDA' : trade.status || '—'}
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
  );
};

export default Dashboard;
