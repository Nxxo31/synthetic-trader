import styles from './projection.module.css';

/**
 * Streaming fallback shown while /projection page payload is in flight.
 * Mirrors the split-pane layout so the prefetch→render swap is visually stable.
 */
export default function Loading() {
  return (
    <div className={styles.app} aria-busy="true" aria-live="polite">
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.logo} style={{ opacity: 0.4 }}>pr<b>a</b>dx</span>
        </div>
        <div className={styles.headerRight} style={{ opacity: 0.4 }}>
          <div className={styles.balanceCard}>
            <span className={styles.balanceLabel}>Wallet</span>
            <span className={styles.balanceValue}>—</span>
          </div>
        </div>
      </header>
      <section className={styles.leftPane}>
        <div className={styles.sectionTitle} style={{ opacity: 0.5 }}>Cargando Asignador de capital…</div>
        <div className={styles.allocVisual} style={{ minHeight: 180, opacity: 0.4 }} />
        <div className={styles.controlGroup} style={{ opacity: 0.4 }}>
          <div className={styles.controlLabel}><span>Reserva base</span></div>
          <div className={styles.range} style={{ height: 6 }} />
        </div>
      </section>
      <section className={styles.rightPane}>
        <div className={styles.sectionTitle} style={{ opacity: 0.5 }}>Cargando Proyección estadística…</div>
        <div className={styles.chartContainer} style={{ minHeight: 280, opacity: 0.4 }} />
        <div className={styles.attrCard} style={{ minHeight: 160, opacity: 0.4 }} />
      </section>
    </div>
  );
}
