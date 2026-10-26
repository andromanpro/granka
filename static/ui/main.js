import {
  $,
  S,
  el,
  button,
  api,
  events,
  emit,
  version,
  annotations,
  latest,
  loadDocument,
  toast,
  allowNavigation,
} from "./core.js";
import { editor, initEditor, save, setView } from "./editor.js";
import { initMeter, measure } from "./meter.js";
import { initReview } from "./review.js";
import { initDeepReview } from "./deep-review.js";
import { initDiff } from "./diff.js";
import { initImages, openImages } from "./images.js";
import { initSettings } from "./settings.js";
import { initJobs, startJob, workingJob } from "./jobs.js";
import { initVoice } from "./voice.js";
import { initCover } from "./cover.js";
import { initTelegram } from "./telegram.js";
import { initSite } from "./site.js";
import { initPlanner } from "./planner.js";
import { initBatch } from "./batch.js";
import { initPreferences } from "./preferences.js";
import { initSpices } from "./spices.js";
import { initHistory, selectVersion } from "./history.js";
let sources = [], sourceFilter = "draft";
function pane(name) {
  S.pane = name;
  $("#meterwidget").hidden = name === "spices";
  document
    .querySelectorAll("[data-pane]")
    .forEach((b) => b.classList.toggle("selected", b.dataset.pane === name));
  document
    .querySelectorAll("[data-panel]")
    .forEach((p) => (p.hidden = p.dataset.panel !== name));
}
function controls() {
  if (!S.document) return;
  $("#docsel").disabled = S.busy || S.recording;
  $("#versel").disabled = $("#docsel").disabled;
  $("#opensrc").disabled = $("#docsel").disabled;
  $("#analysisbtn").disabled = S.busy || !!workingJob() || !latest();
  $("#analysisbtn").textContent = S.editor
    ? "Сохранить и проверить"
    : "Проверить текст";
  const editable =
    !S.busy && !S.recording && latest() && S.view === "article"
      ? "true"
      : "false";
  if ($("#doc").contentEditable !== editable)
    $("#doc").contentEditable = editable;
  $("#downloadbtn").disabled = S.busy;
  $("#readybtn").title =
    "Применить принятые решения, собрать версию и обновить Markdown-файл";
}
function documentHeader() {
  const select = $("#versel");
  select.replaceChildren();
  for (const v of S.document.versions)
    select.appendChild(new Option(v.id + " · " + (v.label || ""), v.id));
  select.value = S.version;
  document.title = (S.document.doc || "Статья") + " — Гранка";
  const docs = $("#docsel"),
    key = S.root + "|" + S.file;
  if (![...docs.options].some((o) => o.value === key))
    docs.appendChild(new Option(S.document.doc, key));
  docs.value = key;
  controls();
}
async function documents() {
  const requested = new URLSearchParams(location.search);
  if (requested.has("source_file")) {
    const root = requested.get("root"), relative = requested.get("source_file");
    const catalog = (await api("/api/sources")).sources || [];
    const source = catalog.flatMap(s => [s, ...(s.copies || [])]).find(s =>
      !s.external && s.root === root && s.source_rel === relative);
    if (!source) {
      $("#doc").textContent = "Статья по этой ссылке не найдена. Открой библиотеку и проверь источник.";
      toast("Статья по ссылке больше не доступна");
      return;
    }
    await openSource(source);
    return;
  }
  if (requested.has("source")) {
    await openSources();
    $("#source-search").value = requested.get("source").slice(0, 300);
    drawSources();
    return;
  }
  const { docs } = await api("/api/docs");
  const select = $("#docsel");
  select.replaceChildren();
  for (const d of docs || []) {
    if (!d.error) select.appendChild(new Option(d.doc, d.root + "|" + d.rel));
  }
  if (!select.options.length) {
    $("#doc").textContent = "Открой статью, чтобы начать.";
    return;
  }
  const remembered = localStorage.getItem("granka.doc");
  if ([...select.options].some((o) => o.value === remembered))
    select.value = remembered;
  const requestedKey = requested.get("root") + "|" + requested.get("f");
  if ([...select.options].some((o) => o.value === requestedKey))
    select.value = requestedKey;
  else if (requested.has("root") || requested.has("f")) {
    $("#doc").textContent = "Статья по этой ссылке не найдена. Открой библиотеку и проверь источник.";
    toast("Статья по ссылке больше не доступна");
    return;
  }
  const [root, file] = select.value.split("|");
  await loadDocument(root, file);
}
async function openSources() {
  if (!$("#source-dialog").open) $("#source-dialog").showModal();
  $("#source-search").value = "";
  $("#source-list").textContent = "Загружаю статьи…";
  try {
    sources = (await api("/api/sources")).sources || [];
    drawSources();
    await sourceLocations();
  } catch (error) {
    toast(error.message, true);
  }
}
let locationsRevision = null;
async function sourceLocations() {
  try {
    const result = await api("/api/library-locations");
    locationsRevision = result.revision;
    const list = $("#source-locations-list");
    list.replaceChildren();
    for (const item of result.locations) {
      const row = el("p", "source-location", item.path);
      row.append(el("small", null, item.files ? "Файлы: " + item.files.join(", ") : "Все статьи в папке"));
      list.append(row);
    }
    if (!result.locations.length) list.append(el("p", "fine-print", "Здесь появятся выбранные файлы и папки."));
    return true;
  } catch (error) {
    $("#source-location-status").textContent = error.status === 404
      ? "Для выбора файлов сохрани текущую статью и перезапусти Гранку: сервер ещё работает с прежней версией."
      : error.message;
    return false;
  }
}
async function connectSource(kind, picker) {
  if (!locationsRevision && !(await sourceLocations())) return;
  const buttons = ["#source-file", "#source-folder", "#source-path-add"].map($);
  for (const button of buttons) button.disabled = true;
  $("#source-location-status").textContent = picker ? "Выбери " + (kind === "file" ? "статью" : "папку") + " в системном окне…" : "Подключаю источник…";
  try {
    const result = await api("/api/library-locations", {method: "POST", body: {
      kind, picker, path: picker ? undefined : $("#source-path").value,
      expected_revision: locationsRevision,
    }});
    if (result.cancelled) {
      $("#source-location-status").textContent = "Выбор отменён.";
      return;
    }
    locationsRevision = result.revision;
    sources = (await api("/api/sources")).sources || [];
    drawSources();
    await sourceLocations();
    $("#source-location-status").textContent = result.selection ? "Файл выбран." : "Папка добавлена. Статьи доступны в библиотеке и календаре.";
    if (result.selection) await openSource(result.selection);
    else $("#source-search").focus();
  } catch (error) {
    $("#source-location-status").textContent = error.status === 404
      ? "Сохрани текущую статью и перезапусти Гранку, чтобы включить выбор файлов."
      : error.message;
    if (error.conflict) await sourceLocations();
  } finally {
    for (const button of buttons) button.disabled = false;
  }
}
async function openSource(source) {
  try {
    if (!(await allowNavigation())) return;
    if (!source.has_granka) await api("/api/create-granka", { method:"POST", body:{root:source.root, rel:source.rel, operation_id:crypto.randomUUID()} });
    $("#source-dialog").close();
    await loadDocument(source.root, source.granka_rel);
  } catch (error) { toast(error.message, true); }
}
function drawSources() {
  const list = $("#source-list"),
    q = $("#source-search").value.toLowerCase();
  list.replaceChildren();
  document.querySelectorAll('[data-source-filter]').forEach(b => {
    b.classList.toggle('selected', b.dataset.sourceFilter === sourceFilter);
    const count = sources.filter(s => b.dataset.sourceFilter === 'all' || s.state === b.dataset.sourceFilter).length;
    b.querySelector('span').textContent = count;
  });
  const matches = sources.filter(s => (sourceFilter === 'all' || s.state === sourceFilter) &&
    (s.name + ' ' + s.dir + ' ' + (s.copies || []).map(c => c.rel).join(' ')).toLowerCase().includes(q));
  $("#source-summary").textContent = `Найдено: ${matches.length} · доступны и в календаре`;
  for (const source of matches) {
    const row = el('article', 'source-item');
    const main = source.external ? el('a', 'source-open') : button('', () => openSource(source), 'source-open');
    if (source.external) { main.href = source.url; main.target = '_blank'; main.rel = 'noopener noreferrer'; }
    const label = source.state === 'published' ? 'Опубликовано' + (source.published_date ? ' · ' + source.published_date : '') : source.planning_state === 'excluded' ? 'Исключено из плана' :
      source.planned_date ? 'В плане · ' + source.planned_date : (source.readiness?.percent ?? 0) + '% · без даты';
    const location = source.dir === '.' ? (source.root === 'blog' ? 'Блог' : 'Рабочая папка') : source.dir;
    main.append(el('strong', null, source.name), el('small', null, (source.channel === 'telegram' ? 'Telegram · ' : '') + location), el('span', 'tag', label));
    row.append(main);
    if (source.url && source.state === 'published' && !source.external) {
      const link = el('a', 'source-publication', 'Читать публикацию');
      link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      row.append(link);
    }
    if (source.state !== 'published') row.append(button(source.planning_state === 'excluded' ? 'Вернуть в план' : 'В календарь', async () => {
      try {
        if (source.planning_state === 'excluded') {
          const current = await api('/api/publication-plan');
          await api('/api/publication-plan', {method:'POST', body:{action:'include', id:source.planning_id, expected_revision:current.revision}});
        }
        $('#source-dialog').close(); $('#planner-btn').click();
      } catch (error) { toast(error.message, true); }
    }, 'source-plan quiet'));
    if (source.copies?.length) {
      const copies = el('details', 'source-copies'); copies.append(el('summary', null, 'Другие исходники · ' + source.copies.length));
      for (const copy of source.copies) copies.append(button(copy.copy_label + ': ' + copy.rel, () => openSource(copy), 'quiet'));
      row.append(copies);
    }
    list.append(row);
  }
  if (!matches.length) list.append(el('p', 'fine-print', 'По этому фильтру статей нет.'));
}
async function gitStatus() {
  if (!S.document) return;
  try {
    const result = await api(
      "/api/git?" + new URLSearchParams({ root: S.root, f: S.file }),
    );
    $("#syncbtn").textContent = !result.repo
      ? "Без Git"
      : result.dirty
        ? "Синхронизировать · " + result.dirty
        : "Синхронизировать";
    $("#syncbtn").title = result.repo
      ? "Последний снимок: " + (result.last?.msg || "—")
      : "Для этой папки нет Git";
  } catch (_) {}
}
async function sync() {
  if (S.editor && !(await save())) return;
  try {
    const out = await api("/api/git-sync", {
      method: "POST",
      body: { root: S.root, rel: S.file, message: "Гранка: " + S.document.doc },
    });
    if (!out.ok) throw new Error(out.error);
    toast(
      out.pushed
        ? "Отправлено в репозиторий"
        : out.nothing
          ? "Изменений нет"
          : "Снимок сохранён локально",
    );
    gitStatus();
  } catch (error) {
    toast(error.message, true);
  }
}
initEditor();
initReview();
initDeepReview();
initMeter();
initDiff();
initHistory();
initImages();
initCover();
initTelegram();
initSite();
initPlanner();
initBatch();
initPreferences();
initSpices();
initSettings();
initVoice();
initJobs();
events.addEventListener("document", () => {
  documentHeader();
  gitStatus();
});
events.addEventListener("controls", controls);
events.addEventListener("pane", (e) => pane(e.detail.pane));
events.addEventListener("sync", gitStatus);
$("#docsel").addEventListener("change", () => {
  const [root, file] = $("#docsel").value.split("|");
  $("#docsel").value = S.root + "|" + S.file;
  loadDocument(root, file).catch(() => {});
});
$("#versel").addEventListener("change", async () => {
  const target = $("#versel").value;
  $("#versel").value = S.version;
  if (target !== S.version) await selectVersion(target);
});
document
  .querySelectorAll("[data-pane]")
  .forEach((b) => b.addEventListener("click", () => pane(b.dataset.pane)));
