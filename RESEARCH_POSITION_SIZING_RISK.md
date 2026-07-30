# Investigación: Patrones de Position Sizing Dinámico y Risk Management en Trading Algorithmic Open Source

## Resumen Ejecutivo
Investigación sobre 7 patrones clave de position sizing y risk management en bots de trading open source:
1. Kelly Criterion y fractional Kelly
2. Fixed Fractional positioning  
3. Volatility scaling (ATR-based, volatility targeting)
4. Circuit Breaker pattern
5. Trailing stops dinámicos (Chandelier exit, ATR trailing)
6. Monte Carlo simulation para backtesting
7. Walk-forward optimization

Incluye tabla comparativa, snippets de código reales y recomendaciones específicas para mejorar el bot de Range Break en RB100.

---

## 1. Kelly Criterion y Fractional Kelly

### Fórmula Base
Kelly % = (bp - q) / b
Donde:
- b = odds received (avg_win / avg_loss)
- p = win probability  
- q = loss probability (1 - p)

### Implementaciones Reales

**Ejemplo 1: Sistema con Confidence Adjustment y Volatility Scaling** (de algos.pro)
```python
@dataclass
class TradeSetup:
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float  # 0.0 to 1.0
    strategy_win_rate: float
    strategy_avg_win: float
    strategy_avg_loss: float

class KellyPositionSizer:
    def __init__(self, account_size: float, max_position_pct: float = 0.05, 
                 kelly_fraction: float = 0.25, min_position_pct: float = 0.005, 
                 max_daily_risk: float = 0.02):
        self.account_size = account_size
        self.max_position_pct = max_position_pct
        self.kelly_fraction = kelly_fraction
        self.min_position_pct = min_position_pct
        self.max_daily_risk = max_daily_risk
        self.daily_risk_used = 0.0

    def calculate_kelly(self, setup: TradeSetup) -> float:
        p = setup.strategy_win_rate
        w = setup.strategy_avg_win
        l = setup.strategy_avg_loss
        
        if w <= 0 or l <= 0:
            return 0.0
            
        kelly = (p * w - (1 - p) * l) / w
        return max(0.0, kelly)

    def calculate_position_size(self, setup: TradeSetup, current_positions: int = 0) -> dict:
        # 1. raw kelly
        raw_kelly = self.calculate_kelly(setup)
        
        if raw_kelly <= 0:
            return {'shares': 0, 'dollars': 0, 'risk_pct': 0, 'reason': 'negative_edge'}
        
        # 2. apply fractional kelly
        fractional_kelly = raw_kelly * self.kelly_fraction
        
        # 3. confidence adjustment
        confidence_adjusted = fractional_kelly * setup.confidence
        
        # 4. position count adjustment
        position_factor = 1.0 / (1 + current_positions * 0.2)
        adjusted_pct = confidence_adjusted * position_factor
        
        # 5. apply bounds
        bounded_pct = np.clip(adjusted_pct, self.min_position_pct, self.max_position_pct)
        
        # 6. daily risk check
        risk_per_trade = self._calculate_risk(setup, bounded_pct)
        remaining_daily_risk = self.max_daily_risk - self.daily_risk_used
        
        if risk_per_trade > remaining_daily_risk:
            scale_factor = remaining_daily_risk / risk_per_trade
            bounded_pct *= scale_factor
            risk_per_trade = remaining_daily_risk
        
        # 7. calculate final values
        position_dollars = self.account_size * bounded_pct
        shares = int(position_dollars / setup.entry_price)
        
        return {
            'shares': shares,
            'dollars': shares * setup.entry_price,
            'risk_pct': risk_per_trade,
            'position_pct': bounded_pct,
            'raw_kelly': raw_kelly,
            'confidence_factor': setup.confidence,
            'reason': 'calculated'
        }
```

