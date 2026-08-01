# Plan de Integración con el Sistema Synthetic-Trader Actual

## Estado Actual del Sistema
Basado en el análisis de `/home/sebas/proyectos/synthetic-trader/`:

### Stack Tecnológico Actual:
- **Backend**: Python 3.12 + FastAPI
- **Frontend**: Next.js (dashboard en `/dashboard/`)
- **Base de Datos**: SQLite (metadatos) + Parquet (OHLCV/candles)
- **Estrategias**: 4 implementadas (range_break, volatility, confluence, gems) vía factory pattern
- **Backtesting**: Engine con walk-forward, Monte Carlo, simulación de latencia y spread/slippage
- **Paper Trading**: Pipeline en tiempo real con actualización de state/equity/trades cada 2-10s
- **Gestión de Riesgo**: Kelly dinámico, dual circuit breaker (consecutive losses + drawdown)
- **Arquitectura**: Capas claramente separadas (connection → data → analysis → strategies → risk → trading)

## Plan de Integración por Fases

### Fase 0: Preparación y Foundation (Semana 1)
**Objetivo**: Establecer la base de datos de estrategias y versionado sin afectar funcionalidad existente

#### Tareas:
1. [ ] Crear migración de base de datos inicial usando el schema definido en `STRATEGY_DB_DESIGN.md`
2. [ ] Implementar `strategy_service.py` con operaciones CRUD básicas para estrategias
3. [ ] Extender `strategy_factory.py` con métodos de persistencia:
   - `save_strategy_version(strategy, performance_metrics)` 
   - `load_strategy_version(strategy_id)` o `load_strategy_version(name, version)`
   - `get_active_strategy(name)` 
   - `get_strategy_lineage(strategy_id)`
4. [ ] Modificar `backtest/engine.py` para:
   - Después de cada backtest exitoso, llamar a `strategy_service.save_strategy_version()`
   - Guardar trades detallados en `strategy_results` 
   - Opcional: exportar resultados completos a Parquet para análisis profundo
5. [ ] Crear endpoint API básico:
   - `GET /api/strategy/history/{name}` - historial de versiones
   - `GET /api/strategy/active/{name}` - versión actualmente activa
   - `POST /api/strategy/activate` - activar una versión como productiva

#### Archivos a Crear/Modificar:
- `/home/sebas/proyectos/synthetic-trader/src/strategy_service.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/trading/strategy_factory.py` (modificar)
- `/home/sebas/proyectos/synthetic-trader/src/backtest/engine.py` (modificar)
- `/home/sebas/proyectos/synthetic-trader/src/api/server.py` (añadir endpoints)
- `/home/sebas/proyectos/synthetic-trader/migrations/001_init_strategy_db.sql` (nuevo)

### Fase 1: Detección de Regímenes Básica (Semana 2-3)
**Objetivo**: Implementar detección de régimen de mercado simple pero efectiva

#### Tareas:
1. [ ] Implementar `regime_detector.py` usando hmmlearn o implementación manual de HMM gaussian HMM con 3-5 estados (bull, bear, sideways, high_vol, crisis)
2. [ ] Crear `feature_engine.py` para extraer características de régimen:
   - Returns logarítmicos múltiples timeframes
   - Volatilidad realizada (multiple windows)
   - Indicadores de tendencia (precio vs SMA50/200, MACD, ADX)
   - Medidas de microestructura si están disponibles (spread estimado, order flow imbalance proxy)
3. [ ] Integrar detección de régimen en el pipeline de análisis:
   - Modificar `analysis/signal_scorer.py` para aceptar peso de régimen
   - Crear middleware que ejecute detección de régimen antes de generar señales
   - Almacenar resultados en tabla `market_regimes` cada vez que se procese un candle nuevo
4. [ ] Crear endpoints API para monitoreo de régimen:
   - `GET /api/regime/current` - régimen actual con probabilidades
   - `GET /api/regime/history` - historial reciente de detección
   - `GET /api/regime/features` - características utilizadas para detección

