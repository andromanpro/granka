import { t, localizedOption, locale, localize } from './i18n.js';
import { $, S, api, button, el, emit, toast, allowNavigation } from './core.js';

let state = null, pending = false, action = null, opener;
function lock(value) {
  pending = value;
  for (const node of document.querySelectorAll('#trash-dialog button, #trash-confirm button')) node.disabled = value;
  $('#trash-dialog').setAttribute('aria-busy', String(value));
  $('#trash-confirm').setAttribute('aria-busy', String(value));
  if (!value && state) $('#trash-empty').disabled = !state.entries.length;
}
export async function refreshTrashCount() {
  state = await api('/api/article-trash');
  localize($('#source-trash'),"textContent",()=>t("ui.addfdca11e14") + state.entries.length);
  return state;
}
async function draw() {
  await refreshTrashCount();
  const list = $('#trash-list'); list.replaceChildren();
  $('#trash-empty').disabled = !state.entries.length;
  for (const item of state.entries) {
    const row = el('article', 'trash-item');
    row.append(el('strong', null, item.title), el('small', null, ()=>item.external_id ? t("ui.103498b9f79a") : item.rel),
      el('small', null, ()=>item.deleted_at.replace('T', ' ') + t("ui.e4ba86126631") + item.files));
    if (item.status !== 'trashed') row.append(el('p', 'fine-print', ()=>t("ui.64f34713f972")));
    const restore = button(()=>t("ui.76f55a988f8a"), async () => {
      if (pending) return;
      lock(true); localize($('#trash-status'),"textContent",()=>t("ui.6b21df6afe6d"));
      try {
        await api('/api/article-trash/restore', {method: 'POST', body: {id: item.id, expected_revision: state.revision}});
        localize($('#trash-status'),"textContent",()=>t("ui.760cf2d4a35b")); emit('trash-changed');
      } catch (error) { $('#trash-status').textContent = error.message; }
      finally { lock(false); try { await draw(); } catch (error) { $('#trash-status').textContent = error.message; } }
    });
    restore.disabled = item.status === 'purging'; row.append(restore); list.append(row);
  }
  if (!state.entries.length) list.append(el('p', 'fine-print', ()=>t("ui.ff6db3280fce")));
}
function confirm(title, detail, label, callback) {
  opener = document.activeElement; action = callback;
  $('#trash-confirm-title').textContent = title;
  $('#trash-confirm-detail').replaceChildren(...detail.map(text => el('p', null, text)));
  $('#trash-confirm-apply').textContent = label; $('#trash-confirm-status').textContent = '';
  $('#trash-confirm').showModal(); $('#trash-confirm-cancel').focus();
}
export async function trashArticle(source) {
  if (pending || S.busy || S.recording || S.workspaceLocked) { toast(t("ui.f9e86ebf2b9c"), true); return; }
  if (!(await allowNavigation())) return;
  const body = source.external ? {external_id: source.id} : {root: source.root, rel: source.rel};
  try {
    const plan = await api('/api/article-trash/preview', {method: 'POST', body});
    const lines = [plan.title, plan.external_id ? t("ui.7c7265b67b3d") :
      t("ui.c888e8068f8d")];
    lines.push(...plan.files.slice(0, 12).map(file => file.rel));
    if (plan.files.length > 12) lines.push(t("ui.4806a70b1777") + (plan.files.length - 12));
    if (plan.shared.length) lines.push(t("ui.7a0494be96ef"));
    if (source.copies?.length) lines.push(t("ui.21cbae7415ba"));
    const request = {...body, expected_revision: plan.revision, operation_id: crypto.randomUUID()};
    confirm(t("ui.927437110e37"), lines, t("ui.4a252fb127a9"), async () => {
      if (!(await allowNavigation())) { localize($('#trash-confirm-status'),"textContent",()=>t("ui.c381b5001c99")); return; }
      await api('/api/article-trash', {method: 'POST', body: request});
      $('#trash-confirm').close();
      // The save/discard guard ran before preview; reload drops any stale open
      // document, including aliases pointing at the same physical source.
      history.replaceState(null, '', '/#library');
      location.reload();
    });
  } catch (error) { toast(error.message, true); }
}
export function initTrash() {
  $('#source-trash').addEventListener('click', async () => {
    if (pending) return; opener = document.activeElement;
    $('#trash-status').textContent = ''; $('#trash-dialog').showModal();
    try { await draw(); } catch (error) { $('#trash-status').textContent = error.message; }
  });
  $('#trash-close').addEventListener('click', () => { if (!pending) $('#trash-dialog').close(); });
  $('#trash-dialog').addEventListener('close', () => $('#source-trash').focus());
  for (const id of ['#trash-dialog', '#trash-confirm']) $(id).addEventListener('cancel', event => { if (pending) event.preventDefault(); });
  $('#trash-confirm-cancel').addEventListener('click', () => { if (!pending) $('#trash-confirm').close(); });
  $('#trash-confirm').addEventListener('close', () => { if (opener?.isConnected) opener.focus(); });
  $('#trash-empty').addEventListener('click', () => {
    if (pending || !state?.entries.length) return;
    const revision = state.revision;
    confirm(t("ui.443697867cbe"), [t("ui.749e2440b8cd") + state.entries.length,
      ...state.entries.slice(0, 8).map(item => item.title),
      t("ui.67a4f68fca10")], t("ui.abac4685c644"), async () => {
      await api('/api/article-trash/empty', {method: 'POST', body: {confirm: true, expected_revision: revision}});
      $('#trash-confirm').close(); localize($('#trash-status'),"textContent",()=>t("ui.53901b453491")); await draw(); emit('trash-changed');
    });
  });
  $('#trash-confirm-apply').addEventListener('click', async () => {
    if (pending || !action) return;
    lock(true); localize($('#trash-confirm-status'),"textContent",()=>t("ui.13d135d51d25"));
    try { await action(); }
    catch (error) { $('#trash-confirm-status').textContent = error.message; }
    finally { lock(false); }
  });
}
