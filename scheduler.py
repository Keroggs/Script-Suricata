"""
scheduler.py
Evalúa la hora actual y retorna el estado de departamentos requerido.
"""

import datetime
import logging
from config import (
    SPECIAL_DAYS,
    GUARD_SCHEDULE,
    BASE_DEPARTMENTS,
    SPECIAL_DEPARTMENTS,
    WORK_START_HOUR, WORK_START_MINUTE,
    WORK_END_HOUR, WORK_END_MINUTE,
    GUARD_START_HOUR, GUARD_START_MINUTE,
    GUARD_END_HOUR, GUARD_END_MINUTE,
    RESET_HOUR, RESET_MINUTE,
)

logger = logging.getLogger(__name__)


def get_target_state():
    """
    Determina qué departamentos deben estar activos según la hora actual.

    Retorna:
        dict con {"departments": [...], "reason": "...", "agents": [...]}
        o None si no hay cambio necesario en este momento.
    """
    now = datetime.datetime.now()
    current_time = now.time()
    day = now.day
    date_str = now.strftime("%Y-%m-%d")

    work_start  = datetime.time(WORK_START_HOUR,  WORK_START_MINUTE)
    work_end    = datetime.time(WORK_END_HOUR,    WORK_END_MINUTE)
    guard_start = datetime.time(GUARD_START_HOUR, GUARD_START_MINUTE)
    guard_end   = datetime.time(GUARD_END_HOUR,   GUARD_END_MINUTE)
    reset_time  = datetime.time(RESET_HOUR,       RESET_MINUTE)

    logger.debug(
        f"Evaluando estado — Hora: {current_time.strftime('%H:%M')} | "
        f"Día: {day} ({date_str})"
    )

    # Ventana laboral regular: 08:00 - 17:00
    if work_start <= current_time < work_end:
        if day in SPECIAL_DAYS:
            return {
                "departments": SPECIAL_DEPARTMENTS,
                "reason": f"Horario laboral — Día especial {day} (Cashea activado)",
            }
        else:
            return {
                "departments": BASE_DEPARTMENTS,
                "reason": f"Horario laboral — Día normal {day}",
            }

    # Ventana de guardia nocturna: 18:00 - 23:50
    if guard_start <= current_time < guard_end:
        # Buscar guardias programadas por número de día (ej: 15) o por fecha (ej: "2026-08-25")
        guard_agents = GUARD_SCHEDULE.get(day) or GUARD_SCHEDULE.get(date_str)
        if guard_agents:
            return {
                "departments": SPECIAL_DEPARTMENTS,
                "agents": guard_agents,
                "reason": f"Guardia nocturna (18:00 - 23:50) — Agentes: {', '.join(guard_agents)}",
            }

    # Ventana de reset: 23:50 en adelante
    if current_time >= reset_time:
        return {
            "departments": BASE_DEPARTMENTS,
            "reason": "Reset nocturno a las 23:50 — Estado inicial restaurado",
        }

    # Fuera de horario de acción: sin cambios
    return None
