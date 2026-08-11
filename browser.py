"""
browser.py
Controla el navegador Chrome con Selenium para gestionar departamentos
de agentes en datavoip.suricata.cloud/agentes2.
"""

import json
import logging
import os
import re
import time

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from config import AGENTS_URL, BASE_URL, TARGET_AGENTS

load_dotenv()
logger = logging.getLogger(__name__)


class SuricataBot:
    """Bot de automatización para la gestión de departamentos de agentes."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.driver = None
        self.wait = None

    # ------------------------------------------------------------------
    # Ciclo de vida del navegador
    # ------------------------------------------------------------------

    def start(self):
        """Inicializa el driver de Chrome."""
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=es-ES")
        options.add_argument("--disable-notifications")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, 20)
        logger.info("Navegador iniciado")

    def stop(self):
        """Cierra el navegador."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            logger.info("Navegador cerrado")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self):
        """Inicia sesión en el portal con las credenciales del .env."""
        url  = os.getenv("SURICATA_URL", BASE_URL)
        user = os.getenv("SURICATA_USER")
        pwd  = os.getenv("SURICATA_PASSWORD")

        if not user or not pwd:
            raise ValueError("Credenciales no encontradas en el archivo .env")

        logger.info(f"Navegando a: {url}")
        self.driver.get(url)

        # Campo email — el form usa type="text" con name="email"
        try:
            user_field = self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[name='email']")
                )
            )
        except TimeoutException:
            raise TimeoutException("No se encontró el campo de email en la página de login")

        user_field.clear()
        user_field.send_keys(user)

        # Campo contraseña
        pass_field = self.driver.find_element(By.CSS_SELECTOR, "input[name='password']")
        pass_field.clear()
        pass_field.send_keys(pwd)

        # Botón "Iniciar sesión" — no tiene type="submit", usa clase btn-primary-custom
        submit = self.driver.find_element(
            By.CSS_SELECTOR,
            "button.btn-primary-custom, button[type='submit'], input[type='submit']"
        )
        submit.click()

        # Esperar a que la navegación termine
        time.sleep(3)
        logger.info(f"Login completado. URL actual: {self.driver.current_url}")


    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    def go_to_agents_page(self):
        """Navega a la página de agentes y espera a que carguen los botones."""
        logger.info(f"Navegando a: {AGENTS_URL}")
        self.driver.get(AGENTS_URL)

        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "button[title='Editar Departamentos']")
                )
            )
        except TimeoutException:
            raise TimeoutException(
                "No se encontraron botones 'Editar Departamentos'. "
                "Verifica que el login fue exitoso."
            )

        logger.info("Página de agentes cargada correctamente")

    # ------------------------------------------------------------------
    # Extracción de agentes
    # ------------------------------------------------------------------

    def _get_agent_buttons(self) -> list[tuple[str, object]]:
        """
        Retorna lista de (nombre_completo, elemento_boton) para todos
        los agentes encontrados en la página.
        """
        buttons = self.driver.find_elements(
            By.CSS_SELECTOR, "button[title='Editar Departamentos']"
        )
        result = []
        for btn in buttons:
            onclick = btn.get_attribute("onclick") or ""
            # Extraer el JSON del onclick="abrirEditarDeptos({...})"
            match = re.search(r"abrirEditarDeptos\((\{.+\})\)", onclick, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
                nombre    = (data.get("nombre", "") or "").strip()
                apellido  = (data.get("last_name", "") or "").strip()
                full_name = f"{nombre} {apellido}".strip()
                result.append((full_name, btn))
            except json.JSONDecodeError as e:
                logger.warning(f"No se pudo parsear JSON del botón: {e}")

        logger.debug(f"Agentes encontrados en página: {[n for n, _ in result]}")
        return result

    @staticmethod
    def _normalize(name: str) -> str:
        return name.lower().strip()

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

        Args:
            target_departments: Lista de departamentos a marcar.
                                Pasar lista vacía [] para desmarcar todos.
            agents_override:    Lista de nombres de agentes a procesar.
                                Si es None, usa TARGET_AGENTS de config.py.

        Returns:
            Resumen con contadores de éxito y error.
        """
        agents_to_process = agents_override if agents_override is not None else TARGET_AGENTS
        page_agents = self._get_agent_buttons()
        summary = {"ok": 0, "not_found": 0, "error": 0}

        for target_name in agents_to_process:
            # Buscar agente en la página
            match_btn = None
            for full_name, btn in page_agents:
                if self._normalize(full_name) == self._normalize(target_name):
                    match_btn = btn
                    break

            if match_btn is None:
                logger.warning(f"⚠  Agente no encontrado en página: '{target_name}'")
                summary["not_found"] += 1
                continue

            try:
                self._process_agent(target_name, match_btn, target_departments)
                summary["ok"] += 1
            except Exception as e:
                logger.error(f"✗  Error procesando '{target_name}': {e}", exc_info=True)
                summary["error"] += 1
                # Intentar cerrar el modal si quedó abierto
                self._safe_close_modal()

        return summary

    def _process_agent(self, agent_name: str, btn, target_departments: list[str]):
        """Abre el modal de edición y aplica los checkboxes correctos."""
        target_normalized = {self._normalize(d) for d in target_departments}

        logger.info(f"  → Procesando: {agent_name}")

        # Scroll al botón y abrir modal
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.4)
        self.driver.execute_script("arguments[0].click();", btn)

        # Esperar a que el modal esté completamente visible y con checkboxes
        try:
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".modal.show input[type='checkbox']")
                )
            )
        except TimeoutException:
            # Fallback si el modal no tiene clase .show pero igual está visible
            self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".modal input[type='checkbox']")
                )
            )
        time.sleep(0.5)

        # ── Leer todos los checkboxes y sus etiquetas vía JavaScript ──────────
        # Más confiable que Selenium para estructuras Bootstrap dinámicas
        cb_data = self.driver.execute_script("""
            var results = [];
            // Buscar dentro del modal visible
            var modal = document.querySelector('.modal.show') ||
                        document.querySelector('.modal[style*="display: block"]') ||
                        document.querySelector('.modal[style*="display:block"]');
            if (!modal) return results;

            var checkboxes = modal.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(function(cb, idx) {
                var label = '';

                // 1) label[for=id]
                if (cb.id) {
                    var lbl = modal.querySelector('label[for="' + cb.id + '"]');
                    if (lbl) label = lbl.textContent.trim();
                }

                // 2) label hermano siguiente
                if (!label) {
                    var sib = cb.nextElementSibling;
                    while (sib) {
                        if (sib.tagName === 'LABEL') { label = sib.textContent.trim(); break; }
                        sib = sib.nextElementSibling;
                    }
                }

                // 3) texto del contenedor padre (form-check)
                if (!label && cb.parentElement) {
                    label = cb.parentElement.textContent.trim();
                }

                results.push({ index: idx, label: label, checked: cb.checked });
            });
            return results;
        """)

        if not cb_data:
            raise NoSuchElementException(
                f"No se encontraron checkboxes en el modal de '{agent_name}'"
            )

        logger.debug(f"    Checkboxes encontrados: {[(d['label'], d['checked']) for d in cb_data]}")

        # Obtener los elementos DOM exactos de los checkboxes vía JS para que coincidan con idx
        checkboxes_els = self.driver.execute_script("""
            var modal = document.querySelector('.modal.show') ||
                        document.querySelector('.modal[style*="display: block"]') ||
                        document.querySelector('.modal[style*="display:block"]') ||
                        document.querySelector('.modal');
            return modal ? Array.from(modal.querySelectorAll('input[type="checkbox"]')) : [];
        """)

        # Log del HTML del modal para diagnóstico si fuera necesario
        modal_html = self.driver.execute_script("""
            var modal = document.querySelector('.modal.show') ||
                        document.querySelector('.modal[style*="display: block"]') ||
                        document.querySelector('.modal[style*="display:block"]') ||
                        document.querySelector('.modal');
            return modal ? modal.outerHTML : 'No modal found';
        """)
        logger.debug(f"    [DEBUG] Modal HTML: {modal_html}")

        # Registrar estado inicial de cada checkbox en los logs
        logger.info("    [ESTADO INICIAL DE CHECKBOXES]:")
        for item in cb_data:
            if item["label"]:
                estado_inicial = "MARCADO (✓)" if item["checked"] else "DESMARCADO (✗)"
                logger.info(f"      • {item['label']}: {estado_inicial}")

        changes = []
        for item in cb_data:
            label_text = item["label"]
            is_checked  = item["checked"]
            idx         = item["index"]

            if not label_text:
                logger.warning(f"    Checkbox #{idx} sin etiqueta, omitiendo")
                continue

            should_check = self._normalize(label_text) in target_normalized

            if should_check == is_checked:
                logger.info(f"    [ACCION] {label_text} → Sin cambios (ya en estado deseado)")
                continue

            try:
                cb_el = checkboxes_els[idx]
                
                # Hacer clic en el checkbox tal como lo haría un usuario
                # (dispara todos los eventos del navegador de forma nativa)
                self.driver.execute_script("arguments[0].scrollIntoView(true);", cb_el)
                try:
                    cb_el.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", cb_el)

                # Verificar si el estado cambió correctamente
                now_checked = self.driver.execute_script("return arguments[0].checked;", cb_el)
                
                # Si no cambió, intentar clickear sobre la etiqueta label asociada
                if now_checked != should_check:
                    self.driver.execute_script("""
                        var cb = arguments[0];
                        var label = cb.labels ? cb.labels[0] : null;
                        if (!label && cb.id) label = document.querySelector('label[for="' + cb.id + '"]');
                        if (!label) label = cb.nextElementSibling;
                        if (label) label.click();
                        else cb.click();
                    """, cb_el)

                if should_check:
                    changes.append(f"✓ {label_text}")
                    logger.info(f"    [MARCADO]    {label_text}")
                else:
                    changes.append(f"✗ {label_text}")
                    logger.info(f"    [DESMARCADO] {label_text}")
                time.sleep(0.2)
            except (IndexError, Exception) as err:
                logger.warning(f"    No se pudo acceder al checkbox #{idx} ({label_text}): {err}")

        # Guardar: obtener el botón e inspeccionar
        save_btn = self._find_save_button_js()
        btn_html = save_btn.get_attribute("outerHTML")
        logger.info(f"    Guardando cambios. Botón HTML: {btn_html}")

        # Ejecutar clic con Selenium nativo, ActionChains y JS de respaldo
        try:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).move_to_element(save_btn).click().perform()
        except Exception as e:
            logger.debug(f"    ActionChains click fallo, usando click directo: {e}")
            try:
                save_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", save_btn)

        # Esperar a que el modal se oculte (confirmación de guardado servidor)
        try:
            self.wait.until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, ".modal.show, .modal[style*='display: block']")
                )
            )
            logger.info("    Modal cerrado exitosamente tras guardar")
        except TimeoutException:
            logger.warning("    El modal no se cerró automáticamente tras 20s. Forzando espera...")
            time.sleep(3)

        cambios_str = ", ".join(changes) if changes else "sin cambios"
        logger.info(f"  ✓ {agent_name} guardado — Cambios: {cambios_str}")


    def _get_checkbox_label(self, checkbox, container) -> str | None:
        """Intenta obtener el texto de la etiqueta asociada a un checkbox."""
        # Estrategia 1: label con atributo for
        cb_id = checkbox.get_attribute("id")
        if cb_id:
            try:
                label = container.find_element(By.CSS_SELECTOR, f"label[for='{cb_id}']")
                text = label.text.strip()
                if text:
                    return text
            except NoSuchElementException:
                pass

        # Estrategia 2: label hermano inmediato
        try:
            label = checkbox.find_element(By.XPATH, "following-sibling::label[1]")
            text = label.text.strip()
            if text:
                return text
        except NoSuchElementException:
            pass

        # Estrategia 3: texto del elemento padre
        try:
            parent = checkbox.find_element(By.XPATH, "..")
            text = parent.text.strip()
            if text:
                return text
        except NoSuchElementException:
            pass

        return None

    def _find_save_button_js(self):
        """Busca el botón Guardar dentro del modal utilizando JavaScript."""
        save_btn = self.driver.execute_script("""
            var modal = document.querySelector('.modal.show') ||
                        document.querySelector('.modal[style*="display: block"]') ||
                        document.querySelector('.modal[style*="display:block"]');
            if (!modal) return null;
            var buttons = modal.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.trim().toLowerCase().includes('guardar')) {
                    return buttons[i];
                }
            }
            return modal.querySelector('button.btn-primary') || null;
        """)
        if not save_btn:
            raise NoSuchElementException("No se encontró el botón 'Guardar' en el modal")
        return save_btn

    def _find_save_button(self, modal):
        """Busca el botón Guardar dentro del modal."""
        selectors = [
            ".//button[normalize-space(text())='Guardar']",
            ".//button[contains(text(),'Guardar')]",
            ".//button[contains(@class,'btn-primary')]",
        ]
        for selector in selectors:
            try:
                btn = modal.find_element(By.XPATH, selector)
                if btn.is_displayed():
                    return btn
            except NoSuchElementException:
                continue
        raise NoSuchElementException("No se encontró el botón 'Guardar' en el modal")

    def _safe_close_modal(self):
        """Intenta cerrar cualquier modal abierto de forma segura."""
        try:
            close_btn = self.driver.find_element(
                By.CSS_SELECTOR, ".modal.show .btn-close, .modal.show [data-dismiss='modal'], .modal.show .close"
            )
            close_btn.click()
            time.sleep(0.5)
        except Exception:
            try:
                cancel_btn = self.driver.find_element(
                    By.XPATH, "//div[contains(@class,'modal')]//button[contains(text(),'Cancelar')]"
                )
                cancel_btn.click()
                time.sleep(0.5)
            except Exception:
                pass