**Pitfalls Identificados:**
- Kelly asume win rate y profit factor constantes (raramente cierto en la práctica)
- Full Kelly es demasiado volátil (drawdowns potenciales >50%)
- Quarter Kelly captura ~75% del crecimiento con mucha menos varianza
- Usar Kelly para asignación relativa de estrategias, no para sizing absoluto
- Siempre combinar con límites de riesgo duro (max position %, daily risk budget)

---

## 2. Fixed Fractional Positioning

### Fórmula
Position Size = (Account Equity × Risk %) / |Entry Price - Stop Loss|

### Cuándo Usarlo
- Cuando se tiene un stop loss definido basado en soporte/resistencia o volatilidad
- Cuando se quiere riesgo consistente por trade (ej: 1-2% de cuenta)
- En estrategias con distribuciones de retorno relativamente normales

### Ventajas
- Simple de implementar y entender
- Riesgo consistente por trade independientemente de volatilidad
- Funciona bien cuando el stop loss refleja correctamente el riesgo del trade

### Limitaciones
- No ajusta por edge/confianza de la estrategia
- Puede ser demasiado conservador en setups de alta confianza
- No aprovecha información de probabilidad de win/loss como Kelly

---

## 3. Volatility Scaling (ATR-based, Volatility Targeting)

### Concepto Core
Ajustar position size inversamente proporcional a volatilidad para mantener riesgo consistente.

### Fórmula ATR-based
Position Size = (Account Balance × Risk %) / (ATR × ATR Multiplier)

### Implementaciones Reales

**Ejemplo 1: Dynamic Position Sizer ATR Calculator**
```python
def calculate_dynamic_position_size(df, account_balance, risk_pct=0.02, atr_multiplier=2.0):
    # 1. Calculate ATR
    df.ta.atr(length=14, append=True)
    atr_col = [c for c in df.columns if 'ATRr' in c][0]
    
    current_price = df['close'].iloc[-1]
    current_atr = df[atr_col].iloc[-1]
    
    # 2. Stop Loss distance based on volatility
    stop_loss_distance = current_atr * atr_multiplier
    stop_loss_price = current_price - stop_loss_distance
    
    # 3. Capital at risk
    capital_at_risk = account_balance * risk_pct
    
    # 4. Dynamic Position Size
    position_size = capital_at_risk / stop_loss_distance
    
    return position_size, stop_loss_price
```

**Ejemplo 2: Volatility-Adjusted Kelly Sizer**
```python
class VolatilityAdjustedSizer(KellyPositionSizer):
    def __init__(self, *args, target_vol: float = 0.02, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_vol = target_vol

    def calculate_position_size(self, setup: TradeSetup, current_vol: float, current_positions: int = 0) -> dict:
        # get base kelly size
        base_result = super().calculate_position_size(setup, current_positions)
        
        if base_result['shares'] == 0:
            return base_result
        
        # volatility adjustment
        vol_ratio = self.target_vol / current_vol if current_vol > 0 else 1.0
        vol_adjusted_pct = base_result['position_pct'] * vol_ratio
        
        # re-apply bounds after vol adjustment
        vol_adjusted_pct = np.clip(
            vol_adjusted_pct,
            self.min_position_pct,
            self.max_position_pct
        )
        
        position_dollars = self.account_size * vol_adjusted_pct
        shares = int(position_dollars / setup.entry_price)
        
        return {
            **base_result,
            'shares': shares,
            'dollars': shares * setup.entry_price,
            'position_pct': vol_adjusted_pct,
            'vol_adjustment': vol_ratio
        }
```

### Reglas Prácticas
- ATR Multiplier: 1.0-1.5x para intraday, 1.5-2.5x para swing, 2.5-3.5x para multi-semana
- Siempre aplicar hard caps de posición máxima independientemente de la fórmula
- Combinar con detección de régimen de volatilidad (ATR corto / ATR largo)

---

## 4. Circuit Breaker Pattern

### Implementaciones Reales

