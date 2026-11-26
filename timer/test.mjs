import assert from 'node:assert/strict';
import { localMinutes, runTimer, shouldDispatch, dispatchRequest } from './index.mjs';

const env = { GITHUB_REPO: 'example-org/dochunt', WORKFLOW: 'document-hunt.yml', GITHUB_REF: 'main',
  TIME_ZONE: 'America/Los_Angeles', SEND_TIME: '06:00', GITHUB_TOKEN: 'fixture-token' };

// Exactly one 5-minute wake-up per day dispatches, in local time, whatever the offset or season.
function dispatchesOn(day, e) {
  const start = Date.parse(`${day}T00:00:00Z`) - 14 * 3600e3;   // cover every offset around this date
  const hits = [];
  for (let t = start; t < start + 52 * 3600e3; t += 5 * 60e3) {
    const d = new Date(t);
    const localDay = new Intl.DateTimeFormat('en-CA', { timeZone: e.TIME_ZONE }).format(d);
    if (localDay === day && shouldDispatch(d, e)) hits.push(d);
  }
  return hits;
}
for (const [zone, time] of [['America/Los_Angeles', '06:00'], ['America/Chicago', '07:30'], ['Asia/Kolkata', '07:30'],
                            ['Africa/Casablanca', '06:00'], ['Asia/Tokyo', '07:30'], ['Australia/Lord_Howe', '06:15']]) {
  for (const day of ['2026-01-15', '2026-03-01', '2026-03-08', '2026-07-15', '2026-11-01', '2026-11-26']) {
    const e = { ...env, TIME_ZONE: zone, SEND_TIME: time };
    const hits = dispatchesOn(day, e);
    assert.equal(hits.length, 1, `${zone} ${time} on ${day}: ${hits.length} dispatches`);
    assert.equal(localMinutes(hits[0], zone), Number(time.slice(0, 2)) * 60 + Number(time.slice(3)));
  }
}
assert.equal(localMinutes(new Date('2026-11-26T08:00:00Z'), 'America/Los_Angeles'), 0); // midnight is 0, not 24:00
assert.throws(() => shouldDispatch(new Date(), { ...env, SEND_TIME: '6am' }), /HH:MM/);
assert.throws(() => shouldDispatch(new Date(), { ...env, TIME_ZONE: '' }), /TIME_ZONE must be set/);
import { readFileSync } from 'node:fs';
assert.match(readFileSync(new URL('./wrangler.toml', import.meta.url), 'utf8'), /^workers_dev = false/m);
assert.throws(() => shouldDispatch(new Date(), { ...env, SEND_TIME: '23:58' }), /23:55 or earlier/);
assert.equal(dispatchesOn('2026-07-15', { ...env, SEND_TIME: '23:55' }).length, 1);
assert.equal(dispatchesOn('2026-07-15', { ...env, SEND_TIME: '06:03' }).length, 1);  // not on a 5-minute mark
// Send times inside clock changes: a skipped spring time fires once after the jump; a repeated
// fall time fires once, on its first occurrence.
// Matches DocHunt's schedule check: a skipped 02:30 becomes 03:30 PDT (10:30 UTC).
const spring = dispatchesOn('2026-03-08', { ...env, SEND_TIME: '02:30' });
assert.equal(spring.length, 1); assert.equal(spring[0].toISOString(), '2026-03-08T10:30:00.000Z');
const fall = dispatchesOn('2026-11-01', { ...env, SEND_TIME: '01:30' });
assert.equal(fall.length, 1); assert.equal(fall[0].toISOString(), '2026-11-01T08:30:00.000Z');  // 01:30 PDT
// Lord Howe Island falls back by 30 minutes: 01:45 occurs twice on April 5, 2026; dispatch once.
const lordHowe = dispatchesOn('2026-04-05', { ...env, TIME_ZONE: 'Australia/Lord_Howe', SEND_TIME: '01:45' });
assert.equal(lordHowe.length, 1); assert.equal(lordHowe[0].toISOString(), '2026-04-04T14:45:00.000Z');
for (const time of ['00:00', '00:05', '01:00', '01:30', '02:00', '02:30', '03:00', '12:00', '23:55']) {
  for (const day of ['2026-03-08', '2026-11-01', '2026-06-01']) {
    assert.equal(dispatchesOn(day, { ...env, SEND_TIME: time }).length, 1, `${time} on ${day}`);
  }
}

// The request is fixed to one workflow, one branch and mode=scheduled.
const { url, init } = dispatchRequest(env);
assert.equal(url, 'https://api.github.com/repos/example-org/dochunt/actions/workflows/document-hunt.yml/dispatches');
assert.deepEqual(JSON.parse(init.body), { ref: 'main', inputs: { mode: 'scheduled' } });
assert.equal(init.headers.Authorization, 'Bearer fixture-token');
assert.throws(() => dispatchRequest({ ...env, GITHUB_REPO: 'evil/repo/../x' }), /owner\/name/);
assert.throws(() => dispatchRequest({ ...env, GITHUB_TOKEN: '' }), /GITHUB_TOKEN/);

// Dispatches at the send time, skips otherwise, and reports GitHub failures.
const calls = [];
const ok = async (u, i) => { calls.push([u, i]); return { status: 204 }; };
assert.deepEqual(await runTimer(new Date('2026-11-26T14:00:00Z'), env, ok), { action: 'dispatched' });
assert.deepEqual(await runTimer(new Date('2026-11-26T14:05:00Z'), env, ok), { action: 'skip' });
assert.equal(calls.length, 1);
await assert.rejects(runTimer(new Date('2026-11-26T14:00:00Z'), env, async () => ({ status: 403 })), /HTTP 403/);

// The public endpoint never dispatches.
const worker = (await import('./index.mjs')).default;
assert.equal((await worker.fetch(new Request('https://timer.example/'))).status, 200);

console.log('dochunt timer tests passed');
