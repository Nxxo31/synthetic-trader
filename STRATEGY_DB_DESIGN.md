# Diseño de Base de Datos Histórica para Estrategias de Trading Algorítmico

## Visión General
Base de datos que almacena versiones históricas de estrategias, sus métricas de performance, comparaciones y evoluciones para permitir auto-mejora continua.

## Tecnologías Utilizadas
- **SQLite**: Para almacenamiento estructurado de metadatos y configuraciones
- **Parquet/JSONL**: Para almacenamiento de resultados detallados y series temporales
- **Versioning semántico**: Para tracking de cambios en estrategias

## Schema de Base de Datos

### Tabla Principal: `strategies`
```sql
CREATE TABLE strategies (
    strategy_id TEXT PRIMARY KEY,           -- UUID único para cada versión
    name TEXT NOT NULL,                     -- Nombre canónico (range_break, volatility, etc.)
    version TEXT NOT NULL,                  -- Version semántica (v1.2.3)
    symbol TEXT NOT NULL,                   -- Símbolo asociado (RB100, R_100, etc.)
    config_json TEXT NOT NULL,              -- Configuración completa de la estrategia
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 0,            -- Versión actualmente activa
    parent_strategy_id TEXT,                -- Referencia a versión anterior (para lineage)
    FOREIGN KEY (parent_strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX idx_strategies_name_version ON strategies(name, version);
CREATE INDEX idx_strategies_symbol ON strategies(symbol);
CREATE INDEX idx_strategies_active ON strategies(is_active);
```

### Tabla de Métricas de Performance: `strategy_performance`
```sql
CREATE TABLE strategy_performance (
    perf_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    backtest_id TEXT UNIQUE,                -- Referencia a reporte de backtest específico
    timeframe TEXT NOT NULL,                -- Período de backtest (ej: "2024-01-01_to_2024-12-31")
    total_trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,                 -- 0.0 a 1.0
    sharpe_ratio REAL NOT NULL,
    max_drawdown REAL NOT NULL,             -- Valor negativo (ej: -0.15 para -15%)
    profit_factor REAL NOT NULL,
    expectancy REAL NOT NULL,               -- En R-multiples
    total_pnl REAL NOT NULL,
    avg_pnl_per_trade REAL NOT NULL,
    total_return REAL NOT NULL,             -- % retorno total
    volatility REAL NOT NULL,               -- Volatilidad anualizada
    sortino_ratio REAL,
    calmar_ratio REAL,
    var_95 REAL,                            -- Value at Risk 95%
    cvar_95 REAL,                           -- Conditional Value at Risk 95%
    stability_score REAL,                   -- Métrica de consistencia entre ventanas walk-forward
    overfitting_risk REAL,                  -- Score de riesgo de overfitting (0-1)
    robustness_passed BOOLEAN DEFAULT 0,    -- Resultado de walk-forward + monte carlo
    gate_passed BOOLEAN DEFAULT 0,          -- Si pasó los gates internos
    gate_failures TEXT,                     -- JSON array de fallos de gates
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX idx_performance_strategy ON strategy_performance(strategy_id);
CREATE INDEX idx_performance_timeframe ON strategy_performance(timeframe);
CREATE INDEX idx_performance_sharpe ON strategy_performance(sharpe_ratio);
CREATE INDEX idx_performance_robustness ON strategy_performance(robustness_passed);
```

### Tabla de Resultados Detallados: `strategy_results`
```sql
CREATE TABLE strategy_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    backtest_id TEXT,                       -- Referencia opcional a estrategia_performance
    trade_index INTEGER NOT NULL,           -- Índice dentro del backtest
    entry_time INTEGER NOT NULL,            -- Epoch timestamp
    exit_time INTEGER NOT NULL,
    direction TEXT NOT NULL,                -- LONG o SHORT
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    pnl REAL NOT NULL,                      -- En USD
    pnl_pct REAL NOT NULL,                  -- % del capital arriesgado
    duration INTEGER NOT NULL,              -- Segundos
    win BOOLEAN NOT NULL,
    exit_reason TEXT NOT NULL,              -- TP, SL, TIME
    max_favorable_excursion REAL,           -- MFE en %
    max_adverse_excursion REAL,             -- MAE en %
    signal_confidence REAL,                 -- Confianza de la señal (0-1)
    kelly_fraction REAL,                    -- Fracción de Kelly utilizada
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX idx_results_strategy ON strategy_results(strategy_id);
CREATE INDEX idx_results_time ON strategy_results(entry_time);
CREATE INDEX idx_results_win ON strategy_results(win);
```

