"""Alias de claves JSON — palabras clave completas en español.

Las respuestas de la API usaban abreviaturas y términos en inglés (``pnl``,
``equity``, ``sharpe``, ``win_rate``, etc.).  Para consistencia con el
dominio del proyecto y mejor legibilidad para operadores hispanohablantes,
ahora exponemos además palabras clave completas en español.

Diseño *compatibilidad hacia atrás*:
  - Se añade la nueva clave completa **al lado** de la vieja, sin borrarla.
  - Cualquier consumidor existente que lea ``pnl`` sigue funcionando.
  - Los nuevos consumidores pueden estandarizar en ``resultado_operaciones``.

La función :func:`with_aliases` recorre recursivamente dicts y listas
aplicando el mapeo de alias.  Es idempotente: aplicar dos veces deja el
JSON idéntico (porque las nuevas claves ya existen).

Mapa canónico (viejo → nuevo)::

    pnl              → resultado_operaciones
    total_pnl        → resultado_operaciones_total
    equity           → capital_disponible
    sharpe           → indice_sharpe
    sharpe_ratio     → indice_sharpe
    win_rate         → tasa_aciertos
    max_drawdown     → caida_maxima
    drawdown         → caida_maxima
    sl               → stop_perdida
    stop_loss        → stop_perdida
    tp               → objetivo_ganancia
    take_profit      → objetivo_ganancia
    rr               → relacion_riesgo_beneficio
    risk_reward      → relacion_riesgo_beneficio

Métricas adicionales con alias completo (ético en español):

    profit_factor    → factor_beneficio
    expectancy       → expectativa
    total_trades     → total_operaciones
    trades_today     → operaciones_hoy
    balance          → saldo
    consecutive_losses → perdidas_consecutivas
    is_halted        → detenido
    circuit_breaker  → interruptor_circuito
    gate_passed      → gate_pasada
    gate_failures    → gate_fallos

Uso típico en un endpoint::

    from src.api._aliases import with_aliases

    @app.get("/api/bot/status")
    async def get_bot_status() -> dict:
        ...  # construye payload con claves viejas
        return with_aliases(payload)

Para ``JSONResponse`` que pasan dicts crudos::

    return JSONResponse(with_aliases(data))

Es deliberadamente un *post-proceso* en lugar de un cambio *in-place*:
  - Cero riesgo de romper endpoints si hago el patch atomico.
  - Los helpers internos (lectura de archivos) no se tocan.
  - Las claves nuevas aparecen solo en la serialización final, no contamina
    estructuras internas.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# Mapeo clave vieja → clave nueva (palabra completa en español).
# Si una clave vieja mapea a más de una nueva (ej: ``sharpe`` y
# ``sharpe_ratio`` → ``indice_sharpe``), eso es OK — ambas viejas
# producen la misma nueva y ``with_aliases`` no duplica.
ALIASES: dict[str, str] = {
    # Métricas de trading
    "pnl": "resultado_operaciones",
    "total_pnl": "resultado_operaciones_total",
    "equity": "capital_disponible",
    "balance": "saldo",
    # Ratios de riesgo/rendimiento
    "sharpe": "indice_sharpe",
    "sharpe_ratio": "indice_sharpe",
    "win_rate": "tasa_aciertos",
    "max_drawdown": "caida_maxima",
    "drawdown": "caida_maxima",
    # Stops/TP/RR de trades
    "sl": "stop_perdida",
    "stop_loss": "stop_perdida",
    "tp": "objetivo_ganancia",
    "take_profit": "objetivo_ganancia",
    "rr": "relacion_riesgo_beneficio",
    "risk_reward": "relacion_riesgo_beneficio",
    # Métricas adicionales de backtest/operación
    "profit_factor": "factor_beneficio",
    "expectancy": "expectativa",
    "total_trades": "total_operaciones",
    "trades_today": "operaciones_hoy",
    # Estado del sistema
    "consecutive_losses": "perdidas_consecutivas",
    "is_halted": "detenido",
    "circuit_breaker": "interruptor_circuito",
    "gate_passed": "gate_pasada",
    "gate_failures": "gate_fallos",
}


def with_aliases(payload: Any) -> Any:
    """Añade claves JSON en español **al lado** de las viejas, recursivamente.

    Recibe un dict, una lista, o un escalar.  Recorre estructuras anidadas
    y, para cada clave que esté en :data:`ALIASES`, añade la nueva clave
    con el mismo valor (solo si no existe ya — no sobreescribe).

    Args:
        payload: Estructura JSON-serializable (dict/list/scalar).

    Returns:
        Copia de ``payload`` con las claves nuevas añadidas.  El input
        no se muta.

    Idempotente: aplicar dos veces produce el mismo JSON (las nuevas
    claves ya existen en el segundo pase).

    Examples:
        >>> with_aliases({"pnl": 10.0, "win_rate": 0.6})
        {'pnl': 10.0, 'resultado_operaciones': 10.0,
         'win_rate': 0.6, 'tasa_aciertos': 0.6}

        >>> with_aliases([{"sl": 1.2, "tp": 2.4}])
        [{'sl': 1.2, 'stop_perdida': 1.2, 'tp': 2.4, 'objetivo_ganancia': 2.4}]
    """
    return _apply_aliases(deepcopy(payload))


def _apply_aliases(node: Any) -> Any:
    """Aplica ``ALIASES`` recursivamente (muta la copia ya hecha)."""
    if isinstance(node, dict):
        # Genera claves nuevas en un paso aparte para no iterar sobre
        # mutaciones durante el bucle.
        additions: dict[str, Any] = {}
        for old_key, value in node.items():
            # Recursión primero (nested dicts/lists).
            value = _apply_aliases(value)
            node[old_key] = value
            new_key = ALIASES.get(old_key)
            if new_key is not None and new_key not in node:
                additions[new_key] = value
        node.update(additions)
        return node
    if isinstance(node, list):
        return [_apply_aliases(item) for item in node]
    return node
