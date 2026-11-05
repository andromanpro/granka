import { t, localizedOption, locale, localize } from './i18n.js';
import {uiText} from './i18n.js';
import { $, S, api, el, button, loadDocument, toast, emit, allowNavigation } from "./core.js";
import { editor } from "./editor.js";

import { showWorkspacePage } from './workspace.js';
const statusNames = { idea: t("ui.da0ae5cf7773"), draft: t("ui.65fd24f1cb37"), ready: t("ui.ef05d57959cf"), published: t("ui.382ded9c6877") };
const channelNames = { article: t("ui.4f0548d13284"), telegram: "Telegram" };
const weekdays = [t("ui.31a8ee2fb6b7"), t("ui.839b2d5c5aa3"), t("ui.7ec16b9e41ff"), t("ui.956618723dd8"), t("ui.4ca114c23be8"), t("ui.95dab017a4da"), t("ui.7971972c7cbb")];
let state = { entries: [], revision: "" }, month = new Date(), editing = null, proposal = null, busy = false;
const iso = (value) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
const visible = (entry) => $("#planner-channel").value === "all" || entry.channel === $("#planner-channel").value;

function invalidate() {
  proposal = null;
  $("#planner-apply").disabled = true;
  $("#planner-proposal").replaceChildren();
}
async function operation(fn) {
  if (busy) return;
  busy = true;
  $("#planner-error").textContent = "";
  $("#planner-entry-error").textContent = "";
  $("#planner-entry-form").inert = true;
  $("#planner-schedule").inert = true;
  try { await fn(); }
  catch (error) {
    $("#planner-error").textContent = error.message;
    $("#planner-entry-error").textContent = error.message;
    toast(error.message, true);
    try { state = await api("/api/publication-plan"); invalidate(); render(); } catch (_) {}
  } finally {
    busy = false;
    $("#planner-entry-form").inert = false;
    $("#planner-schedule").inert = false;
  }
}
async function save(value, id = null) {
  state = await api("/api/publication-plan", { method: "POST", body: { action: "save", id, entry: value, expected_revision: state.revision } });
  invalidate(); render();
}
async function changeEntry(item, action) {
  state = await api("/api/publication-plan", { method: "POST", body: { action, id: item.id, expected_revision: state.revision } });
  invalidate(); render();
}
function readinessLabel(item) {
  const readiness = item.readiness;
  if (item.source_error) return t("ui.ea45c29c3aa3");
  if (!readiness) return statusNames[item.status];
  return `${readiness.percent}% · ` + ({steps:t("ui.705567f4dbb2"), author:t("ui.2617469c14af"), published:t("ui.c2c5d71c798d"), status:t("ui.d320a44d0a9e")}[readiness.basis] || t("ui.d5715e612274"));
}
function card(item) {
  const node = el("article", `planner-card channel-${item.channel} status-${item.status}`);
  node.title = item.title + " · " + readinessLabel(item);
  node.dataset.id = item.id;
  const main = button("", () => openEntry(item), "planner-card-main");
  main.append(el("small", null, ()=>channelNames[item.channel] + (item.pinned ? t("ui.075a31004053") : "")), el("strong", null, item.title), el("span", "planner-status", statusNames[item.status]));
  const progress = el("progress", "planner-progress"); progress.max = 100; progress.value = item.readiness?.percent || 0;
  progress.setAttribute("aria-label", t("ui.85f3a836ee37") + readinessLabel(item));
  main.append(progress, el("small", "planner-readiness-label", readinessLabel(item)));
  node.append(main);
  if (!item.readonly && item.status !== "published") {
    const actions = el("div", "planner-card-actions");
    if (item.date) actions.append(button(()=>t("ui.c3c76d85861e"), () => operation(() => changeEntry(item, "unschedule")), "quiet"));
    actions.append(button(()=>item.excluded ? t("ui.c44c4bb3f45d") : t("ui.970575549a13"), () => operation(() => changeEntry(item, item.excluded ? "include" : "exclude")), "quiet"));
    node.append(actions);
  }
  node.draggable = !item.excluded && !item.pinned && !item.readonly && !item.source_error && item.status !== "published";
  node.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("application/x-granka-plan", item.id);
    event.dataTransfer.effectAllowed = "move";
  });
  return node;
}
function dropTarget(node, date) {
  node.addEventListener("dragover", (event) => {
    if (event.dataTransfer.types.includes("application/x-granka-plan")) { event.preventDefault(); node.classList.add("drag-over"); }
  });
  node.addEventListener("dragleave", () => node.classList.remove("drag-over"));
  node.addEventListener("drop", (event) => {
    event.preventDefault(); node.classList.remove("drag-over");
    const item = state.entries.find((e) => e.id === event.dataTransfer.getData("application/x-granka-plan"));
    if (!item || item.excluded || item.pinned || item.readonly || item.source_error || item.status === "published") return;
    operation(() => save({ ...item, date }, item.id));
  });
}
function render() {
  localize($("#planner-month"),'textContent',()=>month.toLocaleDateString(locale(), { month: "long", year: "numeric" }));
  const queue = $("#planner-queue"), grid = $("#planner-calendar"), filtered = state.entries.filter(visible), entries = filtered.filter(e => !e.excluded);
  const excluded = $("#planner-excluded"), excludedEntries = filtered.filter(e => e.excluded);
  excluded.replaceChildren();
  for (const item of excludedEntries) excluded.append(card(item));
  $("#planner-excluded-count").textContent = excludedEntries.length;
  queue.replaceChildren(); grid.replaceChildren();
  const undated = entries.filter((e) => !e.date);
  $("#planner-queue-count").textContent = undated.length;
  for (const item of undated) queue.append(card(item));
  if (!undated.length) queue.append(el("p", "planner-empty", ()=>t("ui.98a0e176a2d8")));
  for (const name of weekdays) grid.append(el("div", "planner-weekday", name));
  const first = new Date(month.getFullYear(), month.getMonth(), 1, 12);
  first.setDate(first.getDate() - (first.getDay() + 6) % 7);
  const today = iso(new Date());
  for (let index = 0; index < 42; index++) {
    const current = new Date(first); current.setDate(first.getDate() + index);
    const date = iso(current), node = el("section", "planner-day");
    node.dataset.date = date;
    if (current.getMonth() !== month.getMonth()) node.classList.add("outside-month");
    if (date === today) node.classList.add("is-today");
    const head = button(String(current.getDate()), () => openEntry(null, date), "planner-day-number");
    localize(head,'ariaLabel',()=>t("ui.ff2ba50e90c6") + current.toLocaleDateString(locale()));
    node.append(head);
    for (const item of entries.filter((e) => e.date === date)) node.append(card(item));
    dropTarget(node, date); grid.append(node);
  }
  const prefix = iso(new Date(month.getFullYear(), month.getMonth(), 1)).slice(0, 7), inMonth = entries.filter((e) => e.date?.startsWith(prefix));
  localize($("#planner-summary"),"textContent",()=>t("ui.99026e48759d",inMonth.length,inMonth.filter((e) => e.status === "published").length,undated.length,excludedEntries.length));
}
function sourceControls() {
  localize($("#planner-entry-source"),"textContent",()=>editing.file ? t("ui.aaaab05fd4bf") + editing.file.split(/[\\/]/).pop() : t("ui.320ac14f351d"));
  $("#planner-entry-link").hidden = !!editing.readonly || editing.origin === "library" || !S.document;
  $("#planner-entry-unlink").hidden = !!editing.readonly || editing.origin === "library" || !editing.file;
  $("#planner-entry-open").hidden = !editing.file;
  $("#planner-entry-visit").hidden = !editing.url;
  if (editing.url) $("#planner-entry-visit").href = editing.url;
}
function openEntry(item = null, date = "") {
  if (busy) return;
  editing = item ? { ...item } : { title: "", channel: $("#planner-channel").value === "telegram" ? "telegram" : "article", status: "idea", date, root: "", file: "", url: "", notes: "", pinned: false };
  localize($("#planner-entry-heading"),"textContent",()=>editing.readonly ? t("ui.a850d8e574f7") : item ? t("ui.7e00703fc124") : t("ui.f441e43014ab"));
  for (const key of ["title", "channel", "status", "date", "url", "notes"]) {
    const input = $("#planner-entry-" + key); input.value = editing[key] || ""; input.disabled = !!editing.readonly;
  }
  $("#planner-entry-pinned").checked = !!editing.pinned;
  $("#planner-entry-pinned").disabled = !!editing.readonly;
  $("#planner-entry-channel").disabled = !!editing.readonly || editing.origin === "library";
  $("#planner-entry-progress").value = editing.progress ?? "";
  $("#planner-entry-progress").disabled = !!editing.readonly;
  $("#planner-entry-save").hidden = !!editing.readonly;
  $("#planner-entry-delete").hidden = !editing.id || !!editing.readonly || editing.origin === "library";
  $("#planner-entry-unschedule").hidden = !editing.date || !!editing.readonly || editing.status === "published";
  $("#planner-entry-exclude").hidden = !editing.id || !!editing.readonly || editing.status === "published";
  localize($("#planner-entry-exclude"),"textContent",()=>editing.excluded ? t("ui.c44c4bb3f45d") : t("ui.15c157781c40"));
  const detail = $("#planner-entry-readiness"); detail.replaceChildren();
  if (editing.readiness) {
    detail.append(el("strong", null, ()=>t("ui.85f3a836ee37") + readinessLabel(editing)));
    for (const check of editing.readiness.checks || []) detail.append(el("div", check.done ? "step-done" : "step-pending", ()=>(check.done ? "✓ " : "○ ") + uiText(check.label)));
    for (const text of editing.readiness.details || []) detail.append(el("p", "fine-print", text));
  }
  $("#planner-entry-error").textContent = "";
  sourceControls(); $("#planner-entry-dialog").showModal();
}
function scheduleOptions() {
  return { start: $("#planner-start").value, capacity: Number($("#planner-capacity").value), channel: $("#planner-channel").value,
    weekdays: [...$("#planner-weekdays").querySelectorAll("input:checked")].map((e) => Number(e.value)), include_ideas: $("#planner-ideas").checked };
}
export async function openPlanner() {
  await operation(async () => {
    state = await api("/api/publication-plan"); month = new Date(); invalidate(); render();
    showWorkspacePage("#plannerdialog", "calendar");
    $("#planner-btn").setAttribute("aria-expanded", "true");

  });
}
export function initPlanner() {
  $('#planner-library').addEventListener('click', () => { $('#plannerdialog').close(); emit('sources'); });
  $("#plannerdialog").addEventListener("close", () => {
    $("#planner-btn").setAttribute("aria-expanded", "false");

  });

  $("#planner-close").addEventListener("click", () => $("#plannerdialog").close());
  $("#planner-entry-close").addEventListener("click", () => $("#planner-entry-dialog").close());
  for (const [id, delta] of [["prev", -1], ["next", 1]]) $("#planner-" + id).addEventListener("click", () => { month = new Date(month.getFullYear(), month.getMonth() + delta, 1); render(); });
  $("#planner-today").addEventListener("click", () => { month = new Date(); render(); });
  $("#planner-channel").addEventListener("change", () => { invalidate(); render(); });
  $("#planner-add").addEventListener("click", () => openEntry());
  dropTarget($("#planner-queue"), null);
  $("#planner-entry-link").addEventListener("click", () => {
    editing.root = S.root; editing.file = S.file;
    if (!$("#planner-entry-title").value) $("#planner-entry-title").value = S.document.doc.replace(/\.md$/, "");
    sourceControls();
  });
  $("#planner-entry-unlink").addEventListener("click", () => { editing.root = ""; editing.file = ""; sourceControls(); });
  $("#planner-entry-open").addEventListener("click", () => operation(async () => {
    if (!(await allowNavigation())) return;
    if (editing.source_rel && !editing.has_granka) await api("/api/create-granka", { method:"POST", body:{root:editing.root, rel:editing.source_rel, operation_id:crypto.randomUUID()} });
    await loadDocument(editing.root, editing.file);
    $("#planner-entry-dialog").close(); $("#plannerdialog").close();
  }));
  $("#planner-entry-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = { ...editing };
    for (const key of ["title", "channel", "status", "date", "url", "notes"]) value[key] = $("#planner-entry-" + key).value;
    value.pinned = $("#planner-entry-pinned").checked;
    value.progress = $("#planner-entry-progress").value === "" ? null : Number($("#planner-entry-progress").value);
    operation(async () => { await save(value, editing.id || null); $("#planner-entry-dialog").close(); });
  });
  $("#planner-entry-delete").addEventListener("click", () => operation(async () => {
    state = await api("/api/publication-plan", { method: "POST", body: { action: "delete", id: editing.id, expected_revision: state.revision } });
    $("#planner-entry-dialog").close(); invalidate(); render();
  }));
  $("#planner-entry-unschedule").addEventListener("click", () => operation(async () => { await changeEntry(editing, "unschedule"); $("#planner-entry-dialog").close(); }));
  $("#planner-entry-exclude").addEventListener("click", () => operation(async () => { await changeEntry(editing, editing.excluded ? "include" : "exclude"); $("#planner-entry-dialog").close(); }));
  weekdays.forEach((name, i) => {
    const label = el("label"), input = el("input"); input.type = "checkbox"; input.value = i; input.checked = [0, 2, 4].includes(i);
    label.append(input, document.createTextNode(name)); $("#planner-weekdays").append(label);
  });
  $("#planner-start").value = iso(new Date());
  $("#planner-distribute").addEventListener("click", () => { $("#planner-schedule").hidden = !$("#planner-schedule").hidden; });
  $("#planner-schedule-close").addEventListener("click", () => { $("#planner-schedule").hidden = true; });
  $("#planner-schedule").addEventListener("input", invalidate);
  $("#planner-preview").addEventListener("click", () => operation(async () => {
    const options = scheduleOptions();
    const result = await api("/api/publication-plan/preview", { method: "POST", body: { expected_revision: state.revision, options } });
    proposal = { ...result, options };
    const box = $("#planner-proposal"); box.replaceChildren();
    for (const item of result.assignments) {
      const row = el("div", "planner-proposed-row"); row.append(el("time", null, item.date), el("span", null, channelNames[item.channel]), el("strong", null, item.title)); box.append(row);
    }
    if (!result.assignments.length) localize(box,"textContent",()=>t("ui.1b673059705d"));
    $("#planner-apply").disabled = !result.assignments.length;
  }));
  $("#planner-apply").addEventListener("click", () => operation(async () => {
    if (!proposal) return;
    state = await api("/api/publication-plan/apply", { method: "POST", body: { expected_revision: proposal.revision, options: proposal.options } });
    month = new Date(proposal.options.start + "T12:00:00");
    invalidate(); render(); toast(t("ui.4fe0b9bf1ab8"));
  }));

}
