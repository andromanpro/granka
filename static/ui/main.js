import { t, localizedOption, locale, localize } from './i18n.js';
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
import { initNewArticle } from './new-article.js';
import { initTrash, trashArticle, refreshTrashCount } from './trash.js';
import { initReview } from "./review.js";
import { initDeepReview } from "./deep-review.js";
import { initDiff } from "./diff.js";
import { initImages, openImages } from "./images.js";
import { openSettings, initSettings } from "./settings.js";
import { initJobs, startJob, workingJob } from "./jobs.js";
import { initVoice } from "./voice.js";
import { initCover } from "./cover.js";
import { openTelegram, initTelegram } from "./telegram.js";
import { initSite } from "./site.js";
import { openPlanner, initPlanner } from "./planner.js";
import { initBatch } from "./batch.js";
import { initPreferences } from "./preferences.js";
import { initSpices } from "./spices.js";
import { initHistory, selectVersion } from "./history.js";
import { initPublicationState } from './publication-state.js';
import { initWorkspace, restoreWorkspace, navigateWorkspace, showWorkspacePage, applyAppearance, readAppearance } from './workspace.js';
const requestedSection = location.hash.slice(1);
let sources = [], sourceFilter = "draft";
function pane(name) {
  S.pane = name;
  emit("pane-selected");
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
  localize($("#analysisbtn"),"textContent",()=>S.editor
    ? t("ui.ac4ba388b05f")
    : t("ui.cbc2f7b99d50"));
  const editable =
    !S.busy && !S.recording && latest() && S.view === "article"
      ? "true"
      : "false";
  if ($("#doc").contentEditable !== editable)
    $("#doc").contentEditable = editable;
  $("#downloadbtn").disabled = S.busy;
  localize($("#readybtn"),"title",()=>t("ui.80853f9c16e3"));
}
function documentHeader() {
  const select = $("#versel");
  select.replaceChildren();
  for (const v of S.document.versions)
    select.appendChild(new Option(v.id + " · " + (v.label || ""), v.id));
  select.value = S.version;
  localize(document,"title",()=>(S.document.doc || t("ui.4f0548d13284")) + t("ui.842d41ecfe60"));
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
      localize($("#doc"),"textContent",()=>t("ui.3c18dbc4647b"));
      toast(t("ui.9ddf73330a49"));
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
    localize($("#doc"),"textContent",()=>t("ui.c9d067a2ccbf"));
    return;
  }
  const remembered = localStorage.getItem("granka.doc");
  if ([...select.options].some((o) => o.value === remembered))
    select.value = remembered;
  const requestedKey = requested.get("root") + "|" + requested.get("f");
  if ([...select.options].some((o) => o.value === requestedKey))
    select.value = requestedKey;
  else if (requested.has("root") || requested.has("f")) {
    localize($("#doc"),"textContent",()=>t("ui.3c18dbc4647b"));
    toast(t("ui.9ddf73330a49"));
    return;
  }
  const [root, file] = select.value.split("|");
  await loadDocument(root, file);
}
async function openSources() {
  showWorkspacePage("#source-dialog", "library");
  $("#source-search").value = "";
  localize($("#source-list"),"textContent",()=>t("ui.2e0d60690250"));
  try {
    sources = (await api("/api/sources")).sources || [];
    drawSources();
    await sourceLocations();
    await refreshTrashCount();
  } catch (error) {
    localize($("#source-list"),"textContent",()=>t("ui.e8dfc5b5dbf7"));
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
      row.append(el("small", null, ()=>item.files ? t("ui.36c5581c4fba") + item.files.join(", ") : t("ui.38090aec74a8")));
      list.append(row);
    }
    if (!result.locations.length) list.append(el("p", "fine-print", ()=>t("ui.4194835a6643")));
    return true;
  } catch (error) {
    localize($("#source-location-status"),"textContent",()=>error.status === 404
      ? t("ui.9fe1d3e68dea")
      : error.message);
    return false;
  }
}
async function connectSource(kind, picker) {
  if (!locationsRevision && !(await sourceLocations())) return;
  const buttons = ["#source-file", "#source-folder", "#source-path-add"].map($);
  for (const button of buttons) button.disabled = true;
  localize($("#source-location-status"),"textContent",()=>picker ? t("ui.d472e521d736") + (kind === "file" ? t("ui.4c531726b656") : t("ui.17fd2d94c7d5")) + t("ui.c9cf281b4635") : t("ui.91a337f9ce2e"));
  try {
    const result = await api("/api/library-locations", {method: "POST", body: {
      kind, picker, path: picker ? undefined : $("#source-path").value,
      expected_revision: locationsRevision,
    }});
    if (result.cancelled) {
      localize($("#source-location-status"),"textContent",()=>t("ui.c709eefbde9d"));
      return;
    }
    locationsRevision = result.revision;
    sources = (await api("/api/sources")).sources || [];
    drawSources();
    await sourceLocations();
    localize($("#source-location-status"),"textContent",()=>result.selection ? t("ui.a09307c73e87") : t("ui.c3859e7cbc41"));
    if (result.selection) await openSource(result.selection);
    else $("#source-search").focus();
  } catch (error) {
    localize($("#source-location-status"),"textContent",()=>error.status === 404
      ? t("ui.cc8adc4d67f8")
      : error.message);
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
  localize($("#source-summary"),"textContent",()=>t("ui.136c9448bca9",matches.length));
  for (const source of matches) {
    const row = el('article', 'source-item');
    row.dataset.state = source.state;
    row.style.setProperty('--tile-index', list.children.length % 3);
    const motif = el('div', 'source-motif', ()=>source.state === 'published' ? t("ui.da0d2a9761ee") : ')))');
    motif.setAttribute('aria-hidden', 'true');
    row.append(motif);
    const main = source.external ? el('a', 'source-open') : button('', () => openSource(source), 'source-open');
    if (source.external) { main.href = source.url; main.target = '_blank'; main.rel = 'noopener noreferrer'; }
    const label = source.state === 'published' ? t("ui.382ded9c6877") + (source.published_date ? ' · ' + source.published_date : '') : source.planning_state === 'excluded' ? t("ui.9106263309ca") :
      source.planned_date ? t("ui.b04293b90df8") + source.planned_date : (source.readiness?.percent ?? 0) + t("ui.60eaf68ae9d9");
    const location = source.dir === '.' ? (source.root === 'blog' ? t("ui.c31cdbd07e6c") : t("ui.bf5423fe8102")) : source.dir;
    main.append(el('strong', null, source.name), el('small', null, (source.channel === 'telegram' ? 'Telegram · ' : '') + location), el('span', 'tag', label));
    row.append(main);
    row.append(button(()=>t("ui.4a252fb127a9"), () => trashArticle(source), 'source-trash quiet'));
    if (source.url && source.state === 'published' && !source.external) {
      const link = el('a', 'source-publication', ()=>t("ui.0c77138c469f"));
      link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      row.append(link);
    }
    if (source.state !== 'published') row.append(button(()=>source.planning_state === 'excluded' ? t("ui.c44c4bb3f45d") : t("ui.1d2c820bb4da"), async () => {
      try {
        if (source.planning_state === 'excluded') {
          const current = await api('/api/publication-plan');
          await api('/api/publication-plan', {method:'POST', body:{action:'include', id:source.planning_id, expected_revision:current.revision}});
        }
        $('#source-dialog').close(); $('#planner-btn').click();
      } catch (error) { toast(error.message, true); }
    }, 'source-plan quiet'));
    if (source.copies?.length) {
      const copies = el('details', 'source-copies'); copies.append(el('summary', null, ()=>t("ui.ce50f63f01e9") + source.copies.length));
      for (const copy of source.copies) {
        const item = el('div', 'source-copy-row');
        item.append(button(copy.copy_label + ': ' + copy.rel, () => openSource(copy), 'quiet'),
          button(()=>t("ui.4a252fb127a9"), () => trashArticle(copy), 'source-trash quiet'));
        copies.append(item);
      }
      row.append(copies);
    }
    list.append(row);
  }
  if (!matches.length) list.append(el('p', 'fine-print', ()=>t("ui.c7435b691e1c")));
}
async function gitStatus() {
  if (!S.document) return;
  try {
    const result = await api(
      "/api/git?" + new URLSearchParams({ root: S.root, f: S.file }),
    );
    localize($("#syncbtn"),"textContent",()=>!result.repo
      ? t("ui.f846dc349965")
      : result.dirty
        ? t("ui.1cd7801d4af8") + result.dirty
        : t("ui.cc1a5e44ed50"));
    localize($("#syncbtn"),"title",()=>result.repo
      ? t("ui.d923a0e684e8") + (result.last?.msg || "—")
      : t("ui.380dee79c84a"));
  } catch (_) {}
}
async function sync() {
  if (S.editor && !(await save())) return;
  try {
    const out = await api("/api/git-sync", {
      method: "POST",
      body: { root: S.root, rel: S.file, message: t("ui.1f8b909a3e74") + S.document.doc },
    });
    if (!out.ok) throw new Error(out.error);
    toast(
      out.pushed
        ? t("ui.54bb7d451c63")
        : out.nothing
          ? t("ui.2f3d5a3bd2b6")
          : t("ui.6e34125ecccd"),
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
initPublicationState();
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
events.addEventListener('trash-changed', async () => {
  try { sources = (await api('/api/sources')).sources || []; drawSources(); await refreshTrashCount(); }
  catch (error) { toast(error.message, true); }
});
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
initNewArticle();
initTrash();
pane("review");
applyAppearance(readAppearance());
initWorkspace({ library: openSources, calendar: openPlanner, telegram: openTelegram, settings: openSettings,
  help: () => showWorkspacePage('#helpdialog', 'help'), pane, setView });
$("#reader-history").addEventListener("click", () => navigateWorkspace("history"));
documents().then(() => restoreWorkspace(requestedSection)).catch(() => {});
setInterval(gitStatus, 60000);

$("#help-close").addEventListener("click", () => $("#helpdialog").close());
