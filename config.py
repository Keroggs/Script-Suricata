# =============================================================================
# Configuración del Suricata Agent Scheduler
# Modifica este archivo para ajustar agentes, horarios y departamentos
# =============================================================================

# ----------------------------------------------------------------------------
# Agentes a gestionar (coincidencia por nombre completo, sin distinción de mayúsculas)
# ----------------------------------------------------------------------------
TARGET_AGENTS = [
    "Merlina Cuicas",
    "ANA GALENO",
    "Arianna Segura",
    "Michelle Pereira",
]

# ----------------------------------------------------------------------------
# Días del mes en que se agrega Cashea al horario laboral
# ----------------------------------------------------------------------------
SPECIAL_DAYS = [5, 6, 7, 15, 16, 17, 25, 26]

# ----------------------------------------------------------------------------
# Configuración de departamentos
# Base: siempre activos en horario laboral (días normales)
# Especial: base + Cashea (días especiales)
# Todos: lista completa para poder desmarcar los que no aplican
# ----------------------------------------------------------------------------
BASE_DEPARTMENTS = ["Ventas", "Atención al Cliente"]
SPECIAL_DEPARTMENTS = ["cashea", "Ventas", "Atención al Cliente", "Cobranzas"]
ALL_DEPARTMENTS = ["Soporte", "Atención al Cliente", "Ventas", "Cobranzas", "cashea"]

# ----------------------------------------------------------------------------
# Programación de guardias
# Mapeo de FECHA EXACTA ("YYYY-MM-DD") a la lista de agentes asignados.
# Se usa fecha completa (y no día del mes) para que el cuadrante de un mes
# no se aplique por accidente al mes siguiente.
# ----------------------------------------------------------------------------
GUARD_SCHEDULE = {
    "2026-09-01": ["Merlina Cuicas"],
    "2026-09-02": ["Yenireth Leal"],
    "2026-09-03": ["ANA GALENO"],
    "2026-09-04": ["Jaime Palma"],
    "2026-09-05": ["Anglly Moron", "Ana Blanco", "Michelle Pereira", "Arianna Segura", "Gleidys Valladares", "Marilyn Vasquez"],
    "2026-09-06": ["Ruby Molleja", "Carlos Rodriguez", "ANA GALENO", "Ana Blanco", "Michelle Pereira", "Arianna Segura", "Merlina Cuicas", "Gleidys Valladares", "Marilyn Vasquez"],
    "2026-09-07": ["Ruby Molleja", "Carlos Rodriguez", "Anglly Moron", "Jaime Palma", "Yenireth Leal", "Gleidys Valladares", "Marilyn Vasquez"],
    "2026-09-08": ["Merlina Cuicas"],
    "2026-09-09": ["Arianna Segura"],
    "2026-09-10": ["Michelle Pereira"],
    "2026-09-11": ["Ana Blanco"],
    "2026-09-12": ["Jaime Palma"],
    "2026-09-13": ["DAISY DESIREE VALBUENA GOMEZ", "Merlina Cuicas"],
    "2026-09-14": ["ANA GALENO"],
    "2026-09-15": ["Ruby Molleja", "Arianna Segura", "Yenireth Leal", "Merlina Cuicas", "Gleidys Valladares", "Romina Brizuela", "Marilyn Vasquez", "Luis Cordero"],
    "2026-09-16": ["ANA GALENO", "Anglly Moron", "Jaime Palma", "Gleidys Valladares", "Romina Brizuela", "Marilyn Vasquez", "Luis Cordero"],
    "2026-09-17": ["DAISY DESIREE VALBUENA GOMEZ", "Carlos Rodriguez", "Ana Blanco", "Michelle Pereira", "Yenireth Leal", "Gleidys Valladares", "Romina Brizuela", "Marilyn Vasquez", "Luis Cordero"],
    "2026-09-18": ["Arianna Segura"],
    "2026-09-19": ["Michelle Pereira"],
    "2026-09-20": ["ANA GALENO", "Anglly Moron"],
    "2026-09-21": ["Jaime Palma"],
    "2026-09-22": ["Carlos Rodriguez"],
    "2026-09-23": ["Yenireth Leal"],
    "2026-09-24": ["DAISY DESIREE VALBUENA GOMEZ"],
    "2026-09-25": ["Ruby Molleja", "Gleidys Valladares", "Marilyn Vasquez", "Luis Cordero"],
    "2026-09-26": ["Carlos Rodriguez", "Romina Brizuela", "Marilyn Vasquez", "Luis Cordero"],
    "2026-09-27": ["DAISY DESIREE VALBUENA GOMEZ", "Anglly Moron"],
    "2026-09-28": ["Arianna Segura"],
    "2026-09-29": ["Michelle Pereira"],
    "2026-09-30": ["DAISY DESIREE VALBUENA GOMEZ"],
}

GUARD_START_HOUR = 18    # 06:00 PM — inicio de guardia
GUARD_START_MINUTE = 0

GUARD_END_HOUR = 23      # 11:50 PM — fin de guardia
GUARD_END_MINUTE = 50

# ----------------------------------------------------------------------------
# Horarios de turno diurno regular
# ----------------------------------------------------------------------------
WORK_START_HOUR = 8      # 08:00 AM — inicio del turno laboral
WORK_START_MINUTE = 0

WORK_END_HOUR = 17       # 05:00 PM — fin del turno laboral
WORK_END_MINUTE = 0

RESET_HOUR = 23          # 11:50 PM — reset al estado inicial
RESET_MINUTE = 50

# ----------------------------------------------------------------------------
# Intervalo máximo de verificación (segundos)
# El scheduler duerme hasta la próxima transición de horario; este valor solo
# acota esa espera como red de seguridad ante cambios de hora del sistema.
# ----------------------------------------------------------------------------
CHECK_INTERVAL = 300

# ----------------------------------------------------------------------------
# Reintentos ante fallos del navegador (backoff exponencial)
# ----------------------------------------------------------------------------
MAX_RETRIES = 5              # intentos máximos por estado antes de rendirse
RETRY_BACKOFF_SECONDS = 60   # espera inicial; se duplica en cada fallo
RETRY_BACKOFF_MAX = 900      # tope de la espera entre reintentos (15 min)

# ----------------------------------------------------------------------------
# Zona horaria
#
# CRÍTICO: todo el script decide en base a la hora LOCAL del sistema. Una VM de
# Ubuntu recién instalada usa UTC por defecto, lo que correría todos los
# horarios 4 horas (Venezuela es UTC-4) y las guardias se aplicarían al día
# equivocado. Al arrancar, main.py fuerza esta zona horaria en el proceso, así
# que el script funciona correctamente aunque el sistema esté en UTC.
#
# Poner None para usar la zona horaria del sistema tal cual.
# ----------------------------------------------------------------------------
TIMEZONE = "America/Caracas"

# ----------------------------------------------------------------------------
# Estado persistente (sobrevive a reinicios del servicio)
#
# Las rutas relativas se resuelven contra el directorio de este archivo, no
# contra el directorio de trabajo actual, para que el servicio escriba siempre
# en el mismo sitio sin depender de WorkingDirectory.
# ----------------------------------------------------------------------------
STATE_FILE = "logs/.last_state"
LOG_DIR = "logs"

# ----------------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------------
BASE_URL = "https://datavoip.suricata.cloud"
AGENTS_URL = "https://datavoip.suricata.cloud/agentes2"
LOGIN_URL = "https://datavoip.suricata.cloud"
