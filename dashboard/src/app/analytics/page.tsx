'use client';

import { useEffect, useState } from 'react';
import { fetchAPI } from '@/lib/api';
import styles from './analytics.module.css';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Area } from 'recharts';

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
    last_loss_time: string | null;
    is_halted: boolean;
    halt_reason: string;
    halt_until: string | null;
    cooldown_minutes: number;
    today: string;
    perdidas_consecutivas: number;
    detenido: boolean;
  };
  kpi: {
    sharpe_ratio: number;
    win_rate: number;
    max_drawdown: number;
    profit_factor: number;
    expectancy: number;
    indice_sharpe: number;
    tasa_aciertos: number;
    caida_maxima: number;
    factor_beneficio: number;
    expectativa: number;
  };
  gate_passed: boolean;
  gate_failures: string[];
  last_update: string;
  saldo: number;
  resultado_operaciones: number;
  operaciones_hoy: number;
  detenido: boolean;
  interruptor_circuito: {
    consecutive_losses: number;
    last_loss_time: string | null;
    is_halted: boolean;
    halt_reason: string;
    halt_until: string | null;
    cooldown_minutes: number;
    today: string;
    perdidas_consecutivas: number;
    detenido: boolean;
  };
  gate_pasada: boolean;
  gate_fallos: string[];
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
  score: number;
  contract_id: string;
  pnl: number;
  exit_reason: string;
  duration_seconds: number;
  status: string;
  symbol?: string;
  stop_perdida: number;
  objetivo_ganancia: number;
  resultado_operaciones: number;
}

interface AttributionMatrix {
  strategies: string[];
  symbols: string[];
  matrix: Array<Array<{
    strategy_name: string;
    symbol: string;
    pnl: number;
    sharpe: number;
  }>>;
}

interface AttributionRanking {
  ranking: Array<{
    symbol: string;
    strategy_name: string;
    pnl: number;
    sharpe: number;
  }>;
}

interface AllocationData {
  reserve: number;
  daily_surplus: number;
  reinvestable: number;
  total_available: number;
  live_balance: number;
  live_pnl: number;
  allocation_pct: {
    reserve: number;
    surplus: number;
  };
  micro_stake_size: number;
  micro_stakes_count: number;
}

// Helper function to check if we have enough trades for meaningful analysis
const hasSufficientData = (trades: TradeRecord[]): boolean => trades.length >= 5;

