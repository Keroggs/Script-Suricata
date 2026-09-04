"""
browser.py
Controla el navegador Chrome con Selenium para gestionar departamentos
de agentes en datavoip.suricata.cloud/agentes2.

Estrategia de rendimiento: la tabla de agentes se recorre en una sola pasada
(no una recarga por agente) y toda la manipulación del modal se hace en una
única llamada JS, para minimizar los round-trips al chromedriver, que son el
coste dominante del script.

Nota sobre el portal: la tabla se reordena entre peticiones, de modo que una
fila puede cambiar de página de una carga a otra. Por eso el modal se abre
buscando y pulsando por NOMBRE dentro de una misma ejecución de JS (atómico), y
si al terminar una pasada quedan agentes sin localizar se repite el recorrido.
"""

import difflib
import logging
import os
import re
import shutil
import tempfile
import time

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    SessionNotCreatedException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import AGENTS_URL, BASE_URL, TARGET_AGENTS

load_dotenv()
logger = logging.getLogger(__name__)

AGENT_BUTTON_SELECTOR = "button[title='Editar Departamentos']"
MODAL_VISIBLE_SELECTOR = ".modal.show, .modal[style*='display: block']"

# ---------------------------------------------------------------------------
# Scripts JS. Se definen a nivel de módulo para no reconstruir la cadena en
# cada llamada y para mantener legible el código Python.
# ---------------------------------------------------------------------------

# Extrae los nombres de todos los agentes de la página actual en una sola
# llamada, parseando el onclick en el navegador en vez de hacer un
# get_attribute() por botón desde Python.
JS_SCRAPE_AGENTS = r"""
var out = [];
var btns = document.querySelectorAll("button[title='Editar Departamentos']");
for (var i = 0; i < btns.length; i++) {
    var oc = btns[i].getAttribute('onclick') || '';
    var m = oc.match(/abrirEditarDeptos\((\{[\s\S]+\})\)/);
    var name = '';
    if (m) {
        try {
            var d = JSON.parse(m[1]);
            name = ((d.nombre || '') + ' ' + (d.last_name || '')).trim();
        } catch (e) { name = ''; }
    }
    out.push(name);
}
return out;
"""

# Lee, modifica y reporta todos los checkboxes del modal en una sola llamada.
# Devuelve {initial, changes, unchanged, unlabeled} para el log.
JS_APPLY_DEPARTMENTS = r"""
var targets = arguments[0];
var modal = document.querySelector('.modal.show') ||
            document.querySelector('.modal[style*="display: block"]') ||
            document.querySelector('.modal[style*="display:block"]');
if (!modal) return null;

var norm = function (s) { return (s || '').toLowerCase().trim(); };
var wanted = {};
for (var t = 0; t < targets.length; t++) { wanted[norm(targets[t])] = true; }

var boxes = modal.querySelectorAll('input[type="checkbox"]');
var initial = [], changes = [], unchanged = [], unlabeled = 0;

for (var i = 0; i < boxes.length; i++) {
    var cb = boxes[i];
    var label = '';

    if (cb.id) {
        var lbl = modal.querySelector('label[for="' + cb.id + '"]');
        if (lbl) label = lbl.textContent.trim();
    }
    if (!label) {
        var sib = cb.nextElementSibling;
        while (sib) {
            if (sib.tagName === 'LABEL') { label = sib.textContent.trim(); break; }
            sib = sib.nextElementSibling;
        }
    }
    if (!label && cb.parentElement) { label = cb.parentElement.textContent.trim(); }

    if (!label) { unlabeled++; continue; }

    initial.push({ label: label, checked: cb.checked });

    var should = !!wanted[norm(label)];
    if (should === cb.checked) { unchanged.push(label); continue; }

    // Click nativo para que se disparen los listeners de la página.
    cb.scrollIntoView({ block: 'center' });
    try { cb.click(); } catch (e) { }

    // Si el estado no cambió, intentar a través de la etiqueta asociada.
    if (cb.checked !== should) {
        var lb = (cb.labels && cb.labels[0]) ? cb.labels[0] : null;
        if (!lb && cb.id) lb = document.querySelector('label[for="' + cb.id + '"]');
        if (!lb) lb = cb.nextElementSibling;
        if (lb) { try { lb.click(); } catch (e) { } }
    }

    // Último recurso: fijar la propiedad y notificar el cambio.
    if (cb.checked !== should) {
        cb.checked = should;
        cb.dispatchEvent(new Event('change', { bubbles: true }));
    }

    changes.push({ label: label, checked: should, ok: cb.checked === should });
}

return { initial: initial, changes: changes, unchanged: unchanged, unlabeled: unlabeled };
"""

