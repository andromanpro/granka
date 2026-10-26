import {
  $,
  S,
  api,
  emit,
  events,
  query,
  version,
  annotations,
  deepSelection,
  latest,
  loadDocument,
  busy,
  toast,
  setNavigationGuard,
} from "./core.js";
import { meterHighlights } from "./meter.js";
let reader = null;
let navigationQuestion = null;
const draftKey = () => `granka.text-draft:${S.root}|${S.file}`;
function text() {
  return S.editor?.mode === "source"
    ? $("#texteditor").value
    : reader?.serialize() || version()?.text || "";
}
function storeDraft() {
  if (S.editor)
    try {
      localStorage.setItem(
        draftKey(),
        JSON.stringify({
          text: text(),
          baseVersion: S.editor.baseVersion,
          baseText: S.editor.original,
        }),
      );
    } catch (_) {}
}
async function confirmNavigation() {
  if (!S.editor) return true;
  if (text() === S.editor.original) {
    localStorage.removeItem(draftKey());
    S.editor = null;
    emit("version");
    emit("controls");
    return true;
  }
  if (!navigationQuestion) {
    navigationQuestion = new Promise((resolve) => {
      const dialog = $("#unsaved-dialog");
      const finish = (choice) => {
        dialog.close();
        resolve(choice);
      };
      $("#unsaved-save").onclick = () => finish("save");
      $("#unsaved-discard").onclick = () => finish("discard");
      $("#unsaved-cancel").onclick = () => finish("cancel");
      dialog.oncancel = (event) => { event.preventDefault(); finish("cancel"); };
      dialog.showModal();
      $("#unsaved-cancel").focus();
    });
  }
  const choice = await navigationQuestion;
  navigationQuestion = null;
  if (choice === "save") return save();
  if (choice !== "discard") return false;
  localStorage.removeItem(draftKey());
  S.editor = null;
  emit("version");
  emit("controls");
  return true;
}
function start(mode = "rich") {
  if (S.editor) return S.editor.mode === mode;
  if (S.busy || S.recording || !latest()) return false;
  S.editor = {
    mode,
    baseVersion: S.version,
    revision: S.revision,
    original: version().text,
    operationId: crypto.randomUUID(),
    imageNotes: [],
  };
  emit("dirty");
  emit("controls");
  return true;
}
function changed() {
  if (!S.editor) start(S.view === "source" ? "source" : "rich");
  storeDraft();
  if ($("#editor-status").textContent !== "Есть несохранённые правки")
    $("#editor-status").textContent = "Есть несохранённые правки";
}
export async function save() {
  if (!S.editor) return true;
  if (S.busy) return false;
  const state = S.editor,
    value = text();
  if (!value.trim()) {
    toast("Текст не может быть пустым", true);
    return false;
  }
  storeDraft();
  busy(true);
  if (state.sent !== undefined && state.sent !== value)
    state.operationId = crypto.randomUUID();
  state.sent = value;
  try {
    await api("/api/text?" + query(), {
      method: "POST",
      body: {
        text: value,
        base_version: state.baseVersion,
        expected_revision: state.revision,
        operation_id: state.operationId,
        fulfilled_image_notes: state.imageNotes.filter((x) =>
          value.includes("](" + x.url + ")"),
        ),
      },
    });
    localStorage.removeItem(draftKey());
    await loadDocument();
    emit("sync");
    toast("Версия сохранена");
    return true;
  } catch (error) {
    $("#editor-status").textContent = error.conflict
      ? "Файл изменился снаружи. Твой черновик сохранён локально."
      : "Не сохранилось: " + error.message;
    toast($("#editor-status").textContent, true);
    return false;
  } finally {
    busy(false);
  }
}
async function cancel() {
  if (!S.editor) return;
  if (
    text() !== S.editor.original &&
    !confirm("Отменить правку? Черновик останется локально.")
  )
    return;
  storeDraft();
  await loadDocument();
}
function render() {
  if (!S.document || S.editor) return;
  picked = null;
  const current = version(),
    box = $("#doc");
  $("#doc").hidden = S.view !== "article";
  $("#texteditor").hidden = S.view !== "source";
  $("#diffwrap").hidden = S.view !== "diff";
  $("#diff-controls").hidden = S.view !== "diff";
  $("#formatbar").hidden = S.view !== "article";
  $("#texteditor").value = current.text;
  const spans = annotations(),
    extra = meterHighlights()
      .map((h) => ({ ...h, meter: true }));
  const deep = deepSelection().map((a, index) => ({ ...a, id: "deep-anchor-" + index, deep: true, deepIndex: index }));
  reader = GrankaMarkdown.render(box, current.text, [...spans, ...extra, ...deep], {
    imageUrl: (src) =>
      "/api/article-image?" + query() + "&src=" + encodeURIComponent(src),
    mark: (a, overlapping = []) => {
      const mark = document.createElement("mark");
      mark.className = a.deep ? "deep-mark" : a.meter
        ? "meter"
        : (a.type === "protected"
            ? "protected"
            : a.type === "note"
              ? "note"
              : a.severity || "P2") +
          (a.decision ? " decided" : "") +
          (a.id === S.active ? " active" : "");
      const reasons = overlapping.filter((span) => span.meter).map((span) => span.why);
      if (reasons.length) {
        mark.classList.add("meter");
        mark.title = [...new Set(reasons)].join(" · ");
      }
      const linked = overlapping.filter((span) => span.deep);
      if (linked.length) {
        mark.classList.add("deep-mark");
        mark.dataset.deepIndexes = linked.map((span) => span.deepIndex).join(" ");
      }
      if (!a.meter && !a.deep) mark.dataset.id = a.id;
      mark.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!getSelection().isCollapsed) return;
        if (a.deep) return;
        if (a.meter) toast(a.why);
        else {
          S.active = a.id;
          emit("selection", { scroll: false });
        }
      });
      return mark;
    },
  });
  $("#editor-status").textContent = latest()
    ? "Изменения сохраняются новой версией"
    : "Предыдущая версия · только чтение";
  $("#restore-draft").hidden = true;
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey()) || "null");
    $("#restore-draft").hidden =
      !latest() || !draft || draft.text === current.text;
  } catch (_) {}
  controls();
}
export function setView(view) {
  if (S.editor) {
    toast("Сначала сохрани текущую правку");
    return;
  }
  S.view = view;
  emit("view");
  render();
}
function controls() {
  const editable =
    !S.busy && !S.recording && latest() && S.view === "article"
      ? "true"
      : "false";
  if ($("#doc").contentEditable !== editable)
    $("#doc").contentEditable = editable;
  $("#texteditor").readOnly = S.busy || !latest();
  $("#savebtn").disabled = S.busy || !S.editor;
  $("#cancel-edit").hidden = !S.editor;
  $("#reader-shell").classList.toggle("dirty", !!S.editor);
  for (const b of document.querySelectorAll("[data-view]")) {
    b.classList.toggle("selected", b.dataset.view === S.view);
    b.disabled =
      !!S.editor ||
      S.busy ||
      (b.dataset.view === "diff" &&
        (S.document?.versions.length || 0) < 2);
  }
  $("#reader-hint").textContent = S.editor
    ? "Ctrl+S — сохранить · Ctrl+Z — отменить ввод"
    : latest()
      ? "Кликни в статью и пиши. Правая кнопка — вставить картинку."
      : "Выбери последнюю версию для редактирования.";
}
export function context(range) {
  const box = $("#doc"),
    sel = getSelection();
  range = range || (sel.rangeCount ? sel.getRangeAt(0) : null);
  if (
    !range ||
    !box.contains(range.startContainer) ||
    !box.contains(range.endContainer)
  ) {
    range = document.createRange();
    range.selectNodeContents(box);
  }
  range = range.cloneRange();
  range.collapse(false);
  return { range, root: S.root, file: S.file, version: S.version };
}
export function contextForAnnotation(a) {
  return { ...context(reader.rangeAtOffset(a.end)), annotationId: a.id };
}
export function selection() {
  const sel = getSelection();
  if (S.editor || sel.isCollapsed || !sel.rangeCount) return null;
  return reader?.selectionOffsets(sel.getRangeAt(0));
}
let picked = null;
export function imagesIn(range) {
  const box = $("#doc");
  if (!range || !box.contains(range.commonAncestorContainer)) return [];
  return [...box.querySelectorAll(".md-image")].filter((figure) =>
    range.intersectsNode(figure),
  );
}
export function clearPicked() {
  if (picked) delete picked.dataset.picked;
  picked = null;
}
export function pickImage(figure) {
  clearPicked();
  if (!figure || !$("#doc").contains(figure)) return;
  picked = figure;
  figure.dataset.picked = "1";
  const range = document.createRange();
  range.selectNode(figure);
  const sel = getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  $("#reader-hint").textContent =
    "Картинка выделена · Delete убирает её из статьи";
  emit("image-picked", { figure });
}
export function removeImages(figures) {
  const box = $("#doc"),
    list = (figures || (picked ? [picked] : [])).filter((f) => box.contains(f));
  if (!list.length) return false;
  if (S.busy || !latest() || !start()) {
    toast("Правка доступна в последней версии статьи", true);
    return false;
  }
  for (const figure of list) {
    const block = figure.closest("[data-md-block]");
    figure.remove();
    if (block && !block.querySelector(".md-image") && !block.textContent.trim())
      block.remove();
  }
  picked = null;
  getSelection().removeAllRanges();
  $("#selpop").hidden = true;
  changed();
  controls();
  toast(list.length > 1 ? "Картинки убраны" : "Картинка убрана");
  return true;
}
export async function insertImage(path, alt, target) {
  if (
    S.busy ||
    !target ||
    target.root !== S.root ||
    target.file !== S.file ||
    target.version !== S.version ||
    !start()
  )
    return false;
  busy(true);
  try {
    const prepared = await api("/api/prepare-image?" + query(), {
      method: "POST",
      body: {
        path,
        expected_revision: S.editor.revision,
        operation_id: crypto.randomUUID(),
      },
    });
    $("#imagegallery").close();
    busy(false);
    $("#doc").focus();
    const range = target.range.cloneRange();
    range.collapse(false);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    const node = document.createElement("span");
    node.className = "md-image";
    node.contentEditable = "false";
    node.dataset.mdUrl = prepared.url;
    node.dataset.mdAlt = alt || prepared.name;
    const img = document.createElement("img");
    img.src =
      "/api/article-image?" +
      query() +
      "&src=" +
      encodeURIComponent(prepared.url);
    img.alt = node.dataset.mdAlt;
    const caption = document.createElement("span");
    caption.className = "md-image-caption";
    caption.textContent = img.alt;
    node.append(img, caption);
    if (!document.execCommand("insertHTML", false, node.outerHTML))
      throw new Error("Не удалось вставить изображение");
    if (target.annotationId)
      S.editor.imageNotes.push({ id: target.annotationId, url: prepared.url });
    changed();
    return true;
  } catch (error) {
    toast(
      error.conflict
        ? "Версия изменилась. Сохрани черновик и перечитай статью."
        : error.message,
      true,
    );
    return false;
  } finally {
    busy(false);
  }
}
export function download() {
  const url = URL.createObjectURL(
      new Blob([text()], { type: "text/markdown;charset=utf-8" }),
    ),
    a = document.createElement("a");
  a.href = url;
  a.download = S.document.doc || "article.md";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function initEditor() {
  setNavigationGuard(confirmNavigation);
  events.addEventListener("document", render);
  events.addEventListener("version", render);
  events.addEventListener("reader", render);
  events.addEventListener("controls", controls);
  $("#savebtn").addEventListener("click", save);
  $("#cancel-edit").addEventListener("click", cancel);
  $("#downloadbtn").addEventListener("click", download);
  $("#doc").addEventListener("beforeinput", (e) => {
    if (!start()) e.preventDefault();
  });
  $("#doc").addEventListener("input", changed);
  $("#texteditor").addEventListener("input", changed);
  for (const target of [$("#doc"), $("#texteditor")])
    target.addEventListener("keydown", (e) => {
      if (
        (e.ctrlKey || e.metaKey) &&
        (e.key.toLowerCase() === "s" || e.key === "Enter")
      ) {
        e.preventDefault();
        save();
      }
    });
  $("#doc").addEventListener("paste", (e) => {
    e.preventDefault();
    if (start()) {
      document.execCommand(
        "insertText",
        false,
        e.clipboardData.getData("text/plain"),
      );
      changed();
    }
  });
  $("#doc").addEventListener("drop", (e) => e.preventDefault());
  $("#doc").addEventListener("click", (e) => {
    if (e.target.closest("a") && !e.ctrlKey && !e.metaKey) e.preventDefault();
    const figure = e.target.closest?.(".md-image");
    if (figure && S.view === "article" && latest() && !S.busy && !S.recording)
      pickImage(figure);
    else clearPicked();
  });
  $("#doc").addEventListener("keydown", (e) => {
    if (picked && (e.key === "Delete" || e.key === "Backspace")) {
      e.preventDefault();
      removeImages();
    } else if (e.key === "Escape") clearPicked();
  });
  document.querySelectorAll("[data-format]").forEach((b) => {
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", () => {
      if (start()) {
        document.execCommand(b.dataset.format);
        changed();
      }
    });
  });
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#restore-draft").addEventListener("click", () => {
    const draft = JSON.parse(localStorage.getItem(draftKey()) || "null");
    if (!draft || !start()) return;
    reader = GrankaMarkdown.render($("#doc"), draft.text, [], {
      imageUrl: (src) =>
        "/api/article-image?" + query() + "&src=" + encodeURIComponent(src),
    });
    changed();
    if(draft.baseVersion!==S.version){
      const warning='Восстановлен черновик от '+draft.baseVersion+'. Текущая сохранённая версия — '+S.version+'. Проверь текст перед сохранением.';
      $('#editor-status').textContent=warning;toast(warning,true);
    }
  });
  events.addEventListener("selection", (e) => {
    if (S.editor) return;
    document
      .querySelectorAll("#doc mark[data-id]")
      .forEach((m) => m.classList.toggle("active", m.dataset.id === S.active));
    if (e.detail.scroll) {
      const mark = [...document.querySelectorAll("#doc mark")].find(
        (m) => m.dataset.id === S.active,
      );
      if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
      else {
        const a = annotations().find((a) => a.id === S.active);
        if (a) {
          setView("source");
          const input = $("#texteditor");
          input.focus();
          input.setSelectionRange(
            [...version().text].slice(0, a.start).join("").length,
            [...version().text].slice(0, a.end).join("").length,
          );
        }
      }
    }
  });
  events.addEventListener("deep-selection", () => {
    if (S.editor) return;
    setView("article");
    render();
    const index = S.deepFocus?.anchorIndex || 0;
    const mark = [...document.querySelectorAll("#doc mark[data-deep-indexes]")].find(
      (node) => node.dataset.deepIndexes.split(" ").includes(String(index)),
    );
    if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
    else {
      const anchor = deepSelection()[index];
      if (!anchor) return;
      setView("source");
      const input = $("#texteditor");
      input.focus();
      input.setSelectionRange(
        [...version().text].slice(0, anchor.start).join("").length,
        [...version().text].slice(0, anchor.end).join("").length,
      );
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if (S.editor && text() !== S.editor.original) {
      storeDraft();
      e.preventDefault();
      e.returnValue = "";
    }
  });
}
export const editor = {
  text,
  save,
  cancel,
  context,
  contextForAnnotation,
  selection,
  blockRange: (element) => S.editor ? null : reader?.blockOffsets(element),
  insertImage,
  render,
  setView,
};
