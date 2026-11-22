"""Turn host input like '48h', 'Nov 24 8pm', 'fri 9pm' or '2026-11-24 20:00' into a timestamp."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}


class TimeError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TimeError(f"Unknown timezone “{name}”. Use a name like America/Chicago or Europe/London.")


DUR_RE = re.compile(r"^\+?\s*((?:\d+(?:\.\d+)?\s*(?:d|day|days|h|hr|hrs|hour|hours|m|min|mins|minutes?)\s*)+)$")
DUR_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(d|day|days|h|hr|hrs|hour|hours|m|min|mins|minutes?)")


def parse_duration(text: str) -> float | None:
    """'48h', '2d 6h', '90m', '+1.5h' -> seconds. None if it isn't a duration."""
    t = text.strip().lower()
    if not DUR_RE.match(t):
        return None
    total = 0.0
    for n, unit in DUR_PART.findall(t):
        n = float(n)
        total += n * (86400 if unit.startswith("d") else 60 if unit.startswith("m") else 3600)
    return total


def _clock(text: str) -> tuple[int, int] | None:
    """'8pm', '8:30 pm', '20:00', '2030' (24h) -> (hour, minute)."""
    t = text.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?(am|pm)?", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            return None
        h = (h % 12) + (12 if ap == "pm" else 0)
    elif m.group(2) is None and len(m.group(1)) <= 2 and h <= 12 and not ap:
        # a bare number like "8" is ambiguous; require am/pm or HH:MM
        return None
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi


def parse_when(text: str, now: float, tz_name: str) -> float:
    """Return a unix timestamp in the future, or raise TimeError with a helpful message."""
    raw = (text or "").strip()
    if not raw:
        raise TimeError("Give a time, like `48h`, `Nov 24 8pm` or `2026-11-24 20:00`.")
    dur = parse_duration(raw)
    if dur is not None:
        if dur <= 0:
            raise TimeError("The length has to be more than zero.")
        return now + dur
    tz = zone(tz_name)
    local_now = datetime.fromtimestamp(now, tz)
    t = raw.lower().replace(",", " ").replace(" at ", " ")
    t = re.sub(r"\s+", " ", t).strip()
    date = None
    rest = t

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ t]?(.*)$", t)                 # 2026-11-24 20:00
    if m:
        date = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        rest = m.group(4)
    if date is None:
        m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))? ?(.*)$", t)          # 11/24 8pm (US order)
        if m:
            y = int(m.group(3)) if m.group(3) else local_now.year
            if y < 100:
                y += 2000
            date = (y, int(m.group(1)), int(m.group(2)))
            rest = m.group(4)
            if not m.group(3) and datetime(y, date[1], min(date[2], 28), tzinfo=tz) < local_now - timedelta(days=1):
                date = (y + 1, date[1], date[2])
    if date is None:
        m = re.match(r"^([a-z]{3})[a-z]* (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))? ?(.*)$", t)   # nov 24 8pm
        if m and m.group(1) in MONTHS:
            y = int(m.group(3)) if m.group(3) else local_now.year
            date = (y, MONTHS[m.group(1)], int(m.group(2)))
            rest = m.group(4)
            if not m.group(3) and datetime(y, date[1], min(date[2], 28), tzinfo=tz) < local_now - timedelta(days=1):
                date = (y + 1, date[1], date[2])
    if date is None:
        m = re.match(r"^(today|tonight|tomorrow|tmrw|[a-z]{3})[a-z]* ?(.*)$", t)  # tomorrow 8pm, fri 9pm
        if m:
            word = m.group(1)
            if word in ("today", "tonight"):
                d = local_now.date()
            elif word in ("tomorrow", "tmrw"):
                d = local_now.date() + timedelta(days=1)
            elif word in DAYS:
                ahead = (DAYS[word] - local_now.weekday()) % 7
                d = local_now.date() + timedelta(days=ahead)
            else:
                d = None
            if d is not None:
                date = (d.year, d.month, d.day)
                rest = m.group(2)

    clock = _clock(rest) if rest else None
    if rest and clock is None:
        raise TimeError(f"I couldn't read the time in “{raw}”. Try `8pm`, `8:30pm` or `20:00`.")
    if date is None:
        if clock is None:
            raise TimeError(f"I couldn't read “{raw}”. Try `48h`, `Nov 24 8pm`, `fri 9pm` or `2026-11-24 20:00`.")
        cand = local_now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
        if cand <= local_now:
            cand += timedelta(days=1)
        return cand.timestamp()
    h, mi = clock or (23, 59)
    try:
        dt = datetime(date[0], date[1], date[2], h, mi, tzinfo=tz)
    except ValueError:
        raise TimeError(f"“{raw}” isn't a real date.")
    if dt.timestamp() <= now and date and re.match(r"^[a-z]{3}", t) and t[:3] in DAYS:
        dt += timedelta(days=7)          # "fri 9pm" said on Friday after 9pm -> next Friday
    if dt.timestamp() <= now:
        raise TimeError(f"{dt.strftime('%b %d %I:%M %p')} has already passed.")
    return dt.timestamp()


def default_deadline(now: float, hours: float, clock: str, tz_name: str) -> float | None:
    """A phase length in hours, optionally moved to the nearest given clock time (e.g. '20:00')."""
    if not hours or hours <= 0:
        return None
    target = now + hours * 3600
    hm = _clock(clock) if clock else None
    if not hm:
        return target
    tz = zone(tz_name)
    local = datetime.fromtimestamp(target, tz)
    best = None
    for shift in (-1, 0, 1):
        cand = (local + timedelta(days=shift)).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand.timestamp() <= now + 3600:      # never less than an hour away
            continue
        if best is None or abs(cand.timestamp() - target) < abs(best.timestamp() - target):
            best = cand
    return best.timestamp() if best else target


def stamp(ts: float, style: str = "F") -> str:
    """Discord timestamp: each reader sees it in their own timezone."""
    return f"<t:{int(ts)}:{style}>"