**Ejemplo 1: Circuit Breaker Simple (DEV Community)**
```python
class CircuitBreaker:
    def __init__(self, max_daily_loss_pct=0.02, max_consecutive_losses=3):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.tripped = False
        self.trip_time = None
        self.cooldown_hours = 24

    def record_trade(self, pnl_pct: float):
        if pnl_pct < 0:
            self.daily_loss += abs(pnl_pct)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        self._check_trip()

    def _check_trip(self):
        if self.daily_loss >= self.max_daily_loss_pct:
            self._trip("Daily loss limit hit")
        elif self.consecutive_losses >= self.max_consecutive_losses:
            self._trip(f"{self.consecutive_losses} consecutive losses")

    def _trip(self, reason: str):
        self.tripped = True
        self.trip_time = datetime.now()
        logger.warning(f"CIRCUIT BREAKER TRIPPED: {reason}")

    def is_open(self) -> bool:
        if not self.tripped:
            return False
        hours_since = (datetime.now() - self.trip_time).seconds / 3600
        if hours_since >= self.cooldown_hours:
            self.reset()
            return False
        return True

    def reset(self):
        self.tripped = False
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        logger.info("Circuit breaker reset")
```

**Ejemplo 2: Sistema Multi-Nivel (KillSwitch - AI-MultiColony)**
```python
class KillSwitch:
    def check_auto_activate(
        self,
        daily_pnl_pct: float = 0.0,
        weekly_pnl_pct: float = 0.0,
        max_drawdown_pct: float = 0.0,
        volatility_pct: float = 0.0,
    ) -> Optional[KillSwitchEvent]:
        # Check daily loss
        daily_loss = abs(min(0, daily_pnl_pct))
        if daily_loss >= self._config.auto_daily_loss_pct:
            return self.activate(
                level=KillSwitchLevel.LEVEL_1,
                reason=f"Daily loss {daily_loss:.2f}% exceeded threshold {self._config.auto_daily_loss_pct}%",
                trigger=KillSwitchTrigger.DAILY_LOSS_EXCEEDED,
                auto_activated=True,
            )
        
        # Check weekly loss
        weekly_loss = abs(min(0, weekly_pnl_pct))
        if weekly_loss >= self._config.auto_weekly_loss_pct:
            return self.activate(
                level=KillSwitchLevel.LEVEL_2,
                reason=f"Weekly loss {weekly_loss:.2f}% exceeded threshold {self._config.auto_weekly_loss_pct}%",
                trigger=KillSwitchTrigger.WEEKLY_LOSS_EXCEEDED,
                auto_activated=True,
            )
        
        # Check max drawdown
        if max_drawdown_pct >= self._config.auto_max_drawdown_pct:
            return self.activate(
                level=KillSwitchLevel.LEVEL_2,
                reason=f"Drawdown {max_drawdown_pct:.2f}% exceeded threshold {self._config.auto_max_drawdown_pct}%",
                trigger=KillSwitchTrigger.DRAWDOWN_EXCEEDED,
                auto_activated=True,
            )
        
        # Check volatility spike
        if volatility_pct >= self._config.auto_volatility_spike_pct:
            return self.activate(
                level=KillSwitchLevel.LEVEL_1,
                reason=f"Volatility {volatility_pct:.2f}% spike exceeded threshold",
                trigger=KillSwitchTrigger.VOLATILITY_SPIKE,
                auto_activated=True,
            )
        
        return None
```

### Parámetros que Funcionan en la Práctica
- Daily loss limit: 2% (más agresivo) a 5% (más conservador)
- Consecutive losses: 3-5 antes de activar breaker
- Cooldown: 24 horas para reset diario, 1-7 días para pérdidas consecutivas
- Siempre combinar múltiples breakers (daily loss, consecutive losses, drawdown)

---

## 5. Trailing Stops Dinámicos

### Chandelier Exit
Long Stop = Highest High(lookback) - (ATR × multiplier)
Short Stop = Lowest Low(lookback) + (ATR × multiplier)

