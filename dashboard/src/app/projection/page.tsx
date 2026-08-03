'use client';

import { useState } from 'react';
import styles from './projection.module.css';

/**
 * Zona de Proyección Económica — Variante B (split panes).
 *
 * Layout: Capital Allocator (left) | Return Projection + Strategy Attribution (right).
 * ベースは sketches/projection-zone/B-split-panes/index.html の洗練版。
 * Re-rendered as a Next.js 16 Client Component with local state for the
 * interactive controls (sliders, toggle, time horizon tabs).
 *
 * Note: data is currently mocked to match the design. When the backend
 * projection endpoint (/api/projection or similar) is available, replace
 * the constants in DATA below with a fetch via @/lib/api.
 */

type Horizon = '7D' | '30D' | '90D';

const DATA = {
  walletTotal: 1_000,
  walletDelta: 24.8,
  surplus: 200,
  reservePct: 80, // %
  surplusPct: 20, // %
  reinvestible: 0,
  projectionP50: 48.2,
  projectionP95: 72.4,
  projectionP5: -12.8,
  microOps: [
    { strategy: 'RangeBreak', symbol: 'RB100', score: 0.72, kelly: 4.2, status: 'active' as const },
    { strategy: 'Volatility', symbol: 'R_100', score: 0.58, kelly: 2.8, status: 'active' as const },
  ],
  attribution: [
    { strategy: 'RangeBreak', symbol: 'RB100', pnl: 14.2, wr: 92.5, sharpe: 5.31, rating: 'BEST', max: 14.2 },
    { strategy: 'Volatility', symbol: 'R_100', pnl: 7.3, wr: 64.3, sharpe: 1.42, rating: 'OK', max: 14.2 },
    { strategy: 'MeanReversion', symbol: 'R_50', pnl: 1.2, wr: 58.1, sharpe: 0.89, rating: 'OK', max: 14.2 },
    { strategy: 'PairTrading', symbol: 'BOOM1K', pnl: -1.2, wr: null, sharpe: null, rating: 'DROP', max: 14.2 },
  ],
};

