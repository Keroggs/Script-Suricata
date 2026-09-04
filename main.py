"""
main.py
Punto de entrada del Suricata Agent Scheduler.

Modos de ejecución:
  python main.py           → Modo normal: duerme hasta la próxima transición
                             de horario y solo entonces abre el navegador
  python main.py --test    → Modo test: asigna "Ventas" al agente
                             "iclass iclass" inmediatamente y sale
"""

import argparse
import logging
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from config import (
    CHECK_INTERVAL,
    LOG_DIR,
    MAX_RETRIES,
    RETRY_BACKOFF_MAX,
    RETRY_BACKOFF_SECONDS,
    STATE_FILE,
    TIMEZONE,
)

# ---------------------------------------------------------------------------
# Zona horaria
#
# Se fija ANTES de importar nada que mire el reloj. Una VM de Ubuntu recién
# instalada corre en UTC; sin esto, todos los horarios se desplazarían 4 horas
# y las guardias caerían en el día equivocado.
# ---------------------------------------------------------------------------
# Solo en Unix: el CRT de Windows no entiende los nombres IANA ("America/Caracas")
# y trataría la variable como UTC, desplazando la hora en vez de corregirla. En
# Windows (solo desarrollo) se usa la hora local del sistema tal cual.
_tz_error = None
if TIMEZONE and hasattr(time, "tzset"):
    # Una imagen mínima de Ubuntu puede no traer el paquete `tzdata`. En ese caso
    # TZ se ignora y el proceso se queda en UTC SIN avisar, con lo que todos los
    # horarios se correrían 4 horas. Se comprueba explícitamente para fallar de
    # forma ruidosa en vez de silenciosa.
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(TIMEZONE)
    except Exception as e:
        _tz_error = (
            f"No se pudo cargar la zona horaria '{TIMEZONE}' ({e}). "
            f"Instala la base de datos de zonas horarias:  sudo apt install -y tzdata"
        )

    os.environ["TZ"] = TIMEZONE
    time.tzset()

from browser import SuricataBot  # noqa: E402  (después de fijar TZ)
from scheduler import get_target_state, next_transition_time  # noqa: E402

# ---------------------------------------------------------------------------
# Rutas
#
# Ancladas al directorio del proyecto y no al directorio de trabajo actual, para
# que el servicio escriba siempre en el mismo sitio aunque systemd (o quien lo
# lance) tenga otro cwd.
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
log_dir = BASE_DIR / LOG_DIR
log_dir.mkdir(parents=True, exist_ok=True)


class MonthlyFileHandler(logging.FileHandler):
    """
    Escribe en logs/suricata_YYYYMM.log y cambia de archivo al cambiar el mes.

    El nombre se calculaba una sola vez al importar el módulo: un proceso que
    corre durante meses como servicio seguiría escribiendo en el archivo del mes
    en que arrancó.
    """

    def __init__(self, directory: Path):
        self._directory = directory
        self._period = self._current_period()
        super().__init__(self._path_for(self._period), encoding="utf-8", delay=False)

    @staticmethod
    def _current_period() -> str:
        return datetime.now().strftime("%Y%m")

    def _path_for(self, period: str) -> str:
        return str(self._directory / f"suricata_{period}.log")

    def emit(self, record):
        period = self._current_period()
        if period != self._period:
            self._period = period
            self.close()
            self.baseFilename = self._path_for(period)
            self.stream = self._open()
        super().emit(record)


log_file = str(log_dir / f"suricata_{datetime.now().strftime('%Y%m')}.log")

# La consola de Windows usa cp1252 por defecto y no puede escribir los símbolos
# (✓, ✗, 🧪) que lleva el log. Se fuerza UTF-8 en stdout; si la consola no lo
# admite, se sustituyen los caracteres en vez de romper el logging.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        MonthlyFileHandler(log_dir),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

if _tz_error:
    logger.error(_tz_error)
    raise SystemExit(_tz_error)

if TIMEZONE and hasattr(time, "tzset"):
    logger.info(
        f"Zona horaria del proceso: {TIMEZONE} (UTC{time.strftime('%z')}) — "
        f"hora local: {datetime.now():%Y-%m-%d %H:%M:%S}"
    )
elif TIMEZONE:
    logger.warning(
        f"TIMEZONE='{TIMEZONE}' no se puede aplicar en esta plataforma; "
        f"usando la hora local del sistema: {datetime.now():%Y-%m-%d %H:%M:%S}"
    )

# ---------------------------------------------------------------------------
# Manejo de señales para cierre limpio (Ctrl+C / kill)
#
# Se usa un Event en vez de un sleep en bucle: el proceso duerme hasta la
# próxima transición de horario y despierta al instante ante SIGINT/SIGTERM.
# ---------------------------------------------------------------------------
_stop = threading.Event()


