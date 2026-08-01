# Research: Estrategias Gems Mejoradas para índices sintéticos Boom/Crash de Deriv

**Fecha:** 2026-07-31
**Autor:** research agent (leaf worker)
**Proyecto:** synthetic-trader
**Estado:** Completado

---

## ⚠️ Hallazgo crítico upfront

Antes de las mejoras técnicas, el hallazgo más importante de esta investigación:

> **La hipótesis Post-Spike Drift Capture (PSDC) ha sido falsada con datos reales de Deriv.** El proceso de spikes de Boom/Crash 1000 es estadísticamente **memoryless (Poisson)**, y las ventanas post-spike son **indistinguibles de ventanas aleatorias** en todos los tamaños testeados (50, 100, 300, 600 ticks). Combinado con costos de spread (~1,430 pts round-trip), la estrategia mean-reversion post-spike es **negative-EV** antes de considerar pérdidas por spikes. [confirmed — Orphy123/deriv-research, 15M ticks analizados]

Este documento aún documenta las técnicas investigadas — pueden ser útiles para otras hipótesis o como filtros de riesgo — pero ninguna debe considerarse validada para edge-generation en sintéticos sin su propio pre-registro y test estadístico riguroso.

**Niveles de confianza:**
- `confirmed` — respaldado por múltiples estudios o datos empíricos verificables
- `probable` — respaldado por un estudio sólido o evidencia indirecta fuerte
- `speculative` — teoría plausible sin evidencia empírica directa sobre sintéticos

---

## 1. Z-score dinámico adaptativo [probable — general finance; speculative — synthetics]

### Hallazgos

**EWMA-based volatility estimation** supera a rolling-window fija en markets reales:

- **EWMA (RiskMetrics, J.P. Morgan 1994):** `σ²_t = λ·σ²_{t-1} + (1−λ)·r²_{t-1}` con `λ=0.94`. Reacciona más rápido a cambios de régimen sin el lag de ventanas fijas. No requiere fitting. Es el baseline industry. [probable — ArkaKhorchidian/Regime-Detection-Volatility-Forecasting]