#### Archivos a Crear/Modificar:
- `/home/sebas/proyectos/synthetic-trader/src/analysis/regime_detector.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/analysis/feature_engine.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/analysis/signal_scorer.py` (modificar)
- `/home/sebas/proyectos/synthetic-trader/src/api/server.py` (añadir endpoints de régimen)
- `/home/sebas/proyectos/synthetic-trader/src/connection/deriv_client.py` (posible modificación para pasar régimen al pipeline)

### Fase 2: Sistema de Optimización Avanzada (Semana 4-6)
**Objetivo**: Implementar capacidades de optimización automática usando múltiples algoritmos

#### Tareas:
1. [ ] Implementar `optimization_engine.py` que orquestra diferentes optimizadores:
   - Walk-forward optimizer mejorado (basado en scripts existentes pero más flexible)
   - Bayesian optimizer (usando scikit-optimize o similar)
   - Genetic algorithm optimizer (usando DEAP, PyGAD o implementación custom)
2. [ ] Crear `parameter_space.py` para definir espacios de búsqueda flexibles:
   - Parámetros continuos (rango, distribución prior)
   - Parámetros discretos (opciones, categorias)
   - Parámetros estructurales (genetic switches para activar/desactivar componentes)
3. [ ] Mejorar `scripts/walk_forward_validation.py` para:
   - Soportar múltiples algoritmos de optimización (no solo grid search en range_break)
   - Trabajar con cualquier estrategia registrada en el factory
   - Generar experimentos Trackables en la base de datos
   - Exportar resultados detallados para análisis
4. [ ] Implementar `experiment_tracker.py` para logging detallado de experimentos:
   - Tracking de métricas por iteración/generación
   - Guardado de mejores candidatos intermedios
   - Registro de parámetros utilizados y resultados obtenidos
5. [ ] Crear endpoints API para gestión de optimización:
   - `POST /api/optimization/start` - iniciar nuevo experimento
   - `GET /api/optimization/status/{experiment_id}` - estado de ejecución
   - `GET /api/optimization/results/{experiment_id}` - resultados finales
   - `POST /api/optimization/compare` - comparar múltiples experimentos

#### Archivos a Crear/Modificar:
- `/home/sebas/proyectos/synthetic-trader/src/optimization/optimization_engine.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/optimization/parameter_space.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/optimization/experiment_tracker.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/scripts/walk_forward_validation.py` (mejorar significativamente)
- `/home/sebas/proyectos/synthetic-trader/src/api/server.py` (añadir endpoints de optimización)
- `/home/sebas/proyectos/synthetic-trader/requirements.txt` (añadir dependencias: scikit-optimize, deap, etc.)

### Fase 3: Aprendizaje por Refuerzo para Ejecución (Semana 7-9)
**Objetivo**: Implementar RL para adaptación en tiempo real de posición size y timing

#### Tareas:
1. [ ] Evaluar y seleccionar framework de RL:
   - Opción 1: Stable-Baselines3 (más estable, documentación buena)
   - Opción 2: RLlib de Ray (más escalable, pero más complejo)
   - Opción 3: Implementación custom simple de P2 o A2C para comenzar
2. [ ] Implementar `rl_environment.py` que encapsule el entorno de trading:
   - Estado: características de mercado + posición actual + métricas de riesgo
   - Acción: ajustes de posición size, timing de entrada/salida, nivel de riesgo
   - Recompensa: función de retorno ajustado por riesgo (Sortino, Calmar, Sharpe con drawdown penalty)
   - Simulador: usar datos históricos o generar sintéticamente para entrenamiento
3. [ ] Implementar `rl_trainer.py` para entrenar políticas:
   - Loop de entrenamiento con experience replay
   - Guardado/carga de políticas entrenadas
   - Evaluación periódica en datos fuera de muestra