# Localiza y pulsa el botón Guardar del modal.
JS_CLICK_SAVE = r"""
var modal = document.querySelector('.modal.show') ||
            document.querySelector('.modal[style*="display: block"]') ||
            document.querySelector('.modal[style*="display:block"]');
if (!modal) return false;
var buttons = modal.querySelectorAll('button');
var target = null;
for (var i = 0; i < buttons.length; i++) {
    if (buttons[i].textContent.trim().toLowerCase().indexOf('guardar') !== -1) {
        target = buttons[i];
        break;
    }
}
if (!target) target = modal.querySelector('button.btn-primary');
if (!target) return false;
target.scrollIntoView({ block: 'center' });
target.click();
return true;
"""

# Avanza a la siguiente página de la tabla. Devuelve false si no hay más.
JS_NEXT_PAGE = r"""
var modal = document.querySelector('.modal.show, .modal[style*="display: block"]');
if (modal) return false;

var candidate = null;
var navs = document.querySelectorAll('.pagination, .dataTables_paginate, nav[aria-label*="pagination"], nav[aria-label*="Paginación"]');
var scope = navs.length > 0 ? Array.prototype.slice.call(navs) : [document.body];

for (var c = 0; c < scope.length && !candidate; c++) {
    var links = scope[c].querySelectorAll('a, button, li');
    for (var i = 0; i < links.length; i++) {
        var el = links[i];
        if (el.tagName === 'LI') {
            var child = el.querySelector('a, button');
            if (child) el = child;
        }
        var li = el.closest('li');
        if (li && (li.classList.contains('disabled') || li.getAttribute('aria-disabled') === 'true')) continue;
        if (el.classList.contains('disabled') || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true') continue;

        var text  = (el.textContent || '').trim().toLowerCase();
        var label = (el.getAttribute('aria-label') || '').toLowerCase();
        var rel   = (el.getAttribute('rel') || '').toLowerCase();
        var id    = (el.id || '').toLowerCase();
        var cls   = (el.className || '').toLowerCase();

        if (text.indexOf('anterior') !== -1 || text.indexOf('previous') !== -1 ||
            label.indexOf('anterior') !== -1 || label.indexOf('previous') !== -1 ||
            text === '‹' || text === '«' || text === '<') continue;

        if (text === 'siguiente' || text.indexOf('siguiente') !== -1 ||
            text === 'next' || text.indexOf('next') !== -1 ||
            text === '›' || text === '»' || text === '>' ||
            label.indexOf('siguiente') !== -1 || label.indexOf('next') !== -1 ||
            rel === 'next' || id.indexOf('next') !== -1 || cls.indexOf('next') !== -1) {
            candidate = el;
            break;
        }
    }
}

if (!candidate) {
    var active = document.querySelector('.pagination .active, .dataTables_paginate .current');
    if (active) {
        var nxt = active.nextElementSibling;
        if (nxt && !nxt.classList.contains('disabled')) {
            candidate = nxt.querySelector('a, button') ||
                        ((nxt.tagName === 'A' || nxt.tagName === 'BUTTON') ? nxt : null);
        }
    }
}

if (!candidate) return false;
candidate.scrollIntoView({ block: 'center' });
candidate.click();
return true;
"""

