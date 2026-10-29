import { $, S, api, el, button, events, emit, version, latest, query, operation, toast } from './core.js';
import { editor, setView } from './editor.js';
import { startJob, workingJob } from './jobs.js';
import { openSettings } from './settings.js';

const spices = [
  { id: 'irony', name: 'Ирония', letter: 'И', hint: 'Сухая, по вкусу' },
  { id: 'vulgar', name: 'Пошлые шутки', letter: 'П', hint: 'Осторожно, острая' },
  { id: 'metaphor', name: 'Метафоры', letter: 'М', hint: 'Букет образов' },
  { id: 'allegory', name: 'Аллегории', letter: 'А', hint: 'Смысл в каплях' },
];
const doses = ['Нет', 'Щепотка', 'Заметно', 'От души'];
const MIME = 'application/x-granka-spice';
let catalog = { builtins: [], recipes: [], revision: null };
let mix = [2, 0, 0, 0], target = null, lastSelection = null, picking = false;
let resultJob = null, shown = '', ignored = new Set(), pending = false;
let focusResult = false;

function allRecipes() { return [...catalog.builtins, ...catalog.recipes]; }
function recipe() {
  return { name: $('#spice-recipe-name').value.trim() || allRecipes().find(r => r.id === $('#spice-recipe').value)?.name || 'Своя смесь',
    doses: [...mix], mode: $('#spice-mode').value, density: $('#spice-density').value, prompt: $('#spice-prompt').value };
}
async function recipes() {
  const selected = $('#spice-recipe').value;
  catalog = await api('/api/spice-recipes');
  $('#spice-recipe').replaceChildren(new Option('Своя смесь', 'mix'));
  allRecipes().forEach(r => $('#spice-recipe').append(new Option(r.name, r.id)));
  $('#spice-recipe').value = allRecipes().some(r => r.id === selected) ? selected : 'mix';
  recipeButtons();
}
function recipeButtons() {
  const custom = catalog.recipes.some(r => r.id === $('#spice-recipe').value);
  $('#spice-update').hidden = $('#spice-delete').hidden = !custom;
}
function chooseRecipe() {
  const selected = allRecipes().find(r => r.id === $('#spice-recipe').value);
  if (selected) {
    mix = [...selected.doses];
    $('#spice-mode').value = selected.mode;
    $('#spice-density').value = selected.density;
    $('#spice-prompt').value = selected.prompt;
    $('#spice-recipe-name').value = selected.name;
  }
  recipeButtons(); ingredients(); controls();
}
async function saveRecipe(update = false, remove = false) {
  try {
    const out = await api('/api/spice-recipes', { method: 'POST', body: {
      expected_revision: catalog.revision, recipe: recipe(),
      ...(update || remove ? { id: $('#spice-recipe').value } : {}), delete: remove,
    }});
    await recipes();
    $('#spice-recipe').value = out.saved_id || 'mix';
    chooseRecipe(); $('#spice-recipe-editor').open = false;
    toast(remove ? 'Рецепт удалён' : 'Рецепт сохранён');
  } catch (error) {
    toast(error.message, true);
    if (error.conflict) await recipes();
  }
}
function ingredients() {
  const box = $('#spice-ingredients');
  box.dataset.view = S.settings.spice_view || 'jars';
  box.replaceChildren();
  spices.forEach((spice, i) => {
    const tile = el('div', 'spice-tile spice-' + spice.id);
    const jar = button('', () => { mix[i] = (mix[i] + 1) % 4; ingredients(); controls(); }, 'spice-vessel-button');
    jar.draggable = true;
    jar.setAttribute('aria-label', spice.name + ': ' + doses[mix[i]] + '. Взять приправу');
    jar.setAttribute('aria-pressed', String(mix[i] > 0));
    jar.append(el('span', 'spice-name', spice.name));
    const vessel = el('span', 'spice-vessel');
    vessel.setAttribute('aria-hidden', 'true');
    vessel.style.setProperty('--spice-fill', (10 + mix[i] * 23) + '%');
    const label = el('span', 'spice-label', spice.letter);
    label.append(el('small', '', doses[mix[i]]));
    vessel.append(el('span', 'spice-lid'), el('span', 'spice-powder'), label);
    jar.append(vessel, el('small', 'spice-flavour', spice.hint));
    jar.addEventListener('dragstart', event => {
      if (!ready()) { event.preventDefault(); return; }
      event.dataTransfer.setData(MIME, JSON.stringify({ index: i, root: S.root, file: S.file, version: S.version }));
      event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setDragImage(vessel, 35, 45);
      $('#doc').classList.add('spice-dragging');
    });
    jar.addEventListener('dragend', clearDrop);
    const control = el('div', 'spice-dose');
    const minus = button('−', () => dose(i, -1)), plus = button('+', () => dose(i, 1));
    minus.setAttribute('aria-label', 'Убавить: ' + spice.name); plus.setAttribute('aria-label', 'Добавить: ' + spice.name);
    control.append(minus, el('output', '', String(mix[i])), plus);
    tile.append(jar, control); box.append(tile);
  });
}
function dose(index, amount) { mix[index] = Math.min(3, Math.max(0, mix[index] + amount)); ingredients(); controls(); }
function ready() { return !!S.document && latest() && !S.editor && !S.busy && !S.recording && S.view === 'article' && !workingJob() && !pending; }
function setTarget(range, label) {
  if (!range || !version()) return;
  target = { ...range, quote: [...version().text].slice(range.start, range.end).join(''), root: S.root, file: S.file, version: S.version };
  $('#spice-target').textContent = label + ': ' + target.quote.replace(/\s+/g, ' ').slice(0, 130);
  controls();
}
function currentTarget() { return target && target.root === S.root && target.file === S.file && target.version === S.version ? target : null; }
function clearDrop() { $('#doc').classList.remove('spice-dragging'); document.querySelectorAll('.spice-drop-target').forEach(n => n.classList.remove('spice-drop-target')); }
function paragraph(node) { const p = node?.closest?.('#doc > p[data-md-block]'); return p && $('#doc').contains(p) ? p : null; }
function stopPicking() { picking = false; $('#spice-pick').classList.remove('selected'); $('#doc').classList.remove('spice-picking'); }
async function run(single = null) {
  if (!ready() || !currentTarget()) { toast('Сохрани текст и выбери абзац или выделение в последней версии'); return; }
  focusResult = document.activeElement?.id === 'spice-run';
  const selected = recipe();
  if (single != null) {
    selected.doses = mix.map((n, i) => i === single ? Math.max(1, n) : 0);
    selected.name = spices[single].name;
    selected.prompt = '';
  }
  pending = true; controls();
  try {
    const job = await startJob('spice', { recipe: selected, target: { start: target.start, end: target.end, quote: target.quote } });
    if (job) { resultJob = null; shown = ''; $('#spice-result').hidden = true; emit('pane', { pane: 'spices' }); }
  } finally { pending = false; controls(); jobs(); }
}
function animate(job) {
  document.querySelectorAll('.spice-working').forEach(n => { n.classList.remove('spice-working'); n.removeAttribute('aria-busy'); });
  $('#doc').dataset.spiceMotion = S.settings.spice_motion || 'ripple';
  if (!job || !['queued', 'running'].includes(job.status) || S.editor || job.version !== S.version) return;
  const range = job.target || target;
  if (!range) return;
  for (const node of document.querySelectorAll('#doc > [data-md-block]')) {
    const block = editor.blockRange(node);
    if (block && block.start < range.end && block.end > range.start) { node.classList.add('spice-working'); node.setAttribute('aria-busy', 'true'); }
  }
}
function jobs() {
  const job = S.jobs.find(j => j.kind === 'spice' && !ignored.has(j.id) && j.version === S.version);
  const active = S.jobs.find(j => j.kind === 'spice' && ['queued', 'running'].includes(j.status));
  animate(active);
  if (active) $('#spice-status').textContent = 'Подбираем формулировки. Текст пока не меняется.';
  else if (job?.status === 'succeeded') {
    $('#spice-status').textContent = job.result.base_revision === S.revision ? 'Предложения готовы — выбери, что оставить' : 'Статья изменилась. Получи предложения для текущей версии.';
    if (shown !== job.id) { shown = job.id; resultJob = job; showResult(job.result); }
  } else if (job && ['failed', 'conflict', 'interrupted'].includes(job.status)) $('#spice-status').textContent = job.message;
  controls();
}
function showResult(result) {
  $('#spice-result').hidden = false; $('#spice-summary').textContent = result.summary;
  $('#spice-changes').replaceChildren();
  result.changes.forEach((change, index) => {
    const card = el('article', 'spice-change'); card.dataset.index = index;
    const heading = el('label', 'spice-choice'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = true; checkbox.addEventListener('change', controls);
    heading.append(checkbox, document.createTextNode('Оставить правку'));
    const before = el('blockquote', 'spice-before', change.quote);
    const label = el('label', 'spice-prompt', 'Новая формулировка');
    const input = document.createElement('textarea'); input.value = change.variants[0]; input.maxLength = 16000; label.append(input);
    card.append(heading, before);
    if (change.variants.length > 1) {
      const options = document.createElement('select'); options.setAttribute('aria-label', 'Вариант замены');
      change.variants.forEach((v, i) => options.append(new Option('Вариант ' + (i + 1), String(i))));
      options.addEventListener('change', () => input.value = change.variants[Number(options.value)]); card.append(options);
    }
    card.append(label, el('p', 'fine-print', change.why)); $('#spice-changes').append(card);
  });
  if (!result.changes.length) $('#spice-summary').textContent = result.summary || 'Уместных изменений не нашлось. Можно изменить рецепт.';
  controls();
  if (S.pane === 'spices') {
    $('#spice-result').scrollIntoView({ block: 'nearest' });
    if (focusResult && (document.activeElement === document.body || document.activeElement?.id === 'spice-run')) {
      $('#spice-summary').tabIndex = -1;
      $('#spice-summary').focus({ preventScroll: true });
    }
  }
  focusResult = false;
}
function controls() {
  if (!$('#spice-run')) return;
  $('#spice-run').textContent = $('#spice-mode').value === 'reduce' ? 'Убавить лишнее' : 'Приправить';
  $('#spice-run').disabled = !ready() || !currentTarget() || (!mix.some(Boolean) && !$('#spice-prompt').value.trim());
  $('#spice-apply').disabled = !resultJob || S.busy || !!S.editor || !!workingJob() || resultJob.result.base_revision !== S.revision || !latest() || !$('#spice-changes input:checked');
  for (const id of ['spice-pick', 'spice-all']) $('#' + id).disabled = !ready();
  $('#spice-selection').disabled = !ready() || !lastSelection;
}
async function apply() {
  if ($('#spice-apply').disabled) return;
  const jobId = resultJob.id;
  const choices = [...document.querySelectorAll('.spice-change')].filter(card => card.querySelector('input').checked).map(card => ({ index: Number(card.dataset.index), text: card.querySelector('textarea').value }));
  const out = await operation('/api/spice-apply', { job_id: jobId, choices });
  if (!out) return;
  ignored.add(jobId); resultJob = null; $('#spice-result').hidden = true;
  if (out.version) { S.version = out.version; emit('version'); setView('diff'); }
  if (out.warning) toast(out.warning, true);
  else toast(out.unchanged ? 'Текст остался прежним' : 'Правки сохранены в новой версии');
}
function reset() {
  focusResult = false;
  target = lastSelection = resultJob = null; shown = ''; stopPicking(); clearDrop();
  $('#spice-target').textContent = 'Выбери область обработки'; $('#spice-result').hidden = true; $('#spice-status').textContent = '';
  controls(); jobs();
}
export function initSpices() {
  ingredients(); recipes().catch(() => { $('#spice-status').textContent = 'Перезапусти Гранку, чтобы включить приправы'; });
  $('#spice-settings').addEventListener('click', openSettings);
  $('#spice-recipe').addEventListener('change', chooseRecipe);
  $('#spice-mode').addEventListener('change', controls); $('#spice-prompt').addEventListener('input', controls);
  $('#spice-new').addEventListener('click', () => { $('#spice-recipe-editor').open = true; $('#spice-recipe-name').value = ''; $('#spice-recipe-name').focus(); });
  $('#spice-save').addEventListener('click', () => saveRecipe()); $('#spice-update').addEventListener('click', () => saveRecipe(true)); $('#spice-delete').addEventListener('click', () => saveRecipe(false, true));
  $('#spice-pick').addEventListener('click', () => { picking = !picking; $('#spice-pick').classList.toggle('selected', picking); $('#doc').classList.toggle('spice-picking', picking); $('#spice-target').textContent = picking ? 'Нажми на нужный абзац в статье. Esc — отмена выбора.' : target ? target.quote.slice(0, 130) : 'Выбери область обработки'; if (picking) $('#inspector').classList.remove('mobile-open'); });
  $('#spice-selection').addEventListener('click', () => { stopPicking(); setTarget(lastSelection, 'Выделение'); });
  $('#spice-all').addEventListener('click', () => { stopPicking(); setTarget({ start: 0, end: [...version().text].length }, 'Вся статья'); });
  $('#spice-run').addEventListener('click', () => run()); $('#spice-apply').addEventListener('click', apply);
  $('#spice-discard').addEventListener('click', () => { if (resultJob) ignored.add(resultJob.id); resultJob = null; $('#spice-result').hidden = true; $('#spice-status').textContent = 'Оставлен исходный текст'; controls(); });
  document.addEventListener('selectionchange', () => { const range = editor.selection(); if (range) { lastSelection = range; controls(); } });
  $('#doc').addEventListener('click', event => {
    if (!picking) return;
    const p = paragraph(event.target); if (!p) return;
    event.preventDefault(); event.stopImmediatePropagation(); setTarget(editor.blockRange(p), 'Абзац'); stopPicking();
    if (getComputedStyle($('#inspector-close')).display !== 'none') $('#inspector').classList.add('mobile-open');
  }, true);
  $('#doc').addEventListener('dragover', event => {
    if (![...event.dataTransfer.types].includes(MIME)) return;
    event.preventDefault(); event.stopImmediatePropagation(); clearDrop();
    const p = paragraph(event.target); if (p && ready()) { event.dataTransfer.dropEffect = 'copy'; p.classList.add('spice-drop-target'); }
    else event.dataTransfer.dropEffect = 'none';
  }, true);
  $('#doc').addEventListener('drop', event => {
    if (![...event.dataTransfer.types].includes(MIME)) return;
    event.preventDefault(); event.stopImmediatePropagation(); clearDrop();
    let payload; try { payload = JSON.parse(event.dataTransfer.getData(MIME)); } catch (_) { return; }
    const p = paragraph(event.target);
    if (!p || !ready() || payload.root !== S.root || payload.file !== S.file || payload.version !== S.version || !Number.isInteger(payload.index) || payload.index < 0 || payload.index > 3) return;
    setTarget(editor.blockRange(p), 'Абзац'); run(payload.index);
  }, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { stopPicking(); clearDrop(); } });
  events.addEventListener('settings', () => { ingredients(); jobs(); });
  events.addEventListener('jobs', jobs); events.addEventListener('controls', controls);
  events.addEventListener('document', reset); events.addEventListener('version', reset);
}
