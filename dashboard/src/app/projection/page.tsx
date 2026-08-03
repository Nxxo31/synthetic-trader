'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { fetchAPI } from '@/lib/api';
import styles from './projection.module.css';

/* ============================================================
 * SynthIA Terminal — Zona de Proyección Económica
 *
 * Real API integration (FastAPI :8001):
 *   GET  /api/allocator/allocate  → live balance + split
 *   GET  /api/allocator/config    → current allocator params
 *   POST /api/allocator/config    → update allocator params
 *   GET  /api/projection/equity    → Monte Carlo P5/P50/P95
 *   GET  /api/attribution/ranking  → strategy ranking
 *
 * Empty-state handling:
 *   - Ranking []           → "Sin estrategias rankeadas (necesita historial)"
 *   - Projection with <10   → "Necesita mínimo 10 trades para proyección"
 *   - Allocator down       → retry on next interval
 * ============================================================ */

/* ---- API response shapes (verified against live backend) ---- */

interface AllocateResponse {
  reserve: number;
  daily_surplus: number;
  reinvestable: number;
  total_available: number;
  micro_stake_size: number;
  micro_stakes_count: number;
  live_balance: number;
  live_pnl: number;
  allocation_pct: { reserve: number; surplus: number };
}

interface AllocatorConfig {
  capital_total: number;
  reserva_pct: number; // 0–1
  max_daily_pct: number; // 0–1
  reinvest_profits: boolean;
  min_micro_stake: number;
  max_micro_stake_pct: number;
}

interface ProjectionResponse {
  config: { days: number; surplus: number; seed: number };
  projection: {
    equity_p5: number[];
    equity_p50: number[];
    equity_p95: number[];
    final_value_p5: number;
    final_value_p50: number;
    final_value_p95: number;
    return_p5: number;
    return_p50: number;
    return_p95: number;
    max_dd_p5: number;
    max_dd_p50: number;
    max_dd_p95: number;
    prob_profit: number;
    sharpe_estimate: number;
  };
}

interface RankingEntry {
  symbol: string;
  strategy_name: string;
  pnl: number;
  sharpe: number;
}

/* ---- UI constants ---- */

type Horizon = '7D' | '30D' | '90D';
const HORIZON_DAYS: Record<Horizon, number> = { '7D': 7, '30D': 30, '90D': 90 };
const HORIZON_LABEL: Record<Horizon, string> = {
  '7D': '7 días',
  '30D': '30 días',
  '90D': '90 días',
};
const HORIZONS: Horizon[] = ['7D', '30D', '90D'];

const MIN_TRADES_FOR_PROJECTION = 10;

/* ---- Format helpers ---- */