### Implementación Reala
```python
class NovaQuantPosition:
    def __init__(self, entry_price, initial_stop_loss, atr_multiplier=2.0):
        self.entry_price = entry_price
        self.current_stop_loss = initial_stop_loss
        self.highest_price_reached = entry_price
        self.atr_multiplier = atr_multiplier
        self.is_active = True

    def update_trailing_stop(self, current_price, current_atr):
        if not self.is_active:
            return

        # 1. Update highest price since entry
        if current_price > self.highest_price_reached:
            self.highest_price_reached = current_price
            
            # 2. Calculate proposed trailing stop
            proposed_stop = self.highest_price_reached - (current_atr * self.atr_multiplier)
            
            # 3. Ratchet: Only move UP
            if proposed_stop > self.current_stop_loss:
                self.current_stop_loss = proposed_stop
                print(f"[RISK ENGINE] Trailing Stop Ratcheted UP to: ${self.current_stop_loss:,.2f}")

        # 4. Check for Stop Loss Trigger
        if current_price <= self.current_stop_loss:
            self.execute_exit(current_price)

    def execute_exit(self, exit_price):
        self.is_active = False
        pnl = exit_price - self.entry_price
        print(f"[POSITION CLOSED] Stop Loss Hit at ${exit_price:,.2f}. PnL: ${pnl:,.2f}")
```

### Variantes
- **ATR Trailing Stop Simple**: Trail = highest_close - ATR × multiplier (solo mueve up)
- **EMA Trailing**: Exit on N consecutive closes below EMA
- **Parabolic SAR**: Sistema de aceleración integrado

---

## 6. Monte Carlo Simulation para Backtesting

### Métodos en Jesse
1. **Trade-Order Shuffling**: Mezcla orden de trades manteniendo resultados individuales
2. **Candles-Based Monte Carlo**: Usa pipelines para crear versiones ligeramente cambiadas de datos de mercado

### Implementación Reala (AlgoKing)
```python
@dataclass
class MonteCarloConfig:
    n_simulations: int = 10_000
    confidence_levels: List[float] = field(
        default_factory=lambda: [0.05, 0.25, 0.50, 0.75, 0.95]
    )
    use_block_bootstrap: bool = True
    block_size: int = 5  # preserve some autocorrelation
    account_size: float = 1_200_000.0
    max_workers: int = 8

class MonteCarloBacktest:
    def _single_simulation(self, seed: int) -> np.ndarray:
        rng = np.random.RandomState(seed)
        n_trades = len(self._pnl_array)

        if self.config.use_block_bootstrap:
            # block bootstrap preserves short-term autocorrelation
            blocks = []
            while len(blocks) < n_trades:
                start = rng.randint(0, n_trades - self.config.block_size)
                block = self._pnl_array[start:start + self.config.block_size]
                blocks.extend(block)
            resampled = np.array(blocks[:n_trades])
        else:
            # simple bootstrap - iid assumption
            indices = rng.randint(0, n_trades, size=n_trades)
            resampled = self._pnl_array[indices]

        # cumulative equity curve
        equity = self.config.account_size + np.cumsum(resampled)
        return equity

    def run(self) -> Dict:
        seeds = range(self.config.n_simulations)
        
        # parallel execution
        with ProcessPoolExecutor(max_workers=self.config.max_workers) as executor:
            equity_curves = list(executor.map(self._single_simulation, seeds))

        curves = np.array(equity_curves)
        
        # calculate stats across all simulations
        final_values = curves[:, -1]
        returns = (final_values - self.config.account_size) / self.config.account_size
        
        # drawdown calculation for each path
        max_drawdowns = []
        for curve in curves:
            running_max = np.maximum.accumulate(curve)
            drawdowns = (curve - running_max) / running_max
            max_drawdowns.append(np.min(drawdowns))

        max_drawdowns = np.array(max_drawdowns)
        
        # percentile analysis
        percentiles = {}
        for level in self.config.confidence_levels:
            pct = int(level * 100)
            percentiles[f'p{pct}'] = {
                'final_return': float(np.percentile(returns, pct)),
                'max_drawdown': float(np.percentile(max_drawdowns, pct)),
                'final_value': float(np.percentile(final_values, pct))
            }

        return {
            'n_simulations': self.config.n_simulations,
            'n_trades': len(self.trades),
            'median_return': float(np.median(returns)),
            'mean_return': float(np.mean(returns)),
            'std_return': float(np.std(returns)),
            'median_max_drawdown': float(np.median(max_drawdowns)),
            'worst_drawdown': float(np.min(max_drawdowns)),
            'prob_profit': float(np.mean(final_values > self.config.account_size)),
            'prob_ruin': float(np.mean(np.min(curves, axis=1) < self.config.account_size * 0.75)),
            'percentiles': percentiles,
        }
```

