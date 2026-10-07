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
function secretNames() { return [state.config.ai.provider === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY', ...(state.config.email.provider === 'resend' ? ['RESEND_API_KEY'] : ['SMTP_USERNAME', 'SMTP_PASSWORD']), ...(state.config.retrieval.firecrawl_enabled ? ['FIRECRAWL_API_KEY'] : [])]; }
function secretsUI() {
  $('secret-fields').replaceChildren();
  for (const name of secretNames()) {
    const label = document.createElement('label'); label.textContent = name + (state.secrets.includes(name) ? ' · configured' : ' · not configured');
    const input = document.createElement('input'); input.type = 'password'; input.dataset.secret = name; input.autocomplete = 'off'; input.spellcheck = false;
    label.append(input); $('secret-fields').append(label);
  }
}
function invalidateReview() { plan=null; $('review').hidden=true; $('preview-reviewed').checked=false; }
function addResource(source={url:'',label:'',notes:''}) {
  const row=document.createElement('fieldset'); row.dataset.resource='row';
  const legend=document.createElement('legend'); legend.textContent='Document source'; row.append(legend);
  for(const [key,title] of [['url','Public URL'],['label','Resource name'],['notes','Access notes']]) {
    const label=document.createElement('label'); label.textContent=title;
    const input=document.createElement(key==='notes' ? 'textarea' : 'input'); input.dataset.resourceField=key; input.value=source[key];
    if(key==='url') { input.type='url'; input.required=true; } if(key==='notes') input.rows=3;
    label.append(input); row.append(label);
  }
  const remove=document.createElement('button'); remove.type='button'; remove.textContent='Remove resource';
  remove.addEventListener('click',()=>{ row.remove(); invalidateReview(); }); row.append(remove); $('resources').append(row);
}
function readResources() {
  return [...document.querySelectorAll('[data-resource]')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-resource-field]')].map(input=>[input.dataset.resourceField,input.value])));
}
const fieldLabels={firecrawl_enabled:'Page fallback',firecrawl_max_calls:'Maximum fallback attempts',firecrawl_timeout_seconds:'Fallback timeout (seconds)',id:'Newsroom identifier',newsletter_name:'Newsletter name',sender_name:'Sender name',accent_color:'Accent color',coverage_brief:'Coverage brief',geography:'Geography',exclusions:'Exclusions',max_final_leads:'Maximum final leads',provider:'Provider',model:'Research model',reviewer_model:'Reviewer model',reasoning_effort:'Reasoning effort',sender:'Sender email',recipients:'Recipients',smtp_host:'SMTP hostname',smtp_port:'SMTP port',enabled:'Scheduling',timezone:'Timezone',weekdays:'Weekdays',preferred_time:'Preferred local time',workflow_timeout_minutes:'Workflow timeout (minutes)',application_deadline_minutes:'Application deadline (minutes)',max_candidates:'Maximum candidates',research_requests:'Research requests',review_requests:'Review requests',searches:'Searches',fetches:'Fetches',input_tokens:'Input tokens',output_tokens:'Output tokens',transient_retries:'Transient retries'};
const sectionLabels={retrieval:'Optional page access',identity:'Newsroom',editorial:'Coverage',ai:'AI account',email:'Email delivery',schedule:'Schedule',sources:'Recommended resources',limits:'Usage limits'};
function readableValue(key,value) {
  if(key==='enabled' || key==='firecrawl_enabled') return value ? 'Enabled' : 'Disabled';
  if(key==='weekdays') return value.map(v=>['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][v]).join(', ');
  if(Array.isArray(value)) return value.join('\n') || 'None';
  return String(value) || 'None';
}
function readableSources(sources) { return sources.map((source,index)=>`Resource ${index+1}\nPublic URL: ${source.url}\nResource name: ${source.label || 'None'}\nAccess notes: ${source.notes || 'None'}`).join('\n\n') || 'None'; }
function readableChanges(changes) {
  return changes.map(change=>{
    if(change.section==='sources') return `Recommended resources\nPrevious:\n${readableSources(change.before)}\nNew:\n${readableSources(change.after)}`;
    return sectionLabels[change.section]+'\n'+Object.keys(change.after).filter(key=>JSON.stringify(change.before[key])!==JSON.stringify(change.after[key])).map(key=>`${fieldLabels[key]}\nPrevious: ${readableValue(key,change.before[key])}\nNew: ${readableValue(key,change.after[key])}`).join('\n\n');
  }).join('\n\n') || 'No settings changed.';
}
function populate() {
  for (const input of document.querySelectorAll('input[name], textarea[name], select[name]')) { const [section,key] = input.name.split('.'); input.value = state.config[section][key]; }
  document.querySelector('[name="editorial.max_final_leads"]').max = state.config.limits.max_final_leads;
  $('resources').replaceChildren(); state.config.sources.forEach(addResource);
  $('recipients').value = state.config.email.recipients.join('\n');
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
  c.retrieval.firecrawl_enabled=document.querySelector('[name="retrieval.firecrawl_enabled"]').value==='true';
  c.sources = readResources();
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
$('add-resource').addEventListener('click',()=>{ addResource(); invalidateReview(); });
$('settings').addEventListener('submit', async e => { e.preventDefault(); try { await review(); } catch(e) { error(e); } });
$('enable').addEventListener('click', async () => { try { if (!$('preview-reviewed').checked) throw new Error('Review and approve the current preview and inbox test first.'); await review(true); } catch(e) { error(e); } });
$('settings').addEventListener('input', e => {
  invalidateReview();
  if (e.target.name === 'retrieval.firecrawl_enabled') { state.config.retrieval.firecrawl_enabled=e.target.value==='true'; secretsUI(); }
  if (e.target.name === 'ai.provider' || e.target.name === 'email.provider') { state.config[e.target.name.split('.')[0]].provider=e.target.value; secretsUI(); }
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