# Abre el modal del agente cuyo nombre coincide EXACTAMENTE con el recibido.
#
# La búsqueda y el click ocurren dentro de la misma ejecución de JS, de forma
# atómica. Es imprescindible porque la tabla se reordena sola entre peticiones:
# resolver el elemento en una llamada y pulsarlo en otra provoca
# StaleElementReference y, peor aún, puede acabar pulsando la fila equivocada si
# el orden cambió en medio.
JS_OPEN_AGENT_MODAL = r"""
var wanted = arguments[0];
var btns = document.querySelectorAll("button[title='Editar Departamentos']");
for (var i = 0; i < btns.length; i++) {
    var oc = btns[i].getAttribute('onclick') || '';
    var m = oc.match(/abrirEditarDeptos\((\{[\s\S]+\})\)/);
    if (!m) continue;
    var name = '';
    try {
        var d = JSON.parse(m[1]);
        name = ((d.nombre || '') + ' ' + (d.last_name || '')).trim();
    } catch (e) { continue; }

    if (name === wanted) {
        btns[i].scrollIntoView({ block: 'center' });
        btns[i].click();
        return true;
    }
}
return false;
"""

JS_CLOSE_MODAL = r"""
var modal = document.querySelector('.modal.show, .modal[style*="display: block"]');
if (!modal) return false;
var btn = modal.querySelector('.btn-close, [data-dismiss="modal"], [data-bs-dismiss="modal"], .close');
if (!btn) {
    var buttons = modal.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        if (buttons[i].textContent.trim().toLowerCase().indexOf('cancelar') !== -1) { btn = buttons[i]; break; }
    }
}
if (btn) { btn.click(); return true; }
return false;
"""