### Insights Clave
- Block bootstrap (tamaño 5 para trades diarios) preserva autocorrelation mejor que iid bootstrap
- Siempre mirar las colas (5º percentil) no solo la mediana
- Probabilidad de ruin > 2% indica fragilidad significativa
- 10,000 simulaciones es mínimo para estimaciones de cola estables

---

## 7. Walk-Forward Optimization

### Enfoques
- **Anchored (Expanding)**: Ventana de entrenamiento crece desde fecha fija
- **Rolling**: Ventana de entrenamiento de longitud fija que se desliza

### Plantilla de 60 líneas (AI Fin Hub)
```python
def walk_forward(
    returns: pd.Series,
    fit_fn,           # (is_returns) -> params
    signal_fn,        # (oos_returns, params) -> pd.Series of {-1, 0, +1}
    is_days: int = 1260,     # ~5 trading years
    oos_days: int = 252,     # ~1 trading year
    step: int | None = None, # default: non-overlapping
    anchored: bool = True,
) -> pd.DataFrame:
    if step is None:
        step = oos_days
    n = len(returns)
    rows = []
    start = 0
    while start + is_days + oos_days <= n:
        is_start = 0 if anchored else start
        is_end = start + is_days
        oos_end = is_end + oos_days

        is_slice = returns.iloc[is_start:is_end]
        oos_slice = returns.iloc[is_end:oos_end]

        params = fit_fn(is_slice)
        is_sig = signal_fn(is_slice, params)
        oos_sig = signal_fn(oos_slice, params)

        is_pnl = (is_sig.shift(1).fillna(0) * is_slice).dropna()
        oos_pnl = (oos_sig.shift(1).fillna(0) * oos_slice).dropna()

        rows.append({
            "fold_start": str(is_slice.index[0].date()),
            "fit_end": str(is_slice.index[-1].date()),
            "oos_end": str(oos_slice.index[-1].date()),
            "params": params,
            "is_sharpe": _sharpe(is_pnl),
            "oos_sharpe": _sharpe(oos_pnl),
            "oos_return": float(oos_pnl.sum()),
            "oos_equity": oos_pnl,
        })
        start += step
    return pd.DataFrame(rows)

def _sharpe(x: pd.Series, freq: int = 252) -> float:
    if len(x) < 2 or x.std(ddof=0) == 0:
        return float("nan")
    return float(np.sqrt(freq) * x.mean() / x.std(ddof=0))
```

### Buenas Prácticas
- IS length ≥ 5× OOS length para estimaciones confiables
- Step size = OOS length (ventanas no sobrepostas)
- Re-optimizar cada fold por defecto
- Documentar metodología antes de ver resultados
- Hacer tanto anchored como rolling para diagnóstico de régimen

---

## Tabla Comparativa: Enfoques de Position Sizing

