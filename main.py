"""
main.py
Punto de entrada del Suricata Agent Scheduler.

Modos de ejecución:
  python main.py           → Modo normal: loop continuo cada 5 minutos
  python main.py --test    → Modo test: quita todos los departamentos a
                             "iclass iclass" inmediatamente y sale
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from browser import SuricataBot
from config import CHECK_INTERVAL
from scheduler import get_target_state

# ---------------------------------------------------------------------------
# Configuración de logging
# ---------------------------------------------------------------------------
Path("logs").mkdir(exist_ok=True)
log_file = f"logs/suricata_{datetime.now().strftime('%Y%m')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manejo de señales para cierre limpio (Ctrl+C / kill)
# ---------------------------------------------------------------------------
_running = True


def _shutdown(signum, frame):
    global _running
    logger.info("Señal de cierre recibida. Deteniendo scheduler...")
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def run_cycle(headless: bool = True):
    """Ejecuta un ciclo completo: evalúa horario → actualiza agentes."""
    state = get_target_state()

    if state is None:
        logger.info(
            f"[{datetime.now().strftime('%H:%M')}] Fuera de ventana de acción — sin cambios"
        )
        return

    departments = state["departments"]
    reason      = state["reason"]

    logger.info("=" * 60)
    logger.info(f"CICLO INICIADO: {reason}")
    logger.info(f"Departamentos objetivo: {', '.join(departments)}")
    logger.info("=" * 60)

    bot = SuricataBot(headless=headless)
    try:
        bot.start()
        bot.login()
        bot.go_to_agents_page()
        agents = state.get("agents")
        summary = bot.apply_departments(departments, agents_override=agents)

        logger.info(
            f"CICLO COMPLETADO — "
            f"OK: {summary['ok']} | "
            f"No encontrados: {summary['not_found']} | "
            f"Errores: {summary['error']}"
        )

    except Exception as e:
        logger.error(f"ERROR CRÍTICO en ciclo: {e}", exc_info=True)
    finally:
        bot.stop()


# ---------------------------------------------------------------------------
# Modo test
# ---------------------------------------------------------------------------

TEST_AGENT = "iclass iclass"


def run_test(headless: bool = False):
    """
    Modo test: asigna únicamente el departamento "Ventas" al agente "iclass iclass"
    inmediatamente, sin respetar horarios. Útil para verificar que el
    script puede hacer login, abrir el modal, desmarcar otros y guardar cambios.
    """
    logger.info("=" * 60)
    logger.info("  🧪 MODO TEST ACTIVADO")
    logger.info(f"  Agente objetivo : {TEST_AGENT}")
    logger.info("  Acción          : Asignar SOLO el departamento 'Ventas'")
    logger.info(f"  Modo gráfico    : {'Oculto (headless)' if headless else 'Visible (navegador)'}")
    logger.info("=" * 60)

    bot = SuricataBot(headless=headless)
    try:
        bot.start()
        bot.login()
        bot.go_to_agents_page()

        # Marcar solo "Ventas" (desmarcar los demás)
        summary = bot.apply_departments(
            target_departments=["Ventas"],
            agents_override=[TEST_AGENT],
        )

        if summary["ok"] == 1:
            logger.info(f"✅ TEST EXITOSO — Departamento 'Ventas' asignado a '{TEST_AGENT}'")
        elif summary["not_found"] == 1:
            logger.error(f"❌ Agente '{TEST_AGENT}' no encontrado en la página. Verifica el nombre exacto.")
        else:
            logger.error(f"❌ Error al procesar '{TEST_AGENT}'. Revisa el log para más detalles.")

    except Exception as e:
        logger.error(f"ERROR en modo test: {e}", exc_info=True)
    finally:
        bot.stop()


def main(headless: bool = True):
    logger.info("=" * 60)
    logger.info("  Suricata Agent Scheduler — INICIADO")
    logger.info(f"  Intervalo de verificación: {CHECK_INTERVAL // 60} minutos")
    logger.info(f"  Log guardado en: {log_file}")
    logger.info("=" * 60)

    # Ejecutar el primer ciclo inmediatamente al arrancar
    run_cycle(headless=headless)

    while _running:
        logger.info(f"Esperando {CHECK_INTERVAL // 60} minutos para el próximo ciclo...")
        # Esperar en intervalos pequeños para poder responder a SIGTERM
        for _ in range(CHECK_INTERVAL):
            if not _running:
                break
            time.sleep(1)

        if _running:
            run_cycle(headless=headless)

    logger.info("Scheduler detenido correctamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Suricata Agent Scheduler",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=(
            "Modo test: quita todos los departamentos al agente\n"
            "'iclass iclass' inmediatamente y sale.\n"
            "No sigue ningún horario."
        ),
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Muestra el navegador (solo para depuración local). En producción siempre corre headless.",
    )
    args = parser.parse_args()

    headless = not args.no_headless  # Por defecto headless=True (producción)

    if args.test:
        run_test(headless=headless)
    else:
        main(headless=headless)
