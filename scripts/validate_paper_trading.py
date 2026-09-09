#!/usr/bin/env python3
"""
Paper Trading Health Validation Component
Validates: balance, P&L, circuit breaker status, strategy alignment, symbol
"""
import json
from pathlib import Path

def validate_paper_trading_health():
    project_root = Path('/home/sebas/proyectos/synthetic-trader')
    realtime_dir = project_root / 'realtime'
    
    results = {
        'balance_check': False,
        'pnl_check': False,
        'circuit_breaker_check': False,
        'strategy_check': False,
        'symbol_check': False,
        'state_files_exist': False,
        'errors': []
    }
    
    state_file = realtime_dir / 'paper_state.json'
    equity_file = realtime_dir / 'equity.jsonl'
    trades_file = realtime_dir / 'trades.jsonl'
    
    results['state_files_exist'] = all(f.exists() for f in [state_file, equity_file, trades_file])
    
    if state_file.exists():
        try:
            with open(state_file) as f:
                state = json.load(f)
            results['balance_check'] = state.get('balance', 0) > 0
            results['pnl_check'] = isinstance(state.get('pnl', 0), (int, float))
            results['strategy_check'] = state.get('strategy', '') == 'step_index'
            results['symbol_check'] = state.get('symbol', '') == 'stpRNG'
            cb = state.get('circuit_breaker', {})
            results['circuit_breaker_check'] = not cb.get('is_halted', False)
        except Exception as e:
            results['errors'].append(f'State file error: {e}')
    
    return results

if __name__ == '__main__':
    result = validate_paper_trading_health()
    print(json.dumps(result, indent=2))