| Característica | Kelly Criterion | Fixed Fractional | Volatility Scaling (ATR) |
|----------------|-----------------|------------------|--------------------------|
| **Base Teórica** | Maximización de crecimiento logarítmico | Riesgo fijo por trade | Riesgo ajustado por volatilidad |
| **Fórmula** | f* = (bp - q) / b | Size = (Equity × Risk %) / |Entry-SL| | Size = (Equity × Risk %) / (ATR × Mult) |
| **Inputs Requeridos** | Win rate, avg win/loss | Stop loss distance | ATR value |
| **Ajusta por Edge** | ✅ Sí (probabilidad y payoff) | ❌ No | ❌ No (solo volatilidad) |
| **Ajusta por Volatilidad** | Indirectamente (a través de avg win/loss) | ❌ No | ✅ Sí (directamente) |
| **Complejidad** | Media-Alta | Baja | Media |
| **Robustez a Cambios de Régimen** | Baja (asume parámetros estables) | Media | Alta (se adapta a volatilidad) |
| **Drawdown Típico** | Alto (sin fraccionamiento) | Medio | Bajo-Medio |
| **Mejor Para** | Estrategias con edge medible y estable | Estrategias con stops bien definidos | Mercados con volatilidad cambiante |
| **Implementación en Proyecto Actual** | ✅ Sí (Quarter Kelly 0.25) | ❌ No implementado | ❌ No implementado |
| **Recomendación para RB100** | Mantener pero añadir ajustes de confidence y volatilidad | Considerar para casos de stops basados en soporte/resistencia | Altamente recomendado para RB100 |

---

## Snippets de Código de Circuit Breakers Reales

### 1. Circuit Breaker con Múltiples Condiciones (Daily Loss + Consecutive Losses)
```python
class EnhancedCircuitBreaker:
    def __init__(self, max_daily_loss_pct=0.03, max_consecutive_losses=4, 
                 max_drawdown_pct=0.10, cooldown_hours=24):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.max_drawdown_pct = max_drawdown_pct
        self.cooldown_hours = cooldown_hours
        
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.peak_equity = 0.0
        self.current_equity = 0.0
        self.tripped = False
        self.trip_time = None
        self.trip_reason = ""
    
    def update_equity(self, current_equity: float):
        self.current_equity = current_equity
        if self.peak_equity == 0:
            self.peak_equity = current_equity
        else:
            self.peak_equity = max(self.peak_equity, current_equity)
    
    def record_daily_pnl(self, daily_pnl_pct: float):
        if daily_pnl_pct < 0:
            self.daily_loss += abs(daily_pnl_pct)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self._check_conditions()
    
    def _check_conditions(self):
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity if self.peak_equity > 0 else 0
        
        conditions = [
            (self.daily_loss >= self.max_daily_loss_pct, f"Daily loss limit: {self.daily_loss:.2%}"),
            (self.consecutive_losses >= self.max_consecutive_losses, f"Consecutive losses: {self.consecutive_losses}"),
            (drawdown >= self.max_drawdown_pct, f"Drawdown limit: {drawdown:.2%}")
        ]
        
        for condition, reason in conditions:
            if condition and not self.tripped:
                self._trip(reason)
                break
    
    def _trip(self, reason: str):
        self.tripped = True
        self.trip_time = datetime.now()
        self.trip_reason = reason
        logger.warning(f"CIRCUIT BREAKER TRIPPED: {reason}")
    
    def is_open(self) -> bool:
        if not self.tripped:
            return False
        hours_since = (datetime.now() - self.trip_time).total_seconds() / 3600
        if hours_since >= self.cooldown_hours:
            self.reset()
            return False
        return True
    
    def reset(self):
        self.tripped = False
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.trip_time = None
        self.trip_reason = ""
        logger.info("Circuit breaker reset")
```

