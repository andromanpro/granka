import { $, S, el, button, events, emit, allowNavigation, operation, toast } from './core.js';
import { setView } from './editor.js';
import { compareVersions } from './diff.js';
import { workingJob } from './jobs.js';

const key = () => S.root + '|' + S.file;
export async function selectVersion(id) {
  const documentKey = key();
  if (S.busy || S.recording || !(await allowNavigation()) || key() !== documentKey || !S.document?.versions.some(v => v.id === id)) return;
  S.version = id; S.active = null; S.deepFocus = null;
  $('#versel').value = id;
  emit('version'); emit('controls');
}
async function openVersion(id) {
  const documentKey = key();
  await selectVersion(id);
  if (S.version !== id || key() !== documentKey || S.editor) return;
  setView('article');
  if (getComputedStyle($('#inspector-close')).display !== 'none') $('#inspector').classList.remove('mobile-open');
}
async function restore(id) {
  const documentKey = key();
  if (S.busy || S.recording || workingJob() || !(await allowNavigation()) || key() !== documentKey) return;
  const out = await operation('/api/restore-version', {source_version: id, base_version: S.document.versions.at(-1).id});
  if (!out) return;
  S.version = out.version || S.document.versions.at(-1).id; S.active = null; S.deepFocus = null;
  $('#versel').value = S.version;
  emit('version'); setView('article'); emit('controls');
  if (out.warning) toast(out.warning, true);
  else toast(out.unchanged ? 'Эта версия уже в работе' : 'В работу возвращена ' + id + ' · создана ' + S.version);
}
function render() {
  const box = $('#version-history'); box.replaceChildren();
  if (!S.document) return;
  const head = S.document.versions.at(-1).id;
  for (const version of [...S.document.versions].reverse()) {
    const row = el('article', 'version-history-item'); row.dataset.version = version.id;
    row.classList.toggle('current', version.id === S.version);
    row.append(el('strong', '', version.id + (version.id === head ? ' · в работе' : '') + (version.id === S.version ? ' · открыта' : '')));
    if (version.label) row.append(el('p', '', version.label));
    if (version.ts) row.append(el('time', '', version.ts.replace('T', ' ')));
    if (version.restored_from) row.append(el('p', 'fine-print', 'Возврат из ' + version.restored_from));
    const actions = el('div', 'version-history-actions');
    const open = button('Открыть', () => openVersion(version.id));
    const compare = button('Сравнить с открытой', () => compareVersions(version.id));
    const take = button('Вернуть в работу', () => restore(version.id), 'quiet');
    open.dataset.historyAction = 'open'; compare.dataset.historyAction = 'compare'; take.dataset.historyAction = 'restore';
    actions.append(open, compare, take); row.append(actions); box.append(row);
  }
  controls();
}
function controls() {
  for (const row of document.querySelectorAll('#version-history [data-version]')) {
    for (const action of row.querySelectorAll('[data-history-action]')) {
      const kind = action.dataset.historyAction;
      action.disabled = S.busy || !!S.recording ||
        (kind === 'compare' && row.dataset.version === S.version) ||
        (kind === 'restore' && (!!workingJob() || row.dataset.version === S.document?.versions.at(-1).id));
    }
  }
}
export function initHistory() {
  for (const name of ['document', 'version']) events.addEventListener(name, render);
  for (const name of ['controls', 'jobs']) events.addEventListener(name, controls);
}
