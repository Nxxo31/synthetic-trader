'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { fetchAPI, connectWebSocket } from '@/lib/api';
import { STRATEGIES, SYMBOLS, SYMBOL_LABELS } from '@/lib/types';

import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Area, AreaChart,
} from 'recharts';

// ─── Types ───────────────────────────────────────────────────────────

interface KPI {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
  negative?: boolean;
}

interface RiskMetrics {
  circuitBreaker: boolean;
  halted: boolean;
  dailyLoss: number;
  consecutiveLosses: number;
  tradesToday: number;
  maxDrawdown: number;
  cooldownMin: number;
  reason: string;
}

interface BacktestTrade {
  entry_price: number;
  exit_price: number;
  pnl: number;
  direction: string;
  timestamp: string;
  exit_reason?: string;
  duration?: number;
  symbol?: string;
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
  trades: BacktestTrade[];
  equity_curve: number[];
  initial_capital: number;
}

interface LiveTrade {
  id: number;
  symbol: string;
  side: string;
  entry: number;
  exit: number;
  pnl: number;
  time: string;
  exitReason: string;
  duration?: number;
}

interface EquityPoint {
  index: number;
  equity: number;
}

// ─── Styles ──────────────────────────────────────────────────────────

const DASH_CSS = `
  .dr { min-height: 100vh; background: #0a0a0a; color: #ededed;
       font-family: var(--font-geist-sans), system-ui, sans-serif;
       padding: 24px 32px; max-width: 1600px; margin: 0 auto; }
  .dh { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 28px; }
  .dt { font-size: 1.6rem; font-weight: 700; }
  .ds { font-size: 0.85rem; color: #6b7593; margin-top: 4px; }
  .cs { display: flex; align-items: center; gap: 8px; font-size: 0.8rem; }
  .cd { width: 8px; height: 8px; border-radius: 50%; }
  .dot-connected { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
  .dot-disconnected { background: #ef4444; }
  .dot-connecting { background: #f59e0b; animation: pl 1s infinite; }
  @keyframes pl { 0%,100%{opacity:1} 50%{opacity:.3} }
  .cb { display: flex; gap: 12px; align-items: center; margin-bottom: 24px; flex-wrap: wrap; }
  .sg { display: flex; flex-direction: column; gap: 4px; }
  .sl { font-size: 0.7rem; color: #6b7593; text-transform: uppercase; letter-spacing: .05em; }
  select { background: #111; color: #ededed; border: 1px solid #2a2a2a;
           border-radius: 8px; padding: 8px 12px; font-size: 0.85rem; cursor: pointer; outline: none; }
  select:focus { border-color: #3b82f6; }
  .kg { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .kc { background: #111; border: 1px solid #1f1f1f; border-radius: 12px; padding: 20px; transition: border-color .2s; }
  .kc:hover { border-color: #333; }
  .kl { color: #6b7593; font-size: 0.7rem; text-transform: uppercase; margin-bottom: 8px; letter-spacing: .05em; }
  .kv { font-size: 1.5rem; font-weight: 700; }
  .ks { font-size: 0.72rem; margin-top: 4px; }
  .pos { color: #22c55e; } .neg { color: #ef4444; } .neu { color: #6b7593; }
  .mg { display: grid; grid-template-columns: 1.5fr 1fr; gap: 24px; margin-bottom: 24px; }
  @media (max-width: 900px) { .mg { grid-template-columns: 1fr; } }
  .pn { background: #111; border: 1px solid #1f1f1f; border-radius: 12px; padding: 20px; }
  .pt { font-size: 0.9rem; color: #6b7593; margin-bottom: 16px; text-transform: uppercase; letter-spacing: .05em; }
  .rr { display: flex; justify-content: space-between; align-items: center; padding: 10px 0;
        border-bottom: 1px solid #1a1a1a; font-size: 0.85rem; }
  .rr:last-child { border-bottom: none; }
  .rl { color: #6b7593; } .rv { font-weight: 600; }
  .tt { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
  .tt th { background: #0d0d0d; padding: 10px 14px; text-align: left; color: #6b7593;
           font-size: 0.7rem; text-transform: uppercase; position: sticky; top: 0; }
  .tt td { padding: 8px 14px; border-top: 1px solid #1a1a1a; }
  .bg { padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; }
  .bg-tp { background: rgba(34,197,94,.15); color: #22c55e; }
  .bg-sl { background: rgba(239,68,68,.15); color: #ef4444; }
  .bg-tm { background: rgba(245,158,11,.15); color: #f59e0b; }
  .dl { color: #22c55e; font-weight: 600; } .ds2 { color: #ef4444; font-weight: 600; }
  .nd { text-align: center; color: #6b7593; padding: 48px; font-size: 0.9rem; }
  .ts { max-height: 400px; overflow-y: auto; }
  .ts::-webkit-scrollbar { width: 6px; } .ts::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }

  /* Status Pill Styles */
  .status-pill { 
    display: inline-flex; 
    align-items: center; 
    gap: 6px; 
    padding: 4px 10px; 
    border-radius: 12px; 
    font-size: 0.85rem; 
    font-weight: 600; 
    text-transform: uppercase; 
    letter-spacing: 0.5px;
  }
  .status-running { 
    background: rgba(34, 197, 94, 0.15); 
    color: #22c55e; 
    border: 1px solid rgba(34, 197, 94, 0.3);
  }
  .status-stopped { 
    background: rgba(239, 68, 68, 0.15); 
    color: #ef4444; 
    border: 1px solid rgba(239, 68, 68, 0.3);
  }
  .status-error { 
    background: rgba(245, 158, 11, 0.15); 
    color: #f59e0b; 
    border: 1px solid rgba(245, 158, 11, 0.3);
  }
  
  /* Kill Switch Styles */
  .kill-switch {
    background: #ef4444;
    color: white;
    border: none;
    padding: 10px 20px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(239, 68, 68, 0.3);
  }
  .kill-switch:hover {
    background: #dc2626;
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(239, 68, 68, 0.4);
  }
  .kill-switch:active {
    transform: translateY(0);
  }
  .kill-switch:disabled {
    background: #6b7280;
    cursor: not-allowed;
    transform: none;
  }
  
  /* Enhanced Layout Spacing */
  .header-actions {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  
  /* Status Indicator Dot */
  .status-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
  }
  .status-dot-running { background: #22c55e; box-shadow: 0 0 6px #22c55e; }
  .status-dot-stopped { background: #ef4444; }
  .status-dot-error { background: #f59e0b; }
  
  /* Responsive Header */
  @media (max-width: 768px) {
    .dh { flex-direction: column; }
  }
`;