### 2. Sistema de Escala de Tamaño basado en Condiciones de Mercado
```python
class DynamicPositionScaler:
    def __init__(self):
        self.volatility_regime = "normal"  # low, normal, high
        self.consecutive_losses = 0
        self.drawdown_pct = 0.0
    
    def get_size_multiplier(self) -> float:
        """Returns multiplier to apply to base position size"""
        multipliers = []
        
        # Volatility adjustment
        if self.volatility_regime == "high":
            multipliers.append(0.5)  # Reduce size by 50% in high vol
        elif self.volatility_regime == "low":
            multipliers.append(1.2)  # Increase size by 20% in low vol
        
        # Consecutive losses adjustment
        if self.consecutive_losses >= 5:
            multipliers.append(0.0)  # Halt
        elif self.consecutive_losses >= 3:
            multipliers.append(0.5)  # Half size
        elif self.consecutive_losses >= 1:
            multipliers.append(0.8)  # Reduce slightly
        
        # Drawdown adjustment
        if self.drawdown_pct >= 0.10:  # 10% drawdown
            multipliers.append(0.3)
        elif self.drawdown_pct >= 0.05:  # 5% drawdown
            multipliers.append(0.6)
        
        # Return most restrictive multiplier
        return min(multipliers) if multipliers else 1.0
    
    def update_market_conditions(self, atr_ratio: float, daily_pnl_pct: float, 
                               peak_equity: float, current_equity: float):
        # Update volatility regime (ATR short / ATR long)
        if atr_ratio > 1.5:
            self.volatility_regime = "high"
        elif atr_ratio < 0.7:
            self.volatility_regime = "low"
        else:
            self.volatility_regime = "normal"
        
        # Update consecutive losses
        if daily_pnl_pct < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        
        # Update drawdown
        if peak_equity > 0:
            self.drawdown_pct = (peak_equity - current_equity) / peak_equity
```

---

## Recomendaciones Específicas para el Bot de Range Break en RB100

### Estado Actual (del PROJECT.md y risk manager)
- Kelly Quarter (0.25) implementado ✅
- Circuit breaker: 5 pérdidas consecutivas ✅
- Daily DD limit: 5% ✅
- Max trades/day: 8 ✅
- Max risk per trade: 1.5% ✅

### Áreas de Mejoria Recomendadas

#### 1. Mejorar el Sistema Kelly Actual
**Problema**: Kelly actual solo usa win/loss amounts sin considerar confidence o volatilidad del setup.

**Solución**: Implementar Kelly ajustado por confidence y volatilidad (similar al ejemplo de algos.pro)
```python
# En src/risk/manager.py, extender el método position_size
def position_size_enhanced(
    self,
    capital: float,
    win_probability: float,
    win_amount: float,
    loss_amount: float,
    confidence: float = 0.5,  # 0-1 scale based on setup quality
    current_volatility: float = None,  # ATR value or volatility percentile
    target_volatility: float = 0.02,  # Target volatility for scaling
    current_positions: int = 0
) -> float:
    # Kelly base
    p = win_probability
    q = 1.0 - p
    b = win_amount / loss_amount if loss_amount > 0 else 0
    kelly = (p * b - q) / b if b > 0 else 0
    
    # Fractional Kelly (quarter)
    fractional_kelly = kelly * self.config.kelly_fraction
    
    # Confidence adjustment (0.5-1.5 range)
    confidence_factor = 0.5 + confidence  # Maps 0-1 to 0.5-1.5
    confidence_adjusted = fractional_kelly * confidence_factor
    
    # Volatility adjustment (if data provided)
    if current_volatility and current_volatility > 0:
        vol_ratio = target_volatility / current_volatility
        vol_adjusted = confidence_adjusted * vol_ratio
    else:
        vol_adjusted = confidence_adjusted
    
    # Position count adjustment (reduce size with more positions)
    position_factor = 1.0 / (1 + current_positions * 0.15)
    adjusted_pct = vol_adjusted * position_factor
    
    # Apply bounds
    max_risk_pct = self.config.max_risk_per_trade
    min_risk_pct = 0.005  # 0  # Minimum
    bounded_pct = np.clip(adjusted_pct, min_risk_pct, max_risk_pct)
    
    # Daily risk check (simplified)
    risk_per_trade = bounded_pct  # approximation
    if hasattr(self, 'daily_risk_used'):
        remaining_daily_risk = self.config.max_daily_drawdown - self.daily_risk_used
        if risk_per_trade > remaining_daily_risk:
            scale_factor = remaining_daily_risk / risk_per_trade
            bounded_pct *= scale_factor
    
    size = bounded_pct * capital
    
    if size <= 0:
        return 0.0
    
    return round(size, 2)
```

