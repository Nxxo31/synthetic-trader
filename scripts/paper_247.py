"""Wrapper 24/7 para paper trading — mantiene el bot corriendo indefinidamente.

Envuelve ``python -m src.main paper`` (que ya tiene su propia reconexión
interna al WebSocket de Deriv y un *circuit breaker* que puede detener el
engine limpiamente tras 5 fallos consecutivos de reconexión) con un
supervisor a nivel proceso que:

  1. Reinicia el bot si termina por cualquier motivo (crash, halt del
     circuit breaker, KeyboardInterrupt, drain de memoria, etc.).
  2. Aplica backoff exponencial entre intentos para no caer en un
     crash-loop contra una API que esté caída.
  3. Detecta *crash-loops* (procesos que mueren < ``MIN_UPTIME_SECONDS``
     segundos tras arranque) y entra en cool-down largo antes de
     reintentar, evitando quemar CPU y rate-limit.
  4. Propaga ``SIGTERM`` / ``SIGINT`` al proceso hijo para shutdown
     limpio (systemd envía SIGTERM al detener el unit).
  5. Lleva un log rotativo en ``logs/paper_247.log``.

Diseño:
  - Proceso supervisor puro (no async) — usa ``subprocess`` y señales.
  - No toca ``src/risk/manager.py`` ni el engine interno del bot; el
    engine entrega el halt limpio y termina el proceso, el supervisor
    decide si reiniciar.
  - Honra un archivo *stop semáforo* (``realtime/paper_247.stop``) que,
    si existe, hace que el supervisor no reinicie — útil para drenar
    manualmente sin matar el unit de systemd.

Usage::

    python scripts/paper_247.py
    python scripts/paper_247.py --max-restarts 50 --min-uptime 60
    python scripts/paper_247.py --once     # una sola ejecución (no reinicia)

El unit de systemd en ``deploy/systemd/synthetic-trader-paper.service``
invoca este script con ``--max-restarts 0`` deshabilitado por defecto
(usa el ``Restart=always`` de systemd como mecanismo primario, dejando
el supervisor interno como *belt-and-suspenders*).  Ver el archivo del
unit.

Exit codes del supervisor:
  0 — shutdown limpio (recibió SIGTERM/SIGINT y el hijo terminó).
  2 — se excedió ``--max-restarts``.
  3 — *crash-loop* persistente tras el cool-down de seguridad.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------
# Configuración (constantes; sobreescribibles vía CLI)
# ----------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
"""Ruta absoluta al proyecto synthetic-trader (padre de ``scripts/``)."""

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "paper_247.log"
"""Log rotativo del supervisor (no del bot — ese va a su propio logger)."""

STOP_SEMAPHORE = PROJECT_ROOT / "realtime" / "paper_247.stop"
"""Si este archivo existe, el supervisor NO reinicia el bot tras un exit.
Útil para drenar manteniendo el unit de systemd activo: ``touch`` del
archivo, esperar a que el bot termine su candle/posición, y luego el
supervisor simplemente esperará hasta que se elimine."""

BOT_COMMAND: list[str] = [sys.executable, "-m", "src.main", "paper"]
"""Comando del bot. Usa el mismo intérprete Python que el supervisor."""

# Defaults de backoff y seguridad
DEFAULT_MAX_RESTARTS = 0            # 0 = ilimitado (default para systemd)
DEFAULT_MIN_UPTIME = 30             # seg — menos que esto = crash-loop
DEFAULT_INITIAL_BACKOFF = 5.0       # seg entre el 1er y 2do intento
DEFAULT_MAX_BACKOFF = 600.0         # tope superior del backoff (10 min)
DEFAULT_BACKOFF_MULTIPLIER = 2.0    # duplica cada retry
DEFAULT_CRASH_LOOP_COOLDOWN = 900.0  # 15 min si detecta crash-loop

# ----------------------------------------------------------------------
# Logging rotativo
# ----------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> logging.Logger:
    """Configura logging del supervisor a archivo rotativo + stderr."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Si logging.handlers está disponible, usamos RotatingFileHandler.
    # Sino (no debería pasar en 3.12), caemos a FileHandler simple.
    try:
        from logging.handlers import RotatingFileHandler

        file_handler: logging.Handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except Exception:  # pragma: no cover — fallback defensivo
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")

    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(
        logging.Formatter("[paper_247] %(levelname)s: %(message)s")
    )

    logger = logging.getLogger("paper_247")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Limpia handlers previos (idempotente si _setup_logging se llama 2x).
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


