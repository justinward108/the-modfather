from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from modfather.timeparse import TimeError, default_deadline, parse_duration, parse_when

TZ = "America/Chicago"
CT = ZoneInfo(TZ)
# Sunday 2026-11-22 14:50 Central
NOW = datetime(2026, 11, 22, 14, 50, tzinfo=CT).timestamp()


def local(ts):
    return datetime.fromtimestamp(ts, CT).strftime("%Y-%m-%d %H:%M")


def test_durations():
    assert parse_duration("48h") == 48 * 3600
    assert parse_duration("2d 6h") == 54 * 3600
    assert parse_duration("90m") == 5400
    assert parse_duration("+1.5h") == 5400
    assert parse_duration("nov 24") is None
    assert parse_when("24h", NOW, TZ) == NOW + 86400


@pytest.mark.parametrize("text,expected", [
    ("2026-11-24 20:00", "2026-11-24 20:00"),
    ("2026-11-24 8pm", "2026-11-24 20:00"),
    ("11/24 8pm", "2026-11-24 20:00"),
    ("11/24/2026 8:30 pm", "2026-11-24 20:30"),
    ("Nov 24 8pm", "2026-11-24 20:00"),
    ("november 24th at 8:00pm", "2026-11-24 20:00"),
    ("tomorrow 9pm", "2026-11-23 21:00"),
    ("tonight 10pm", "2026-11-22 22:00"),
    ("fri 9pm", "2026-11-27 21:00"),
    ("sun 2pm", "2026-11-29 14:00"),          # today is Sunday and 2pm has passed -> next week
    ("8pm", "2026-11-22 20:00"),
    ("2pm", "2026-11-23 14:00"),              # already passed today -> tomorrow
    ("20:00", "2026-11-22 20:00"),
    ("jan 3 noon".replace("noon", "12pm"), "2027-01-03 12:00"),   # month in the past -> next year
])
def test_dates(text, expected):
    assert local(parse_when(text, NOW, TZ)) == expected


@pytest.mark.parametrize("text", ["", "soon", "nov 31 8pm", "2026-11-20 8pm", "8", "fri 25pm"])
def test_bad_input(text):
    with pytest.raises(TimeError):
        parse_when(text, NOW, TZ)


def test_dst_and_other_zone():
    # 2027-03-14 is the US DST change; 8pm that day is CDT (UTC-5)
    ts = parse_when("2027-03-14 20:00", NOW, TZ)
    assert datetime.fromtimestamp(ts, ZoneInfo("UTC")).hour == 1
    ts = parse_when("2026-11-24 20:00", NOW, "Europe/London")
    assert datetime.fromtimestamp(ts, ZoneInfo("UTC")).hour == 20


def test_default_deadline_clock_rounding():
    assert default_deadline(NOW, 0, "", TZ) is None
    assert default_deadline(NOW, 48, "", TZ) == NOW + 48 * 3600
    # 48h from Sun 14:50 is Tue 14:50; nearest 20:00 is Tue 20:00
    assert local(default_deadline(NOW, 48, "20:00", TZ)) == "2026-11-24 20:00"
    # 24h -> Mon 14:50; nearest 8am is Mon 08:00
    assert local(default_deadline(NOW, 24, "8am", TZ)) == "2026-11-23 08:00"
    # short nights never land less than an hour away
    assert default_deadline(NOW, 0.5, "15:00", TZ) - NOW >= 3600
