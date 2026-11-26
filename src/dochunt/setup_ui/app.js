'use strict';
const token = document.querySelector('meta[name=setup-token]').content;
let state, plan, downloadURL;
const $ = id => document.getElementById(id);
async function api(path, body) {
  const response = await fetch(path, {method: body ? 'POST' : 'GET', headers: {'X-Setup-Token': token, 'Content-Type': 'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
function secretNames() { return [state.config.agent.runtime === 'codex' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY', ...(state.config.email.provider === 'resend' ? ['RESEND_API_KEY'] : ['SMTP_USERNAME', 'SMTP_PASSWORD']), ...(state.config.email.subscription_url ? ['SUBSCRIPTION_API_TOKEN'] : [])]; }
function secretsUI() {
  $('secret-fields').replaceChildren();
  for (const name of secretNames()) {
    const label = document.createElement('label'); label.textContent = name + (state.secrets.includes(name) ? ' · configured' : ' · not configured');
    const input = document.createElement('input'); input.type = 'password'; input.dataset.secret = name; input.autocomplete = 'off'; input.spellcheck = false;
    label.append(input); $('secret-fields').append(label);
  }
}
function invalidateReview() { plan=null; $('review').hidden=true; $('preview-reviewed').checked=false; }
function lines(id) { return $(id).value.split('\n').map(v => v.trim()).filter(Boolean); }
const fieldLabels={id:'Newsroom identifier',newsletter_name:'Newsletter name',sender_name:'Sender name',accent_color:'Accent color',name:'Beat name',brief:'Coverage brief',priorities:'Priorities',source_families:'Source families',geography:'Geography',exclusions:'Exclusions',records_law:'Records law',freshness_days:'Freshness window (days)',max_leads:'Maximum leads',extra_instructions:'Extra instructions',runtime:'Agent',model:'Model',reasoning_effort:'Reasoning effort',max_budget_usd:'Spending cap per run (USD)',timeout_minutes:'Agent time limit (minutes)',provider:'Provider',sender:'Sender email',recipients:'Recipients',smtp_host:'SMTP hostname',smtp_port:'SMTP port',enabled:'Scheduling',timezone:'Timezone',weekdays:'Weekdays',preferred_time:'Preferred local time'};
const sectionLabels={identity:'Newsroom',beat:'Beat',agent:'AI agent',email:'Email delivery',schedule:'Schedule'};
function readableValue(key,value) {
  if(key==='enabled') return value ? 'Enabled' : 'Disabled';
  if(key==='weekdays') return value.map(v=>['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][v]).join(', ');
  if(Array.isArray(value)) return value.join('\n') || 'None';
  return String(value) || 'None';
}
function readableChanges(changes) {
  return changes.map(change=>{
    return sectionLabels[change.section]+'\n'+Object.keys(change.after).filter(key=>JSON.stringify(change.before[key])!==JSON.stringify(change.after[key])).map(key=>`${fieldLabels[key]}\nPrevious: ${readableValue(key,change.before[key])}\nNew: ${readableValue(key,change.after[key])}`).join('\n\n');
  }).join('\n\n') || 'No settings changed.';
}
function audienceText(email) {
  if (email.subscription_url) return `everyone opted in on the subscription list at ${email.subscription_url} (allowed: ${email.subscription_domains.join(', ')}, plus ${email.recipients.join(', ') || 'no exceptions'}). The recipients below can receive test sends; they get scheduled issues only if they are on the list.`;
  return email.recipients.join(', ');
}
function proposedEmail() {
  return {...state.config.email, recipients: $('recipients').value.split('\n').map(v => v.trim()).filter(Boolean)};
}
function updateAudience() {
  const saved = audienceText(state.config.email), proposed = audienceText(proposedEmail());
  // Unsaved edits are a proposal until they are saved; never present them as the current audience.
  $('audience-text').textContent = proposed === saved ? saved : `${saved} — proposed after saving: ${proposed}`;
}
function populate() {
  for (const input of document.querySelectorAll('input[name], textarea[name], select[name]')) { const [section,key] = input.name.split('.'); input.value = state.config[section][key]; }
  $('priorities').value = state.config.beat.priorities.join('\n');
  $('source-families').value = state.config.beat.source_families.join('\n');
  $('recipients').value = state.config.email.recipients.join('\n');
  updateAudience();
  $('weekdays').value = state.config.schedule.weekdays.join(', ');
  $('target').textContent = `${state.mock ? 'SAFE MOCK — no GitHub writes. ' : ''}${state.repository} · default branch ${state.default_branch} · private${state.protected ? ' · branch rules: use manual PR' : ''}`;
  $('actions-links').replaceChildren();
  for (const [text,path] of [['Open Actions','/actions/workflows/document-hunt.yml'],['Repository secrets','/settings/secrets/actions'],['Edit configuration / propose PR','/edit/'+encodeURIComponent(state.branch)+'/newsroom.toml']]) {
    const link = document.createElement('a'); link.textContent = text; link.href = state.url+path; link.target='_blank'; link.rel='noopener noreferrer'; $('actions-links').append(link,document.createTextNode(' · '));
  }
  secretsUI();
}
function readConfig() {
  const c = structuredClone(state.config);
  for (const input of document.querySelectorAll('input[name], textarea[name], select[name]')) { const [section,key] = input.name.split('.'); c[section][key] = input.type === 'number' ? Number(input.value) : input.value; }
  c.beat.priorities = lines('priorities');
  c.beat.source_families = lines('source-families');
  c.email.recipients = $('recipients').value.split('\n').map(v => v.trim()).filter(Boolean);
  c.schedule.weekdays = $('weekdays').value.split(',').map(v => { if (!/^\s*[0-6]\s*$/.test(v)) throw new Error('Use unique weekday numbers 0 through 6.'); return Number(v.trim()); });
  c.schedule.enabled = false;
  return c;
}
async function review(enable=false) {
  plan = await api('/plan', {config: readConfig(), enable, reviewed_preview: $('preview-reviewed').checked, secret_updates: enable ? [] : [...document.querySelectorAll('[data-secret]')].filter(input => input.value).map(input => input.dataset.secret)});
  $('review-target').textContent = `${plan.repository} · default branch ${plan.default_branch} · ${enable ? 'ENABLE SCHEDULING' : 'Scheduling disabled'}`;
  $('changes').textContent = readableChanges(plan.changes) + '\n\nSecret updates: '+(plan.secret_updates.join(', ') || 'none; existing secrets preserved');
  $('confirm').checked = false; $('review').hidden = false; $('review').scrollIntoView({behavior:'smooth'});
}
function error(e) { $('result').textContent = e.message; }
$('settings').addEventListener('submit', async e => { e.preventDefault(); try { await review(); } catch(e) { error(e); } });
$('enable').addEventListener('click', async () => { try { if (!$('preview-reviewed').checked) throw new Error('Review and approve the current preview and inbox test first.'); await review(true); } catch(e) { error(e); } });
$('settings').addEventListener('input', e => {
  invalidateReview();
  updateAudience();
  if (e.target.name === 'agent.runtime') { state.config.agent.runtime=e.target.value; document.querySelector('[name="agent.model"]').value=''; secretsUI(); }
  if (e.target.name === 'email.provider') { state.config.email.provider=e.target.value; secretsUI(); }
});
$('save').addEventListener('click', async () => {
  if (!plan || !$('confirm').checked) return error(new Error('Review changes and confirm the repository and branch first.'));
  const values={};
  for (const input of document.querySelectorAll('[data-secret]')) values[input.dataset.secret]=input.value;
  $('save').disabled=true;
  try {
    const result = await api(plan.enable ? '/enable' : '/save', {plan_id:plan.plan_id, confirm_repository:plan.repository, confirm_branch:plan.branch, secrets:plan.enable ? {} : values});
    $('result').textContent = result.config.message + (result.complete ? ' All requested operations succeeded.' : ' Setup is incomplete; inspect each result below.') + (result.schedule_enabled === true ? ' Scheduling is enabled.' : result.schedule_enabled === false ? ' Scheduling is verified disabled.' : ' Schedule state is unknown; inspect the default-branch configuration before proceeding.') + ' Already-running Actions jobs are not cancelled.';
    $('secret-results').replaceChildren();
    for (const name of result.missing_secrets) { const li=document.createElement('li'); li.textContent=name+': required secret is not configured; add it before activation.'; $('secret-results').append(li); }
    for (const item of result.secrets) { const li=document.createElement('li'); li.textContent=`${item.name}: ${item.success ? 'updated' : 'failed / uncertain'} — ${item.message}`; $('secret-results').append(li); }
    $('fallback').hidden = !result.config.manual_toml;
    if (result.config.manual_toml) { $('manual').textContent=result.config.manual_toml; if(downloadURL) URL.revokeObjectURL(downloadURL); downloadURL=URL.createObjectURL(new Blob([result.config.manual_toml],{type:'text/plain'})); $('download').href=downloadURL; }
    if (result.config.pushed) { state=await api('/state'); populate(); }
  } catch(e) { error(e); }
  finally { for (const input of document.querySelectorAll('[data-secret]')) input.value=''; for(const key of Object.keys(values)) delete values[key]; plan=null; $('review').hidden=true; $('save').disabled=false; $('preview-reviewed').checked=false; }
});
api('/state').then(value => { state=value; populate(); }).catch(error);