def _shutdown(signum, frame):
    logger.info("Señal de cierre recibida. Deteniendo scheduler...")
    _stop.set()


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# ---------------------------------------------------------------------------
# Estado persistente
#
# Sobrevive a los reinicios del servicio (systemd Restart=always), evitando
# repetir un ciclo de navegador que ya se aplicó correctamente.
# ---------------------------------------------------------------------------

_state_path = BASE_DIR / STATE_FILE


def _load_last_key() -> str | None:
    try:
        return _state_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _save_last_key(key: str):
    try:
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        _state_path.write_text(key, encoding="utf-8")
    except OSError as e:
        logger.warning(f"No se pudo guardar el estado en {STATE_FILE}: {e}")


# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------


def run_cycle(state: dict, headless: bool = True) -> bool:
    """
    Ejecuta la actualización de departamentos en Suricata para un estado objetivo.
    Retorna True si el ciclo se completó sin errores, False si ocurrió un error.
    """
    departments = state["departments"]
    reason = state["reason"]

    logger.info("=" * 60)
    logger.info(f"EJECUTANDO CAMBIO EN NAVEGADOR: {reason}")
    logger.info(f"Departamentos objetivo: {', '.join(departments)}")
    logger.info("=" * 60)

    started = datetime.now()
    bot = SuricataBot(headless=headless)
    try:
        bot.start()
        bot.login()
        summary = bot.apply_departments(departments, agents_override=state.get("agents"))

        elapsed = (datetime.now() - started).total_seconds()
        logger.info(
            f"CAMBIO COMPLETADO en {elapsed:.1f}s — "
            f"OK: {summary['ok']} | "
            f"No encontrados: {summary['not_found']} | "
            f"Errores: {summary['error']}"
        )
        # Solo se considera aplicado si ningún agente falló; de lo contrario
        # se reintenta con backoff en vez de dar el estado por bueno.
        return summary["error"] == 0 and summary["not_found"] == 0

    except Exception as e:
        logger.error(f"ERROR CRÍTICO en ciclo de actualización: {e}", exc_info=True)
        return False
    finally:
        bot.stop()


# ---------------------------------------------------------------------------
# Modo test
# ---------------------------------------------------------------------------

TEST_AGENT = "iclass iclass"


