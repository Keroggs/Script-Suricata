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
SPECIAL_DAYS = [5, 6, 7, 15, 16, 17]

# ----------------------------------------------------------------------------
# Configuración de departamentos
# Base: siempre activos en horario laboral (días normales)
# Especial: base + Cashea (días especiales)
# Todos: lista completa para poder desmarcar los que no aplican
# ----------------------------------------------------------------------------
BASE_DEPARTMENTS = ["Ventas", "Atención al Cliente"]
SPECIAL_DEPARTMENTS = ["cashea", "Ventas", "Atención al Cliente"]
ALL_DEPARTMENTS = ["Soporte", "Atención al Cliente", "Ventas", "Cobranzas", "cashea"]

# ----------------------------------------------------------------------------
# Programación de guardias para el mes de Agosto
# Mapeo de día del mes (1-31) a la lista de agentes asignados según cuadrante.
# ----------------------------------------------------------------------------
GUARD_SCHEDULE = {
    1:  ["DAISY DESIREE VALBUENA GOMEZ"],
    2:  ["Ruby Molleja", "Carlos Rodriguez"],
    3:  ["ANA GALENO"],
    4:  ["Michelle Pereira"],
    5:  ["ANA GALENO", "Anglly Moron", "Jaime Palma", "Ana Blanco", "Arianna Segura"],
    6:  ["Ruby Molleja", "Carlos Rodriguez", "Yenireth Leal", "Merlina Cuicas"],
    7:  ["DAISY DESIREE VALBUENA GOMEZ", "ANA GALENO", "Anglly Moron", "Jaime Palma"],
    8:  ["Ana Blanco"],
    9:  ["Michelle Pereira", "Arianna Segura"],
    10: ["Yenireth Leal"],
    11: ["Merlina Cuicas"],
    12: ["Carlos Rodriguez"],
    13: ["Ruby Molleja"],
    14: ["DAISY DESIREE VALBUENA GOMEZ"],
    15: ["ANA GALENO", "Anglly Moron", "Jaime Palma", "Ana Blanco", "Merlina Cuicas"],
    16: ["DAISY DESIREE VALBUENA GOMEZ", "Ruby Molleja", "Carlos Rodriguez", "ANA GALENO", "Michelle Pereira", "Arianna Segura", "Yenireth Leal", "Merlina Cuicas"],
    17: ["Anglly Moron", "Jaime Palma", "Ana Blanco", "Michelle Pereira"],
    18: ["Arianna Segura"],
    19: ["Yenireth Leal"],
    20: ["Merlina Cuicas"],
    21: ["DAISY DESIREE VALBUENA GOMEZ"],
    22: ["Ruby Molleja"],
    23: ["DAISY DESIREE VALBUENA GOMEZ", "Carlos Rodriguez"],
    24: ["Anglly Moron"],
    25: ["Jaime Palma"],
    26: ["Ana Blanco"],
    27: ["Michelle Pereira"],
    28: ["Arianna Segura"],
    29: ["Yenireth Leal"],
    30: ["DAISY DESIREE VALBUENA GOMEZ", "Merlina Cuicas"],
    31: ["Ruby Molleja"],
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
# Intervalo de verificación (segundos)
# ----------------------------------------------------------------------------
CHECK_INTERVAL = 300     # 5 minutos

# ----------------------------------------------------------------------------
# URLs
# ----------------------------------------------------------------------------
BASE_URL = "https://datavoip.suricata.cloud"
AGENTS_URL = "https://datavoip.suricata.cloud/agentes2"
LOGIN_URL = "https://datavoip.suricata.cloud"