#### 2. Mejorar el Circuit Breaker
**Problema**: Solo considera pérdidas consecutivas, ignora drawdown y límites diarios de forma independiente.

**Solución**: Implementar circuit breaker multi-condicional con escalado de tamaño
- Daily loss limit: 3% (más agresivo que actual 5%)
- Consecutive losses: 4 (en lugar de 5)
- Drawdown limit: 8% desde peak
- Añadir sistema de escalado basado en condiciones de mercado

#### 3. Añadir Volatility Scaling
**Problema**: No hay ajuste por volatilidad del mercado, lo que puede lead to overexposure en períodos de alta volatilidad.

**Solución**: Añadir ATR-based position sizing como capa adicional
- Calcular ATR(14) en tiempo real
- Usar fórmula: Position Size = (Account × Risk %) / (ATR × 2.0)
- Aplicar como límite superior al Kelly sizing (tomar el mínimo de ambos)

#### 4. Implementar Trailing Stops Dinámicos
**Problema**: Las estrategias de rango break pueden desarrollar tendencias fuertes que se cortan prematuramente con stops fijos.

**Solución**: Añadir ATR trailing stop (Chandelier exit) como opción de salida
- Para longs: Trail = Highest High(22) - (ATR × 2.5)
- Para shorts: Trail = Lowest Low(22) + (ATR × 2.5)
- Activar solo después de cierto nivel de ganancia (ej: 1R) para evitar whipsaw

#### 5. Mejorar el Backtesting con Monte Carlo y Walk-Forward
**Problema**: El backtesting actual podría no capturar suficientemente la variabilidad de resultados.

**Solución**: 
- Implementar Monte Carlo simulation con block bootstrap (tamaño de bloque = 5 trades)
- Añadir walk-forward analysis con ventanas anchored (2 años training, 3 meses testing)
- Reportar no solo mediana, sino también percentiles 5º y 95º de Sharpe y drawdown

### Implementación Priorizada

**Fase 1 (Inmediata)**: 
- Mejorar position sizing con confidence adjustment
- Mejorar circuit breaker con múltiples condiciones y drawdown tracking

**Fase 2 (Corto plazo)**:
- Añadir volatility scaling (ATR-based) como filtro superior
- Implementar trailing stop básico para captura de tendencias

**Fase 3 (Mediano plazo)**:
- Implementar Monte Carlo simulation para validación de robustness
- Añadir walk-forward analysis para detección de overfitting

---

## Conclusión

El bot de Range Break en RB100 ya tiene una base sólida de risk management con Kelly quarter y circuit breaker básico. Las mejoras recomendadas se enfocan en:

1. **Hacer el Kelly más inteligente** agregando confidence y volatilidad adjustments
2. **Hacer el circuit breaker más robusto** con múltiples condiciones y monitoreo de drawdown
3. **Añadir adaptación a volatilidad** mediante ATR-based scaling
4. **Mejorar captura de tendencias** con trailing stops dinámicos
5. **Validar rigurosamente** con Monte Carlo y walk-forward analysis

Estas mejoras deberían aumentar el ratio de Sharpe manteniendo o reduciendo el drawdown máximo, haciendo el bot más adaptable a diferentes regímenes de mercado en el RB100.

*Investigación completada el $(date)*