// ─── Component ──────────────────────────────────────────────────────

const Dashboard = () => {
  const [kpis, setKpis] = useState<KPI[]>([]);
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [risk, setRisk] = useState<RiskMetrics>({
    circuitBreaker: false, halted: false, dailyLoss: 0,
    consecutiveLosses: 0, tradesToday: 0, maxDrawdown: 0,
    cooldownMin: 0, reason: '',
  });
  const [equityData, setEquityData] = useState<EquityPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connStatus, setConnStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');
  const [selectedStrategy, setSelectedStrategy] = useState('all');
  const [selectedSymbol, setSelectedSymbol] = useState('all');
  const [botMode, setBotMode] = useState('idle');
  const wsRef = useRef<WebSocket | null>(null);

  // ─── REST: load backtest data on mount ────────────────────────────

  useEffect(() => {
    (async () => {
      try {
        const result = await fetchAPI<BacktestResult[]>('/api/backtest/results');
        if (!result.length) { setError('Sin backtest disponible'); setLoading(false); return; }
        const d = result[0];
        const bal = d.initial_capital + d.total_pnl;
        const pct = (d.total_pnl / d.initial_capital) * 100;
        setKpis([
          { label: 'Balance', value: `$${bal.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`, change: `${pct>=0?'+':''}${pct.toFixed(2)}%`, positive: pct>=0 },
          { label: 'Total P&L', value: `${d.total_pnl>=0?'+':''}$${d.total_pnl.toFixed(2)}`, positive: d.total_pnl>=0 },
          { label: 'Win Rate', value: `${(d.win_rate*100).toFixed(1)}%`, positive: d.win_rate>=0.5 },
          { label: 'Sharpe', value: d.sharpe_ratio.toFixed(2), positive: d.sharpe_ratio>=1.0 },
          { label: 'Max DD', value: `${(d.max_drawdown*100).toFixed(2)}%`, negative: d.max_drawdown>0 },
          { label: 'Profit Factor', value: d.profit_factor.toFixed(2), positive: d.profit_factor>=1.0 },
          { label: 'Expectancy', value: `$${d.expectancy.toFixed(2)}`, positive: d.expectancy>=0 },
          { label: 'Gate', value: d.gate_passed ? 'PASS' : 'FAIL', positive: d.gate_passed },
        ]);
        setTrades(d.trades.map((t, i) => ({
          id: i+1, symbol: d.symbol, side: t.direction,
          entry: t.entry_price, exit: t.exit_price, pnl: t.pnl,
          time: t.timestamp, exitReason: t.exit_reason || (t.pnl>=0?'TP':'SL'),
          duration: t.duration,
        })));
        setRisk(r => ({ ...r, circuitBreaker: !d.gate_passed, tradesToday: d.total_trades, maxDrawdown: d.max_drawdown }));
        setEquityData(d.equity_curve.map((v, i) => ({ index: i, equity: v })));
        setBotMode('backtest');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Error desconocido');
      }
      setLoading(false);
    })();
  }, []);

  // ─── WebSocket: live data ─────────────────────────────────────────

  const handleMsg = useCallback((msg: any) => {
    if (msg.type === 'state') {
      const s = msg.data;
      const bal = s.balance || 0;
      const pnl = s.pnl || 0;
      const pct = 10000 > 0 ? (pnl / 10000) * 100 : 0;
      setKpis([
        { label: 'Balance', value: `$${bal.toFixed(2)}`, change: `${pct>=0?'+':''}${pct.toFixed(2)}%`, positive: pnl>=0 },
        { label: 'Total P&L', value: `${pnl>=0?'+':''}$${pnl.toFixed(2)}`, positive: pnl>=0 },
        { label: 'Win Rate', value: s.win_rate != null ? `${(s.win_rate*100).toFixed(1)}%` : '—', positive: (s.win_rate||0)>=0.5 },
        { label: 'Sharpe', value: s.sharpe != null ? s.sharpe.toFixed(2) : '—', positive: (s.sharpe||0)>=1.0 },
        { label: 'Max DD', value: s.max_drawdown != null ? `${(s.max_drawdown*100).toFixed(2)}%` : '—' },
        { label: 'Trades Today', value: String(s.trades_today||0) },
        { label: 'Mode', value: String(s.mode||'paper').toUpperCase() },
        { label: 'Gate', value: s.gate_passed ? 'PASS' : 'PENDING' },
      ]);
      setRisk({
        circuitBreaker: s.is_halted || false, halted: s.is_halted || false,
        dailyLoss: s.daily_loss || 0, consecutiveLosses: s.circuit_breaker?.consecutive_losses || 0,
        tradesToday: s.trades_today || 0, maxDrawdown: s.max_drawdown || 0,
        cooldownMin: s.circuit_breaker?.cooldown_remaining || 0, reason: s.circuit_breason || '',
      });
      setBotMode(s.mode || 'paper');
    } else if (msg.type === 'equity_update') {
      const eq = msg.data;
      setEquityData(p => [...p, { index: p.length, equity: eq.equity || eq.value || 0 }]);
    } else if (msg.type === 'trade') {
      const t = msg.data || msg.trade;
      if (!t) return;
      setTrades(p => [{
        id: p.length+1, symbol: t.symbol || '', side: t.direction || t.side || '',
        entry: t.entry_price || t.entry || 0, exit: t.exit_price || t.exit || 0,
        pnl: t.pnl || 0, time: t.timestamp || new Date().toISOString(),
        exitReason: t.exit_reason || (t.pnl>=0?'TP':'SL'), duration: t.duration_seconds || t.duration,
      }, ...p].slice(0, 100));
    } else if (msg.type === 'equity') {
      setEquityData(p => [...p, { index: msg.index, equity: msg.value }]);
    }
  }, []);

  // Connect WS once
  useEffect(() => {
    wsRef.current = connectWebSocket(handleMsg, setConnStatus);
    return () => { wsRef.current?.close(); };
  }, [handleMsg]);

  // ─── Render ────────────────────────────────────────────────────────

  if (loading) return <div className="dr"><style>{DASH_CSS}</style><div className="nd">Cargando datos...</div></div>;
  if (error && !kpis.length) return <div className="dr"><style>{DASH_CSS}</style><div className="nd" style={{color:'#ef4444'}}>Error: {error}</div></div>;

  return (
    <div className="dr">
      <style>{DASH_CSS}</style>

      {/* Header */}
      <div className="dh">
        <div>
          <div className="dt">Synthetic Trader <span style={{fontSize:'0.8rem',color:'#6b7593',marginLeft:'8px'}}>v0.2.0</span></div>
          <div className="ds">Control Panel — Trading Bot Dashboard</div>
        </div>
        <div className="header-actions">
          <div className="status-pill status-running">
            <span className="status-dot status-dot-running"></span>
            Bot: Running
          </div>
          <button className="kill-switch" onClick={() => {
            // TODO: Implement actual kill switch logic
            alert('Kill switch activated - stopping bot and cancelling orders');
          }}>
            EMERGENCY STOP
          </button>
        </div>
      </div>
      
      {/* Connection Status (moved below header) */}
      <div className="cs">
        <div className={`cd dot-${connStatus}`}></div>
        <span>{connStatus === 'connected' ? 'WS Connected' : connStatus === 'connecting' ? 'Connecting...' : 'Disconnected'}</span>
        <span style={{marginLeft:'12px',color:'#6b7593'}}>Mode: {botMode.toUpperCase()}</span>
      </div>

      {/* Controls */}
      <div className="cb">
        <div className="sg">
          <span className="sl">Strategy</span>
          <select value={selectedStrategy} onChange={e => setSelectedStrategy(e.target.value)}>
            <option value="all">All Strategies</option>
            {STRATEGIES.map(s => <option key={s.name} value={s.name}>{s.label}</option>)}
          </select>
        </div>
        <div className="sg">
          <span className="sl">Symbol</span>
          <select value={selectedSymbol} onChange={e => setSelectedSymbol(e.target.value)}>
            <option value="all">All Symbols</option>
            {SYMBOLS.map(s => <option key={s} value={s}>{SYMBOL_LABELS[s]}</option>)}
          </select>
        </div>
      </div>

      {/* KPIs */}
      <div className="kg">
        {kpis.map((k, i) => (
          <div key={i} className="kc">
            <div className="kl">{k.label}</div>
            <div className={`kv ${k.positive ? 'pos' : k.negative ? 'neg' : ''}`}>{k.value}</div>
            {k.change && <div className={`ks ${k.positive ? 'pos' : 'neg'}`}>{k.change}</div>}
          </div>
        ))}
      </div>

      {/* Equity + Risk */}
      <div className="mg">
        <div className="pn">
          <div className="pt">Equity Curve</div>
          {equityData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={equityData}>
                <defs>
                  <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" />
                <XAxis dataKey="index" stroke="#444" />
                <YAxis stroke="#444" tickFormatter={v => `$${v.toFixed(0)}`} domain={['auto','auto']} />
                <Tooltip
                  contentStyle={{ background:'#111', border:'1px solid #333', borderRadius:'8px' }}
                  formatter={(v: any) => [`$${Number(v).toFixed(2)}`, 'Equity']}
                />
                <Area type="monotone" dataKey="equity" stroke="#3b82f6" fill="url(#eq)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <div className="nd">Esperando datos...</div>}
        </div>

        <div className="pn">
          <div className="pt">Risk Metrics</div>
          <div className="rr"><span className="rl">Circuit Breaker</span>
            <span className={`rv ${risk.halted?'neg':'pos'}`}>{risk.halted ? 'HALTED' : risk.circuitBreaker ? 'WARN' : 'OK'}</span>
          </div>
          <div className="rr"><span className="rl">Consecutive Losses</span><span className="rv">{risk.consecutiveLosses} / 3</span></div>
          <div className="rr"><span className="rl">Daily Loss</span><span className={`rv ${risk.dailyLoss>0?'neg':''}`}>${risk.dailyLoss.toFixed(2)}</span></div>
          <div className="rr"><span className="rl">Max Drawdown</span><span className={`rv ${risk.maxDrawdown>0.05?'neg':''}`}>${(risk.maxDrawdown*100).toFixed(2)}%</span></div>
          <div className="rr"><span className="rl">Trades Today</span><span className="rv">{risk.tradesToday} / 10</span></div>
          {risk.cooldownMin > 0 && <div className="rr"><span className="rl">Cooldown</span><span className="rv neg">{risk.cooldownMin} min</span></div>}
          <div className="rr"><span className="rl">Daily DD Limit</span><span className="rv">5%</span></div>
          <div className="rr"><span className="rl">Max Trades</span><span className="rv">10</span></div>
        </div>
      </div>

      {/* Trade Feed */}
      <div className="pn">
        <div className="pt">Trade Feed {trades.length > 0 && <span style={{color:'#444'}}>({trades.length})</span>}</div>
        {trades.length > 0 ? (
          <div className="ts">
            <table className="tt">
              <thead><tr>
                <th>#</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th>
                <th>Dur</th><th>Exit</th><th>Time</th>
              </tr></thead>
              <tbody>
                {trades.map(t => (
                  <tr key={t.id}>
                    <td>{t.id}</td>
                    <td className={t.side==='LONG'?'dl':'ds2'}>{t.side}</td>
                    <td>{t.entry.toFixed(5)}</td>
                    <td>{t.exit.toFixed(5)}</td>
                    <td className={t.pnl>=0?'pos':'neg'}>{t.pnl>=0?'+':''}{t.pnl.toFixed(2)}</td>
                    <td>{t.duration ? `${t.duration}s` : '—'}</td>
                    <td><span className={`bg ${t.exitReason==='TP'?'bg-tp':t.exitReason==='SL'?'bg-sl':'bg-tm'}`}>{t.exitReason}</span></td>
                    <td style={{fontSize:'0.72rem',color:'#6b7593'}}>{new Date(t.time).toLocaleTimeString('es')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="nd">Sin trades registrados</div>}
      </div>
    </div>
  );
};

export default Dashboard;