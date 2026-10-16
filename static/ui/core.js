export const $ = (selector) => document.querySelector(selector);
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
export function button(text, action, className = "") {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}
let noticeTimer;
export function toast(text, error = false) {
  const node = $("#toast");
  (document.querySelector("dialog[open]") || document.body).appendChild(node);
  node.textContent = text;
  node.classList.toggle("error", error);
  node.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => (node.hidden = true), error ? 7000 : 3500);
}
export const S = {
  document: null,
  revision: null,
  root: null,
  file: null,
  version: null,
  active: null,
  busy: false,
  view: "article",
  pane: "review",
  editor: null,
  meter: null,
  jobs: [],
  editorialRequests: {},
  loadedAt: 0,
  settings: {
    analysis_provider: "codex",
    image_provider: "codex",
    codex_model: "",
    openrouter_text_model: "",
    openrouter_image_model: "",
    image_aspect: "16:9",
  },
};
export const events = new EventTarget();
export const emit = (type, detail = {}) =>
  events.dispatchEvent(new CustomEvent(type, { detail }));
export const version = () =>
  S.document?.versions.find((v) => v.id === S.version);
export const annotations = () =>
  S.document?.annotations
    .filter((a) => a.version === S.version)
    .sort((a, b) => a.start - b.start) || [];
export const latest = () =>
  !!S.document && S.version === S.document.versions.at(-1).id;
export const query = () =>
  new URLSearchParams({ root: S.root, f: S.file }).toString();
export const providerName = (p) =>
  p === "openrouter" ? "OpenRouter" : p === "local" ? "Гранка" : "Codex";
export function busy(value) {
  S.busy = value;
  emit("controls");
}
export async function api(path, options = {}) {
  const headers = {
    "X-Granka-Token": $('meta[name="granka-token"]').content,
    ...options.headers,
  };
  if (
    options.body &&
    typeof options.body === "object" &&
    !(options.body instanceof Blob)
  ) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  let data;
  try {
    data = await response.json();
  } catch (_) {
    data = {};
  }
  if (!response.ok) {
    const error = new Error(data.error || "HTTP " + response.status);
    error.conflict = response.status === 409;
    error.status = response.status;
    throw error;
  }
  return data;
}
let loadSequence = 0;
let navigationGuard = async () => true;
export function setNavigationGuard(guard) {
  navigationGuard = guard;
}
export const allowNavigation = () => navigationGuard();
export async function loadDocument(
  root = S.root,
  file = S.file,
  { keepVersion = false } = {},
) {
  if ((root !== S.root || file !== S.file) && !(await allowNavigation())) return false;
  const seq = ++loadSequence,
    old = S.version;
  busy(true);
  try {
    const result = await api(
      "/api/granka?" + new URLSearchParams({ root, f: file }),
    );
    if (seq !== loadSequence) return;
    const changed = root !== S.root || file !== S.file;
    Object.assign(S, {
      root,
      file,
      document: result.data,
      editorialRequests: result.editorial_requests || {},
      revision: result.revision,
      loadedAt: Date.now() / 1000,
      editor: null,
    });
    S.version =
      keepVersion && result.data.versions.some((v) => v.id === old)
        ? old
        : result.data.versions.at(-1).id;
    if (changed) {
      S.active = null;
      S.jobs = [];
    }
    S.meter = null;
    localStorage.setItem("granka.doc", root + "|" + file);
    emit("document", { errors: result.contract_errors || [] });
  } catch (error) {
    toast("Не удалось открыть статью: " + error.message, true);
    throw error;
  } finally {
    if (seq === loadSequence) busy(false);
  }
}
export async function operation(path, body, { reload = true } = {}) {
  if (S.busy) return null;
  busy(true);
  try {
    const result = await api(path + "?" + query(), {
      method: "POST",
      body: {
        ...body,
        expected_revision: S.revision,
        operation_id: crypto.randomUUID(),
      },
    });
    S.revision = result.revision || S.revision;
    if (reload) await loadDocument(S.root, S.file, { keepVersion: true });
    emit("sync");
    return result;
  } catch (error) {
    toast(
      error.conflict
        ? "Статья изменилась в другой вкладке. Перечитай её."
        : "Не сохранилось: " + error.message,
      true,
    );
    return null;
  } finally {
    busy(false);
  }
}