4. [ ] Integrar con el sistema existente:
   - Crear `rl_strategy_wrapper.py` que envuelva cualquier estrategia base
   - El wrapper modifica las señales de la estrategia base según la política de RL
   - Mantiene compatibilidad con la interface existente de Strategy
5. [ ] Crear endpoints API para monitoreo y control de RL:
   - `GET /api/rl/policy/status` - estado de la política actual
   - `POST /api/rl/policy/update` - actualizar política con nuevos datos
   - `GET /api/rl/metrics` - métricas de performance de la política

#### Archivos a Crear/Modificar:
- `/home/sebas/proyectos/synthetic-trader/src/rl/rl_environment.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/rl/rl_trainer.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/rl/rl_strategy_wrapper.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/api/server.py` (endpoints de RL)
- `/home/sebas/proyectos/synthetic-trader/requirements.txt` (añadir: stable-baselines3[extra], torch, etc.)

### Fase 4: Integración Completa y Dashboard (Semana 10-12)
**Objetivo**: Unificar todos los componentes y proporcionar visibilidad completa

#### Tareas:
1. [ ] Implementar `model_selector.py` para decidir cuándo promover estrategias:
   - Lógica de decisión basada en significancia estadística, riesgo, robustez y régimen actual
   - Sistema de voting o scoring multi-criterial
   - Umbrales configurables para promoción a producción
2. [ ] Mejorar el pipeline de auto-mejoramiento:
   - Triggers automáticos de re-optimización (performance decay, regime shift, time-based)
   - Sistema de rollback automático si nueva versión empeora performance significativamente
   - Bandit algorítmico para exploración/explotación de diferentes estrategias
3. [ ] Crear vista de dashboard comprehensiva:
   - Vista de evolución de estrategia (línea de tiempo de versiones y performance)
   - Vista de detección de régimen en tiempo real con probabilidades
   - Vista de experimentos de optimización activos y completados
   - Vista de comparativas de rendimiento entre versiones
   - Vista de métricas de riesgo y drawdown histórico
4. [ ] Implementar sistema de notificaciones y alertas:
   - Alertas cuando se detecta régimen de alto riesgo
   - Notificaciones cuando se activa una nueva versión de estrategia
   - Reportes de performance semanal/mensual automáticos
5. [ ] Documentación completa y testing:
   - Tests unitarios para todos los nuevos componentes
   - Tests de integración para flujos completos
   - Documentación de API actualizada
   - Manual de uso para operadores y desarrolladores

#### Archivos a Crear/Modificar:
- `/home/sebas/proyectos/synthetic-trader/src/selection/model_selector.py` (nuevo)
- `/home/sebas/proyectos/synthetic-trader/src/strategy_service.py` (mejorar con lógica de selección/promoción)
- `/home/sebas/proyectos/synthetic-trader/src/api/server.py` (endpoints adicionales para dashboard)
- `/home/sebas/proyectos/synthetic-trader/dashboard/src/app/` (componentes de React para nuevas vistas)
- `/home/sebas/proyectos/synthetic-trader/tests/` (tests unitarios y de integración)
- `/home/sebas/proyectos/synthetic-trader/docs/` (documentación actualizada)

## Plan de Riesgos y Mitigación

### Riesgo 1: Complejidad excesiva afectando estabilidad
- **Mitigación**: Implementación incremental por fases, cada fase debe ser desplegable y testeable independientemente
- **Métrica de éxito**: Cada fase debe pasar todos los tests existentes antes de continuar

### Riesgo 2: Overfitting en el proceso de optimización
- **Mitigación**: Validación rigurosa fuera de muestra, uso de walk-forward + monte carlo en cada paso de optimización
- **Métrica de éxito**: Ratio de rendimiento OOS/IS debe mantenerse > 0.7 en estrategias promovidas