def run_test(headless: bool = False):
    """
    Modo test: asigna únicamente el departamento "Ventas" al agente
    "iclass iclass" inmediatamente, sin respetar horarios. Útil para verificar
    login, recorrido de la tabla, modal y guardado.
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
        summary = bot.apply_departments(
            target_departments=["Ventas"],
            agents_override=[TEST_AGENT],
        )

        if summary["ok"] == 1:
            logger.info(f"✅ TEST EXITOSO — Departamento 'Ventas' asignado a '{TEST_AGENT}'")
        elif summary["not_found"] == 1:
            logger.error(
                f"❌ Agente '{TEST_AGENT}' no encontrado en la tabla. Verifica el nombre exacto."
            )
        else:
            logger.error(f"❌ Error al procesar '{TEST_AGENT}'. Revisa el log para más detalles.")

    except Exception as e:
        logger.error(f"ERROR en modo test: {e}", exc_info=True)
    finally:
        bot.stop()


def run_check_env() -> int:
    """
    Diagnóstico del entorno. No toca el portal ni modifica ningún agente:
    solo verifica que la máquina tenga todo lo necesario para correr el script.

    Pensado para ejecutarse en la VM de Ubuntu ANTES de habilitar el servicio.
    Devuelve 0 si todo está correcto, 1 si hay algún fallo.
    """
    import platform
    import shutil as _shutil

    problems, warnings_ = [], []

    def ok(msg):
        print(f"  [ OK ]  {msg}")

    def fail(msg):
        print(f"  [FALLO] {msg}")
        problems.append(msg)

    def warn(msg):
        print(f"  [AVISO] {msg}")
        warnings_.append(msg)

    print("=" * 68)
    print("  DIAGNÓSTICO DEL ENTORNO — Suricata Agent Scheduler")
    print("=" * 68)

    # --- Sistema y Python ---
    print("\n[1] Sistema")
    print(f"  Plataforma : {platform.platform()}")
    print(f"  Python     : {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 10):
        fail(f"Se requiere Python 3.10 o superior (se encontró {sys.version_info.major}.{sys.version_info.minor})")
    else:
        ok(f"Versión de Python compatible")

    # --- Zona horaria ---
    print("\n[2] Zona horaria")
    now = datetime.now()
    print(f"  Hora local : {now:%Y-%m-%d %H:%M:%S} (UTC{time.strftime('%z')})")
    print(f"  TIMEZONE   : {TIMEZONE or '(usar la del sistema)'}")
    if TIMEZONE and hasattr(time, "tzset"):
        offset = time.strftime("%z")
        if offset == "+0000" and TIMEZONE != "UTC":
            fail(
                f"La zona horaria quedó en UTC pese a TIMEZONE='{TIMEZONE}'. "
                "Falta el paquete tzdata:  sudo apt install -y tzdata"
            )
        else:
            ok(f"Zona horaria aplicada correctamente (UTC{offset})")
        state = get_target_state()
        print(f"  Estado que aplicaría ahora mismo: {state['key'] if state else 'ninguno (fuera de ventana)'}")
    elif not hasattr(time, "tzset"):
        warn("Plataforma sin tzset (Windows): se usa la hora local del sistema")

    # --- Credenciales ---
    print("\n[3] Credenciales (.env)")
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        fail(f"No existe el archivo {env_path}")
    else:
        ok(f"{env_path} encontrado")
        missing = [v for v in ("SURICATA_USER", "SURICATA_PASSWORD") if not os.getenv(v)]
        if missing:
            fail(f"Faltan variables en .env: {', '.join(missing)}")
        else:
            ok("SURICATA_USER y SURICATA_PASSWORD definidos")
        try:
            mode = env_path.stat().st_mode & 0o777
            if hasattr(time, "tzset") and mode & 0o077:
                warn(f".env es legible por otros usuarios (permisos {oct(mode)}). Recomendado: chmod 600 .env")
        except OSError:
            pass

    # --- Navegador ---
    print("\n[4] Navegador")
    binary = SuricataBot._find_chrome_binary()
    if binary:
        ok(f"Chrome/Chromium encontrado en {binary}")
    elif hasattr(time, "tzset"):
        fail(
            "No se encontró Google Chrome. Instálalo con el .deb oficial:\n"
            "          wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb\n"
            "          sudo apt install -y ./google-chrome-stable_current_amd64.deb"
        )
    else:
        warn("Detección de binario omitida (no es Linux); Selenium usará el Chrome del sistema")

    # Arranque real de Chrome: es lo único que distingue "está instalado" de
    # "de verdad funciona en esta VM".
    if binary or _shutil.which("google-chrome"):
        print("  Probando arranque real de Chrome headless...")
        import subprocess

        exe = binary or _shutil.which("google-chrome")
        try:
            proc = subprocess.run(
                [exe, "--headless=new", "--no-sandbox", "--disable-gpu",
                 "--dump-dom", "about:blank"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                ok("Chrome headless arranca correctamente")
            else:
                fail(
                    f"Chrome headless salió con código {proc.returncode}. stderr:\n          "
                    + "\n          ".join((proc.stderr or "(vacío)").strip().splitlines()[:10])
                )
                print("\n  --- Causas probables ---")
                print(SuricataBot._diagnose_chrome_launch(exe))
        except subprocess.TimeoutExpired:
            fail("Chrome headless se quedó colgado más de 60 s (falta de RAM, típicamente)")
        except OSError as e:
            fail(f"No se pudo ejecutar {exe}: {e}")

    if _shutil.which("chromedriver"):
        ok(f"chromedriver del sistema en {_shutil.which('chromedriver')}")
    else:
        print("  chromedriver no está en el PATH; Selenium Manager lo descargará solo")
        if not os.getenv("HOME") and hasattr(time, "tzset"):
            fail("HOME no está definido: Selenium Manager no podrá cachear el driver en ~/.cache/selenium")

    # --- Escritura y espacio ---
    print("\n[5] Rutas de escritura")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok(f"Escritura correcta en {log_dir}")
    except OSError as e:
        fail(f"No se puede escribir en {log_dir}: {e}")

    import tempfile as _tempfile

    try:
        with _tempfile.TemporaryDirectory(prefix="suricata_probe_"):
            pass
        ok(f"Escritura correcta en {_tempfile.gettempdir()} (perfiles de Chrome)")
    except OSError as e:
        fail(f"No se puede escribir en {_tempfile.gettempdir()}: {e}")

    # --- Memoria ---
    print("\n[6] Recursos")
    try:
        meminfo = Path("/proc/meminfo").read_text()
        total_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
        total_mb = total_kb // 1024
        print(f"  RAM total  : {total_mb} MB")
        if total_mb < 1024:
            fail(f"Solo {total_mb} MB de RAM. Chrome headless necesita ~500 MB; el OOM killer lo matará.")
        elif total_mb < 2048:
            warn(f"{total_mb} MB de RAM. Funciona, pero 2 GB da más margen.")
        else:
            ok(f"{total_mb} MB de RAM, suficiente")
        if not any(Path("/proc/swaps").read_text().splitlines()[1:]):
            warn("Sin swap configurada; un pico de memoria mataría el proceso")
    except (OSError, AttributeError, IndexError):
        print("  (métricas de memoria no disponibles en esta plataforma)")

    # --- Resultado ---
    print("\n" + "=" * 68)
    if problems:
        print(f"  RESULTADO: {len(problems)} problema(s) que impiden el funcionamiento")
        for p in problems:
            print(f"    ✗ {p.splitlines()[0]}")
    else:
        print("  RESULTADO: entorno correcto")
    if warnings_:
        print(f"  ({len(warnings_)} aviso(s) no bloqueante(s))")
    print("=" * 68)
    print("\nSiguiente paso: probar el acceso real al portal con  python main.py --test")
    return 1 if problems else 0


def _sleep_until_next_check(retry_delay: int | None = None):
    """
    Duerme hasta la próxima transición de horario (o hasta el reintento pendiente),
    acotado por CHECK_INTERVAL como red de seguridad ante cambios de hora del
    sistema. Despierta de inmediato si llega una señal de cierre.
    """
    now = datetime.now()
    seconds_to_transition = max(1.0, (next_transition_time(now) - now).total_seconds())

    delay = min(seconds_to_transition, CHECK_INTERVAL)
    if retry_delay is not None:
        delay = min(delay, retry_delay)

    logger.debug(f"Durmiendo {delay:.0f}s (próxima transición en {seconds_to_transition:.0f}s)")
    _stop.wait(delay)


def main(headless: bool = True):
    logger.info("=" * 60)
    logger.info("  Suricata Agent Scheduler — INICIADO (Modo Event-Driven)")
    logger.info(f"  Espera máxima entre comprobaciones: {CHECK_INTERVAL}s")
    logger.info(f"  Log guardado en: {log_file}")
    logger.info("=" * 60)

    last_executed_key = _load_last_key()
    if last_executed_key:
        logger.info(f"Estado previo recuperado de {STATE_FILE}: '{last_executed_key}'")

    last_logged_status = None
    failures = 0
    retry_delay = None

    while not _stop.is_set():
        state = get_target_state()

        if state is None:
            status_msg = "Fuera de ventana de acción — sin cambios"
            if last_logged_status != status_msg:
                logger.info(f"[{datetime.now().strftime('%H:%M')}] {status_msg}")
                last_logged_status = status_msg
            failures, retry_delay = 0, None

        elif state["key"] != last_executed_key:
            if failures >= MAX_RETRIES:
                # Se agotaron los reintentos: no seguir arrancando Chrome cada
                # pocos minutos. Se esperará al próximo estado distinto.
                status_msg = f"Estado '{state['key']}' abandonado tras {MAX_RETRIES} intentos"
                if last_logged_status != status_msg:
                    logger.error(status_msg)
                    last_logged_status = status_msg
            else:
                logger.info("=" * 60)
                logger.info(f"TRANSICIÓN DE EVENTO DETECTADA: {state['reason']}")
                logger.info("=" * 60)

                if run_cycle(state, headless=headless):
                    last_executed_key = state["key"]
                    _save_last_key(last_executed_key)
                    last_logged_status = f"Estado '{last_executed_key}' aplicado exitosamente"
                    failures, retry_delay = 0, None
                else:
                    failures += 1
                    retry_delay = min(
                        RETRY_BACKOFF_SECONDS * (2 ** (failures - 1)), RETRY_BACKOFF_MAX
                    )
                    logger.warning(
                        f"La ejecución para '{state['key']}' falló "
                        f"(intento {failures}/{MAX_RETRIES}). "
                        f"Reintento en {retry_delay}s."
                    )
        else:
            status_msg = f"En ventana '{state['key']}' — ya ejecutado (navegador en reposo)"
            if last_logged_status != status_msg:
                logger.info(f"[{datetime.now().strftime('%H:%M')}] {status_msg}")
                last_logged_status = status_msg
            failures, retry_delay = 0, None

        _sleep_until_next_check(retry_delay)

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
            "Modo test: asigna solo 'Ventas' al agente\n"
            "'iclass iclass' inmediatamente y sale.\n"
            "No sigue ningún horario."
        ),
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help=(
            "Diagnostica el entorno (zona horaria, Chrome, permisos,\n"
            "credenciales, RAM) y sale. No toca el portal.\n"
            "Ejecútalo en la VM antes de habilitar el servicio."
        ),
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Muestra el navegador (solo para depuración local). En producción siempre corre headless.",
    )
    args = parser.parse_args()

    headless = not args.no_headless  # Por defecto headless=True (producción)

    if args.check_env:
        raise SystemExit(run_check_env())
    elif args.test:
        run_test(headless=headless)
    else:
        main(headless=headless)