log = _setup_logging()

# ----------------------------------------------------------------------
# Supervisor
# ----------------------------------------------------------------------


class PaperSupervisor:
    """Supervisor de proceso para el bot de paper trading.

    Mantiene ``python -m src.main paper`` corriendo como subprocess y lo
    reinicia con backoff exponencial cuando termina.  Es cooperativo con
    el halt interno del engine (que ya tiene su propio circuit breaker
    y reconexión WS): el supervisor cubre el caso de *terminación del
    proceso* (crash, OOM, halt definitivo, signal externo).

    Attributes:
        max_restarts: Máximo número de reinicios (0 = ilimitado).
        min_uptime: Segundos mínimos de vida para considerar un run
            "saludable". Si el bot muere antes, cuenta como crash-loop.
        backoff: Backoff actual entre intentos (crece exponencialmente).
    """

    def __init__(
        self,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        min_uptime: int = DEFAULT_MIN_UPTIME,
        initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
        crash_loop_cooldown: float = DEFAULT_CRASH_LOOP_COOLDOWN,
        once: bool = False,
    ) -> None:
        self.max_restarts = max_restarts
        self.min_uptime = min_uptime
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_multiplier = backoff_multiplier
        self.crash_loop_cooldown = crash_loop_cooldown
        self.once = once

        self._current_backoff = initial_backoff
        self._restart_count = 0
        self._consecutive_crash_loops = 0
        self._child: subprocess.Popen[bytes] | None = None
        self._stopping = False

    # ---------------- signals ---------------- #

    def _install_signal_handlers(self) -> None:
        """Instala handlers de SIGTERM y SIGINT para shutdown limpio.

        Al recibir la señal, marca ``_stopping`` y reenvía SIGTERM al
        proceso hijo (el bot ya maneja SIGTERM en su ``main()``).
        """

        def _handler(signum: int, _frame: object) -> None:
            name = signal.Signals(signum).name
            log.info("Recibida señal %s — iniciando shutdown limpio...", name)
            self._stopping = True
            self._forward_signal_to_child(signum)

        # SIGTERM (systemd), SIGINT (Ctrl-C).  Evita que el handler
        # inicial interrumpa un SIGINT reentrante.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # En threads no-main o plataformas sin la señal, lo ignora.
                pass

    def _forward_signal_to_child(self, signum: int) -> None:
        """Reenvía una señal al proceso hijo en ejecución, si lo hay."""
        child = self._child
        if child is None or child.poll() is not None:
            return
        try:
            child.send_signal(signum)
            log.debug("Señal %s reenviada al hijo (pid=%d)", signum, child.pid)
        except Exception as e:  # pragma: no cover — defensivo
            log.warning("No se pudo reenviar señal al hijo: %s", e)

    # ---------------- stop semáforo ---------------- #

    @staticmethod
    def _stop_semaphore_engaged() -> bool:
        """True si el archivo semáforo de stop existe.

        El operador puede ``touch realtime/paper_247.stop`` para pedir
        al supervisor que NO reinicie tras el próximo exit del bot.
        Eliminar el archivo reanuda los reinicios automáticamente.
        """
        return STOP_SEMAPHORE.exists()

    # ---------------- loop principal ---------------- #

    def run(self) -> int:
        """Loop principal del supervisor.

        Returns:
            Exit code del supervisor:
              0 — shutdown limpio (SIGTERM/SIGINT).
              2 — se excedió ``max_restarts`` (> 0).
              3 — crash-loop persistente tras cool-down de seguridad.
        """
        self._install_signal_handlers()
        log.info(
            "PaperSupervisor iniciado — comando=%s, max_restarts=%s, "
            "min_uptime=%ds, once=%s, cwd=%s",
            " ".join(BOT_COMMAND),
            "ilimitado" if self.max_restarts == 0 else self.max_restarts,
            self.min_uptime,
            self.once,
            PROJECT_ROOT,
        )

        while not self._stopping:
            # Chequeo del semáforo de stop entre iteraciones.
            if self._stop_semaphore_engaged():
                log.info(
                    "Semáforo de stop detectado (%s) — no reinicio. "
                    "Elimina el archivo para reanudar.",
                    STOP_SEMAPHORE,
                )
                self._wait_while_stopping_or_semaphore(60.0)
                continue

            exit_code = self._launch_one_run()
            if self._stopping:
                break
            if self.once:
                log.info("Modo --once: no reinicio. Exit code del bot: %d", exit_code)
                return 0 if exit_code == 0 else exit_code

            # Decisión de reinicio.
            if self._should_give_up():
                log.error(
                    "Límite de reinicios alcanzado (%d). Abortando supervisor.",
                    self._restart_count,
                )
                return 2
            self._backoff_before_next_run(exit_code)

        log.info("PaperSupervisor finalizado limpiamente.")
        return 0

    def _launch_one_run(self) -> int:
        """Lanza una ejecución del bot y espera a que termine.

        Returns:
            Exit code del proceso hijo, o -1 si no se pudo lanzar.
        """
        self._restart_count += 1 if not self.once else 0
        start_ts = time.monotonic()
        start_wall = datetime.now(timezone.utc).isoformat()
        log.info(
            "[%s] Iniciando bot (intento %s)...", start_wall,
            "único" if self.once else f"#{self._restart_count}",
        )

        try:
            # Popen con el cwd del proyecto para que `src.main` resuelva.
            self._child = subprocess.Popen(
                BOT_COMMAND,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # No hereda stdin — el bot no lo usa y evita bloqueos
                # si el supervisor corre bajo systemd sin TTY.
                stdin=subprocess.DEVNULL,
                # Start new session para que el hijo sobreviva a un
                # SIGINT enviado solo al supervisor (Ctrl-C en shell).
                start_new_session=True,
            )
        except FileNotFoundError as e:
            log.error("No se pudo lanzar el bot (interprete no encontrado): %s", e)
            return -1
        except Exception as e:
            log.error("Error lanzando el bot: %s", e)
            return -1

        # Stream del stdout/stderr del hijo a nuestro log, línea por línea,
        # hasta que el proceso termine.  Esto da visibilidad del bot en
        # logs/paper_247.log sin duplicar su propio log interno.
        assert self._child is not None
        assert self._child.stdout is not None
        for raw_line in self._child.stdout:
            try:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                line = "<binary line>"
            if line:
                log.info("bot| %s", line)
            if self._stopping:
                # El handler de señal ya reenvió SIGTERM al hijo; seguimos
                # drenando stdout hasta que cierre, pero no logueamos más.
                pass

        exit_code = self._child.wait()
        uptime = time.monotonic() - start_ts
        log.info(
            "Bot terminó: exit_code=%d, uptime=%.1fs", exit_code, uptime
        )

        # Categoriza el run para el backoff.
        if exit_code == 0 and not self._stopping:
            log.info("Bot terminó exitosamente (puede ser halt limpio del circuit breaker).")
        if uptime < self.min_uptime and exit_code != 0 and not self._stopping:
            self._consecutive_crash_loops += 1
            log.warning(
                "Run duró < %ds antes de morir (crash-loop #%d).",
                self.min_uptime, self._consecutive_crash_loops,
            )
        else:
            self._consecutive_crash_loops = 0

        self._last_uptime = uptime
        self._last_exit_code = exit_code
        self._child = None
        return exit_code

    # ---------------- backoff y decisión ---------------- #

    def _should_give_up(self) -> bool:
        """True si el supervisor debe dejar de reintentar."""
        if self.max_restarts > 0 and self._restart_count >= self.max_restarts:
            return True
        # Crash-loop severo: tras varios arranques instantáneos, aborta
        # para no quemar recursos.  Esto es un safety net — el operador
        # puede investigar y reiniciar el unit manualmente.
        if self._consecutive_crash_loops >= 5:
            return True
        return False

    def _backoff_before_next_run(self, exit_code: int) -> None:
        """Espera con backoff exponencial antes de la próxima iteración.

        Si el run fue saludable (uptime >= min_uptime) o un shutdown
        limpio (exit 0), resetea el backoff al valor inicial — no
        penalizamos reinicios que el bot pidió deliberadamente.
        """
        healthy = (
            getattr(self, "_last_uptime", 0.0) >= self.min_uptime
            or exit_code == 0
        )
        if healthy:
            self._current_backoff = self.initial_backoff
            log.debug("Run saludable — backoff reseteado a %.1fs.", self._current_backoff)
        else:
            # Aumenta exponencialmente, con tope.
            self._current_backoff = min(
                self._current_backoff * self.backoff_multiplier,
                self.max_backoff,
            )

        # Crash-loop → cool-down largo antes de aplicar el backoff normal.
        if self._consecutive_crash_loops >= 3:
            log.warning(
                "Crash-loop detectado (%d veces). Cool-down de %.0fs "
                "antes de reintentar para evitar quemar recursos.",
                self._consecutive_crash_loops, self.crash_loop_cooldown,
            )
            self._wait_while_stopping_or_semaphore(self.crash_loop_cooldown)
            if self._stopping:
                return

        wait = self._current_backoff
        log.info(
            "Próximo intento en %.1fs (backoff, intento #%d)...",
            wait, self._restart_count + 1,
        )
        self._wait_while_stopping_or_semaphore(wait)

    def _wait_while_stopping_or_semaphore(self, seconds: float) -> None:
        """Espera ``seconds`` pero interrumpe si llega SIGTERM/SIGINT.

        Implementa el wait en intervalos de 1s para chequear
        ``_stopping`` periódicamente y no bloquear el shutdown.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stopping:
                return
            time.sleep(1.0)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="paper_247",
        description="Supervisor 24/7 para paper trading (envuelve `python -m src.main paper`).",
    )
    p.add_argument(
        "--max-restarts",
        type=int,
        default=DEFAULT_MAX_RESTARTS,
        help=f"Máximo de reinicios (0=ilimitado, default={DEFAULT_MAX_RESTARTS}).",
    )
    p.add_argument(
        "--min-uptime",
        type=int,
        default=DEFAULT_MIN_UPTIME,
        help=f"Segundos mínimos de uptime para no contar como crash-loop "
             f"(default={DEFAULT_MIN_UPTIME}).",
    )
    p.add_argument(
        "--initial-backoff",
        type=float,
        default=DEFAULT_INITIAL_BACKOFF,
        help=f"Backoff inicial entre intentos en segundos (default={DEFAULT_INITIAL_BACKOFF}).",
    )
    p.add_argument(
        "--max-backoff",
        type=float,
        default=DEFAULT_MAX_BACKOFF,
        help=f"Tope del backoff exponencial en segundos (default={DEFAULT_MAX_BACKOFF}).",
    )
    p.add_argument(
        "--backoff-multiplier",
        type=float,
        default=DEFAULT_BACKOFF_MULTIPLIER,
        help=f"Multiplicador del backoff por intento (default={DEFAULT_BACKOFF_MULTIPLIER}).",
    )
    p.add_argument(
        "--crash-loop-cooldown",
        type=float,
        default=DEFAULT_CRASH_LOOP_COOLDOWN,
        help=f"Segundos de cool-down si detecta crash-loop (default="
             f"{DEFAULT_CRASH_LOOP_COOLDOWN}).",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta el bot una sola vez y no reinicia (modo diagnóstico).",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Logging DEBUG del supervisor.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.verbose:
        log.setLevel(logging.DEBUG)

    supervisor = PaperSupervisor(
        max_restarts=args.max_restarts,
        min_uptime=args.min_uptime,
        initial_backoff=args.initial_backoff,
        max_backoff=args.max_backoff,
        backoff_multiplier=args.backoff_multiplier,
        crash_loop_cooldown=args.crash_loop_cooldown,
        once=args.once,
    )
    try:
        return supervisor.run()
    except KeyboardInterrupt:
        # Fallback si el handler de señal no se instaló a tiempo.
        log.info("Interruptado por teclado.")
        return 0
    except Exception:
        log.exception("Error fatal en el supervisor.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