### Tabla de Experimentos de Auto-Mejoramiento: `optimization_experiments`
```sql
CREATE TABLE optimization_experiments (
    experiment_id TEXT PRIMARY KEY,         -- UUID único del experimento
    strategy_id TEXT NOT NULL,              -- Estrategia base que se optimizó
    experiment_type TEXT NOT NULL,          -- walk_forward, genetic, reinforcement, regime_detection
    parameters_json TEXT NOT NULL,          -- Parámetros utilizados en el experimento
    start_date TEXT NOT NULL,               -- Fecha de inicio del experimento
    end_date TEXT NOT NULL,                 -- Fecha de fin del experimento
    total_iterations INTEGER NOT NULL,            -- Número de iteraciones/generaciones
    best_strategy_id TEXT,                  -- Referencia a la mejor estrategia encontrada
    improvement_metrics TEXT,               -- JSON con mejoras encontradas
    status TEXT DEFAULT 'completed',        -- running, completed, failed, cancelled
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (best_strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX idx_experiments_strategy ON optimization_experiments(strategy_id);
CREATE INDEX idx_experiments_type ON optimization_experiments(experiment_type);
CREATE INDEX idx_experiments_status ON optimization_experiments(status);
```

### Tabla de Detección de Regímenes: `market_regimes`
```sql
CREATE TABLE market_regimes (
    regime_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,             -- Epoch timestamp
    regime_type TEXT NOT NULL,              -- bull, bear, sideways, high_vol, crisis, etc.
    regime_probability REAL NOT NULL,       -- Probabilidad del régimen detectado (0-1)
    features_json TEXT NOT NULL,            -- Características utilizadas para detección
    model_version TEXT,                     -- Versión del modelo de detección utilizado
    FOREIGN KEY (regime_type) REFERENCES regime_types(type)  -- Si se crea tabla de tipos
);

CREATE INDEX idx_regimes_timestamp ON market_regimes(timestamp);
CREATE INDEX idx_regimes_type ON market_regimes(regime_type);
```

### Tabla de Comparaciones entre Estrategias: `strategy_comparisons`
```sql
CREATE TABLE strategy_comparisons (
    comparison_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_a_id TEXT NOT NULL,
    strategy_b_id TEXT NOT NULL,
    comparison_type TEXT NOT NULL,          -- head_to_head, tournament, ablation
    timeframe TEXT NOT NULL,
    winner_strategy_id TEXT,                -- NULL si empate o inconclusivo
    win_rate_a REAL NOT NULL,
    win_rate_b REAL NOT NULL,
    sharpe_a REAL NOT NULL,
    sharpe_b REAL NOT NULL,
    max_dd_a REAL NOT NULL,
    max_dd_b REAL NOT NULL,
    total_return_a REAL NOT NULL,
    total_return_b REAL NOT NULL,
    statistical_significance REAL,          -- p-value de la comparación
    confidence_interval TEXT,               -- JSON con intervalo de confianza
    notes TEXT,                             -- Observaciones adicionales
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_a_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (strategy_b_id) REFERENCES strategies(strategy_id),
    FOREIGN KEY (winner_strategy_id) REFERENCES strategies(strategy_id)
);

CREATE INDEX idx_comparisons_strategies ON strategy_comparisons(strategy_a_id, strategy_b_id);
CREATE INDEX idx_comparisons_timeframe ON strategy_comparisons(timeframe);
```

## Versioning de Estrategias

### Estrategia de Versionado Semántico
Cada versión sigue el formato `MAJOR.MINOR.PATCH`:
- **MAJOR**: Cambios rompiendo en la lógica de señal o gestión de riesgo
- **MINOR**: Nuevas funcionalidades indicadores o mejoras sustanciales
- **PATCH**: Bug fixes, ajustes menores de parámetros

### Línea de Tiempo (Linaje)
Cada estrategia tiene un `parent_strategy_id` que apunta a su versión anterior, permitiendo:
- Tracking de evolución completa
- Rollback a versiones anteriores
- Análisis de qué cambios mejoraron/empeoraron performance

## Almacenamiento de Resultados Detallados

### Resultados de Backtest
Los resultados detallados de cada backtest se almacenan en:
1. **Tabla `strategy_performance`**: Métricas agregadas
2. **Tabla `strategy_results`**: Trade-by-trade detallado
3. **Archivos Parquet**: Para análisis profundo y reproducibilidad
   - Ruta: `data/backtest_results/{strategy_id}_{timestamp}.parquet`
   - Contiene: OHLCV, señales, trades, métricas por ventana walk-forward

