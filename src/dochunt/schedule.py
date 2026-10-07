"""Local-calendar eligibility, without automatic backfill."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScheduleDecision:
    due: bool
    reason: str
    issue_key: str


def due(config, now_utc, states, *, manual=False):
    if now_utc.tzinfo is None: raise ValueError('An aware UTC clock is required')
    local=now_utc.astimezone(ZoneInfo(config.schedule.timezone))
    key=f'{config.identity.id}:{local.date().isoformat()}'
    def decision(ok, reason): return ScheduleDecision(ok,reason,key)
    if key in states.get('completed',{}): return decision(False,'completed')
    for record in states.get('attempts',{}).values():
        p=record['prepared']
        if p['preview_test']: continue
        if record['status']=='unknown' or (record['status']=='sending' and p['provider']=='smtp'):
            return decision(False,'unresolved delivery; manual provider investigation required')
        if record['status']!='accepted':
            if p['issue']['issue_key']!=key: return decision(False,'unresolved older issue; no automatic backfill')
            prepared=datetime.fromisoformat(p['prepared_at'])
            if now_utc.astimezone(timezone.utc)-prepared>=timedelta(hours=24):
                return decision(False,'idempotency window expired')
    if manual: return decision(True,'manual current-date recovery')
    if not config.schedule.enabled: return decision(False,'disabled')
    if local.weekday() not in config.schedule.weekdays: return decision(False,'weekday excluded')
    preferred=datetime.combine(local.date(),time.fromisoformat(config.schedule.preferred_time),local.tzinfo)
    # fold=0 picks the first repeated wall time. A nonexistent spring time is
    # mapped forward through UTC; the four-hour window is elapsed time.
    preferred=preferred.astimezone(timezone.utc)
    elapsed=now_utc.astimezone(timezone.utc)-preferred
    if elapsed<timedelta(): return decision(False,'before preferred time')
    if elapsed>=timedelta(hours=4): return decision(False,'window expired')
    return decision(True,'due')