// Format helpers
const fmtPrice = (n: number) =>
  n ? n.toLocaleString('es-ES', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
const fmtUSD = (n: number, decimals = 2) =>
  n ? n.toLocaleString('es-ES', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '0';
const fmtPct = (n: number) =>
  n !== 0 ? `${n >= 0 ? '+' : ''}${n.toFixed(2)}%` : '—';
const fmtRatio = (n: number) => (n !== 0 ? n.toFixed(2) : '—');
const fmtTime = (ts: string) => {
  if (!ts) return '—';
  try {
    return new Date(ts).toLocaleTimeString('es-ES', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
};

export default function AnalyticsPage() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [attributionMatrix, setAttributionMatrix] = useState<AttributionMatrix>({
    strategies: [],
    symbols: [],
    matrix: [],
  });
  const [attributionRanking, setAttributionRanking] = useState<AttributionRanking>({
    ranking: [],
  });
  const [allocationData, setAllocationData] = useState<AllocationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        // Fetch all required data
        const [status, tradesRes, matrixRes, rankingRes, allocRes] = await Promise.all([
          fetchAPI<BotStatus>('/api/bot/status'),
          fetchAPI<TradeRecord[]>('/api/bot/trades').catch(() => []),
          fetchAPI<AttributionMatrix>('/api/attribution/matrix').catch(() => ({
            strategies: [],
            symbols: [],
            matrix: [],
          })),
          fetchAPI<AttributionRanking>('/api/attribution/ranking').catch(() => ({
            ranking: [],
          })),
          fetchAPI<AllocationData>('/api/allocator/allocate').catch(() => null),
        ]);

        setBotStatus(status);
        setTrades(Array.isArray(tradesRes) ? tradesRes : []);
        setAttributionMatrix(matrixRes);
        setAttributionRanking(rankingRes);
        setAllocationData(allocRes);
        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error desconocido');
        setLoading(false);
      }
    };

    fetchAll();
    const interval = setInterval(fetchAll, 15000); // Update every 15 seconds
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className={styles.loading}>
        Cargando analítica...
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.error}>
        <div className={styles.errorTitle}>Error de conexión</div>
        <div className={styles.errorMsg}>{error}</div>
        <div className={styles.errorHint}>
          Verifica que la API esté corriendo en el puerto 8001
        </div>
      </div>
    );
  }

  // Calculate equity curve from trades (same as main dashboard)
  const equityData = (() => {
    if (!botStatus || trades.length === 0) return [];
    const runningBalance = botStatus.balance - botStatus.pnl;
    const equityPoints: { name: string; value: number }[] = [
      { name: 'Inicio', value: runningBalance },
    ];
    let running = runningBalance;
    for (const t of trades) {
      running += Number(t.pnl) || 0;
      const ts = t.timestamp
        ? new Date(t.timestamp).toLocaleTimeString('es-ES', {
            hour: '2-digit',
            minute: '2-digit',
          })
        : `T${equityPoints.length}`;
      equityPoints.push({ name: ts, value: running });
    }
    equityPoints.push({ name: 'Ahora', value: botStatus.balance });
    return equityPoints;
  })();

  // Calculate P&L distribution data for histogram
  const pnlDistribution = (() => {
    if (trades.length === 0) return [];
    const wins = trades.filter(t => t.status === 'WON' || t.status === 'GANADA').length;
    const losses = trades.filter(t => t.status === 'LOST' || t.status === 'PERDIDA').length;
    const open = trades.length - wins - losses;
    return [
      { name: 'Ganadas', value: wins, color: 'var(--accent-green)' },
      { name: 'Perdidas', value: losses, color: 'var(--accent-red)' },
      { name: 'Abiertas', value: open, color: 'var(--accent-cyan)' },
    ];
  })();

  // Prepare strategy comparison table data
  const strategyTableData = (() => {
    // If we have attribution ranking, use that; otherwise fallback to KPI from bot status
    if (attributionRanking.ranking.length > 0) {
      return attributionRanking.ranking.map((item, index) => ({
        ...item,
        // Add rating based on Sharpe ratio
        rating: item.sharpe >= 2.0 ? 'BEST' : item.sharpe >= 0.5 ? 'OK' : 'DROP',
        winRate: 0, // Would need more detailed data, placeholder for now
        expectancy: 0, // Would need more detailed data
      }));
    }
    
    // Fallback: single strategy from bot status
    if (!botStatus) return [];
    return [{
      symbol: botStatus.symbol,
      strategy_name: botStatus.strategy,
      pnl: botStatus.pnl,
      sharpe: botStatus.kpi.indice_sharpe,
      winRate: botStatus.kpi.tasa_aciertos,
      expectancy: botStatus.kpi.expectativa,
      rating: botStatus.kpi.indice_sharpe >= 2.0 ? 'BEST' : botStatus.kpi.indice_sharpe >= 0.5 ? 'OK' : 'DROP',
    }];
  })();

  // Prepare heatmap matrix data
  const heatmapData = (() => {
    if (attributionMatrix.strategies.length === 0 || attributionMatrix.symbols.length === 0) {
      return {
        strategies: [],
        symbols: [],
        cells: [],
      };
    }

    const cells: Array<{
      strategy: string;
      symbol: string;
      pnl: number;
      sharpe: number;
      intensity: string; // CSS class for coloring
    }> = [];

    attributionMatrix.strategies.forEach((strategy, si) => {
      attributionMatrix.symbols.forEach((symbol, sj) => {
        const cell = attributionMatrix.matrix[si]?.[sj];
        if (cell) {
          const pnl = cell.pnl || 0;
          const sharpe = cell.sharpe || 0;
          let intensity = 'cellEmpty';
          
          if (pnl > 0.5) {
            intensity = pnl > 2.0 ? 'cellPositiveStrong' : 'cellPositive';
          } else if (pnl < -0.5) {
            intensity = pnl < -2.0 ? 'cellNegativeStrong' : 'cellNegative';
          }
          
          cells.push({
            strategy,
            symbol,
            pnl,
            sharpe,
            intensity,
          });
        }
      });
    });

    return {
      strategies: attributionMatrix.strategies,
      symbols: attributionMatrix.symbols,
      cells,
    };
  })();

  // Calculate trade analytics (expectancy, profit factor per strategy)
  const tradeAnalytics = (() => {
    if (trades.length === 0) return [];

    // Group trades by strategy (using direction and symbol from botStatus as fallback)
    const strategyGroups: Record<string, TradeRecord[]> = {};
    trades.forEach(t => {
      const symbol = t.symbol || (botStatus ? botStatus.symbol : 'UNKNOWN');
      const key = `${t.direction}-${symbol}`;
      if (!strategyGroups[key]) strategyGroups[key] = [];
      strategyGroups[key].push(t);
    });

    return Object.entries(strategyGroups).map(([key, group]) => {
      const wins = group.filter(t => t.status === 'WON' || t.status === 'GANADA').length;
      const losses = group.filter(t => t.status === 'LOST' || t.status === 'PERDIDA').length;
      const total = wins + losses;
      
      if (total === 0) return { strategy: key, trades: 0, winRate: 0, expectancy: 0, profitFactor: 0 };
      
      const winRate = wins / total;
      const totalWin = group
        .filter(t => t.status === 'WON' || t.status === 'GANADA')
        .reduce((sum, t) => sum + Math.max(t.pnl, 0), 0);
      const totalLoss = Math.abs(
        group
          .filter(t => t.status === 'LOST' || t.status === 'PERDIDA')
          .reduce((sum, t) => sum + Math.min(t.pnl, 0), 0)
      );
      
      const expectancy = (totalWin - totalLoss) / total;
      const profitFactor = totalLoss > 0 ? totalWin / totalLoss : 0;
      
      return {
        strategy: key,
        trades: group.length,
        wins,
        losses,
        winRate: winRate * 100,
        expectancy,
        profitFactor,
      };
    });
  })();

  // Format helpers for display
  const getRatingBadge = (rating: string) => {
    switch (rating) {
      case 'BEST': return <span className={`${styles.rating} ${styles.ratingBest}`}>BEST</span>;
      case 'OK': return <span className={`${styles.rating} ${styles.ratingOk}`}>OK</span>;
      case 'DROP': return <span className={`${styles.rating} ${styles.ratingDrop}`}>DROP</span>;
      default: return <span className={`${styles.rating} ${styles.ratingOk}`}>OK</span>;
    }
  };

  return (
    <div className={styles.app}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Analítica de Rendimiento</h1>
          <div className={styles.subtitle}>
            <span className={styles.subtitleSymbol}>
              {botStatus?.symbol || 'R_100'}
            </span>
            <span className={styles.subtitleDot}>·</span>
            <span className={styles.subtitleMode}>
              {botStatus?.mode || 'paper'}
            </span>
          </div>
        </div>
        <div className={styles.refresh}>
          <div className={styles.refreshStatus}>
            <div className={styles.refreshDot} />
            <span className={styles.refreshLabel}>Actualizando</span>
          </div>
          {botStatus?.last_update && (
            <div className={styles.refreshTime}>
              {fmtTime(botStatus.last_update)}
            </div>
          )}
        </div>
      </div>

      {/* KPI Strip - Key metrics from bot status */}
      <div className={styles.kpis}>
        <div className={`${styles.kpi} ${styles.kpiCyan}`}>
          <div className={styles.kpiLabel}>Balance</div>
          <div className={styles.kpiValue}>${fmtUSD(botStatus?.balance || 0)}</div>
          <div className={styles.kpiSub}>
            {botStatus && botStatus.pnl !== 0
              ? `${botStatus.pnl >= 0 ? '+' : ''}$${fmtUSD(Math.abs(botStatus.pnl || 0))}`
              : '—'}
          </div>
        </div>
        
        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Sharpe Ratio</div>
          <div className={styles.kpiValue}>
            {fmtRatio(botStatus?.kpi.indice_sharpe || 0)}
          </div>
          <div className={styles.kpiSub}>
            {botStatus && botStatus.kpi.tasa_aciertos !== 0
              ? `${(botStatus.kpi.tasa_aciertos * 100).toFixed(1)}%`
              : '—'}
          </div>
        </div>
        
        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Drawdown Máx.</div>
          <div className={styles.kpiValue}>
            {fmtPct(botStatus?.kpi.caida_maxima || 0)}
          </div>
        </div>
        
        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Factor de Beneficio</div>
          <div className={styles.kpiValue}>
            {fmtRatio(botStatus?.kpi.factor_beneficio || 0)}
          </div>
        </div>
        
        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Expectativa</div>
          <div className={styles.kpiValue}>
            {fmtRatio(botStatus?.kpi.expectativa || 0)}
          </div>
        </div>
      </div>

      {/* Main Content Grid */}
      <div className={styles.gridTwo}>
        {/* Left Column: Strategy Analysis */}
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Comparación de Estrategias</h2>
            <div className={styles.panelMeta}>
              Última actualización: {fmtTime(new Date().toISOString())}
            </div>
          </div>
          
          {hasSufficientData(trades) ? (
            <>
              {/* Strategy Ranking Table */}
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Estrategia</th>
                      <th className={styles.right}>Símbolo</th>
                      <th className={styles.right}>P&L</th>
                      <th className={styles.right}>Sharpe</th>
                      <th className={styles.right}>Win Rate</th>
                      <th className={styles.right}>Expect.</th>
                      <th>Rating</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strategyTableData.map((item, index) => (
                      <tr key={index} className={styles.tableRow}>
                        <td className={styles.stratName}>
                          {item.strategy_name}
                        </td>
                        <td className={`${styles.right} ${styles.stratSymbol}`}>
                          {item.symbol}
                        </td>
                        <td className={`${styles.right} ${item.pnl >= 0 ? styles.pnlPositive : item.pnl < 0 ? styles.pnlNegative : styles.pnlZero}`}>
                          {fmtUSD(item.pnl)}
                        </td>
                        <td className={styles.right}>
                          {fmtRatio(item.sharpe)}
                        </td>
                        <td className={styles.right}>
                          {item.winRate !== 0 ? `${item.winRate.toFixed(1)}%` : '—'}
                        </td>
                        <td className={styles.right}>
                          {fmtRatio(item.expectancy)}
                        </td>
                        <td>
                          {getRatingBadge(item.rating)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              
              {/* P&L Distribution Histogram */}
              <div className={styles.panel} style={{ marginTop: '16px' }}>
                <div className={styles.panelHeader}>
                  <h2 className={styles.panelTitle}>Distribución de P&L</h2>
                </div>
                <div className={styles.empty}>
                  {!hasSufficientData(trades) && (
                    <>
                      <div className={styles.emptyIcon}>📊</div>
                      <div>Acumulando datos...</div>
                      <div className={styles.emptySub}>
                        {trades.length} operaciones registradas
                      </div>
                    </>
                  )}
                  {hasSufficientData(trades) && (
                    <div>
                      <div className={styles.emptyIcon}>📈</div>
                      <div>Distribución de resultados</div>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>📊</div>
              <div>Acumulando datos...</div>
              <div className={styles.emptySub}>
                {trades.length} operaciones registradas
              </div>
            </div>
          )}
        </div>

        {/* Right Column: Attribution & Allocation */}
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Atribución Estratégica</h2>
            <div className={styles.panelMeta}>
              Motor Brinson-Fachler
            </div>
          </div>
          
          {/* Strategy × Symbol Heatmap */}
          <div className={styles.panel} style={{ marginTop: '12px' }}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>Matriz de Atribución</h2>
            </div>
            
            {hasSufficientData(trades) ? (
              <>
                {heatmapData.strategies.length > 0 && heatmapData.symbols.length > 0 ? (
                  <div className={styles.heatmap}>
                    {/* Header row with symbols */}
                    {heatmapData.symbols.map((symbol, index) => (
                      <div
                        key={index}
                        className={`${styles.heatmapHeader} ${index === 0 ? styles.borderLeft : ''}`}
                      >
                        {symbol}
                      </div>
                    ))}
                    
                    {/* Strategy rows */}
                    {heatmapData.strategies.map((strategy, si) => (
                      <>
                        <div key={`row-label-${si}`} className={styles.heatmapRowLabel}>
                          {strategy}
                        </div>
                        {heatmapData.symbols.map((symbol, sj) => {
                          const cellIndex = si * heatmapData.symbols.length + sj;
                          const cell = heatmapData.cells[cellIndex];
                          return (
                            <div
                              key={`cell-${si}-${sj}`}
                              className={`${styles.heatmapCell} ${cell?.intensity || styles.cellEmpty}`}
                            >
                              {cell?.pnl !== undefined ? fmtUSD(cell.pnl) : '0.00'}
                            </div>
                          );
                        })}
                      </>
                    ))}
                  </div>
                ) : (
                  <div className={styles.empty}>
                    <div className={styles.emptyIcon}>🔥</div>
                    <div>Sin datos de matriz</div>
                    <div className={styles.emptySub}>
                      Esperando más operaciones...
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className={styles.empty}>
                <div className={styles.emptyIcon}>🔥</div>
                <div>Acumulando datos...</div>
                <div className={styles.emptySub}>
                  {trades.length} operaciones registradas
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Full-width sections */}
      <div className={styles.fullWidth}>
        {/* Trade Analytics Section */}
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Análisis por Estrategia</h2>
          </div>
          
          {hasSufficientData(trades) ? (
            <div className={styles.stratCards}>
              {tradeAnalytics.map((stats, index) => {
                const isBest = stats.profitFactor > 1.5 && stats.winRate > 60;
                const isOk = stats.profitFactor > 1.0 && stats.winRate > 50;
                return (
                  <div
                    key={index}
                    className={`${styles.stratCard} ${
                      isBest ? styles.stratCardBest : isOk ? styles.stratCardOk : styles.stratCardDrop
                    }`}
                  >
                    <div className={styles.stratCardHeader}>
                      <span className={styles.stratCardName}>
                        {stats.strategy}
                      </span>
                      {stats.trades > 0 && (
                        <span className={styles.kpiSub}>
                          {stats.trades} trades
                        </span>
                      )}
                    </div>
                    <div className={styles.stratCardRows}>
                      <div className={styles.stratMetric}>
                        <span className={styles.stratMetricLabel}>Win Rate</span>
                        <span className={styles.stratMetricValue}>
                          {stats.winRate.toFixed(1)}%
                        </span>
                      </div>
                      <div className={styles.stratMetric}>
                        <span className={styles.stratMetricLabel}>Expectancy</span>
                        <span className={styles.stratMetricValue}>
                          {fmtRatio(stats.expectancy)}
                        </span>
                      </div>
                      <div className={styles.stratMetric}>
                        <span className={styles.stratMetricLabel}>Profit Factor</span>
                        <span className={styles.stratMetricValue}>
                          {fmtRatio(stats.profitFactor)}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>📋</div>
              <div>Acumulando datos...</div>
              <div className={styles.emptySub}>
                {trades.length} operaciones registradas
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Allocation Breakdown Section */}
      <div className={styles.fullWidth}>
        {allocationData ? (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>Distribución de Capital</h2>
            </div>
            
            <div className={styles.allocRow}>
              <span className={styles.label}>Reserva (80%)</span>
              <span className={styles.value}>
                ${fmtUSD(allocationData.reserve)}
              </span>
            </div>
            <div className={styles.allocRow}>
              <span className={styles.label}>Superávit Operativo (20%)</span>
              <span className={styles.value}>
                ${fmtUSD(allocationData.daily_surplus)}
              </span>
            </div>
            <div className={styles.allocRow}>
              <span className={styles.label}>Total Disponible</span>
              <span className={styles.value}>
                ${fmtUSD(allocationData.total_available)}
              </span>
            </div>
            
            <div className={styles.allocBar}>
              <div
                className={styles.allocReserve}
                style={{ width: `${allocationData.allocation_pct.reserve}%` }}
              />
              <div
                className={styles.allocSurplus}
                style={{ width: `${allocationData.allocation_pct.surplus}%` }}
              />
            </div>
            
<div className={styles.allocLegend}>
              <div className={`${styles.allocLegendItem} `}>
                <div className={`${styles.allocLegendDot} ${styles.allocLegendDotReserve}`} />
                <span>Reserva</span>
              </div>
              <div className={`${styles.allocLegendItem} `}>
                <div className={`${styles.allocLegendDot} ${styles.allocLegendDotSurplus}`} />
                <span>Superávit</span>
              </div>
            </div>
          </div>
        ) : (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>Distribución de Capital</h2>
            </div>
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>💰</div>
              <div>Esperando datos de asignación...</div>
            </div>
          </div>
        )}
      </div>

      {/* Bottom section: Equity Curve (full width) */}
      <div className={styles.fullWidth}>
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Curva de Capital</h2>
            <div className={styles.panelMeta}>
              Evolución del balance con operaciones
            </div>
          </div>
          
          {equityData.length > 1 ? (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={equityData}>
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="1 0" stroke="#1e2530" vertical={false} />
                <XAxis dataKey="name" stroke="#5a6577" fontSize={10} tickLine={false} axisLine={false} />
                <YAxis stroke="#5a6577" fontSize={10} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
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
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#00d4ff"
                  strokeWidth={2}
                  fill="url(#equityGrad)"
                  activeDot={{ r: 4, stroke: '#00d4ff', strokeWidth: 1 }}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className={styles.empty}>
              <div className={styles.emptyIcon}>📈</div>
              <div>El bot está acumulando operaciones...</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}