### Riesgo 3: Latencia incrementada afectando trading en tiempo real
- **Mitigación**: Separar entrenamiento (offline/asíncrono) de inferencia (online/síncrono), cachar resultados costosos
- **Métrica de éxito**: Latencia adicional < 50ms para decisión de trading en producción

### Riesgo 4: Dificultad en interpretación y debugging
- **Mitigación**: Enfocarse en explicabilidad desde el inicio, logging detallado, visualizaciones de importancia de características
- **Métrica de éxito**: Capacidad de explicar por qué se tomó una decisión de trading en términos de características de entrada

### Riesgo 5: Desviación de objetivos (minimización de riesgo vs maximización de retorno)
- **Mitigación**: Funciones de objetivo y recompensa que penalicen explícitamente el riesgo, monitoreo continuo de métricas de riesgo
- **Métrica de éxito**: Max drawdown debe mantenerse debajo del umbral definido (ej: 12%) con alta probabilidad

## Recursos Necesarios

### Dependencias Técnicas Nuevas:
- `scikit-optimize` o `bayesian-optimization` para Bayesian Optimization
- `DEAP` o `PyGAD` para algoritmos genéticos
- `stable-baselines3[extra]` o `torch` para aprendizaje por refuerzo
- `hmmlearn` o `pomegranate` para Modelos de Markov Ocultos
- `scipy`, `numpy`, `pandas` (ya probablemente presentes)
- `scikit-learn` para métricas y utils adicionales

### Recursos Humanos:
- 1 desarrollador backend senior (Python, FastAPI, SQL)
- 1 desarrollador ML/quant (experiencia en trading algorítmico y ML)
- 1 desarrollador frontend (React/Next.js) para dashboard
- 1 QA/tester (para validación de estrategia y backtesting)

### Tiempo Estimado:
- **Fase 0 (Fundación)**: 1 semana
- **Fase 1 (Regímenes)**: 2 semanas  
- **Fase 2 (Optimización)**: 3 semanas
- **Fase 3 (RL)**: 3 semanas
- **Fase 4 (Integración)**: 2 semanas
- **Total**: ~11 semanas (2.5 meses) con equipo dedicado

## Métricas de Éxito

### Métricas Técnicas:
1. **Cobertura de tests**: > 80% para nuevo código
2. **Latencia adicional**: < 50ms por decisión de trading
3. **Uso de memoria**: < 200MB adicional en producción
4. **Tiempo de entrenamiento**: < 2 horas para experimentos típicos

### Métricas de Trading:
1. **Mejora en Sharpe ratio**: > 20% respecto a línea base
2. **Reducción en max drawdown**: > 30% respecto a línea base
3. **Consistencia de rendimiento**: Ratio de rendimiento mensual positivo > 70%
4. **Adaptación a régimen**: Performance en cada régimen debe ser > mediana histórica

### Métricas de Sistema:
1. **Uptime del sistema de optimización**: > 95%
2. **Tiempo medio de recuperación**: < 10 minutos desde fallo
3. **Escalabilidad**: Capaz de manejar 10+ estrategias concurrentes en optimización
4. **Facilidad de uso**: Operador puede entender y usar sistema sin entrenamiento extensivo

## Próximos Pasos Inmediatos

1. **Esta semana**: Revisar y aprobar el diseño de base de datos de DB DESIGN.md`)
   **o 1. (Fase 0 de
- [ ] Base de datos de estrategias y versionado
- [ ] Endpoints API básicos para consultar historial
- [ ] Integración con backtest engine para guardar resultados automáticamente
- [ ] Tests unitarios para service layer

3. **Revisión con el equipo**: Presentar plan y obtener feedback antes de comenzar implementación

Este plan de integración proporciona un camino claro y progresivo para mejorar el sistema synthetic-trader con capacidades de auto-mejora sofisticadas, manteniendo la estabilidad y compatibilidad con el sistema existente mientras se agrega funcionalidad de minimización de riesgo avanzada.