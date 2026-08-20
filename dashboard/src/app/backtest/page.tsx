'use client';

import { useEffect, useState, useMemo } from 'react';
import { fetchAPI, API_URL } from '@/lib/api';
import styles from './backtest.module.css';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';

// ---- Types matching the backtest API responses ----

interface BacktestSummary {
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
}

interface BacktestTrade {
  timestamp?: string;
  direction?: string;
  entry_price: number;
  exit_price: number;
  stop_loss?: number;
  take_profit?: number;
  stake: number;
  pnl: number;
  status: string;
  exit_reason?: string;
  duration_seconds?: number;
}

interface BacktestReport {
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
  trades: BacktestTrade[];
  equity_curve: Array<number | { value: number; name?: string }>;
  gate_passed: boolean;
  gate_failures: string[];
  strategy: string;
  symbol: string;
  config?: Record<string, string | number>;
  circuit_breaker_status?: Record<string, unknown>;
}

// ---- Format helpers ----

const fmtUSD = (n: number, decimals = 2) =>
  n
    ? n.toLocaleString('es-ES', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : '0';

const fmtPct = (n: number) =>
  n !== 0 ? `${n >= 0 ? '+' : ''}${n.toFixed(2)}%` : '—';

const fmtRatio = (n: number) => (n !== 0 ? n.toFixed(2) : '—');

const fmtTime = (ts?: string) => {
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

export default function BacktestPage() {
  const [summaries, setSummaries] = useState<BacktestSummary[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>('');
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Form state for running new backtest
  const [formData, setFormData] = useState({
    strategy: '',
    symbol: '',
    timeframe: '',
    startDate: '',
    endDate: '',
    initialCapital: ''
  });
  const [formLoading, setFormLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<boolean>(false);

  // Fetch list of backtest summaries on mount
  useEffect(() => {
    const fetchSummaries = async () => {
      try {
        const data = await fetchAPI<BacktestSummary[]>('/api/backtest/results');
        if (Array.isArray(data) && data.length > 0) {
          setSummaries(data);
          setSelectedFile(data[0].filename);
        }
        setLoading(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error desconocido');
        setLoading(false);
      }
    };
    fetchSummaries();
  }, []);

  // Fetch full report when selection changes
  useEffect(() => {
    if (!selectedFile) {
      setReport(null);
      return;
    }
    const fetchReport = async () => {
      try {
        setError(null);
        // The latest endpoint returns the most recent; for a specific file,
        // we use the results list which already has summaries. The /latest
        // endpoint gives us the full report object.
        const data =
          selectedFile === (summaries[0]?.filename ?? '')
            ? await fetchAPI<BacktestReport>('/api/backtest/latest')
            : null;
        if (data) setReport(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Error desconocido');
      }
    };
    fetchReport();
  }, [selectedFile, summaries]);

  // Normalize equity curve data for Recharts
  const equityData = useMemo(() => {
    if (!report || !report.equity_curve || report.equity_curve.length === 0)
      return [];
    return report.equity_curve.map((point, i) => {
      const value = typeof point === 'number' ? point : point.value ?? 0;
      return { name: `T${i}`, value };
    });
  }, [report]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    // Validate required fields
    if (!formData.strategy || !formData.symbol || !formData.timeframe || 
        !formData.startDate || !formData.endDate || !formData.initialCapital) {
      setFormError('Por favor complete todos los campos');
      return;
    }

    // Validate date range
    const startDate = new Date(formData.startDate);
    const endDate = new Date(formData.endDate);
    const today = new Date();
    
    if (startDate > endDate) {
      setFormError('La fecha de inicio debe ser anterior a la fecha de fin');
      return;
    }
    
    if (endDate > today) {
      setFormError('La fecha de fin no puede ser futura');
      return;
    }

    setFormLoading(true);
    setFormError(null);
    setFormSuccess(false);

    try {
      const response = await fetch(
        `${API_URL}/api/backtest/run`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            strategy: formData.strategy,
            symbol: formData.symbol,
            timeframe: formData.timeframe,
            start_date: formData.startDate,
            end_date: formData.endDate,
            initial_capital: parseFloat(formData.initialCapital),
          }),
        }
      );

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const result = await response.json();

      // Reset form and show success
      setFormData({
        strategy: '',
        symbol: '',
        timeframe: '',
        startDate: '',
        endDate: '',
        initialCapital: ''
      });
      setFormSuccess(true);
      
      // Refresh the summaries list to include the new backtest
      const fetchSummaries = async () => {
        try {
          const data = await fetchAPI<BacktestSummary[]>('/api/backtest/results');
          if (Array.isArray(data) && data.length > 0) {
            setSummaries(data);
            // Select the most recent one (first in the list)
            setSelectedFile(data[0].filename);
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Error desconocido');
        }
      };
      fetchSummaries();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Error ejecutando backtest');
    } finally {
      setFormLoading(false);
    }
  };

  if (loading) {
    return <div className={styles.loading}>Cargando reportes de backtest…</div>;
  }

  if (error && summaries.length === 0) {
    return (
      <div className={styles.error}>
        <div className={styles.errorTitle}>Error de conexión</div>
        <div className={styles.errorMsg}>{error}</div>
        <div className={styles.errorHint}>
          Verifica que la API esté corriendo en el puerto 8001 y que existan
          reportes en <code>reports/backtest/</code>
        </div>
      </div>
    );
  }

  if (summaries.length === 0) {
    return (
      <div className={styles.app}>
        <div className={styles.header}>
          <div>
            <h1 className={styles.title}>Backtest</h1>
            <div className={styles.subtitle}>
              <span>Sin reportes disponibles</span>
            </div>
          </div>
        </div>
        <div className={styles.empty}>
          <div className={styles.emptyIcon}>📊</div>
          <div className={styles.emptyTitle}>No hay reportes de backtest</div>
          <div className={styles.emptyHint}>
            Ejecuta un backtest desde el backend para generar reportes en{' '}
            <code>reports/backtest/</code>
          </div>
        </div>
      </div>
    );
  }

  const currentSummary =
    summaries.find((s) => s.filename === selectedFile) ?? summaries[0];

  return (
    <div className={styles.app}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>Backtest</h1>
          <div className={styles.subtitle}>
            <span className={styles.subtitleSymbol}>
              {currentSummary?.symbol || '—'}
            </span>
            <span className={styles.subtitleDot}>·</span>
            <span>{currentSummary?.strategy || '—'}</span>
          </div>
        </div>
        <div className={styles.selector}>
          <span className={styles.selectorLabel}>Reporte</span>
          <select
            className={styles.selectorSelect}
            value={selectedFile}
            onChange={(e) => setSelectedFile(e.target.value)}
            aria-label="Seleccionar reporte de backtest"
          >
            {summaries.map((s) => (
              <option key={s.filename} value={s.filename}>
                {s.filename} ({s.strategy} · {s.symbol})
              </option>
            ))}
          </select>
        </div>
        </div>

        {/* Backtest Run Form */}
        <div className={styles.runFormContainer}>
          <div className={styles.runFormHeader}>
            <h2 className={styles.runFormTitle}>Ejecutar Nuevo Backtest</h2>
            {formSuccess && (
              <div className={styles.successAlert}>
                Backtest ejecutado correctamente. Los resultados estarán disponibles en la lista.
              </div>
            )}
            {formLoading && (
              <div className={styles.loadingAlert}>
                Ejecutando backtest... Por favor espere.
              </div>
            )}
          </div>
          <form 
            onSubmit={handleSubmit} 
            className={styles.runForm}
          >
            <div className={styles.formGrid}>
              {/* Strategy */}
              <div className={styles.formGroup}>
                <label htmlFor="strategy" className={styles.formLabel}>Estrategia</label>
                <select
                  id="strategy"
                  value={formData.strategy}
                  onChange={(e) => setFormData(prev => ({...prev, strategy: e.target.value}))}
                  className={styles.formSelect}
                  required
                  disabled={formLoading}
                >
                  <option value="">Seleccionar estrategia</option>
                  <option value="ema_crossover">EMA Crossover</option>
                  <option value="rsi_mean_reversion">RSI Mean Reversion</option>
                  <option value="breakout">Breakout</option>
                  <option value="ml_ensemble">ML Ensemble</option>
                </select>
              </div>
            
              {/* Symbol */}
              <div className={styles.formGroup}>
                <label htmlFor="symbol" className={styles.formLabel}>Símbolo</label>
                <select
                  id="symbol"
                  value={formData.symbol}
                  onChange={(e) => setFormData(prev => ({...prev, symbol: e.target.value}))}
                  className={styles.formSelect}
                  required
                  disabled={formLoading}
                >
                  <option value="">Seleccionar símbolo</option>
                  <option value="R_10">Volatility 10 Index</option>
                  <option value="R_25">Volatility 25 Index</option>
                  <option value="R_50">Volatility 50 Index</option>
                  <option value="R_75">Volatility 75 Index</option>
                  <option value="R_100">Volatility 100 Index</option>
                  <option value="BOOM1000">Boom 1000 Index</option>
                  <option value="CRASH1000">Crash 1000 Index</option>
                  <option value="STEP Index">Step Index</option>
                </select>
              </div>
            
              {/* Timeframe */}
              <div className={styles.formGroup}>
                <label htmlFor="timeframe" className={styles.formLabel}>Timeframe</label>
                <select
                  id="timeframe"
                  value={formData.timeframe}
                  onChange={(e) => setFormData(prev => ({...prev, timeframe: e.target.value}))}
                  className={styles.formSelect}
                  required
                  disabled={formLoading}
                >
                  <option value="">Seleccionar timeframe</option>
                  <option value="1m">1 minuto</option>
                  <option value="5m">5 minutos</option>
                  <option value="15m">15 minutos</option>
                  <option value="30m">30 minutos</option>
                  <option value="1h">1 hora</option>
                  <option value="4h">4 horas</option>
                  <option value="1d">1 día</option>
                </select>
              </div>
            
              {/* Date Range */}
              <div className={styles.formGroupFull}>
                <label className={styles.formLabel}>Rango de Fechas</label>
                <div className={styles.dateRangePicker}>
                  <div>
                    <label htmlFor="startDate" className={styles.formLabelSmall}>Desde</label>
                    <input
                      id="startDate"
                      type="date"
                      value={formData.startDate}
                      onChange={(e) => setFormData(prev => ({...prev, startDate: e.target.value}))}
                      className={styles.dateInput}
                      required
                      disabled={formLoading}
                      max={new Date().toISOString().split('T')[0]}
                    />
                  </div>
                  <div>
                    <label htmlFor="endDate" className={styles.formLabelSmall}>Hasta</label>
                    <input
                      id="endDate"
                      type="date"
                      value={formData.endDate}
                      onChange={(e) => setFormData(prev => ({...prev, endDate: e.target.value}))}
                      className={styles.dateInput}
                      required
                      disabled={formLoading}
                      max={new Date().toISOString().split('T')[0]}
                    />
                  </div>
                </div>
              </div>
            
              {/* Initial Capital */}
              <div className={styles.formGroup}>
                <label htmlFor="initialCapital" className={styles.formLabel}>Capital Inicial ($)</label>
                <input
                  id="initialCapital"
                  type="number"
                  value={formData.initialCapital}
                  onChange={(e) => setFormData(prev => ({...prev, initialCapital: e.target.value}))}
                  className={styles.numberInput}
                  min="100"
                  step="100"
                  required
                  disabled={formLoading}
                  placeholder="Ej: 10000"
                />
              </div>
            </div>
          
            <div className={styles.formActions}>
              <button 
                type="submit" 
                className={styles.submitButton}
                disabled={formLoading || !formData.strategy || !formData.symbol || !formData.timeframe || !formData.startDate || !formData.endDate || !formData.initialCapital}
              >
                {formLoading ? 'Ejecutando...' : 'Ejecutar Backtest'}
              </button>
              <button 
                type="button"
                className={styles.resetButton}
                onClick={() => {
                  setFormData({
                    strategy: '',
                    symbol: '',
                    timeframe: '',
                    startDate: '',
                    endDate: '',
                    initialCapital: ''
                  });
                  setFormError(null);
                  setFormSuccess(false);
                }}
                disabled={formLoading}
              >
                Limpiar
              </button>
            </div>
          
            {formError && (
              <div className={styles.errorAlert}>
                {formError}
              </div>
            )}
          </form>
        </div>

        {/* KPI Strip — from the selected summary */}
      <div className={styles.kpis}>
        <div
          className={`${styles.kpi} ${
            currentSummary?.gate_passed
              ? styles.kpiPass
              : styles.kpiFail
          }`}
        >
          <div className={styles.kpiLabel}>Gate QA</div>
          <div className={styles.kpiValue}>
            {currentSummary?.gate_passed ? 'PASS' : 'FAIL'}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>P&L Total</div>
          <div
            className={`${styles.kpiValue} ${
              (currentSummary?.total_pnl ?? 0) > 0
                ? styles.pnlPos
                : (currentSummary?.total_pnl ?? 0) < 0
                ? styles.pnlNeg
                : ''
            }`}
          >
            ${fmtUSD(currentSummary?.total_pnl ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Win Rate</div>
          <div className={styles.kpiValue}>
            {fmtPct(currentSummary?.win_rate ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Sharpe</div>
          <div className={styles.kpiValue}>
            {fmtRatio(currentSummary?.sharpe_ratio ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Drawdown Máx.</div>
          <div className={styles.kpiValue}>
            {fmtPct(currentSummary?.max_drawdown ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Factor Beneficio</div>
          <div className={styles.kpiValue}>
            {fmtRatio(currentSummary?.profit_factor ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Expectativa</div>
          <div className={styles.kpiValue}>
            {fmtRatio(currentSummary?.expectancy ?? 0)}
          </div>
        </div>

        <div className={`${styles.kpi} ${styles.kpiNeutral}`}>
          <div className={styles.kpiLabel}>Total Trades</div>
          <div className={styles.kpiValue}>
            {currentSummary?.total_trades ?? 0}
          </div>
        </div>
      </div>

      {/* Gate failures panel */}
      {currentSummary && !currentSummary.gate_passed && (
        <div className={styles.gatePanel}>
          <div className={styles.gateStatus}>
            <div className={`${styles.gateIcon} ${styles.gateIconFail}`}>
              ✕
            </div>
            <div className={styles.gateText}>
              <span className={styles.gateLabel}>
                Backtest NO supera el gate de calidad
              </span>
              <span className={styles.gateDesc}>
                {currentSummary.gate_failures.length} fallo(s) detectado(s)
              </span>
            </div>
          </div>
          <ul className={styles.gateFailures}>
            {currentSummary.gate_failures.map((failure, i) => (
              <li key={i} className={styles.gateFailure}>
                <span aria-hidden="true">⚠</span>
                <span>{failure}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {currentSummary && currentSummary.gate_passed && (
        <div className={styles.gatePanel}>
          <div className={styles.gateStatus}>
            <div className={`${styles.gateIcon} ${styles.gateIconPass}`}>
              ✓
            </div>
            <div className={styles.gateText}>
              <span className={styles.gateLabel}>
                Backtest supera el gate de calidad
              </span>
              <span className={styles.gateDesc}>
                Todas las métricas cumplen los umbrales mínimos
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Equity curve + Config */}
      <div className={styles.gridTwo}>
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Curva de Capital</h2>
            <div className={styles.panelMeta}>
              {equityData.length} puntos
            </div>
          </div>
          {equityData.length > 0 ? (
            <div className={styles.chartWrap}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityData}>
                  <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#06b6d4" stopOpacity={0.4} />
                      <stop offset="100%" stopColor="#06b6d4" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#1f2a44" strokeDasharray="3 3" />
                  <XAxis dataKey="name" stroke="#64748b" fontSize={11} tickLine={false} />
                  <YAxis stroke="#64748b" fontSize={11} tickLine={false} />
                  <Tooltip
                    contentStyle={{
                      background: '#111827',
                      border: '1px solid #1f2a44',
                      borderRadius: '8px',
                      color: '#e2e8f0',
                      fontSize: '0.8rem',
                    }}
                  formatter={(v) => [`$${fmtUSD(Number(v))}`, 'Capital']}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#06b6d4"
                    strokeWidth={2}
                    fill="url(#equityGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className={styles.empty}>
              <div className={styles.emptyHint}>
                Sin datos de equity curve para este reporte
              </div>
            </div>
          )}
        </div>

        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Configuración</h2>
            <div className={styles.panelMeta}>
              {report?.strategy || currentSummary?.strategy || '—'}
            </div>
          </div>
          {report?.config && Object.keys(report.config).length > 0 ? (
            <div className={styles.configGrid}>
              {Object.entries(report.config).map(([key, val]) => (
                <div key={key} className={styles.configItem}>
                  <span className={styles.configKey}>{key}</span>
                  <span className={styles.configVal}>{String(val)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className={styles.empty}>
              <div className={styles.emptyHint}>
                Sin configuración detallada en este reporte
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Trades table */}
      <div className={styles.panel}>
        <div className={styles.panelHeader}>
          <h2 className={styles.panelTitle}>Registro de Operaciones</h2>
          <div className={styles.panelMeta}>
            {report?.trades?.length ?? currentSummary?.total_trades ?? 0}{' '}
            operaciones
          </div>
        </div>
        {report?.trades && report.trades.length > 0 ? (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Hora</th>
                  <th>Dirección</th>
                  <th className={styles.right}>Entrada</th>
                  <th className={styles.right}>Salida</th>
                  <th className={styles.right}>Stake</th>
                  <th className={styles.right}>P&L</th>
                  <th>Estado</th>
                  <th>Motivo salida</th>
                </tr>
              </thead>
              <tbody>
                {report.trades.slice(0, 100).map((t, i) => {
                  const pnl = Number(t.pnl) || 0;
                  const isWon =
                    t.status === 'WON' || t.status === 'GANADA';
                  const isLost =
                    t.status === 'LOST' || t.status === 'PERDIDA';
                  return (
                    <tr key={i} className={styles.tableRow}>
                      <td>{fmtTime(t.timestamp)}</td>
                      <td>{t.direction || '—'}</td>
                      <td className={styles.right}>
                        {t.entry_price ? fmtUSD(t.entry_price) : '—'}
                      </td>
                      <td className={styles.right}>
                        {t.exit_price ? fmtUSD(t.exit_price) : '—'}
                      </td>
                      <td className={styles.right}>
                        ${fmtUSD(t.stake || 0)}
                      </td>
                      <td
                        className={`${styles.right} ${
                          pnl > 0
                            ? styles.pnlPos
                            : pnl < 0
                            ? styles.pnlNeg
                            : styles.pnlZero
                        }`}
                      >
                        {pnl >= 0 ? '+' : ''}${fmtUSD(Math.abs(pnl))}
                      </td>
                      <td>
                        <span
                          className={`${styles.badge} ${
                            isWon
                              ? styles.badgeWon
                              : isLost
                              ? styles.badgeLost
                              : styles.badgeOpen
                          }`}
                        >
                          {t.status}
                        </span>
                      </td>
                      <td>{t.exit_reason || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className={styles.empty}>
            <div className={styles.emptyHint}>
              El reporte seleccionado no contiene detalle de operaciones
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
