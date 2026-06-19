"""Delivery schedule — slot generation with UTC+7 deadlines.

Алгоритм (ТЗ, раздел 9):
  Для каждой из ближайших N дат:
    1) ищем записи по конкретной дате → если есть, используем ТОЛЬКО их
       (тип «date» полностью перекрывает дефолт дня недели);
    2) иначе — дефолтные записи дня недели (тип «weekday»);
    3) для каждой записи проверяем now(UTC+7) ≤ deadline_dt;
    4) генерируем слоты из диапазона delivery_start..delivery_end / интервал.
  День без подходящих записей = выходной (has_slots=False).

Все сравнения времени — в UTC+7 (Томск). В БД delivery_start/end и
deadline_time хранятся как «настенное» время Томска, так что сравниваем
их с локальным now Томска напрямую.
"""
from datetime import date as Date, datetime, time as Time, timedelta
from zoneinfo import ZoneInfo

from app.models import ScheduleEntry, ScheduleEntryType

TOMSK_TZ = ZoneInfo("Asia/Tomsk")  # UTC+7
HORIZON_DAYS = 14


def now_tomsk() -> datetime:
    """Текущее «настенное» время Томска (naive, для сравнения с naive-полями БД)."""
    return datetime.now(TOMSK_TZ).replace(tzinfo=None)


def _deadline_dt(target: Date, entry: ScheduleEntry) -> datetime:
    """deadline_dt = delivery_date − deadline_days_before + deadline_time."""
    deadline_day = target - timedelta(days=entry.deadline_days_before or 0)
    return datetime.combine(deadline_day, entry.deadline_time)


def _entries_for_date(target: Date, all_entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """Записи, применимые к дате: записи по конкретной дате перекрывают дефолт."""
    by_date = [
        e for e in all_entries
        if e.entry_type == ScheduleEntryType.date and e.specific_date == target
    ]
    if by_date:
        return by_date
    return [
        e for e in all_entries
        if e.entry_type == ScheduleEntryType.weekday and e.weekday == target.isoweekday()
    ]


def _slots_from_entry(target: Date, entry: ScheduleEntry) -> list[dict]:
    """Разбивает диапазон записи на слоты по интервалу. id слота = schedule_entry_id."""
    interval = timedelta(minutes=entry.slot_interval_min or 60)
    if interval <= timedelta(0):
        return []
    out: list[dict] = []
    current = datetime.combine(target, entry.delivery_start)
    end_dt = datetime.combine(target, entry.delivery_end)
    while current + interval <= end_dt:
        slot_end = current + interval
        start_s = current.strftime("%H:%M")
        end_s = slot_end.strftime("%H:%M")
        out.append({
            "id": entry.id,            # FK schedule_entry_id для заказа
            "schedule_entry_id": entry.id,
            "label": f"{start_s} – {end_s}",
            "start": start_s,
            "end": end_s,
        })
        current = slot_end
    return out


def generate_slots(target: Date, all_entries: list[ScheduleEntry]) -> list[dict]:
    """Все слоты на дату с учётом дедлайнов. Несколько записей на день объединяются."""
    now = now_tomsk()
    today = now.date()
    if target < today:
        return []

    slots: list[dict] = []
    for entry in _entries_for_date(target, all_entries):
        if now > _deadline_dt(target, entry):
            continue
        slots.extend(_slots_from_entry(target, entry))

    # сортируем по времени начала, убираем точные дубли по (start, end)
    slots.sort(key=lambda s: (s["start"], s["end"]))
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for s in slots:
        key = (s["start"], s["end"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def available_days(all_entries: list[ScheduleEntry]) -> list[dict]:
    """Ближайшие HORIZON_DAYS дат с флагом has_slots (день без слотов = выходной)."""
    today = now_tomsk().date()
    days: list[dict] = []
    for delta in range(HORIZON_DAYS):
        d = today + timedelta(days=delta)
        has = len(generate_slots(d, all_entries)) > 0
        days.append({"date": d.isoformat(), "has_slots": has})
    return days