class SuricataBot:
    """Bot de automatización para la gestión de departamentos de agentes."""

    MAX_PAGES = 50   # cota de seguridad ante una paginación que no termine
    MAX_PASSES = 3   # reintentos de recorrido completo (la tabla se reordena sola)

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self.wait = None
        self.user_data_dir = None

    # ------------------------------------------------------------------
    # Ciclo de vida del navegador
    # ------------------------------------------------------------------

    def start(self):
        """Inicializa el driver de Chrome con una configuración de bajo consumo."""
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")

        # No esperar a subrecursos (imágenes, analytics) para dar la página por cargada.
        options.page_load_strategy = "eager"

        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-setuid-sandbox",
            "--disable-extensions",
            "--window-size=1920,1080",
            "--lang=es-ES",
            "--disable-notifications",
            "--disable-blink-features=AutomationControlled",
            # Recorte de consumo: sin imágenes, sin tareas de fondo, sin caché en disco.
            "--blink-settings=imagesEnabled=false",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-client-side-phishing-detection",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--disable-gpu-shader-disk-cache",
            "--disk-cache-size=1048576",
            "--media-cache-size=1048576",
            "--disable-application-cache",
            "--disable-crash-reporter",
            "--disable-breakpad",
            "--remote-debugging-port=0",
            "--log-level=3",
        ):
            options.add_argument(arg)

        binary = self._find_chrome_binary()
        if binary:
            options.binary_location = binary
            logger.info(f"Usando binario de Chrome del sistema: {binary}")

        self._cleanup_stale_profiles()

        # Directorio de datos de usuario aislado para evitar conflictos de
        # sockets/puertos en Linux.
        self.user_data_dir = tempfile.mkdtemp(prefix="suricata_chrome_")
        options.add_argument(f"--user-data-dir={self.user_data_dir}")

        self.driver = self._launch_driver(options, binary)

        self.wait = WebDriverWait(self.driver, 20)
        logger.info("Navegador iniciado")

    def _launch_driver(self, options, binary: str | None):
        """
        Arranca chromedriver distinguiendo los dos fallos posibles.

        - `SessionNotCreatedException` ("Chrome instance exited"): el driver
          funciona, quien muere es **Chrome**. Reintentar con otro driver no
          sirve de nada; hay que diagnosticar por qué se cae el navegador.
        - Cualquier otro fallo: normalmente es que no hay chromedriver
          compatible, y ahí sí tiene sentido el respaldo de ChromeDriverManager.
        """
        try:
            # Arranque normal: sin logs del driver, para no gastar E/S en cada ciclo.
            return webdriver.Chrome(service=Service(), options=options)

        except SessionNotCreatedException as err:
            logger.error("Chrome arrancó y murió inmediatamente. Diagnosticando...")
            raise self._explain_chrome_death(err, options, binary) from err

        except Exception as err:
            logger.warning(
                f"No se pudo iniciar chromedriver ({err}); "
                "probando con ChromeDriverManager de respaldo..."
            )
            try:
                # Import diferido: webdriver_manager solo se necesita aquí.
                from webdriver_manager.chrome import ChromeDriverManager

                return webdriver.Chrome(
                    service=Service(ChromeDriverManager().install()), options=options
                )
            except SessionNotCreatedException as err2:
                raise self._explain_chrome_death(err2, options, binary) from err2

    def _explain_chrome_death(self, err, options, binary: str | None):
        """
        Reintenta UNA vez con el log verboso del driver activado y construye una
        excepción que explica por qué murió Chrome.

        El verbose solo se activa aquí (no en el arranque normal) porque genera
        mucha E/S y Selenium abre el archivo en modo *append*, con lo que en un
        servicio permanente crecería sin límite.
        """
        driver_log = self._driver_log_path()

        # Truncar: solo interesa el intento que acaba de fallar.
        try:
            open(driver_log, "w", encoding="utf-8").close()
        except OSError:
            pass

        try:
            driver = webdriver.Chrome(
                service=Service(log_output=driver_log, service_args=["--verbose"]),
                options=options,
            )
        except Exception:
            pass  # Se esperaba que volviera a fallar; lo que interesa es el log.
        else:
            # Insólito, pero arrancó en el reintento: no dejar el navegador huérfano.
            try:
                driver.quit()
            except Exception:
                pass

        detail = self._diagnose_chrome_launch(binary)
        tail = self._tail_driver_log(driver_log)
        msg = getattr(err, "msg", str(err))
        return SessionNotCreatedException(
            f"{msg}\n\n=== DIAGNÓSTICO ===\n{detail}\n\n"
            f"=== ÚLTIMAS LÍNEAS DE {driver_log} ===\n{tail}"
        )

    @staticmethod
    def _driver_log_path():
        """logs/chromedriver.log junto al proyecto (no depende del cwd)."""
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError:
            return os.path.join(tempfile.gettempdir(), "suricata_chromedriver.log")
        return os.path.join(log_dir, "chromedriver.log")

    @staticmethod
    def _tail_driver_log(path: str, lines: int = 25) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read().splitlines()
        except OSError:
            return "(no se pudo leer el log del driver)"
        # Las líneas relevantes son las que menciona Chrome al morir.
        relevant = [ln for ln in content if "ERROR" in ln or "error" in ln or "Failed" in ln]
        chosen = relevant[-lines:] if relevant else content[-lines:]
        return "\n".join(chosen) or "(log vacío)"

    @classmethod
    def _diagnose_chrome_launch(cls, binary: str | None) -> str:
        """
        Ejecuta Chrome directamente para capturar el motivo real de la caída y
        comprueba las causas típicas en una VM de Ubuntu.
        """
        import subprocess

        notes: list[str] = []

        # 1) Ejecutar el navegador a mano: su stderr suele decir exactamente qué falta.
        exe = binary or shutil.which("google-chrome") or shutil.which("chromium")
        if not exe:
            notes.append("· No se encontró ningún ejecutable de Chrome en el sistema.")
        else:
            cmd = [exe, "--headless=new", "--no-sandbox", "--disable-gpu",
                   "--dump-dom", "about:blank"]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                err_out = (proc.stderr or "").strip()
                if proc.returncode != 0:
                    notes.append(
                        f"· `{exe} --headless=new` salió con código {proc.returncode}.\n"
                        f"  stderr:\n    " + "\n    ".join(err_out.splitlines()[:15] or ["(vacío)"])
                    )
                    if "error while loading shared libraries" in err_out:
                        lib = err_out.split("shared libraries:")[-1].split(":")[0].strip()
                        notes.append(
                            f"  → Falta una biblioteca del sistema ({lib}). Instala las dependencias:\n"
                            "      sudo apt-get install -y -f\n"
                            "      sudo apt-get install -y libnss3 libatk1.0-0 libatk-bridge2.0-0 \\\n"
                            "          libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \\\n"
                            "          libxfixes3 libxrandr2 libgbm1 libasound2t64 libpango-1.0-0"
                        )
                else:
                    notes.append(
                        f"· `{exe} --headless=new` funciona por sí solo "
                        "(el problema está en las opciones o en el entorno del servicio, no en Chrome)."
                    )
            except subprocess.TimeoutExpired:
                notes.append(f"· `{exe} --headless=new` se quedó colgado más de 45 s.")
            except OSError as e:
                notes.append(f"· No se pudo ejecutar {exe}: {e}")

        # 2) Memoria disponible: la causa más habitual de que Chrome muera al instante.
        try:
            meminfo = open("/proc/meminfo", encoding="utf-8").read()
            total_mb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) // 1024
            avail_mb = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1)) // 1024
            notes.append(f"· RAM: {avail_mb} MB disponibles de {total_mb} MB totales.")
            if avail_mb < 400:
                notes.append(
                    "  → INSUFICIENTE. Chrome headless necesita ~400-500 MB y el kernel lo mata "
                    "al arrancar. Amplía la RAM de la VM a 2 GB o añade swap."
                )
        except (OSError, AttributeError):
            pass

        # 3) Ubuntu 23.10+ restringe los user namespaces sin privilegios vía AppArmor,
        #    lo que impide arrancar el sandbox de Chrome.
        try:
            userns = open(
                "/proc/sys/kernel/apparmor_restrict_unprivileged_userns", encoding="utf-8"
            ).read().strip()
            if userns == "1":
                notes.append(
                    "· AppArmor restringe los user namespaces sin privilegios "
                    "(kernel.apparmor_restrict_unprivileged_userns=1, por defecto en Ubuntu 23.10+).\n"
                    "  → El script ya pasa --no-sandbox, pero si persiste, desactívalo:\n"
                    "      echo 'kernel.apparmor_restrict_unprivileged_userns=0' | "
                    "sudo tee /etc/sysctl.d/60-apparmor-namespace.conf\n"
                    "      sudo sysctl --system"
                )
        except OSError:
            pass

        # 4) /dev/shm minúsculo (típico en contenedores; el script ya lo evita).
        try:
            st = os.statvfs("/dev/shm")
            shm_mb = (st.f_blocks * st.f_frsize) // (1024 * 1024)
            if shm_mb < 64:
                notes.append(
                    f"· /dev/shm es de solo {shm_mb} MB. El script ya pasa "
                    "--disable-dev-shm-usage, pero conviene ampliarlo."
                )
        except (OSError, AttributeError):
            pass

        # 5) Usuario y HOME: sin HOME escribible Chrome no puede crear su perfil.
        home = os.getenv("HOME")
        if not home:
            notes.append("· HOME no está definido. Añade `Environment=HOME=/home/USUARIO` a la unit de systemd.")
        elif not os.access(home, os.W_OK):
            notes.append(f"· HOME={home} no es escribible por el usuario actual.")

        return "\n".join(notes) if notes else "(sin causas evidentes)"

    @staticmethod
    def _find_chrome_binary() -> str | None:
        """
        Localiza el ejecutable de Chrome/Chromium del sistema (Linux).

        Evita deliberadamente las versiones empaquetadas como **snap**: en Ubuntu
        22.04+ `apt install chromium-browser` instala un shim de snap cuyo
        confinamiento le impide leer el perfil que creamos en /tmp, y Selenium
        falla con un críptico "unable to discover open pages". Se prefiere
        siempre el .deb de Google Chrome.
        """
        for path in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/opt/google/chrome/chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ):
            if not os.path.exists(path):
                continue

            # Un shim de snap es un enlace/script que apunta a /snap/...
            real = os.path.realpath(path)
            if real.startswith("/snap/") or "/snapd/" in real:
                logger.warning(
                    f"Ignorando '{path}': es un paquete snap ({real}), incompatible con Selenium. "
                    "Instala Google Chrome desde el .deb oficial."
                )
                continue

            return path

        return None

    @staticmethod
    def _cleanup_stale_profiles(max_age_seconds: int = 3600):
        """
        Borra perfiles temporales de ejecuciones previas que quedaron huérfanos.

        Solo se borran los que llevan un rato sin tocarse: si el servicio se
        reinicia mientras la instancia anterior todavía agoniza, borrarle el
        perfil en uso la haría fallar.
        """
        temp_base = tempfile.gettempdir()
        cutoff = time.time() - max_age_seconds
        try:
            entries = os.listdir(temp_base)
        except OSError:
            return

        for item in entries:
            if not item.startswith("suricata_chrome_"):
                continue
            path = os.path.join(temp_base, item)
            try:
                if os.path.getmtime(path) > cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(path, ignore_errors=True)

    def stop(self):
        """Cierra el navegador y limpia el directorio temporal."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            logger.info("Navegador cerrado")

        if self.user_data_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)
            self.user_data_dir = None

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self):
        """Inicia sesión en el portal con las credenciales del .env."""
        url = os.getenv("SURICATA_URL", BASE_URL)
        user = os.getenv("SURICATA_USER")
        pwd = os.getenv("SURICATA_PASSWORD")

        if not user or not pwd:
            raise ValueError("Credenciales no encontradas en el archivo .env")

        logger.info(f"Navegando a: {url}")
        self.driver.get(url)

        # Campo email — el form usa type="text" con name="email"
        try:
            user_field = self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='email']"))
            )
        except TimeoutException:
            raise TimeoutException("No se encontró el campo de email en la página de login")

        user_field.clear()
        user_field.send_keys(user)

        pass_field = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
        pass_field.clear()
        pass_field.send_keys(pwd)

        # Botón "Iniciar sesión" — no tiene type="submit", usa clase btn-primary-custom
        self.driver.find_element(
            By.CSS_SELECTOR,
            "button.btn-primary-custom, button[type='submit'], input[type='submit']",
        ).click()

        # Esperar a que el formulario desaparezca en vez de dormir un tiempo fijo.
        try:
            self.wait.until(EC.staleness_of(pass_field))
        except TimeoutException:
            logger.warning("El formulario de login no cambió tras el envío; continuando de todas formas")

        logger.info(f"Login completado. URL actual: {self.driver.current_url}")

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    def go_to_agents_page(self):
        """Navega a la página de agentes y espera a que carguen los botones."""
        logger.info(f"Navegando a: {AGENTS_URL}")
        self.driver.get(AGENTS_URL)
        self._wait_for_agent_buttons(
            "No se encontraron botones 'Editar Departamentos'. "
            "Verifica que el login fue exitoso."
        )
        logger.info("Página de agentes cargada correctamente")

    def _wait_for_agent_buttons(self, error_msg: str | None = None):
        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, AGENT_BUTTON_SELECTOR))
            )
        except TimeoutException:
            if error_msg:
                raise TimeoutException(error_msg)

    # ------------------------------------------------------------------
    # Coincidencia de nombres
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        return name.lower().strip()

    @staticmethod
    def _clean_tokens(s: str) -> list[str]:
        """Tokeniza un nombre ignorando paréntesis, signos y tokens de una letra."""
        s_no_paren = re.sub(r"\([^\)]*\)", " ", s)
        cleaned = re.sub(r"[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ\s]", " ", s_no_paren)
        return [t for t in cleaned.lower().split() if len(t) > 1]

    @classmethod
    def _match_agent_name(cls, page_name: str, target_name: str) -> bool:
        """Compara nombres tolerando orden de nombre/apellido, mayúsculas y variantes ortográficas."""
        if cls._normalize(page_name) == cls._normalize(target_name):
            return True

        p_tokens = cls._clean_tokens(page_name)
        t_tokens = cls._clean_tokens(target_name)
        if not p_tokens or not t_tokens:
            return False

        p_set, t_set = set(p_tokens), set(t_tokens)
        if p_set == t_set or t_set.issubset(p_set) or p_set.issubset(t_set):
            return True

        # Comparación token por token tolerando pequeñas diferencias tipográficas
        # (ej: ruby/rubi, yenireth/yeniret).
        shorter, longer = (
            (t_tokens, p_tokens) if len(t_tokens) <= len(p_tokens) else (p_tokens, t_tokens)
        )
        return all(
            any(difflib.SequenceMatcher(None, s_tok, l_tok).ratio() >= 0.75 for l_tok in longer)
            for s_tok in shorter
        )

    # ------------------------------------------------------------------
    # Recorrido de la tabla
    # ------------------------------------------------------------------

    def _scrape_page_agents(self) -> list[str]:
        """Nombres de los agentes de la página actual, en una sola llamada JS."""
        names = self.driver.execute_script(JS_SCRAPE_AGENTS) or []
        logger.debug(f"Agentes en la página actual: {names}")
        return names

    def _go_to_next_page(self) -> bool:
        """Avanza a la siguiente página de la tabla. False si no hay más páginas."""
        try:
            if not self.driver.execute_script(JS_NEXT_PAGE):
                return False
        except Exception as e:
            logger.debug(f"Error al intentar avanzar de página: {e}")
            return False

        logger.debug("  → Avanzando a la siguiente página de la tabla...")
        self._wait_for_agent_buttons()
        return True

    # ------------------------------------------------------------------
    # Procesamiento de agentes
    # ------------------------------------------------------------------

    def apply_departments(
        self,
        target_departments: list[str],
        agents_override: list[str] | None = None,
    ) -> dict:
        """
        Aplica la configuración de departamentos a los agentes objetivo.

        La tabla del portal se reordena entre peticiones (las filas cambian de
        página de una carga a otra), así que se recorre en pasadas completas: en
        cada página se procesan todos los pendientes que aparezcan y se avanza;
        si al terminar la pasada quedan agentes sin encontrar, se repite desde la
        página 1, hasta MAX_PASSES veces.

        Args:
            target_departments: Departamentos a marcar. Lista vacía [] desmarca todos.
            agents_override:    Agentes a procesar. Si es None, usa TARGET_AGENTS.

        Returns:
            Resumen con contadores de éxito, no encontrados y error.
        """
        pending = list(agents_override if agents_override is not None else TARGET_AGENTS)
        summary = {"ok": 0, "not_found": 0, "error": 0}

        logger.info(f"Agentes a procesar ({len(pending)}): {', '.join(pending)}")

        for pass_num in range(1, self.MAX_PASSES + 1):
            if not pending:
                break

            if pass_num > 1:
                logger.info(
                    f"Pasada {pass_num}/{self.MAX_PASSES} — "
                    f"{len(pending)} agente(s) sin localizar: {', '.join(pending)} "
                    "(la tabla se reordenó entre páginas)"
                )

            self.go_to_agents_page()
            processed = self._sweep_pages(pending, target_departments, summary)

            if not processed:
                # Una pasada completa sin procesar a nadie: insistir no ayudaría.
                break

        for target_name in pending:
            logger.warning(f"⚠  Agente no encontrado en ninguna página de la tabla: '{target_name}'")
            summary["not_found"] += 1

        return summary

    def _sweep_pages(
        self, pending: list[str], target_departments: list[str], summary: dict
    ) -> int:
        """
        Recorre la paginación de principio a fin procesando los pendientes.

        Muta `pending` (quita los resueltos) y `summary`. Devuelve cuántos
        agentes se procesaron en esta pasada.
        """
        processed = 0
        page_num = 1

        while pending and page_num <= self.MAX_PAGES:
            for target_name, page_name in self._match_page(pending):
                try:
                    if not self._open_agent_modal(page_name):
                        # La fila cambió de página entre el scrape y el click.
                        logger.warning(
                            f"  '{target_name}' ya no está en la página {page_num}; "
                            "se buscará más adelante"
                        )
                        continue

                    logger.info(f"  ✓ '{target_name}' encontrado en la página {page_num}")
                    self._process_agent(target_name, target_departments)
                    summary["ok"] += 1
                except Exception as e:
                    logger.error(f"✗  Error procesando '{target_name}': {e}", exc_info=True)
                    summary["error"] += 1
                    self._safe_close_modal()

                processed += 1
                pending.remove(target_name)

            if not pending:
                break

            if self._go_to_next_page():
                page_num += 1
                logger.info(
                    f"  Buscando {len(pending)} agente(s) restante(s) en la página {page_num}..."
                )
            else:
                break

        return processed

    def _match_page(self, pending: list[str]) -> list[tuple[str, str]]:
        """
        Pares (nombre_objetivo, nombre_en_la_tabla) presentes en la página actual.

        Se devuelve también el nombre exacto tal como aparece en la tabla porque
        es la clave con la que luego se abre el modal de forma atómica.
        """
        found: list[tuple[str, str]] = []
        matched = set()
        for page_name in self._scrape_page_agents():
            if not page_name:
                continue
            for target_name in pending:
                if target_name not in matched and self._match_agent_name(page_name, target_name):
                    found.append((target_name, page_name))
                    matched.add(target_name)
                    break
        return found

    def _open_agent_modal(self, page_name: str) -> bool:
        """
        Abre el modal del agente localizándolo y pulsándolo en una sola llamada JS.

        Hacerlo atómicamente evita StaleElementReference y, sobre todo, evita
        pulsar la fila equivocada si la tabla se reordena en medio.
        """
        return bool(self.driver.execute_script(JS_OPEN_AGENT_MODAL, page_name))

    def _process_agent(self, agent_name: str, target_departments: list[str]):
        """Aplica los checkboxes correctos en el modal ya abierto."""
        logger.info(f"  → Procesando: {agent_name}")

        # Esperar a que el modal esté visible y con checkboxes.
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".modal.show input[type='checkbox']")
                )
            )
        except TimeoutException:
            # Fallback si el modal no lleva la clase .show pero igual está visible.
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".modal input[type='checkbox']")
                )
            )

        # Leer estado, decidir y aplicar todos los cambios en una sola llamada.
        result = self.driver.execute_script(JS_APPLY_DEPARTMENTS, list(target_departments))

        if not result or not result.get("initial"):
            raise NoSuchElementException(
                f"No se encontraron checkboxes con etiqueta en el modal de '{agent_name}'"
            )

        logger.info("    [ESTADO INICIAL DE CHECKBOXES]:")
        for item in result["initial"]:
            estado = "MARCADO (✓)" if item["checked"] else "DESMARCADO (✗)"
            logger.info(f"      • {item['label']}: {estado}")

        if result.get("unlabeled"):
            logger.warning(f"    {result['unlabeled']} checkbox(es) sin etiqueta, omitidos")

        changes = []
        for change in result["changes"]:
            if change["ok"]:
                action = "MARCADO   " if change["checked"] else "DESMARCADO"
                mark = "✓" if change["checked"] else "✗"
                logger.info(f"    [{action}] {change['label']}")
                changes.append(f"{mark} {change['label']}")
            else:
                logger.warning(f"    [FALLÓ] No se pudo cambiar '{change['label']}'")

        for label in result.get("unchanged", []):
            logger.debug(f"    [SIN CAMBIOS] {label} (ya en el estado deseado)")

        # Guardar.
        if not self.driver.execute_script(JS_CLICK_SAVE):
            raise NoSuchElementException(
                f"No se encontró el botón 'Guardar' en el modal de '{agent_name}'"
            )

        # Esperar a que el modal se oculte (confirmación de guardado del servidor).
        try:
            self.wait.until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, MODAL_VISIBLE_SELECTOR))
            )
            logger.debug("    Modal cerrado tras guardar")
        except TimeoutException:
            logger.warning("    El modal no se cerró tras 20s; forzando cierre")
            self._safe_close_modal()

        cambios_str = ", ".join(changes) if changes else "sin cambios"
        logger.info(f"  ✓ {agent_name} guardado — Cambios: {cambios_str}")

    def _safe_close_modal(self):
        """Intenta cerrar cualquier modal abierto de forma segura."""
        try:
            if self.driver.execute_script(JS_CLOSE_MODAL):
                self.wait.until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, MODAL_VISIBLE_SELECTOR)
                    )
                )
        except Exception:
            pass