$("#meterwidget").addEventListener("click", (e) => {
  if (e.target.closest("#meterrefresh")) return;
  pane("meter");
});
$("#analysisbtn").addEventListener("click", async () => {
  if (S.editor && !(await save())) return;
  await startJob("analysis");
});
$("#opensrc").addEventListener("click", openSources);
$("#source-file").addEventListener("click", () => connectSource("file", true));
$("#source-folder").addEventListener("click", () => connectSource("folder", true));
$("#source-path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  connectSource($("#source-path-kind").value, false);
});
$("#source-close").addEventListener("click", () => $("#source-dialog").close());
$("#source-search").addEventListener("input", drawSources);
document.querySelectorAll('[data-source-filter]').forEach(b => b.addEventListener('click', () => { sourceFilter = b.dataset.sourceFilter; drawSources(); }));
events.addEventListener('sources', openSources);
$("#syncbtn").addEventListener("click", sync);
$("#inspector-toggle").addEventListener("click", () =>
  $("#inspector").classList.toggle("mobile-open"),
);
$("#inspector-close").addEventListener("click", () =>
  $("#inspector").classList.remove("mobile-open"),
);
$("#mobile-meter").addEventListener("click", () => {
  $("#inspector").classList.add("mobile-open");
  pane("meter");
});
window.granka = {
  state: S,
  editor,
  loadDocument,
  api,
  startJob,
  annotations,
  version,
  pane,
  openImages,
};
pane("review");
documents().catch(() => {});
setInterval(gitStatus, 60000);
