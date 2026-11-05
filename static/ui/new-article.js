import { t, localizedOption, locale, localize } from './i18n.js';
import { $, S, api, allowNavigation, loadDocument, toast } from './core.js';
import { editor } from './editor.js';
import { navigateWorkspace } from './workspace.js';
import { initArticleDraft } from './new-article-ai.js';

let step = 1, sending = false, operation = crypto.randomUUID(), plan = null, opener, draft;
const values = () => ({title: $('#newarticle-title').value, audience: $('#newarticle-audience').value,
  material_language:S.settings.material_language || 'auto', comments_language:S.settings.comments_language || 'ru',
  purpose: $('#newarticle-purpose').value, mode: $('#newarticle-mode').value,
  notes: $('#newarticle-notes').value, root: $('#newarticle-root').value, folder: $('#newarticle-folder').value.trim(),
  ...($('#newarticle-mode').value === 'ai' ? draft.values() : {})});
function lock(value) {
  sending = value;
  $('#newarticle-dialog').setAttribute('aria-busy', String(value));
  for (const node of $('#newarticle-dialog').querySelectorAll('input,textarea,select,button')) node.disabled = value;
  if (!value) draw();
}
function draw() {
  for (const panel of document.querySelectorAll('[data-newarticle-step]')) panel.hidden = Number(panel.dataset.newarticleStep) !== step;
  localize($('#newarticle-progress'),"textContent",()=>t("ui.3f78ddb85fe2",step,[t("ui.682f961a0751"), t("ui.202d1e749c55"), t("ui.a77d5c4eccaa")][step - 1]));
  $('#newarticle-back').hidden = step === 1;
  localize($('#newarticle-next'),"textContent",()=>step === 3 ? t("ui.1e7950aa16f2") : t("ui.ea58bb0bc6d4"));
  $('#newarticle-next').disabled = sending || (step === 3 && !plan) || ($('#newarticle-mode').value === 'ai' && step >= 2 && (draft?.busy() || !draft?.ready()));
  $('#newarticle-notes-row').hidden = $('#newarticle-mode').value !== 'notes';
}
async function preview() {
  plan = null; draw();
  localize($('#newarticle-files'),"textContent",()=>t("ui.9eeaa9977457"));
  const key = JSON.stringify(values());
  try {
    const result = await api('/api/article-creation/preview', {method: 'POST', body: values()});
    if (key !== JSON.stringify(values()) || !$('#newarticle-dialog').open) return;
    plan = result;
    $('#newarticle-files').replaceChildren(...result.files.map(path => {
      const line = document.createElement('p'); line.textContent = path; return line;
    }));
    $('#newarticle-status').textContent = '';
  } catch (error) { if (key === JSON.stringify(values())) $('#newarticle-status').textContent = error.message; }
  draw();
}
async function open() {
  if (S.busy || S.recording || S.workspaceLocked || sending || !(await allowNavigation())) return;
  opener = document.activeElement;
  $('#newarticle-status').textContent = '';
  try {
    const result = await api('/api/article-creation');
    const select = $('#newarticle-root'), old = select.value;
    select.replaceChildren(...result.targets.map(target => new Option(target.path, target.id)));
    if ([...select.options].some(option => option.value === old)) select.value = old;
    else if ([...select.options].some(option => option.value === S.root)) select.value = S.root;
    $('#newarticle-dialog').showModal(); draw();
    draft.entered();
    if (step === 3) await preview();
    $('#newarticle-dialog').querySelector(`[data-newarticle-step="${step}"] input, [data-newarticle-step="${step}"] select`)?.focus();
  } catch (error) { toast(error.message, true); }
}
export function initNewArticle() {
  draft = initArticleDraft(() => { operation = crypto.randomUUID(); plan = null; draw(); });
  for (const button of document.querySelectorAll('[data-new-article]')) button.addEventListener('click', open);
  $('#newarticle-close').addEventListener('click', () => { if (!sending) $('#newarticle-dialog').close(); });
  $('#newarticle-dialog').addEventListener('cancel', event => { if (sending) event.preventDefault(); });
  $('#newarticle-dialog').addEventListener('close', () => { if (opener?.isConnected) opener.focus(); });
  $('#newarticle-form').addEventListener('input', () => { operation = crypto.randomUUID(); plan = null; draw(); });
  $('#newarticle-mode').addEventListener('change', () => { draw(); draft.entered(); });
  for (const id of ['#newarticle-root', '#newarticle-folder']) $(id).addEventListener('change', preview);
  $('#newarticle-back').addEventListener('click', () => {
    if (!sending) { step = Math.max(1, step - 1); draw(); $('#newarticle-dialog').querySelector(`[data-newarticle-step="${step}"] input, [data-newarticle-step="${step}"] select`)?.focus(); }
  });
  $('#newarticle-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending) return;
    if (!$('#newarticle-title').value.trim()) { step = 1; draw(); $('#newarticle-title').focus(); localize($('#newarticle-status'),"textContent",()=>t("ui.d075373f3487")); return; }
    if (step < 3) {
      step++; draw();
      if (step === 3) await preview();
      if (step === 2) draft.entered();
      $('#newarticle-dialog').querySelector(`[data-newarticle-step="${step}"] input, [data-newarticle-step="${step}"] select`)?.focus();
      return;
    }
    if (!plan) return;
    lock(true); localize($('#newarticle-status'),"textContent",()=>t("ui.f345057088f6"));
    try {
      const result = await api('/api/article-creation', {method: 'POST', body: {...values(), filename: plan.filename, operation_id: operation}});
      await loadDocument(result.root, result.rel);
      $('#newarticle-dialog').close();
      lock(false);
      await navigateWorkspace('editor'); editor.setView('source'); $('#texteditor').focus();
      $('#newarticle-form').reset(); draft.reset(); step = 1; plan = null; operation = crypto.randomUUID();
      toast(t("ui.dcdd90d32a03"));
    } catch (error) { localize($('#newarticle-status'),"textContent",()=>error.message + t("ui.d9b4a3006e75")); }
    finally { lock(false); }
  });
}
