"""Общие правила выгрузки: запись времени и ориентация колец полигонов.

Время. Внутри конвейера время — секунды от начала работ. Наружу оно уходит
двумя способами (docs/export_format.md §2):

* начало работ задано (`Scenario.start_time`) — абсолютное время UTC в форме
  `YYYY-MM-DDThh:mm:ssZ`. Это `dateTime` XML Schema с точностью до секунды —
  ровно то, что читают `TimeStamp`, `TimeSpan` и `gx:Track` в KML и что без
  потерь понимает ISO 8601. Доли секунды отбрасываются округлением: их нет смысла
  выдавать по плану, построенному с шагом в секунды;
* начало работ не задано — относительное время «T+чч:мм:сс». Подставлять
  «сейчас» нельзя: один сценарий давал бы разные файлы (AGENT.md §8).

Кольца. RFC 7946 (§3.1.6) требует: внешнее кольцо — против часовой стрелки,
отверстия — по часовой. Справочник KML требует того же от полигонов: Google
Earth заливает только лицевую сторону. Ориентация входных полигонов сценария
не гарантирована, поэтому выгрузка выставляет её сама.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from core.geo.mask import as_utc

_HALF_SECOND = timedelta(microseconds=500_000)

#: Формат абсолютного времени в выгрузке: XML Schema `dateTime`, UTC, секунды.
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_moment(start_time: datetime, offset_s: float) -> datetime:
    """Момент от начала работ в UTC, округлённый до целой секунды.

    Args:
        start_time: начало работ; время без пояса считается UTC.
        offset_s: смещение от начала работ, секунды.

    Returns:
        Момент в поясе UTC без долей секунды.
    """
    moment = as_utc(start_time).astimezone(timezone.utc) + timedelta(seconds=offset_s)
    return (moment + _HALF_SECOND).replace(microsecond=0)


def utc_time(start_time: datetime | None, offset_s: float) -> str | None:
    """Абсолютное время UTC `YYYY-MM-DDThh:mm:ssZ`, если начало работ задано.

    Args:
        start_time: начало работ; None — не задано.
        offset_s: смещение от начала работ, секунды.

    Returns:
        Строка времени либо None, если начало работ не задано.
    """
    if start_time is None:
        return None
    return utc_moment(start_time, offset_s).strftime(UTC_FORMAT)


def relative_time(offset_s: float) -> str:
    """Время от начала работ «T+чч:мм:сс»; часы не ограничены сутками.

    Args:
        offset_s: смещение от начала работ, секунды (не меньше нуля).

    Returns:
        Строка вида `T+01:12:30`.
    """
    total_s = max(0, round(offset_s))
    hours, rest = divmod(total_s, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"T+{hours:02d}:{minutes:02d}:{seconds:02d}"


def time_label(start_time: datetime | None, offset_s: float) -> str:
    """Подпись момента для человека: «06:12:30 UTC» или «T+01:12:30».

    Если момент приходится не на день начала работ, к времени добавляется дата
    «21.09», иначе подпись двусмысленна у плана длиннее суток.

    Args:
        start_time: начало работ; None — не задано.
        offset_s: смещение от начала работ, секунды.

    Returns:
        Подпись для имени или описания объекта.
    """
    if start_time is None:
        return relative_time(offset_s)
    moment = utc_moment(start_time, offset_s)
    first_day = utc_moment(start_time, 0.0).date()
    if moment.date() == first_day:
        return moment.strftime("%H:%M:%S UTC")
    return moment.strftime("%d.%m %H:%M:%S UTC")


def span_label(start_time: datetime | None, begin_s: float, end_s: float) -> str:
    """Подпись интервала: «06:00:00–06:44:10 UTC» или «T+00:00:00–T+00:44:10»."""
    begin = time_label(start_time, begin_s)
    end = time_label(start_time, end_s)
    if start_time is not None and begin.endswith(" UTC"):
        begin = begin.removesuffix(" UTC")
    return f"{begin}–{end}"


def _twice_signed_area(ring: Sequence[tuple[float, float]]) -> float:
    """Удвоенная площадь кольца со знаком (формула шнурков): плюс — против часовой.

    Нужен только знак. Долгота растёт на восток, широта на север — ориентация
    в градусах та же, что в метрах рабочей проекции, поэтому считать площадь в
    метрах ради знака не нужно, и инвариант 1 не затронут.
    """
    return sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, [*ring[1:], ring[0]], strict=True)
    )


def oriented_rings(
    rings: Iterable[Iterable[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """Замкнуть кольца и выставить ориентацию по RFC 7946 и справочнику KML.

    Args:
        rings: внешнее кольцо, затем отверстия; пары (долгота, широта).

    Returns:
        Замкнутые кольца: внешнее — против часовой стрелки, отверстия — по часовой.
    """
    result: list[list[tuple[float, float]]] = []
    for index, ring in enumerate(rings):
        coords = list(ring)
        if coords and coords[0] == coords[-1]:
            coords.pop()
        counterclockwise = _twice_signed_area(coords) > 0.0
        if counterclockwise != (index == 0):
            coords.reverse()
        coords.append(coords[0])
        result.append(coords)
    return result


__all__ = [
    "UTC_FORMAT",
    "oriented_rings",
    "relative_time",
    "span_label",
    "time_label",
    "utc_moment",
    "utc_time",
]
