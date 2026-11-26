"""The timer Worker and DocHunt's schedule check must agree on the send instant, clock changes included."""
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo
import pytest

CASES = [(zone, day, t)
         # Not Africa/Casablanca: its Ramadan-based changes are predictions that differ between the
         # time-zone data bundled with Node and the copy Python reads, so the runtimes disagree on data.
         for zone in ('America/Los_Angeles', 'America/Chicago', 'Asia/Kolkata', 'Australia/Lord_Howe', 'Europe/London', 'Asia/Tokyo')
         for day in ('2026-01-15', '2026-03-08', '2026-03-29', '2026-04-05', '2026-07-15', '2026-10-25', '2026-11-01')
         for t in ('00:00', '01:30', '01:45', '02:30', '06:00', '07:30', '23:55')]


def python_instant(zone, day, t):
    """The instant schedule.due() treats as preferred_time (fold=0, mapped through UTC)."""
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(t), ZoneInfo(zone)).astimezone(timezone.utc)


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed (CI installs it)')
def test_timer_send_instant_matches_the_schedule_check():
    script = ("import { sendInstant } from './timer/index.mjs';"
              "const cases = JSON.parse(process.argv[1]);"
              "console.log(JSON.stringify(cases.map(([z, d, t]) => { const [y, m, dd] = d.split('-').map(Number);"
              " const [h, mi] = t.split(':').map(Number); return new Date(sendInstant(y, m, dd, h * 60 + mi, z)).toISOString(); })));")
    out = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(CASES)], capture_output=True, text=True,
                         cwd=Path(__file__).parents[1], check=True).stdout
    for (zone, day, t), js in zip(CASES, json.loads(out)):
        expected = python_instant(zone, day, t)
        assert datetime.fromisoformat(js.replace('Z', '+00:00')) == expected, (zone, day, t, js, expected)


def test_due_uses_the_same_rule():
    """Guard the Python side of the parity: due() opens its window at python_instant()."""
    from dataclasses import replace
    from support import config
    from dochunt.schedule import due
    c = config()
    c.schedule = replace(c.schedule, enabled=True, timezone='America/Los_Angeles', preferred_time='02:30', weekdays=[6])
    opens = python_instant('America/Los_Angeles', '2026-03-08', '02:30')
    assert not due(c, opens - timedelta(minutes=1), {}).due
    assert due(c, opens, {}).due
