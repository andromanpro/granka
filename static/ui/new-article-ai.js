import { t, localizedOption, locale, localize } from './i18n.js';
import {recipeLabel} from './i18n.js';
import { $, api, S } from './core.js';

const storageKey = 'granka-new-article-ai';
const names = [t("ui.ad62ec4f065f"), t("ui.2dc88854ded6"), t("ui.982e4714b196"), t("ui.becbbd4988df")];
export function initArticleDraft(changed) {
  let recipes = [], pending = false, uncertain = false, issued = null, job = null, accepted = '', contextSerial = 0, recipeName = t("ui.c4729808c35f");
  const request = () => ({title: $('#newarticle-title').value, audience: $('#newarticle-audience').value,
    material_language:S.settings.material_language || 'auto', comments_language:S.settings.comments_language || 'ru',
    purpose: $('#newarticle-purpose').value, brief: $('#newarticle-brief').value,
    profile: $('#newarticle-profile').value, length: $('#newarticle-length').value,
    use_style: $('#newarticle-style').checked, recipe: $('#newarticle-season').checked ? {
      name: recipeName,
      doses: [...document.querySelectorAll('[data-draft-dose]')].map(n => Number(n.value)),
      mode: $('#newarticle-spice-mode').value, density: $('#newarticle-density').value,
      prompt: $('#newarticle-spice-prompt').value} : null});
  const key = () => JSON.stringify(request());
  const ready = () => accepted === key() && !!$('#newarticle-draft-text').value.trim() && job?.status === 'succeeded';
  function save() {
    try { sessionStorage.setItem(storageKey, JSON.stringify({issued, job, accepted, text: $('#newarticle-draft-text').value})); }
    catch { $('#newarticle-ai-status').textContent += t("ui.e406da2ff078"); }
  }
  function draw() {
    const ai = $('#newarticle-mode').value === 'ai';
    $('#newarticle-ai').hidden = !ai;
    $('#newarticle-local-note').hidden = ai;
    $('#newarticle-ai').setAttribute('aria-busy', String(pending));
    for (const n of document.querySelectorAll('[data-draft-parameter], #newarticle-title, #newarticle-audience, #newarticle-purpose')) n.disabled = pending || uncertain;
    $('#newarticle-mode').disabled = pending || uncertain;
    $('#newarticle-generate').disabled = pending;
    localize($('#newarticle-generate'),"textContent",()=>pending ? t("ui.c338cce3c789") : uncertain ? t("ui.5387d2e5c0e3") : job?.status === 'succeeded' ? t("ui.21ce36c65f55") : t("ui.290bcb6d24cd"));
    $('#newarticle-spice-fields').hidden = !$('#newarticle-season').checked;
    $('#newarticle-draft-text').disabled = pending;
    $('#newarticle-ai-result').hidden = !$('#newarticle-draft-text').value;
    $('#newarticle-ai-stale').hidden = !accepted || accepted === key();
    for (const n of document.querySelectorAll('[data-draft-dose]')) {
      n.nextElementSibling.textContent = names[Number(n.value)];
    }
  }
  async function context() {
    const serial = ++contextSerial;
    localize($('#newarticle-style-info'),"textContent",()=>t("ui.f236ed370e27"));
    try {
      const [info, saved] = await Promise.all([
        api('/api/article-draft/context?profile=' + $('#newarticle-profile').value), api('/api/spice-recipes')]);
      if (serial !== contextSerial) return;
      recipes = [...saved.builtins, ...saved.recipes];
      const select = $('#newarticle-recipe'), old = select.value;
      select.replaceChildren(localizedOption(()=>t("ui.c4729808c35f"),''), ...recipes.map(r => localizedOption(()=>recipeLabel(r), r.id)));
      if ([...select.options].some(n => n.value === old)) select.value = old;
      const matching = recipes.find(r => r.name === recipeName);
      if (matching) select.value = matching.id;
      const corpus = info.corpus;
      localize($('#newarticle-style-info'),"textContent",()=>t("ui.9e46f88e7144",info.rules) +
        (corpus.status === 'ok' ? t("ui.89f78617de81",corpus.profile_label,corpus.files) :
          t("ui.eff3d6a6b545",corpus.reason)) +
        t("ui.c48bd92a4e1b",info.provider === 'codex' ? 'Codex / ChatGPT' : 'OpenRouter',info.model ? ' · ' + info.model : ''));
    } catch (error) { if (serial === contextSerial) localize($('#newarticle-style-info'),"textContent",()=>error.message + t("ui.badefdd5b19e")); }
  }
  async function poll() {
    while (true) {
      job = (await api('/api/job?id=' + job.id)).job; save();
      if (job.status === 'succeeded') {
        $('#newarticle-draft-text').value = job.result.text;
        accepted = JSON.stringify(issued.request);
        const style = job.result.style;
        localize($('#newarticle-ai-status'),"textContent",()=>t("ui.497846341b45",style.rules,style.examples));
        save(); return;
      }
      if (!['queued', 'running'].includes(job.status)) {
        localize($('#newarticle-ai-status'),"textContent",()=>job.message + t("ui.dd206a042300"));
        return;
      }
      localize($('#newarticle-ai-status'),"textContent",()=>job.message + t("ui.c3cd196a06f2"));
      await new Promise(resolve => setTimeout(resolve, 800));
    }
  }
  async function generate() {
    if (pending) return;
    if (!$('#newarticle-title').value.trim()) { localize($('#newarticle-ai-status'),"textContent",()=>t("ui.e5e120d82b86")); return; }
    if (!uncertain) {
      issued = {request: request(), wizard_id: issued?.wizard_id || crypto.randomUUID(), operation_id: crypto.randomUUID()};
      job = null; accepted = ''; save();
    }
    pending = true; uncertain = false; draw(); changed();
    localize($('#newarticle-ai-status'),"textContent",()=>t("ui.8a301a4e0dba"));
    try {
      if (!job) job = (await api('/api/article-draft', {method: 'POST', body: {...issued.request, wizard_id: issued.wizard_id, operation_id: issued.operation_id}})).job;
      save(); await poll();
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && !job) {
        issued = null;
        localize($('#newarticle-ai-status'),"textContent",()=>error.message + t("ui.a6befa4a156f"));
      } else {
        uncertain = true;
        localize($('#newarticle-ai-status'),"textContent",()=>error.message + t("ui.74f21db2d3dc"));
      }
    } finally {
      pending = false; draw(); save(); changed();
      if (ready() && $('#newarticle-dialog').open && $('#newarticle-mode').value === 'ai') $('#newarticle-ai-result').scrollIntoView({block: 'nearest'});
    }
  }
  $('#newarticle-generate').addEventListener('click', generate);
  $('#newarticle-profile').addEventListener('change', context);
  $('#newarticle-recipe').addEventListener('change', () => {
    recipeName = $('#newarticle-recipe').selectedOptions[0].textContent;
    const r = recipes.find(n => n.id === $('#newarticle-recipe').value);
    if (r) {
      document.querySelectorAll('[data-draft-dose]').forEach((n, i) => { n.value = r.doses[i]; });
      $('#newarticle-spice-mode').value = r.mode; $('#newarticle-density').value = r.density;
      $('#newarticle-spice-prompt').value = r.prompt;
    }
    draw(); changed();
  });
  $('#newarticle-form').addEventListener('input', () => { draw(); if (issued) save(); });
  $('#newarticle-form').addEventListener('change', () => { draw(); changed(); });
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (stored?.issued) {
      issued = stored.issued; job = stored.job; accepted = stored.accepted || '';
      const r = issued.request;
      for (const [id, value] of Object.entries({title: r.title, audience: r.audience, purpose: r.purpose, brief: r.brief, profile: r.profile, length: r.length})) $('#newarticle-' + id).value = value;
      $('#newarticle-style').checked = r.use_style; $('#newarticle-season').checked = !!r.recipe;
      if (r.recipe) {
        recipeName = r.recipe.name;
        document.querySelectorAll('[data-draft-dose]').forEach((n, i) => { n.value = r.recipe.doses[i]; });
        $('#newarticle-spice-mode').value = r.recipe.mode; $('#newarticle-density').value = r.recipe.density; $('#newarticle-spice-prompt').value = r.recipe.prompt;
      }
      $('#newarticle-mode').value = 'ai'; $('#newarticle-draft-text').value = stored.text || '';
      if (!job || ['queued', 'running'].includes(job.status)) {
        uncertain = true; localize($('#newarticle-ai-status'),"textContent",()=>t("ui.46c89db1aa78"));
      }
    }
  } catch { sessionStorage.removeItem(storageKey); }
  return {ready, busy: () => pending || uncertain, draw,
    values: () => ({...request(), draft_text: $('#newarticle-draft-text').value, draft_job: job?.id}),
    entered: () => { draw(); if ($('#newarticle-mode').value === 'ai') context(); },
    reset: () => { issued = job = null; accepted = ''; recipeName = t("ui.c4729808c35f"); uncertain = pending = false; $('#newarticle-draft-text').value = ''; sessionStorage.removeItem(storageKey); draw(); }};
}
