"""Replica del flujo de scripts/collect_data.sh (eliminado del repo).

Descarga 5000 candles de 60s para 6 símbolos sintéticos desde Deriv
usando el WebSocket público (sin auth) y los guarda en data/candles/*.parquet.

Uso (desde la raíz del proyecto):
    .venv/bin/python -m scripts.collect_data_run
o
    .venv/bin/python scripts/collect_data_run.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Asegurar que la raíz del proyecto está en sys.path para `import src...`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.connection.deriv_client import DerivClient, DerivConfig
from src.data.collector import DataCollector

SYMBOLS = ["R_100", "R_75", "R_50", "R_25", "R_10", "RB100"]
COUNT = 5000
GRANULARITY = 60  # segundos (1 min)
LOG_PATH = PROJECT_ROOT / "realtime" / "collector.log"
DATA_DIR = PROJECT_ROOT / "data"


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Configurar logging hacia el archivo collector.log (mismo que el script .sh usaba)
    file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    # Limpiar handlers previos para no duplicar en re-runs
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    # También consola para captura de salida del cron
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(console)


async def run() -> int:
    setup_logging()
    log = logging.getLogger("collect_data")
    ts_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("[%s] Starting data collection for %d symbols × %d candles",
             ts_start, len(SYMBOLS), COUNT)

    config = DerivConfig.from_yaml()
    client = DerivClient(config)

    results: dict[str, int] = {}
    exit_code = 0

    try:
        await client.connect_public()
        collector = DataCollector(client, data_dir=str(DATA_DIR))

        for symbol in SYMBOLS:
            try:
                df = await collector.download_candles(
                    symbol=symbol, count=COUNT, granularity=GRANULARITY,
                )
                results[symbol] = len(df)
            except Exception as e:
                log.error("Failed collecting %s: %s", symbol, e)
                results[symbol] = -1
                exit_code = 1

        await client.disconnect()
    except Exception as e:
        log.error("Connection/collection failed: %s", e)
        exit_code = 1
        # Asegurar símbolos marcados como no recolectados
        for s in SYMBOLS:
            results.setdefault(s, -1)

    # Summary idéntico al formato del .sh original
    log.info("=== COLLECTION SUMMARY ===")
    total = 0
    for symbol in SYMBOLS:
        n = results.get(symbol, -1)
        if n >= 0:
            log.info("%s: %d candles [OK]", symbol, n)
            total += n
        else:
            log.info("%s: FAILED", symbol)
    log.info("TOTAL: %d candles across %d symbols", total, len(SYMBOLS))
    ts_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("[%s] Collection finished with exit code %d", ts_end, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