function fmtMoney(n: number, decimals = 2): string {
  const sign = n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toLocaleString('es-ES', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

function fmtPct(n: number, withSign = true): string {
  const sign = withSign && n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(1)}%`;
}

/* ============================================================
 * Page
 * ============================================================ */

export default function ProjectionPage() {
  /* --- Allocator --- */
  const [alloc, setAlloc] = useState<AllocateResponse | null>(null);
  const [config, setConfig] = useState<AllocatorConfig | null>(null);

  /* --- Controls (local, mirror config) --- */
  const [reservePct, setReservePct] = useState(80); // user-facing 0–100
  const [surplusPct, setSurplusPct] = useState(20); // user-facing 0–100
  const [reinvest, setReinvest] = useState(true);

  /* --- Projection --- */
  const [horizon, setHorizon] = useState<Horizon>('7D');
  const [projection, setProjection] = useState<ProjectionResponse | null>(null);

  /* --- Ranking --- */
  const [ranking, setRanking] = useState<RankingEntry[]>([]);

  /* --- Meta --- */
  const [tradeCount, setTradeCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [allocating, setAllocating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* ---- Fetch allocator + projection + ranking ---- */
  const fetchAll = useCallback(async () => {
    try {
      const [allocRes, configRes, projRes, rankRes, tradesRes] = await Promise.all([
        fetchAPI<AllocateResponse>('/api/allocator/allocate'),
        fetchAPI<AllocatorConfig>('/api/allocator/config').catch(() => null),
        fetchAPI<ProjectionResponse>(
          `/api/projection/equity?days=${HORIZON_DAYS[horizon]}&surplus=200`,
        ).catch(() => null),
        fetchAPI<{ ranking: RankingEntry[] }>('/api/attribution/ranking')
          .then((r) => r?.ranking ?? [])
          .catch(() => []),
        fetchAPI<unknown[]>('/api/bot/trades')
          .then((t) => (Array.isArray(t) ? t.length : 0))
          .catch(() => 0),
      ]);

      setAlloc(allocRes);
      if (configRes) {
        setConfig(configRes);
        const reservePctVal = Math.round(Number(configRes.reserva_pct) * 100);
        const surplusPctVal = Math.round(Number(configRes.max_daily_pct) * 100);
        if (Number.isFinite(reservePctVal)) setReservePct(reservePctVal);
        if (Number.isFinite(surplusPctVal)) setSurplusPct(surplusPctVal);
        setReinvest(!!configRes.reinvest_profits);
      }
      setProjection(projRes);
      setRanking(rankRes);
      setTradeCount(tradesRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error de conexión');
    } finally {
      setLoading(false);
    }
  }, [horizon]);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 15000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  /* ---- Re-fetch projection when horizon changes (immediate) ---- */
  useEffect(() => {
    let cancelled = false;
    fetchAPI<ProjectionResponse>(
      `/api/projection/equity?days=${HORIZON_DAYS[horizon]}&surplus=200`,
    )
      .then((r) => {
        if (!cancelled) setProjection(r);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [horizon]);

  /* ---- POST config when sliders/toggle change (debounced) ---- */
  const postTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pushConfig = useCallback(
    (next: { reserva_pct?: number; max_daily_pct?: number; reinvest_profits?: boolean }) => {
      if (postTimer.current) clearTimeout(postTimer.current);
      postTimer.current = setTimeout(async () => {
        setAllocating(true);
        try {
          await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001'}/api/allocator/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              reserva_pct: (next.reserva_pct ?? reservePct / 100),
              max_daily_pct: (next.max_daily_pct ?? surplusPct / 100),
              reinvest_profits: next.reinvest_profits ?? reinvest,
            }),
          });
          // re-fetch allocate to reflect new split
          const fresh = await fetchAPI<AllocateResponse>('/api/allocator/allocate');
          setAlloc(fresh);
        } catch {
          /* silent — controls still update locally */
        } finally {
          setAllocating(false);
        }
      }, 350);
    },
    [reservePct, surplusPct, reinvest],
  );

  const onReserveChange = (v: number) => {
    setReservePct(v);
    const surplus = Math.min(Math.max(100 - v, 5), 50);
    setSurplusPct(surplus);
    pushConfig({ reserva_pct: v / 100, max_daily_pct: surplus / 100 });
  };

  const onSurplusChange = (v: number) => {
    setSurplusPct(v);
    pushConfig({ max_daily_pct: v / 100 });
  };

  const onReinvestToggle = () => {
    const next = !reinvest;
    setReinvest(next);
    pushConfig({ reinvest_profits: next });
  };

  /* ============================================================
   * Render: loading / error / content
   * ============================================================ */

  if (loading) {
    return (
      <div className={styles.loadingWrap}>
        <div className={styles.loadingDot} />
        <span className={styles.loadingText}>Conectando al motor SynthIA…</span>
      </div>
    );
  }

  if (error && !alloc) {
    return (
      <div className={styles.errorWrap}>
        <div className={styles.errorTitle}>Sin conexión al backend</div>
        <div className={styles.errorDetail}>{error}</div>
        <div className={styles.errorHint}>Verifica FastAPI en :8001 — npm run dev para reintentar</div>
      </div>
    );
  }

  /* Derived values from real data */
  const wallet = alloc?.live_balance ?? config?.capital_total ?? 0;
  const walletDelta = alloc?.live_pnl ?? 0;
  const surplus = alloc?.daily_surplus ?? 0;
  const reserve = alloc?.reserve ?? 0;
  const reinvestible = alloc?.reinvestable ?? 0;
  const totalAvailable = alloc?.total_available ?? wallet;

  const allocReservePct = alloc?.allocation_pct.reserve ?? reservePct;
  const allocSurplusPct = alloc?.allocation_pct.surplus ?? surplusPct;
  const reinvPct = Math.max(100 - allocReservePct - allocSurplusPct, 0);

  const canProject = tradeCount >= MIN_TRADES_FOR_PROJECTION;
  const proj = projection?.projection;

  // Build chart data: array of { day, p5, p50, p95 }
  const chartData = proj
    ? proj.equity_p50.map((v50, i) => ({
        day: i,
        p5: proj.equity_p5[i] ?? v50,
        p50: v50,
        p95: proj.equity_p95[i] ?? v50,
      }))
    : [];

  const maxAbsPnl = ranking.length
    ? Math.max(...ranking.map((r) => Math.abs(r.pnl)), 1)
    : 1;

  return (
    <div className={styles.app}>
      {/* ─────────── HEADER ─────────── */}
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.logo}>
            Synth<b>IA</b> Terminal
          </div>
          <span className={styles.separator} aria-hidden="true">/</span>
          <nav className={styles.breadcrumb} aria-label="Ruta">
            Proyección <b className={styles.crumb}>›</b> Zona Económica
          </nav>
        </div>
        <div className={styles.headerRight}>
          <div className={styles.statChip}>
            <span className={styles.chipLabel}>Balance en vivo</span>
            <span className={styles.chipValue}>{fmtMoney(wallet, 2)}</span>
            {walletDelta !== 0 && (
              <span className={walletDelta > 0 ? styles.chipDeltaPos : styles.chipDeltaNeg}>
                {walletDelta > 0 ? '▲' : '▼'} {fmtMoney(Math.abs(walletDelta))}
              </span>
            )}
          </div>
          <div className={styles.statChip}>
            <span className={styles.chipLabel}>Superávit diario</span>
            <span className={`${styles.chipValue} ${styles.chipSurplus}`}>
              {fmtMoney(surplus, 2)}
            </span>
          </div>
          <div className={styles.statChip}>
            <span className={styles.chipLabel}>Trades</span>
            <span className={styles.chipValue}>{tradeCount}</span>
          </div>
        </div>
      </header>

      {/* ─────────── LEFT: Capital Allocator ─────────── */}
      <section className={styles.leftPane} aria-label="Asignador de capital">
        {/* Big number */}
        <div>
          <div className={styles.sectionLabel}>Asignador de capital</div>
          <div className={styles.bigNumber}>
            <span className={styles.currency}>$</span>
            {surplus.toLocaleString('es-ES', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
            <span className={styles.unit}>.{(surplus % 1).toFixed(2).slice(2)}</span>
          </div>
          <div className={styles.bigSubLabel}>
            Superávit operativo diario · {allocSurplusPct.toFixed(0)}% del balance
          </div>
        </div>

        {/* Allocation visual bar */}
        <div className={styles.allocCard}>
          <div className={styles.allocHeader}>
            <span className={styles.allocTotalLabel}>Wallet total</span>
            <span className={styles.allocTotal}>
              {fmtMoney(totalAvailable, 2)}
            </span>
          </div>
          <div
                        className={styles.allocBar}
                        role="img"
                        aria-label={`Distribución: Reserva ${fmtMoney(reserve)} (${Number.isFinite(allocReservePct) ? allocReservePct.toFixed(0) : '0'}%), Superávit ${fmtMoney(surplus)} (${Number.isFinite(allocSurplusPct) ? allocSurplusPct.toFixed(0) : '0'}%), Reinvertible ${fmtMoney(reinvestible)}`}
                        style={{ gridTemplateColumns: `${Number.isFinite(allocReservePct) ? allocReservePct : 0}fr ${Number.isFinite(allocSurplusPct) ? allocSurplusPct : 0}fr ${Number.isFinite(reinvPct) ? reinvPct : 0}fr` }}
                      >
            <div className={`${styles.seg} ${styles.segReserve}`}>
              <span className={styles.segLabel}>Reserva</span>
              <span className={styles.segAmount}>{fmtMoney(reserve, 0)}</span>
            </div>
            <div className={`${styles.seg} ${styles.segSurplus}`}>
              <span className={styles.segLabel}>Superávit</span>
              <span className={styles.segAmount}>{fmtMoney(surplus, 0)}</span>
            </div>
            <div className={`${styles.seg} ${styles.segReinvest}`}>
              <span className={styles.segLabel}>+Reinv.</span>
              <span className={styles.segAmount}>{fmtMoney(reinvestible, 0)}</span>
            </div>
          </div>
          <div className={styles.breakdown}>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>Reserva ({allocReservePct.toFixed(0)}%)</span>
              <span className={styles.breakdownValue}>{fmtMoney(reserve)}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>Superávit ({allocSurplusPct.toFixed(0)}%)</span>
              <span className={`${styles.breakdownValue} ${styles.accent}`}>{fmtMoney(surplus)}</span>
            </div>
            <div className={styles.breakdownItem}>
              <span className={styles.breakdownLabel}>Reinvertible</span>
              <span className={styles.breakdownValue}>{fmtMoney(reinvestible)}</span>
            </div>
          </div>
        </div>

        {/* Slider: reserve */}
        <div className={styles.controlGroup}>
          <div className={styles.controlRow}>
            <label htmlFor="r-reserve" className={styles.controlLabel}>
              Reserva base
            </label>
            <span className={styles.controlVal}>{reservePct}%</span>
          </div>
          <input
            id="r-reserve"
            type="range"
            min={50}
            max={95}
            value={reservePct}
            className={styles.range}
            aria-label="Reserva base porcentaje"
            onChange={(e) => onReserveChange(Number(e.target.value))}
          />
        </div>

        {/* Slider: surplus */}
        <div className={styles.controlGroup}>
          <div className={styles.controlRow}>
            <label htmlFor="r-surplus" className={styles.controlLabel}>
              Superávit operativo diario
            </label>
            <span className={styles.controlVal}>{surplusPct}%</span>
          </div>
          <input
            id="r-surplus"
            type="range"
            min={5}
            max={50}
            value={surplusPct}
            className={styles.range}
            aria-label="Superávit operativo porcentaje"
            onChange={(e) => onSurplusChange(Number(e.target.value))}
          />
        </div>

        {/* Toggle: reinvest */}
        <div className={styles.toggleRow}>
          <label htmlFor="t-reinvest" className={styles.toggleLabel}>
            Reinversión de ganancias
          </label>
          <button
            id="t-reinvest"
            type="button"
            role="switch"
            aria-checked={reinvest}
            aria-label="Reinversión de ganancias"
            className={`${styles.toggle} ${reinvest ? styles.toggleOn : ''}`}
            onClick={onReinvestToggle}
          />
        </div>

        {/* Config status line */}
        {config && (
          <div className={styles.configLine}>
            <span className={styles.configKey}>micro_stake</span>
            <span className={styles.configVal}>
              {fmtMoney(config.min_micro_stake, 2)}–{(config.max_micro_stake_pct * 100).toFixed(0)}%
            </span>
            {allocating && <span className={styles.syncing}>sincronizando…</span>}
          </div>
        )}

        {/* Position sizing preview (Best-Effort) */}
        {alloc && alloc.micro_stake_size > 0 && (
          <div className={styles.posSizeCard}>
            <div className={styles.sectionLabel}>Position sizing preview</div>
            <div className={styles.posSizeRow}>
              <span className={styles.posSizeKey}>Micro-stake</span>
              <span className={styles.posSizeVal}>{fmtMoney(alloc.micro_stake_size)}</span>
            </div>
            <div className={styles.posSizeRow}>
              <span className={styles.posSizeKey}>Slots activos</span>
              <span className={styles.posSizeVal}>{alloc.micro_stakes_count}</span>
            </div>
          </div>
        )}

        {/* Micro-ops info from ranking (best 2) */}
        {ranking.length > 0 && (
          <div>
            <div className={styles.sectionLabel}>Micro-operaciones top</div>
            <div className={styles.microOps}>
              {ranking.slice(0, 2).map((r) => (
                <div key={`${r.strategy_name}-${r.symbol}`} className={styles.microOp}>
                  <div>
                    <div className={styles.microOpTitle}>
                      {r.strategy_name} · {r.symbol}
                    </div>
                    <div className={styles.microOpMeta}>
                      Sharpe {r.sharpe.toFixed(2)}
                    </div>
                  </div>
                  <div className={r.pnl >= 0 ? styles.microOpPnlPos : styles.microOpPnlNeg}>
                    {r.pnl >= 0 ? '▲' : '▼'} {fmtMoney(Math.abs(r.pnl))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ─────────── RIGHT: Projection + Attribution ─────────── */}
      <section className={styles.rightPane} aria-label="Proyección estadística y atribución">
        {/* Projection big number */}
        <div>
          <div className={styles.sectionLabel}>Proyección estadística</div>
          {canProject && proj ? (
            <>
              <div className={`${styles.bigNumber} ${styles.bigP50}`}>
                {fmtMoney(proj.return_p50)}
                <span className={styles.unit}>retorno</span>
              </div>
              <div className={styles.bigSubLabel}>
                P50 mediana · {HORIZON_LABEL[horizon]} · superávit {fmtMoney(200, 0)}
              </div>
            </>
          ) : (
            <div className={styles.emptyProjection}>
              <div className={styles.emptyIcon}>◈</div>
              <div className={styles.emptyTitle}>Acumulando datos…</div>
              <div className={styles.emptySub}>
                Necesita mínimo {MIN_TRADES_FOR_PROJECTION} trades para proyección. Actual: {tradeCount}
              </div>
            </div>
          )}
        </div>

        {/* Monte Carlo chart */}
        <div className={styles.chartCard}>
          <div className={styles.chartHeader}>
            <div className={styles.chartTitleWrap}>
              <h3 className={styles.chartTitle}>Curva de capital · Monte Carlo</h3>
              <div className={styles.chartSubtitle}>
                {canProject
                  ? `${proj?.sharpe_estimate.toFixed(2) ?? '—'} Sharpe · ${proj?.prob_profit.toFixed(0) ?? 0}% prob. profit`
                  : 'Simulación requiere historial de trades'}
              </div>
            </div>
            <div className={styles.timeButtons} role="tablist" aria-label="Horizonte temporal">
              {HORIZONS.map((h) => (
                <button
                  key={h}
                  type="button"
                  role="tab"
                  aria-selected={horizon === h}
                  className={`${styles.timeBtn} ${horizon === h ? styles.timeBtnActive : ''}`}
                  onClick={() => setHorizon(h)}
                >
                  {h}
                </button>
              ))}
            </div>
          </div>

          {canProject && chartData.length > 0 && proj ? (
            <>
              <div className={styles.chartBox}>
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 4 }}>
                    <defs>
                      <linearGradient id="bandGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.18} />
                        <stop offset="100%" stopColor="#00d4ff" stopOpacity={0.02} />
                      </linearGradient>
                      <linearGradient id="p50Grad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.12} />
                        <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#1e2530" strokeDasharray="2 3" vertical={false} />
                    <XAxis
                      dataKey="day"
                      tick={{ fill: '#5a6577', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                      tickLine={false}
                      axisLine={{ stroke: '#1e2530' }}
                      tickFormatter={(d) => `D${d}`}
                    />
                    <YAxis
                      tick={{ fill: '#5a6577', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                      tickLine={false}
                      axisLine={false}
                      width={52}
                      tickFormatter={(v) => `$${Math.round(v)}`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#12161c',
                        border: '1px solid #1e2530',
                        borderRadius: '4px',
                        fontSize: '12px',
                        fontFamily: 'JetBrains Mono, monospace',
                        color: '#e4e9f0',
                      }}
                      labelFormatter={(d) => `Día ${d}`}
                      formatter={(v, name) => [
                        fmtMoney(Number(v)),
                        name === 'p95' ? 'P95' : name === 'p50' ? 'P50' : 'P5',
                      ]}
                    />
                    {/* P95–P5 band (rendered first so it sits behind the lines) */}
                    <Area
                      type="monotone"
                      dataKey="p95"
                      stroke="none"
                      fill="url(#bandGrad)"
                      fillOpacity={1}
                      isAnimationActive={false}
                      legendType="none"
                    />
                    <Area
                      type="monotone"
                      dataKey="p5"
                      stroke="none"
                      fill="#0a0e14"
                      fillOpacity={1}
                      isAnimationActive={false}
                      legendType="none"
                    />
                    {/* P95 line (green dashed) */}
                    <Area
                      type="monotone"
                      dataKey="p95"
                      stroke="#00ff9d"
                      strokeWidth={1.5}
                      strokeDasharray="5 4"
                      fill="none"
                      isAnimationActive={false}
                      dot={false}
                    />
                    {/* P50 line (cyan solid) */}
                    <Area
                      type="monotone"
                      dataKey="p50"
                      stroke="#00d4ff"
                      strokeWidth={2.25}
                      fill="url(#p50Grad)"
                      isAnimationActive={false}
                      dot={false}
                    />
                    {/* P5 line (red dashed) */}
                    <Area
                      type="monotone"
                      dataKey="p5"
                      stroke="#ff3860"
                      strokeWidth={1.5}
                      strokeDasharray="5 4"
                      fill="none"
                      isAnimationActive={false}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Legend */}
              <div className={styles.legend}>
                <span className={styles.legendItem}>
                  <span className={styles.legendBand} /> Banda P5–P95
                </span>
                <span className={`${styles.legendItem} ${styles.legendP50}`}>
                  <span className={styles.legendLine} style={{ background: '#00d4ff' }} /> P50 mediana
                </span>
                <span className={`${styles.legendItem} ${styles.legendP95}`}>
                  <span className={styles.legendLineDashed} style={{ borderColor: '#00ff9d' }} /> P95 óptimo
                </span>
                <span className={`${styles.legendItem} ${styles.legendP5}`}>
                  <span className={styles.legendLineDashed} style={{ borderColor: '#ff3860' }} /> P5 pesimista
                </span>
              </div>

              {/* Stats */}
              <div className={styles.projStats}>
                <div className={styles.projStat}>
                  <div className={styles.projStatLabel}>P50 mediana</div>
                  <div className={`${styles.projStatValue} ${styles.projCyan}`}>
                    {fmtMoney(proj.return_p50)}{' '}
                    <span className={styles.projStatPct}>{fmtPct(proj.return_p50)}</span>
                  </div>
                  <div className={styles.projStatMeta}>DD máx {proj.max_dd_p50.toFixed(1)}%</div>
                </div>
                <div className={styles.projStat}>
                  <div className={styles.projStatLabel}>P95 óptimo</div>
                  <div className={`${styles.projStatValue} ${styles.projGreen}`}>
                    {fmtMoney(proj.return_p95)}{' '}
                    <span className={styles.projStatPct}>{fmtPct(proj.return_p95)}</span>
                  </div>
                  <div className={styles.projStatMeta}>DD máx {proj.max_dd_p95.toFixed(1)}%</div>
                </div>
                <div className={styles.projStat}>
                  <div className={styles.projStatLabel}>P5 pesimista</div>
                  <div className={`${styles.projStatValue} ${styles.projRed}`}>
                    {fmtMoney(proj.return_p5)}{' '}
                    <span className={styles.projStatPct}>{fmtPct(proj.return_p5)}</span>
                  </div>
                  <div className={styles.projStatMeta}>DD máx {proj.max_dd_p5.toFixed(1)}%</div>
                </div>
              </div>

              {/* Secondary metrics */}
              <div className={styles.secondaryStats}>
                <div className={styles.secStat}>
                  <span className={styles.secLabel}>Prob. profit</span>
                  <span className={styles.secValue}>{proj.prob_profit.toFixed(0)}%</span>
                </div>
                <div className={styles.secStat}>
                  <span className={styles.secLabel}>Sharpe est.</span>
                  <span className={styles.secValue}>{proj.sharpe_estimate.toFixed(2)}</span>
                </div>
                <div className={styles.secStat}>
                  <span className={styles.secLabel}>Final P50</span>
                  <span className={styles.secValue}>{fmtMoney(proj.final_value_p50, 0)}</span>
                </div>
              </div>
            </>
          ) : (
            <div className={styles.chartEmpty}>
              <div className={styles.chartEmptyIcon}>≈</div>
              <p className={styles.chartEmptyText}>
                Necesita mínimo {MIN_TRADES_FOR_PROJECTION} trades para proyección.
              </p>
              <p className={styles.chartEmptySub}>
                Actualmente hay {tradeCount} trade{tradeCount === 1 ? '' : 's'} registrado{tradeCount === 1 ? '' : 's'}.
              </p>
            </div>
          )}
        </div>

        {/* Strategy Attribution */}
        <div className={styles.attrCard}>
          <div className={styles.attrHeader}>
            <h3 className={styles.chartTitle}>Análisis por estrategia</h3>
            <div className={styles.chartSubtitle}>Ranking por P&L y Sharpe</div>
          </div>
          {ranking.length > 0 ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.thStrat}>Estrategia</th>
                  <th>Símbolo</th>
                  <th className={styles.thNum}>P&L</th>
                  <th className={styles.thNum}>Sharpe</th>
                  <th>Rating</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((r) => {
                  const isPos = r.pnl >= 0;
                  const widthPct = Math.min((Math.abs(r.pnl) / maxAbsPnl) * 100, 100);
                  let rating: 'BEST' | 'OK' | 'DROP';
                  if (r.pnl > 0 && r.sharpe >= 1.5) rating = 'BEST';
                  else if (r.pnl <= 0 || r.sharpe < 0.5) rating = 'DROP';
                  else rating = 'OK';
                  return (
                    <tr key={`${r.strategy_name}-${r.symbol}`}>
                      <td className={styles.tdStrat}>{r.strategy_name}</td>
                      <td className={styles.tdSymbol}>{r.symbol}</td>
                      <td className={isPos ? styles.tdPnlPos : styles.tdPnlNeg}>
                        <span className={styles.barCell}>
                          <span
                            className={`${styles.barFill} ${isPos ? styles.barPos : styles.barNeg}`}
                            style={{ width: `${widthPct}%` }}
                          />
                        </span>
                        {isPos ? '▲' : '▼'} {fmtMoney(Math.abs(r.pnl))}
                      </td>
                      <td className={styles.tdNum}>{r.sharpe.toFixed(2)}</td>
                      <td>
                        <span
                          className={`${styles.badge} ${
                            rating === 'BEST'
                              ? styles.badgeBest
                              : rating === 'OK'
                                ? styles.badgeOk
                                : styles.badgeDrop
                          }`}
                        >
                          {rating}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className={styles.attrEmpty}>
              <div className={styles.attrEmptyIcon}>◇</div>
              <p className={styles.attrEmptyText}>Sin estrategias rankeadas</p>
              <p className={styles.attrEmptySub}>
                El motor de atribución necesita historial de trades. {tradeCount} registrado{tradeCount === 1 ? '' : 's'}.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
