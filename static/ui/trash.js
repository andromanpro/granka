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
  $('#source-trash').textContent = 'Корзина · ' + state.entries.length;
  return state;
}
async function draw() {
  await refreshTrashCount();
  const list = $('#trash-list'); list.replaceChildren();
  $('#trash-empty').disabled = !state.entries.length;
  for (const item of state.entries) {
    const row = el('article', 'trash-item');
    row.append(el('strong', null, item.title), el('small', null, item.external_id ? 'Ссылка на публикацию · исходника нет' : item.rel),
      el('small', null, item.deleted_at.replace('T', ' ') + ' · файлов: ' + item.files));
    if (item.status !== 'trashed') row.append(el('p', 'fine-print', 'Операция прервана. Восстанови статью перед очисткой.'));
    const restore = button('Восстановить', async () => {
      if (pending) return;
      lock(true); $('#trash-status').textContent = 'Восстанавливаю…';
      try {
        await api('/api/article-trash/restore', {method: 'POST', body: {id: item.id, expected_revision: state.revision}});
        $('#trash-status').textContent = 'Статья восстановлена'; emit('trash-changed');
      } catch (error) { $('#trash-status').textContent = error.message; }
      finally { lock(false); try { await draw(); } catch (error) { $('#trash-status').textContent = error.message; } }
    });
    restore.disabled = item.status === 'purging'; row.append(restore); list.append(row);
  }
  if (!state.entries.length) list.append(el('p', 'fine-print', 'Корзина пуста.'));
}
function confirm(title, detail, label, callback) {
  opener = document.activeElement; action = callback;
  $('#trash-confirm-title').textContent = title;
  $('#trash-confirm-detail').replaceChildren(...detail.map(text => el('p', null, text)));
  $('#trash-confirm-apply').textContent = label; $('#trash-confirm-status').textContent = '';
  $('#trash-confirm').showModal(); $('#trash-confirm-cancel').focus();
}
export async function trashArticle(source) {
  if (pending || S.busy || S.recording || S.workspaceLocked) { toast('Дождись завершения текущей операции', true); return; }
  if (!(await allowNavigation())) return;
  const body = source.external ? {external_id: source.id} : {root: source.root, rel: source.rel};
  try {
    const plan = await api('/api/article-trash/preview', {method: 'POST', body});
    const lines = [plan.title, plan.external_id ? 'В корзину попадёт только локальная ссылка. Публикация останется на сайте.' :
      'В корзине сохранятся текст, история версий и материалы. Статью можно будет восстановить.'];
    lines.push(...plan.files.slice(0, 12).map(file => file.rel));
    if (plan.files.length > 12) lines.push('И ещё файлов: ' + (plan.files.length - 12));
    if (plan.shared.length) lines.push('Общие материалы останутся на месте; их копия сохранится в корзине.');
    if (source.copies?.length) lines.push('Другие исходники этой карточки останутся в библиотеке.');
    const request = {...body, expected_revision: plan.revision, operation_id: crypto.randomUUID()};
    confirm('Переместить статью в корзину?', lines, 'В корзину', async () => {
      if (!(await allowNavigation())) { $('#trash-confirm-status').textContent = 'Перенос отменён: несохранённый текст оставлен в редакторе.'; return; }
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
    confirm('Очистить корзину?', ['Статей в корзине: ' + state.entries.length,
      ...state.entries.slice(0, 8).map(item => item.title),
      'Файлы в корзине будут удалены навсегда. Восстановить их через Гранку уже не получится.'], 'Удалить навсегда', async () => {
      await api('/api/article-trash/empty', {method: 'POST', body: {confirm: true, expected_revision: revision}});
      $('#trash-confirm').close(); $('#trash-status').textContent = 'Корзина очищена'; await draw(); emit('trash-changed');
    });
  });
  $('#trash-confirm-apply').addEventListener('click', async () => {
    if (pending || !action) return;
    lock(true); $('#trash-confirm-status').textContent = 'Выполняю…';
    try { await action(); }
    catch (error) { $('#trash-confirm-status').textContent = error.message; }
    finally { lock(false); }
  });
}