- **Optimal decay parameter:** paper de arxiv ([2105.14382](https://arxiv.org/pdf/2105.14382)) investiga el valor óptimo de λ para EWMA volatility forecasting, mostrando que λ óptimo varía por instrumento y régimen. [probable — academic]

- **Score-driven EWMA** (Blasques et al., 2016, ScienceDirect) extiende EWMA con time-varying parameters via score-driven updates, mejorando VaR forecasting. Más sofisticado pero requiere fitting. [probable — academic]

- **Adaptive Z-Score Oscillator (QuantAlgo, TradingView):** implementa z-score con volatility-adaptive bands usando EWMA std en lugar de rolling window fija. El indicador normaliza precio contra una desviación estándar adaptativa. [probable — indicator implementation, no backtest]

- **kairos-trade (dinethlive):** usa **tres estimadores en paralelo** (Welford rolling variance, EWMA, CUSUM) y dispara cuando el threshold es el **mín** de las dos bandas basadas en varianza — el más rápido reacciona primero. [probable — code implementation on Deriv synthetics]

### Aplicación a Gems

```python
# EWMA z-score en lugar de rolling window
def ewma_zscore(returns, span=20):
    alpha = 2 / (span + 1)
    var = 0.0
    z_scores = []
    for r in returns:
        var = (1 - alpha) * var + alpha * r**2  # EWMA variance
        std = np.sqrt(var)
        z = r / std if std > 0 else 0
        z_scores.append(z)
    return np.array(z_scores)
```

**Caveat para sintéticos:** EWMA asume volatility clustering (GARCH-like). Si los spikes de Boom/Crash son Poisson puro, la volatilidad inter-spike es constante y EWMA no aporta información adicional sobre cuándo vendrá el próximo spike. [confirmed — Orphy123 Phase 0: dispersion index 0.86-0.90, near-Poisson]

### Recomendación
Implementar EWMA z-score como **mejora de robustez** sobre rolling window, pero **no como edge generator**. El z-score mejor detectará spikes post-facto, pero no predice到来.

---

## 2. Volatility regime detection [probable — methods; confirmed not applicable to synthetics]

### Hallazgos

**Métodos jerarquizados por complejidad y eficacia:**

1. **ATR Ratio (simplest):** `ATR_fast / ATR_slow` ratio. Cuando > 1, volatilidad creciente (turbulento); < 1, decreciente (estable). Fácil de implementar, bien documentado en trading discrecional. [probable — widespread practitioner knowledge]

2. **Percentile-based:** Clasificar volatilidad actual como percentil de los últimos N períodos. Simple, no paramétrico. Útil como filtro binario. [probable]

3. **CUSUM (Cumulative Sum):** Detecta change-points en la media o varianza. `k = 0.5σ, h = 4σ` es la configuración estándar. kairos-trade lo usa para regime shift detection en tick deltas. Papers académicos confirman eficacia para regime switching en series financieras (Ideas.repec, Tandfonline). [probable — multiple academic + implementation sources]

4. **GARCH(1,1):** `σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}`. Captura volatility clustering. Persistencia `α+β ≈ 0.99` para SPY. Half-life de vol shock ~58 trading days. [confirmed — stylized fact, ArkaKhorchidian repo]

5. **GJR-GARCH:** Añade leverage effect (γ > 0: shocks negativos amplifican varianza más que positivos). Para SPY γ ≈ 0.098. **Irrelevante para sintéticos** — no hay order flow asimétrico en RNG. [probable — equity markets; not applicable — synthetics]

6. **HAR-RV (Corsi 2009):** Regresión multi-escala (daily/weekly/monthly realized variance). Captura long memory en RV. **Outperforma GARCH y EWMA** en out-of-sample forecasting (QLIKE rank 1). [confirmed — ArkaKhorchidian out-of-sample results]

7. **HMM (Hidden Markov Models):** 2-3 estados Gaussianos, Baum-Welch + Viterbi. Usado por Aleksander-Budzanowski y ArkaKhorchidian. Identifica Bull/Bear/Sideways. [probable — multiple repos]

### Aplicación a sintéticos — el hallazgo brutal

> **Orphy123 Phase 0.5 falsó la hipótesis de drift-regime detection.** Implementó un HMM 2-state (pure numpy Baum-Welch + Viterbi) sobre returns de 1-minuto en Boom/Crash 1000. El HMM identificó estados persistentes (median run-length 173-596 min), **pero son volatility regimes, no drift regimes.** Al testear lag-1 autocorrelation del residual de drift (regressed on spike count), |ACF| = 0.018-0.041, sentado dentro de la banda white-noise ±0.042. **No hay estructura de drift persistente para filtrar.** [confirmed — pre-registered, multiple falsification]

### Recomendación
Volatility regime detection **existe** en sintéticos (HMM lo confirma) pero **no es tradeable**. El drift residual entre spikes es white noise. Un filtro de régimen puede usarse para **risk management** (reducir tamaño en régimen turbulento) pero no genera edge. [confirmed]

---

## 3. Multi-timeframe confirmation [speculative — not validated for synthetics]

### Hallazgos

**En mercados reales:** Multi-timeframe confirmation es estándar — confirmar señal en 1m con tendencia en 5m/15m reduce falsos positivos. Bien documentado en trading discrecional y sistemático.

**Riesgo de overfitting en sintéticos:**
- Sintéticos **no tienen estructura de tendencia** — son random walk con spikes Poisson. Tendencia en 5m es ruido, no señal.
- **derivepractice (gwagsi):** Corrió tsfresh (800 features), STUMPY (matrix profile motifs), y autoencoder sobre 31M ticks de 1HZ10V. **Cero señal encontrada.** AUC = 0.50 en todos los modelos. [confirmed — rigorous ML pipeline]
- Cualquier "confirmación" multi-timeframe en sintéticos es **apophenia** — ver patrones en ruido. [probable — logical deduction from memorylessness]

### Implementación sin overfitting (la honesta)

Si se implementa, debe ser como **filtro de risk-off**, no como edge:
1. Si 5m y 1m z-score spike coinciden → mayor conviction de que el spike fue real (no ruido de cálculo)
2. **No usar** dirección de "tendencia" en timeframe mayor — no existe en sintéticos
3. Validar con **walk-forward** estricto y **pre-registration** de tiempos de timeframe antes de testear

### Recomendación
**No implementar multi-timeframe directional confirmation en sintéticos.** Es overfitting por diseño. El único uso legítimo es verificar que un spike detectado en 1m también aparece en tick data (de-noising, no directional). [speculative → probable no-edge]

---

## 4. Reversion vs continuation discrimination [speculative — no order flow in synthetics]

### Hallazgos

**En mercados reales:**

- **Order flow divergence:** Price hace higher high pero RSI/MACD/delta hace lower high → momentum divergence → señal de reversal. (Quantum-algo, Bookmap) [probable — practitioner consensus]
- **Volume profile:** Spikes con low volume en extremo → más likely reversal. High volume → continuation. [probable]
- **Momentum vs regime:** Detectar si estás en momentum phase o mean-reversion phase determina si spike revertirá o continuará. (Bookmap blog) [probable]

**En sintéticos — el problema fundamental:**

> **No hay order flow, volume, ni participantes en sintéticos.** El precio es generado por un PRNG auditable. Volume, delta, order book imbalance — todos son artificios o no existen.

- **derivepractice:** 70 features engineered, AUC = 0.50. Incluyendo momentum, distribution, complexity features. [confirmed — no predictive signal]
- **Orphy123 Phase 0:** Post-spike drift vs random windows — Welch t-test p-values 0.30-0.97 en todos los tamaños. **Statistically indistinguishable.** [confirmed — pre-registered falsification]
- **El único patrón suggestive:** Boom en w=100 mostró gap de ~1,300 pts entre post-spike (−667) y random (+640), sugiriendo mild mean reversion en primeros 100 ticks. **Pero p=0.36, indistinguible de noise.** Y incluso si fuera real: net P&L = −763 pts/trade después de spread. [confirmed — negative EV either way]

### Recomendación
**No implementar discrimination reversion vs continuation en sintéticos.** El concepto no aplica — los spikes son memoryless y no hay order flow que discriminar. La única "señal" post-spike es el hecho que el spike ocurrió, y eso no predice drift futuro. [confirmed no-edge]

---

## 5. Optimal exit strategies for spike reversion [probable — general; speculative — synthetics]

### Hallazgos

**Estudio de 2M backtests (Polakow, vectorbt):**
- **Take Profit (TP) fijo:** win rate 56.9%, el más consistente, menor std. Limita upside pero reduce volatilidad de returns. Benefiticio en sideways/short-term. [confirmed — 2M backtests]
- **Trailing Stop (TS):** win rate 35.5%. Mejor que SL en expectancy. Solo outperforma holding con stop values 20-40%. Bueno en trending, malo en choppy. [confirmed]
- **Stop Loss (SL) fijo:** win rate 30.9%. Worst expectancy. Se activa justo en bottoms de correcciones. [confirmed]
- **Holding (no stop):** highest mean return ($7/100), but highest std (0.81), win rate 39.5%. [confirmed]
- **Random exit:** outperforms SL y TS en bear markets. Inferior en sideways/bull. [confirmed — surprising]

**Paper académico (Leung & Li, 2015, IJTAF):** "Optimal Mean Reversion Trading with Transaction Costs and Stop-Loss Exit" formula optimal entry/exit thresholds para mean-reverting spreads usando OU process, considerando transaction costs y stop-loss. [probable — academic framework]

**Paper (Ning, Chakraborty, Lee, 2023, arxiv 2309.16008):** "Optimal Entry and Exit with Signature in Statistical Arbitrage" usa signature method para optimal stopping sin asumir dinámica del spread. [probable — academic, model-free]

**Kevin Davey (KJ Trading, 567k backtests):** El exit más simple (Stop & Reverse) supera a exits complejos. Conclusión: simpleza wins en exits. [probable — practitioner authority]

### Aplicación a sintéticos

**Time-based exit** es teóricamente superior a price-based para mean-reversion en sintéticos porque:
1. No hay niveles de soporte/resistencia reales (RNG)
2. Time-based captura el drift estadístico sin depender de niveles artificiales
3. Reduce compleinticidad y overfitting

**Breakeven move timing:** En mean-reversion real, mover SL a breakeven después de que el z-score cruce 0 (retorno a media) maximiza win rate pero reduce expectancy (cortas ganadores grandes). En sintéticos, donde edge es marginal o negativo, este debate es moot.

### Recomendación
Si se opera mean-reversion post-spike (a pesar del edge negativo confirmado):
1. **TP fijo pequeño** (~1.5x spread, ~2,000 pts) — highest win rate confirmado
2. **Time-based exit** como fallback si TP no hit en N ticks (30-50 ticks)
3. **No trailing stop** — en sintéticos sin tendencia, trail stools out
4. SL amplio o ninguno —_SL_ fijo tiene worst expectancy en backtests [confirmed — 2M backtests]

---

## 6. Diferencias estadísticas entre instrumentos: BOOM1000 vs BOOM500 vs CRASH1000

### Hallazgos

**Especificaciones Deriv (confirmed — Deriv official docs):**
| Instrumento | Spike direction | Avg frequency | Between-spike trend |
|---|---|---|---|
| BOOM1000 | UP | 1 spike per 1,000 ticks | Downward |
| BOOM500 | UP | 1 spike per 500 ticks | Downward |
| CRASH1000 | DOWN | 1 drop per 1,000 ticks | Upward |
| CRASH500 | DOWN | 1 drop per 500 ticks | Upward |

**Datos empíricos (confirmed — derivepractice, 16.75M ticks):**
| Symbol | Ticks recolectados | Spikes detectados | Weibull shape | Mean interval real | Advertised interval |
|---|---|---|---|---|---|
| BOOM1000 | 11.65M | 1,659 | 1.005 | ~7,000 ticks | 1,000 ticks |
| CRASH1000 | 5.1M | — | ~1.0 | ~7,000 ticks | 1,000 ticks |

> **Hallazgo clave:** Weibull shape ≈ 1.0 = **memoryless Poisson**. El intervalo real (~7,000 ticks) es **7x mayor** que el advertised (1,000 ticks). Esto sugiere que Deriv describe el parámetro del generador, no la observación empírica — los spikes pequeños no son contados como "spikes" en la práctica. [confirmed — derivepractice rigorous analysis]

**Orphy123 Phase 0 datos complementarios (90 días, ticker MT5):**
| Metric | Boom 1000 | Crash 1000 |
|---|---|---|
| Total ticks | 7,677,891 | 7,509,682 |
| Spikes (≥30k pts) | 6,795 | 5,251 |
| Spikes (≥10k pts) | 7,363 | 6,767 |
| KS test vs exp (10k) | p=0.26 (pass) | p=0.07 (pass) |
| Mean λ (10k) | ~0.00095/s | ~0.00087/s |
| Mean inter-arrival | ~1,056s (~17.6 min) | ~1,149s (~19.2 min) |
| Max spike observed | 573,860 pts | — |
| Dispersion index | 0.895 | 0.856 |

**Diferencias Boom 1000 vs Crash 1000:**
- Boom 1000 tiene **ligeramente más spikes** (7,363 vs 6,767 at 10k threshold) [confirmed]
- Crash 1000 tiene inter-arrival **ligeramente mayor** (~19.2 min vs ~17.6 min) [confirmed]
- Boom 1000 pasa KS test limpiamente; Crash 1000 marginalmente rejecta a 30k (p=0.017) pero pasa a 10k [confirmed]
- Diferencias son **de segundo orden** — ambos memoryless [confirmed]

### No hay datos para BOOM500/CRASH500

derivepractice y Orphy123 solo testearon Boom 1000 y Crash 1000. Las diferencias Boom 500 vs Boom 1000 son:
- **Frecuencia teórica 2x mayor** (500 vs 1,000 ticks) [confirmed — Deriv spec]
- **Spikes proporcionalmente más frecuentes** → más oportunidades pero más riesgo de spike durante posición [probable — logical]
- Dashboard de Deriv confirma comportamiento idéntico, solo escala diferente [confirmed]

### Configuraciones óptimas por instrumento (speculative — no backtested)

Dada la memorianness, las "configuraciones óptimas" son sobre **risk management**, no edge:

| Instrumento | Z-score threshold | Max hold time | Position size | Rationale |
|---|---|---|---|---|
| BOOM1000 | >3σ | 50-100 ticks | Standard | Post-spike mild reversion at w=100 (orphy123, p=0.36) |
| BOOM500 | >3.5σ | 30-50 ticks | Reduced 50% | 2x spike frequency = 2x spike risk during position |
| CRASH1000 | >3σ | 50-100 ticks | Standard | Identical stats to Boom 1000 |
| CRASH500 | >3.5σ | 30-50 ticks | Reduced 50% | 2x spike frequency |

**Importante:** Estas configuraciones **no han sido backtested** y la evidencia de edge es negativa. Son para **minimizar daño**, no maximizar ganancia. [speculative]

---

## Referencias (12 fuentes)

### Papers académicos

1. **Leung, T. & Li, X. (2015).** "Optimal Mean Reversion Trading with Transaction Costs and Stop-Loss Exit." *International Journal of Theoretical and Applied Finance*, 18(3). [World Scientific](https://www.worldscientific.com/doi/10.1142/S021902491550020X) / [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2222196)
   - Framework óptimo para entry/exit en mean-reverting spreads con transaction costs.

2. **Ning, B., Chakraborty, P. & Lee, K. (2023).** "Optimal Entry and Exit with Signature in Statistical Arbitrage." [arXiv:2309.16008](https://arxiv.org/html/2309.16008v4)
   - Sequential optimal stopping para mean-reversion, model-free via signature method.

3. **Blasques, F., Koopman, S.J. & Lucas, A. (2016).** "Score-driven exponentially weighted moving averages and Value-at-Risk forecasting." *International Journal of Forecasting*, 32(2), 293-302. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0169207015001211)
   - EWMA adaptativo con time-varying parameters via score-driven updates.

4. **"The optimal decay parameter in the EWMA model." (2021).** [arXiv:2105.14382](https://arxiv.org/pdf/2105.14382)
   - Análisis del parámetro λ óptimo para EWMA volatility estimation.

5. **CUSUM method in predicting regime shifts.** *Journal of Applied Statistics.* [Tandfonline](https://www.tandfonline.com/doi/abs/10.1080/02664760600708590)
   - Aplicación de CUSUM para detection de regime shifts con transaction fees.

### Repos de GitHub (quant)

6. **Orphy123/deriv-research.** [GitHub](https://github.com/Orphy123/deriv-research) — [FINDINGS.md](https://github.com/Orphy123/deriv-research/blob/main/FINDINGS.md)
   - **La fuente más importante de este documento.** Pre-registered research falsando PSDC y drift-regime hypotheses en Boom/Crash 1000 con 15M+ ticks reales.

7. **gwagsi/derivepractice.** [GitHub](https://github.com/gwagsi/derivepractice)
   - 31M ticks, 70 features, XGBoost + tsfresh + STUMPY + autoencoder. AUC=0.50. Nueva wikull shape analysis para Boom/Crash.

8. **dinethlive/kairos-trade.** [GitHub](https://github.com/dinethlive/kairos-trade)
   - Trading CLI para Deriv synthetics con adaptive threshold engine (Welford + EWMA + CUSUM + Bollinger squeeze).

9. **Aleksander-Budzanowski/HMM-GARCH-Volatility-Strategy.** [GitHub](https://github.com/Aleksander-Budzanowski/HMM-GARCH-Volatility-Strategy)
   - 3-state HMM + GARCH(1,1) para regime detection con realistic backtesting.

10. **ArkaKhorchidian/Regime-Detection-Volatility-Forecasting.** [GitHub](https://github.com/ArkaKhorchidian/Regime-Detection-Volatility-Forecasting)
    - Comparación rigurosa out-of-sample de EWMA, GARCH(1,1), GJR-GARCH, HAR-RV para vol forecasting. HAR-RV rank 1.

### Blogs técnicos y análisis empíricos

11. **Polakow, O. (2020).** "Stop Loss, Trailing Stop, or Take Profit? 2 Million Backtests Shed Light." [Medium/DataDrivenInvestor](https://medium.datadriveninvestor.com/stop-loss-trailing-stop-or-take-profit-2-million-backtests-shed-light-dde23bda40be)
    - 2M backtests con vectorbt: TP highest win rate (56.9%), holding highest mean return, random beats SL/TS en bear markets.

12. **Berko, O. (2026).** "I Analyzed 15 Million Ticks of Deriv Synthetic Data. The Edge Did Not Survive The Costs." [Medium](https://medium.com/@shiekwaku100/i-analyzed-15-million-ticks-of-deriv-synthetic-data-the-edge-did-not-survive-the-costs-5e1e85481c4d)
    - Análisis cost-aware sobre Boom/Crash 1000. Misma conclusión que Orphy123 desde ángulo diferente.

### Fuentes complementarias (no numeradas, referencia rápida)

- QuantAlgo "Adaptive Z-Score Oscillator" — [TradingView](https://www.tradingview.com/script/qTp3HDzg-Adaptive-Z-Score-Oscillator-QuantAlgo/)
- Deriv Crash/Boom official specification — [Deriv FAQ](https://hercules.finance/faq/what-are-derivs-crash-boom-1000-and-step-indices/)
- Keith Rainz Boom/Crash comparison — [keithrainz.me](https://keithrainz.me/difference-between-boom-and-crash-indices/)
- Kevin Davey "567k backtests" — [KJ Trading](https://kjtradingsystems.com/algo-trading-exits.html)
- Corsi, F. (2009). HAR-RV original paper (implícito en ArkaKhorchidian repo).

---

## Resumen ejecutivo para implementación

| Mejora propuesta | Confianza de edge en sintéticos | Recomendación |
|---|---|---|
| EWMA z-score adaptativo | ❌ No genera edge | Implementar por robustez, no por edge |
| Volatility regime detection (HMM/GARCH/CUSUM) | ❌ Régimenes existen pero no son tradeables | Usar solo para risk management |
| Multi-timeframe confirmation | ❌ Overfitting por diseño | **No implementar** |
| Reversion vs continuation discrimination | ❌ No hay order flow en sintéticos | **No implementar** |
| Optimal exit (TP fijo + time-based) | ⚠️ Mejora P&L pero el edge base es negativo | Implementar si se opera a pesar de edge negativo |
| Configuración por instrumento (Boom 500 vs 1000) | ⚠️ Diferencias reales son de 2º orden | Ajustar size/hold por frecuencia de spike |

**Bottom line honesto:** Dos estudios independientes (Orphy123 con 15M ticks pre-registered, derivepractice con 31M ticks ML) llegan a la misma conclusión: **Los sintéticos de Deriv son statisticalmente justos. No hay edge post-spike.** La estrategia Gems actual (mean-reversion post-spike) tiene expected value negativo después de spread. Las mejoras técnicas investigadas aquí pueden mejorar la *ejecución* pero no pueden crear edge donde los datos dicen que no existe.

**Si se continúa:** El camino honesto es (1) backtest riguroso con costos reales, (2) buscar edge en lugares no testeados (time-of-day, cross-symbol dependence), o (3) pivot a mercados reales donde order flow y microestructura existen.
