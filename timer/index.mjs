// DocHunt on-time timer (optional Cloudflare Worker).
//
// GitHub starts scheduled workflows only a few times a day, so this Worker starts the
// Document Hunt workflow at the newsroom's local send hour instead. It can do exactly one
// thing: dispatch `mode=scheduled` for one workflow on one branch. That run gets the same
// checks as GitHub's own schedule (enabled, weekday, send window, already sent), so an extra
// or repeated trigger is harmless. It has no HTTP endpoint that dispatches anything, and the
// GitHub token never leaves the Worker's secrets. It runs from its cron trigger only:
// wrangler.toml turns off the public workers.dev address.

function wall(ms, timeZone) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms));
  const get = type => Number(parts.find(p => p.type === type).value);
  return { y: get('year'), m: get('month'), d: get('day'), minutes: get('hour') * 60 + get('minute') };
}

export function localMinutes(date, timeZone) {
  return wall(date.getTime(), timeZone).minutes;
}

function offsetMs(ms, timeZone) {
  const w = wall(ms, timeZone);
  return Date.UTC(w.y, w.m - 1, w.d, 0, w.minutes) - Math.floor(ms / 60000) * 60000;
}

// The instant DocHunt's schedule check treats as SEND_TIME on a local date (Python's fold=0 rule):
// a time that occurs twice uses its first occurrence; a time skipped by a clock change uses the
// offset from before the change, which lands after the jump (02:30 becomes 03:30 in Los Angeles).
export function sendInstant(y, m, d, send, timeZone) {
  const wallAsUtc = Date.UTC(y, m - 1, d, 0, send);
  const before = wallAsUtc - offsetMs(wallAsUtc - 864e5, timeZone);
  const after = wallAsUtc - offsetMs(wallAsUtc + 864e5, timeZone);
  const real = [before, after].filter(ms => {
    const w = wall(ms, timeZone);
    return w.y === y && w.m === m && w.d === d && w.minutes === send;
  });
  return real.length ? Math.min(...real) : before;
}

export function shouldDispatch(date, env) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(env.SEND_TIME || '');
  if (!match) throw new Error('SEND_TIME must be HH:MM');
  const send = Number(match[1]) * 60 + Number(match[2]);
  if (send > 23 * 60 + 55) throw new Error('SEND_TIME must be 23:55 or earlier (a later wake-up falls on the next day)');
  // The Worker wakes every 5 minutes and dispatches on the one wake-up in [send instant, +5 minutes).
  const zone = env.TIME_ZONE;
  if (!zone) throw new Error('TIME_ZONE must be set (the same value as schedule.timezone)');
  const tick = date.getTime();
  for (const ms of [tick, tick - 5 * 60e3]) {
    const w = wall(ms, zone);
    const target = sendInstant(w.y, w.m, w.d, send, zone);
    if (tick >= target && tick - 5 * 60e3 < target) return true;
  }
  return false;
}

export function dispatchRequest(env) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.GITHUB_REPO || '')) throw new Error('GITHUB_REPO must be owner/name');
  if (!/^[A-Za-z0-9_.-]+\.ya?ml$/.test(env.WORKFLOW || '')) throw new Error('WORKFLOW must be a workflow file name');
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN secret is missing');
  return {
    url: `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.WORKFLOW}/dispatches`,
    init: {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
        'User-Agent': 'dochunt-timer',
      },
      // Fixed: this Worker can only ever request a scheduled run.
      body: JSON.stringify({ ref: env.GITHUB_REF || 'main', inputs: { mode: 'scheduled' } }),
    },
  };
}

export async function runTimer(date, env, fetcher = fetch) {
  if (!shouldDispatch(date, env)) return { action: 'skip' };
  const { url, init } = dispatchRequest(env);
  const response = await fetcher(url, init);
  if (response.status !== 204) throw new Error(`GitHub dispatch failed with HTTP ${response.status}`);
  return { action: 'dispatched' };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runTimer(new Date(event.scheduledTime), env));
  },
  async fetch() {
    return new Response('DocHunt timer: no public actions.\n', { status: 200, headers: { 'Content-Type': 'text/plain' } });
  },
};