function fmtMoney(n: number, decimals = 2): string {
  const sign = n < 0 ? '−' : '';
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

function fmtPct(n: number): string {
  const sign = n < 0 ? '−' : '+';
  return `${sign}${Math.abs(n).toFixed(1)}%`;
}

export default function ProjectionPage() {
  const [reservePct, setReservePct] = useState(DATA.reservePct);
  const [surplusPct, setSurplusPct] = useState(DATA.surplusPct);
  const [reinvest, setReinvest] = useState(true);
  const [horizon, setHorizon] = useState<Horizon>('7D');

  const wallet = DATA.walletTotal;
  const reserve = (wallet * reservePct) / 100;
  const surplus = (wallet * surplusPct) / 100;
  const reinvestibleAmount = DATA.reinvestible;

  return (
    <div className={styles.app}>
      {/* Page header — sits below the global nav */}
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.logo}>
            pr<b>a</b>dx
          </div>
          <div className={styles.breadcrumb}>
            Proyección <b>›</b> Zona Económica
          </div>
        </div>
        <div className={styles.headerRight}>
          <div className={styles.balanceCard}>
            <span className={styles.balanceLabel}>Wallet</span>
            <span className={styles.balanceValue}>{fmtMoney(wallet, 0)}</span>
            <span className={styles.balanceDelta}>↑ {fmtMoney(DATA.walletDelta)}</span>
          </div>
          <div className={styles.balanceCard}>
            <span className={styles.balanceLabel}>Superávit</span>
            <span className={styles.balanceValue} style={{ color: '#2dd4bf' }}>
              {fmtMoney(surplus, 0)}
            </span>
          </div>
        </div>
      </header>

      {/* LEFT: Capital Allocator */}
      <section className={styles.leftPane} aria-label="Asignador de capital">
        <div>
          <div className={styles.sectionTitle}>Asignador de capital</div>
          <div className={styles.bigNumber}>
            {fmtMoney(surplus, 0).replace('$', '$')}
            <span className={styles.unit}>.00</span>
          </div>
          <div className={styles.bigLabel}>Superávit operativo diario</div>
        </div>

        <div className={styles.allocVisual}>
          <div className={styles.allocVisualHeader}>
            <div className={styles.allocTotal}>
              <span className={styles.currency}>$</span>
              {wallet.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
            <div className={styles.allocTotalLabel}>Wallet total</div>
          </div>
          <div
            className={styles.allocBar}
            role="img"
            aria-label={`Distribución: Reserva ${fmtMoney(reserve)}, Superávit ${fmtMoney(surplus)}, Reinvertible ${fmtMoney(reinvestibleAmount)}`}
            style={{ gridTemplateColumns: `${reservePct}fr ${surplusPct}fr ${Math.max(wallet - reserve - surplus, 0) / wallet * 100}fr` }}
          >
            <div className={`${styles.seg} ${styles.segBase}`} style={{ flex: reservePct }}>
              <span>Reserva</span>
              <span className={styles.segAmount}>{fmtMoney(reserve, 0)}</span>
            </div>
            <div className={`${styles.seg} ${styles.segSuper}`} style={{ flex: surplusPct }}>
              <span>Superávit</span>
              <span className={styles.segAmount}>{fmtMoney(surplus, 0)}</span>
            </div>
            <div className={`${styles.seg} ${styles.segReinvert}`} style={{ flex: Math.max(100 - reservePct - surplusPct, 0) }}>
              <span>+Gan</span>
              <span className={styles.segAmount}>{fmtMoney(reinvestibleAmount, 0)}</span>
            </div>
          </div>
          <div className={styles.allocBreakdown}>
            <div className={styles.breakdownItem}>
              <div className={styles.breakdownLabel}>Reserva base ({reservePct}%)</div>
              <div className={styles.breakdownValue}>{fmtMoney(reserve)}</div>
            </div>
            <div className={styles.breakdownItem}>
              <div className={styles.breakdownLabel}>Superávit ({surplusPct}%)</div>
              <div className={`${styles.breakdownValue} ${styles.pos}`}>{fmtMoney(surplus)}</div>
            </div>
            <div className={styles.breakdownItem}>
              <div className={styles.breakdownLabel}>Reinvertible</div>
              <div className={`${styles.breakdownValue} ${styles.accent}`}>{fmtMoney(reinvestibleAmount)}</div>
            </div>
          </div>
        </div>

        <div className={styles.controlGroup}>
          <div className={styles.controlLabel}>
            <label htmlFor="r-base">Reserva base</label>
            <span className={styles.controlVal}>{reservePct}%</span>
          </div>
          <input
            id="r-base"
            type="range"
            min={50}
            max={95}
            value={reservePct}
            className={styles.range}
            aria-label="Reserva base porcentaje"
            onChange={(e) => setReservePct(Number(e.target.value))}
          />
        </div>

        <div className={styles.controlGroup}>
          <div className={styles.controlLabel}>
            <label htmlFor="r-super">Superávit operativo diario</label>
            <span className={styles.controlVal}>{surplusPct}%</span>
          </div>
          <input
            id="r-super"
            type="range"
            min={5}
            max={50}
            value={surplusPct}
            className={styles.range}
            aria-label="Superávit operativo porcentaje"
            onChange={(e) => setSurplusPct(Number(e.target.value))}
          />
        </div>

        <div className={styles.toggleRow}>
          <span className={styles.toggleLabel}>Reinversión de ganancias</span>
          <button
            type="button"
            role="switch"
            aria-checked={reinvest}
            aria-label="Reinversión de ganancias"
            className={`${styles.toggle} ${reinvest ? styles.on : ''}`}
            onClick={() => setReinvest((v) => !v)}
          />
        </div>

        <div>
          <div className={styles.sectionTitle}>Micro-Operaciones Activas</div>
          <div className={styles.microOps}>
            {DATA.microOps.map((op) => (
              <div key={op.strategy} className={styles.microOp}>
                <div>
                  <div className={styles.microOpTitle}>
                    {op.strategy} · {op.symbol}
                  </div>
                  <div className={styles.microOpMeta}>
                    Score {op.score.toFixed(2)} · Kelly {fmtMoney(op.kelly)}
                  </div>
                </div>
                <div style={{ fontWeight: 600, color: '#2dd4bf' }}>{fmtMoney(op.kelly)}</div>
              </div>
            ))}
            <div className={styles.microOp}>
              <div>
                <div className={styles.microOpTitle}>Disponible</div>
                <div className={styles.microOpMeta}>Sin señal activa</div>
              </div>
              <div style={{ fontWeight: 600, color: '#71717a' }}>
                {fmtMoney(surplus - DATA.microOps.reduce((s, o) => s + o.kelly, 0))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* RIGHT: Return Projection + Strategy Attribution */}
      <section className={styles.rightPane} aria-label="Proyección y atribución">
        <div>
          <div className={styles.sectionTitle}>Proyección estadística</div>
          <div className={styles.bigNumber} style={{ color: '#2dd4bf' }}>
            ↑ {fmtMoney(DATA.projectionP50).replace('$', '$')}
            <span className={styles.unit}>.20</span>
          </div>
          <div className={styles.bigLabel}>P50 esperado en {horizon === '7D' ? '7 días' : horizon === '30D' ? '30 días' : '90 días'} · superávit {fmtMoney(surplus, 0)}</div>
        </div>

        <div className={styles.chartContainer}>
          <div className={styles.chartHeaderRow}>
            <div className={styles.chartTitleWrap}>
              <div className={styles.chartTitle}>Curva de capital — Simulación de escenarios</div>
              <div className={styles.chartSubtitle}>10,000 simulaciones · bandas P5/P50/P95</div>
            </div>
            <div className={styles.timeButtons} role="tablist" aria-label="Horizonte temporal">
              {(['7D', '30D', '90D'] as Horizon[]).map((h) => (
                <button
                  key={h}
                  type="button"
                  role="tab"
                  aria-selected={horizon === h}
                  className={`${styles.timeBtn} ${horizon === h ? styles.active : ''}`}
                  onClick={() => setHorizon(h)}
                >
                  {h}
                </button>
              ))}
            </div>
          </div>

          <MonteCarloChart horizon={horizon} />

          <div className={styles.legend} aria-hidden="true">
            <span className={styles.legendItem}>
              <span className={`${styles.legendSwatch} ${styles.band}`} /> Banda P5–P95
            </span>
            <span className={styles.legendItem} style={{ color: '#818cf8' }}>
              <span className={styles.legendSwatch} style={{ background: '#818cf8' }} /> P50 mediana
            </span>
            <span className={styles.legendItem} style={{ color: '#22c55e' }}>
              <span className={styles.legendSwatch} style={{ background: 'repeating-linear-gradient(90deg, #22c55e 0 4px, transparent 4px 8px)' }} /> P95 óptimo
            </span>
            <span className={styles.legendItem} style={{ color: '#ef4444' }}>
              <span className={styles.legendSwatch} style={{ background: 'repeating-linear-gradient(90deg, #ef4444 0 4px, transparent 4px 8px)' }} /> P5 pesimista
            </span>
          </div>

          <div className={styles.projectionStats}>
            <div className={styles.projStat}>
              <div className={styles.projStatLabel}>P50 (mediana)</div>
              <div className={`${styles.projStatValue} ${styles.pos}`}>
                <span className={styles.arrow}>↑</span>
                {fmtMoney(DATA.projectionP50)} <span className={styles.projStatPct}>{fmtPct((DATA.projectionP50 / surplus) * 100)}</span>
              </div>
            </div>
            <div className={styles.projStat}>
              <div className={styles.projStatLabel}>P95 (óptimo)</div>
              <div className={`${styles.projStatValue} ${styles.accent}`}>
                <span className={styles.arrow}>↑</span>
                {fmtMoney(DATA.projectionP95)} <span className={styles.projStatPct}>{fmtPct((DATA.projectionP95 / surplus) * 100)}</span>
              </div>
            </div>
            <div className={styles.projStat}>
              <div className={styles.projStatLabel}>P5 (pesimista)</div>
              <div className={`${styles.projStatValue} ${styles.neg}`}>
                <span className={styles.arrow}>↓</span>
                {fmtMoney(DATA.projectionP5)} <span className={styles.projStatPct}>{fmtPct((DATA.projectionP5 / surplus) * 100)}</span>
              </div>
            </div>
          </div>
        </div>

        <div className={styles.attrCard}>
          <div className={styles.chartHeaderRow}>
            <div className={styles.chartTitleWrap}>
              <div className={styles.chartTitle}>Análisis por estrategia</div>
              <div className={styles.chartSubtitle}>Rentabilidad por estrategia × símbolo · {horizon}</div>
            </div>
          </div>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Estrategia</th>
                <th>Mejor símbolo</th>
                <th>Resultado</th>
                <th>Tasa de aciertos</th>
                <th>Índice de rendimiento</th>
                <th>Rating</th>
              </tr>
            </thead>
            <tbody>
              {DATA.attribution.map((row) => {
                const isPos = row.pnl >= 0;
                const widthPct = Math.min(Math.abs(row.pnl) / row.max * 100, 100);
                return (
                  <tr key={row.strategy}>
                    <td className={styles.strat}>{row.strategy}</td>
                    <td>{row.symbol}</td>
                    <td className={isPos ? styles.pnlPos : styles.pnlNeg}>
                      <span className={styles.barCell}>
                        <span
                          className={`${styles.barFill} ${isPos ? styles.pos : styles.neg}`}
                          style={{ width: `${widthPct}%` }}
                        />
                      </span>
                      {isPos ? '↑' : '↓'} {fmtMoney(Math.abs(row.pnl))}
                    </td>
                    <td>{row.wr === null ? '—' : `${row.wr}%`}</td>
                    <td>{row.sharpe === null ? '—' : row.sharpe.toFixed(2)}</td>
                    <td>
                      <span
                        className={`${styles.badge} ${
                          row.rating === 'BEST'
                            ? styles.badgeBest
                            : row.rating === 'OK'
                            ? styles.badgeWarn
                            : styles.badgeBad
                        }`}
                      >
                        {row.rating}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/* ----- Pure SVG Monte Carlo chart (no charting dependency required) ----- */
function MonteCarloChart({ horizon }: { horizon: Horizon }) {
  // Coords for the three percentile paths; same shape as the mockup.
  const W = 700;
  const H = 240;
  const Y_BASE = 140;
  const pts = (ys: number[]) => ys.map((y, i) => `${(i / (ys.length - 1)) * W},${y}`).join(' ');

  // Band scaled slightly per horizon so the chart visually responds to tab changes.
  const scale = horizon === '7D' ? 1 : horizon === '30D' ? 1.15 : 1.35;
  const p95 = [140, 128, 115, 100, 88, 75, 62, 48, 38, 25, 15].map((y) => Y_BASE - (Y_BASE - y) * scale);
  const p50 = [140, 134, 125, 115, 105, 92, 78, 65, 55, 45, 35].map((y) => Y_BASE - (Y_BASE - y) * scale);
  const p5  = [140, 145, 148, 145, 148, 150, 148, 145, 142, 135, 125].map((y) => Y_BASE - (Y_BASE - y) * scale);

  const bandPath = `${pts(p95)} L${pts(p5.slice().reverse())} Z`;

  return (
    <svg
      className={styles.equitySvg}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Curva de equity con bandas percentiles P5, P50 y P95 sobre ${horizon}`}
    >
      {/* grid */}
      <line x1="0" y1="60" x2={W} y2="60" stroke="#2a2a2e" strokeWidth="0.5" />
      <line x1="0" y1="120" x2={W} y2="120" stroke="#2a2a2e" strokeWidth="0.5" />
      <line x1="0" y1="180" x2={W} y2="180" stroke="#2a2a2e" strokeWidth="0.5" />
      {/* confidence band */}
      <path d={bandPath} fill="#818cf8" opacity="0.18" />
      {/* baseline */}
      <line x1="0" y1={Y_BASE} x2={W} y2={Y_BASE} stroke="#2a2a2e" strokeWidth="1" strokeDasharray="2,3" />
      {/* P95 */}
      <path d={`M${pts(p95)}`} stroke="#22c55e" strokeWidth="2" fill="none" strokeDasharray="6,4" />
      {/* P50 */}
      <path d={`M${pts(p50)}`} stroke="#818cf8" strokeWidth="2.75" fill="none" />
      {/* P5 */}
      <path d={`M${pts(p5)}`} stroke="#ef4444" strokeWidth="2" fill="none" strokeDasharray="6,4" />
      {/* endpoints */}
      <circle cx="0" cy={Y_BASE} r="4" fill="#e4e4e7" />
      <circle cx={W} cy={p50[p50.length - 1]} r="5" fill="#818cf8" />
      <circle cx={W} cy={p95[p95.length - 1]} r="4" fill="#22c55e" opacity="0.7" />
      <circle cx={W} cy={p5[p5.length - 1]} r="4" fill="#ef4444" opacity="0.7" />
      {/* x axis ticks (day labels) */}
      <g fill="#71717a" fontSize="10" fontFamily="Inter, sans-serif">
        <text x="0" y={H - 6} textAnchor="start">D0</text>
        <text x={W / 4} y={H - 6} textAnchor="middle">D{horizon === '7D' ? 2 : horizon === '30D' ? 8 : 24}</text>
        <text x={W / 2} y={H - 6} textAnchor="middle">D{horizon === '7D' ? 4 : horizon === '30D' ? 16 : 48}</text>
        <text x={(W * 3) / 4} y={H - 6} textAnchor="middle">D{horizon === '7D' ? 6 : horizon === '30D' ? 24 : 72}</text>
        <text x={W} y={H - 6} textAnchor="end">D{horizon === '7D' ? 7 : horizon === '30D' ? 30 : 90}</text>
      </g>
    </svg>
  );
}
