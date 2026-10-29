import { $, api } from './core.js';

const storageKey = 'granka-new-article-ai';
const names = ['Без добавок', 'Щепотка', 'Умеренно', 'Щедро'];
export function initArticleDraft(changed) {
  let recipes = [], pending = false, uncertain = false, issued = null, job = null, accepted = '', contextSerial = 0, recipeName = 'Своя смесь';
  const request = () => ({title: $('#newarticle-title').value, audience: $('#newarticle-audience').value,
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
    catch { $('#newarticle-ai-status').textContent += ' Не удалось сохранить запуск в этой вкладке; не обновляй её до завершения.'; }
  }
  function draw() {
    const ai = $('#newarticle-mode').value === 'ai';
    $('#newarticle-ai').hidden = !ai;
    $('#newarticle-local-note').hidden = ai;
    $('#newarticle-ai').setAttribute('aria-busy', String(pending));
    for (const n of document.querySelectorAll('[data-draft-parameter], #newarticle-title, #newarticle-audience, #newarticle-purpose')) n.disabled = pending || uncertain;
    $('#newarticle-mode').disabled = pending || uncertain;
    $('#newarticle-generate').disabled = pending;
    $('#newarticle-generate').textContent = pending ? 'Пишу заготовку…' : uncertain ? 'Проверить запуск' : job?.status === 'succeeded' ? 'Сгенерировать заново' : 'Написать заготовку';
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
    $('#newarticle-style-info').textContent = 'Проверяю авторские правила и корпус…';
    try {
      const [info, saved] = await Promise.all([
        api('/api/article-draft/context?profile=' + $('#newarticle-profile').value), api('/api/spice-recipes')]);
      if (serial !== contextSerial) return;
      recipes = [...saved.builtins, ...saved.recipes];
      const select = $('#newarticle-recipe'), old = select.value;
      select.replaceChildren(new Option('Своя смесь', ''), ...recipes.map(r => new Option(r.name, r.id)));
      if ([...select.options].some(n => n.value === old)) select.value = old;
      const matching = recipes.find(r => r.name === recipeName);
      if (matching) select.value = matching.id;
      const corpus = info.corpus;
      $('#newarticle-style-info').textContent = `Правил автора: ${info.rules}. ` +
        (corpus.status === 'ok' ? `В корпусе «${corpus.profile_label}»: ${corpus.files} текстов; отправится до 3 коротких примеров.` :
          `${corpus.reason} Для голоса пока доступны только найденные авторские правила.`) +
        ` Исполнитель: ${info.provider === 'codex' ? 'Codex / ChatGPT' : 'OpenRouter'}${info.model ? ' · ' + info.model : ''}.`;
    } catch (error) { if (serial === contextSerial) $('#newarticle-style-info').textContent = error.message + ' Данные стиля будут проверены при запуске.'; }
  }
  async function poll() {
    while (true) {
      job = (await api('/api/job?id=' + job.id)).job; save();
      if (job.status === 'succeeded') {
        $('#newarticle-draft-text').value = job.result.text;
        accepted = JSON.stringify(issued.request);
        const style = job.result.style;
        $('#newarticle-ai-status').textContent = `Заготовка готова. Правил: ${style.rules}, примеров голоса: ${style.examples}. Можно править текст перед сохранением.`;
        save(); return;
      }
      if (!['queued', 'running'].includes(job.status)) {
        $('#newarticle-ai-status').textContent = job.message + ' Автоматического повтора нет. Новый запуск — отдельный запрос к ИИ.';
        return;
      }
      $('#newarticle-ai-status').textContent = job.message + ' Можно закрыть мастер: запуск сохранится в этой вкладке.';
      await new Promise(resolve => setTimeout(resolve, 800));
    }
  }
  async function generate() {
    if (pending) return;
    if (!$('#newarticle-title').value.trim()) { $('#newarticle-ai-status').textContent = 'Сначала укажи тему статьи на первом шаге.'; return; }
    if (!uncertain) {
      issued = {request: request(), wizard_id: issued?.wizard_id || crypto.randomUUID(), operation_id: crypto.randomUUID()};
      job = null; accepted = ''; save();
    }
    pending = true; uncertain = false; draw(); changed();
    $('#newarticle-ai-status').textContent = 'Запускаю ИИ. Статья ещё не создана…';
    try {
      if (!job) job = (await api('/api/article-draft', {method: 'POST', body: {...issued.request, wizard_id: issued.wizard_id, operation_id: issued.operation_id}})).job;
      save(); await poll();
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && !job) {
        issued = null;
        $('#newarticle-ai-status').textContent = error.message + ' Проверь параметры перед запуском.';
      } else {
        uncertain = true;
        $('#newarticle-ai-status').textContent = error.message + ' Нажми «Проверить запуск»: будет использован прежний идентификатор, без второго запроса к модели.';
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
        uncertain = true; $('#newarticle-ai-status').textContent = 'Восстановлен прежний запуск. Нажми «Проверить запуск», чтобы получить его результат.';
      }
    }
  } catch { sessionStorage.removeItem(storageKey); }
  return {ready, busy: () => pending || uncertain, draw,
    values: () => ({...request(), draft_text: $('#newarticle-draft-text').value, draft_job: job?.id}),
    entered: () => { draw(); if ($('#newarticle-mode').value === 'ai') context(); },
    reset: () => { issued = job = null; accepted = ''; recipeName = 'Своя смесь'; uncertain = pending = false; $('#newarticle-draft-text').value = ''; sessionStorage.removeItem(storageKey); draw(); }};
}