### Experimentos de Optimización
Los experimentos se almacenan en:
- **Tabla `optimization_experiments`**: Metadatos del experimento
- **Archivos JSON**: Parámetros detallados y resultados intermedios
  - Ruta: `experiments/{experiment_type}/{experiment_id}.json`
  - Contiene: historial de generaciones, parámetros por iteración, curvas de convergencia

## Consultas Comunes

### 1. Obtener la mejor versión activa de una estrategia
```sql
SELECT s.*, sp.* 
FROM strategies s
JOIN strategy_performance sp ON s.strategy_id = sp.strategy_id
WHERE s.name = 'range_break' 
  AND s.is_active = 1 
  AND sp.robustness_passed = 1
ORDER BY sp.sharpe_ratio DESC 
LIMIT 1;
```

### 2. Comparar performance entre versiones
```sql
SELECT 
    s.version,
    sp.sharpe_ratio,
    sp.win_rate,
    sp.max_drawdown,
    sp.total_return
FROM strategies s
JOIN strategy_performance sp ON s.strategy_id = sp.strategy_id
WHERE s.name = 'volatility'
ORDER BY s.version;
```

### 3. Obtener historial de mejoras de un experimento genético
```sql
SELECT 
    oe.experiment_id,
    oe.iterations,
    oe.improvement_metrics,
    s.version as best_version
FROM optimization_experiments oe
JOIN strategies s ON oe.best_strategy_id = s.strategy_id
WHERE oe.experiment_type = 'genetic'
  AND oe.strategy_id = (
    SELECT strategy_id FROM strategies WHERE name = 'confluence' LIMIT 1
  );
```

### 4. Detectar régimen actual para asignación dinámica de estrategias
```sql
SELECT 
    regime_type,
    AVG(regime_probability) as avg_probability
FROM market_regimes
WHERE timestamp > strftime('%s', 'now', '-1 hour')
GROUP BY regime_type
ORDER BY avg_probability DESC
LIMIT 1;
```

## Integración con el Sistema Existente

### Modificaciones Necesarias en el Código Actual

1. **Extensión del Strategy Factory**:
   - Añadir método para guardar versión en DB después de crear instancia
   - Añadir método para cargar versión desde DB por ID o nombre/version

2. **Modificación del Backtest Engine**:
   - Después de cada backtest, guardar métricas en `strategy_performance`
   - Guardar trades detallados en `strategy_results`
   - Opcional: guardar resultados detallados en Parquet

3. **Nuevo Servicio de Gestión de Estrategias**:
   - CRUD operations para estrategias
   - Funciones de versionado y lineage
   - Consultas de performance y comparación

4. **Integración con Experimentos de Optimización**:
   - Al finalizar walk-forward/genético/RL, crear registro en `optimization_experiments`
   - Si se encuentra mejor versión, marcarla como candidata a activar

## Diagramas de Relación

```
strategies 1 ──< strategy_performance
strategies 1 ──< strategy_results
strategies 1 ──< optimization_experiments
strategies 1 ──> strategies (self-referencing para parent)
market_regimes 1 ──< (many timestamps)
strategies 1 ──< strategy_comparisons (como strategy_a y strategy_b)
strategy_comparisons 1 ──> strategies (winner)
```

## Consideraciones de Performance

### Índices Esenciales
- Índices compuestos para búsquedas frecuentes (name+version, strategy_id+timeframe)
- Índices en columnas de filtrado (is_active, robustness_passed, regime_type)

### Particionamiento (Para Futuro)
- Por rango de fechas en `market_regimes` y `strategy_results`
- Por tipo de estrategia en consultas de performance

### Arquitectura de Almacenamiento Híbrido
- **SQLite**: Metadatos estructurados, búsquedas rápidas, transacciones ACID
- **Parquet/JSONL**: Series temporales grandes, análisis analytico, compresión
- **Archivos JSON**: Configuraciones complejas, resultados de experimentos

## Próximos Pasos de Implementación

1. Crear migración de base de datos inicial
2. Extender `strategy_factory.py` con métodos de persistencia
3. Modificar `backtest/engine.py` para guardar resultados en DB
4. Crear nuevo módulo `strategy_service.py` para gestión de DB
5. Actualizar scripts de walk-forward para usar el nuevo sistema de versionado
6. Crear endpoints API para consultar historial de estrategias desde el dashboard