"""
scheduler.py
Evalúa la hora actual y retorna el estado de departamentos requerido.

Además expone `next_transition_time()`, que permite al bucle principal dormir
hasta el próximo cambio de horario en vez de sondear el reloj constantemente.
"""

import datetime
import logging

from config import (
    SPECIAL_DAYS,
    GUARD_SCHEDULE,
    TARGET_AGENTS,
    BASE_DEPARTMENTS,
    SPECIAL_DEPARTMENTS,
    WORK_START_HOUR, WORK_START_MINUTE,
    WORK_END_HOUR, WORK_END_MINUTE,
    GUARD_START_HOUR, GUARD_START_MINUTE,
    GUARD_END_HOUR, GUARD_END_MINUTE,
    RESET_HOUR, RESET_MINUTE,
)

logger = logging.getLogger(__name__)

# Los límites de las ventanas se calculan una sola vez al importar el módulo.
WORK_START  = datetime.time(WORK_START_HOUR,  WORK_START_MINUTE)
WORK_END    = datetime.time(WORK_END_HOUR,    WORK_END_MINUTE)
GUARD_START = datetime.time(GUARD_START_HOUR, GUARD_START_MINUTE)
GUARD_END   = datetime.time(GUARD_END_HOUR,   GUARD_END_MINUTE)
RESET_TIME  = datetime.time(RESET_HOUR,       RESET_MINUTE)

# Instantes en que el estado objetivo puede cambiar, en orden cronológico.
_BOUNDARIES = sorted({WORK_START, WORK_END, GUARD_START, GUARD_END, RESET_TIME})


def _dedupe(names):
    """Elimina duplicados preservando el orden y sin distinguir mayúsculas."""
    seen = set()
    result = []
    for name in names:
        key = name.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result


def get_guard_agents(date_str: str) -> list[str]:
    """Retorna los agentes de guardia para una fecha 'YYYY-MM-DD' (lista vacía si no hay)."""
    return GUARD_SCHEDULE.get(date_str, [])


def get_target_state(now: datetime.datetime | None = None):
    """
    Determina qué departamentos deben estar activos según la hora actual.

    Args:
        now: momento a evaluar. Por defecto la hora actual del sistema
             (parametrizable para poder probar la lógica sin esperar).

    Retorna:
        dict con {"key", "departments", "reason"} y opcionalmente "agents",
        o None si no hay cambio necesario en este momento.
    """
    now = now or datetime.datetime.now()
    current_time = now.time()
    day = now.day
    date_str = now.strftime("%Y-%m-%d")

    logger.debug(
        f"Evaluando estado — Hora: {current_time.strftime('%H:%M')} | "
        f"Día: {day} ({date_str})"
    )

    # Ventana laboral regular: 08:00 - 17:00
    if WORK_START <= current_time < WORK_END:
        if day in SPECIAL_DAYS:
            return {
                "key": f"WORK_{date_str}_SPECIAL",
                "departments": SPECIAL_DEPARTMENTS,
                "reason": f"Horario laboral — Día especial {day} (Cashea activado)",
            }
        return {
            "key": f"WORK_{date_str}_BASE",
            "departments": BASE_DEPARTMENTS,
            "reason": f"Horario laboral — Día normal {day}",
        }

    # Ventana de guardia nocturna: 18:00 - 23:50
    # Solo se tocan los agentes de guardia; los diurnos quedan como están.
    if GUARD_START <= current_time < GUARD_END:
        guard_agents = get_guard_agents(date_str)
        if guard_agents:
            agents_key = "-".join(sorted(guard_agents))
            return {
                "key": f"GUARD_{date_str}_{agents_key}",
                "departments": SPECIAL_DEPARTMENTS,
                "agents": guard_agents,
                "reason": f"Guardia nocturna (18:00 - 23:50) — Agentes: {', '.join(guard_agents)}",
            }

    # Ventana de reset: 23:50 en adelante.
    # Revierte a los agentes fijos Y a los que estuvieron de guardia ese día,
    # que de otro modo conservarían Cashea/Cobranzas indefinidamente.
    if current_time >= RESET_TIME:
        agents = _dedupe(list(TARGET_AGENTS) + get_guard_agents(date_str))
        return {
            "key": f"RESET_{date_str}",
            "departments": BASE_DEPARTMENTS,
            "agents": agents,
            "reason": (
                f"Reset nocturno a las {RESET_TIME.strftime('%H:%M')} — "
                f"Estado inicial restaurado para {len(agents)} agente(s)"
            ),
        }

    # Fuera de horario de acción: sin cambios
    return None


def next_transition_time(now: datetime.datetime | None = None) -> datetime.datetime:
    """
    Retorna el próximo instante en que el estado objetivo puede cambiar.

    Permite al bucle principal dormir hasta ese momento en lugar de despertarse
    cada pocos segundos solo para mirar el reloj.
    """
    now = now or datetime.datetime.now()
    today = now.date()

    for boundary in _BOUNDARIES:
        candidate = datetime.datetime.combine(today, boundary)
        if candidate > now:
            return candidate

    # Ya pasaron todas las fronteras de hoy: la próxima es la primera de mañana.
    tomorrow = today + datetime.timedelta(days=1)
    return datetime.datetime.combine(tomorrow, _BOUNDARIES[0])
