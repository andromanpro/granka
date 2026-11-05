import { t, localizedOption, locale, localize } from './i18n.js';
import {
  $,
  S,
  api,
  query,
  version,
  latest,
  events,
  emit,
  busy,
  loadDocument,
  toast,
} from "./core.js";
import { editor } from "./editor.js";

export async function openCover() {
  if (S.busy || !latest()) return;
  if (S.editor && !(await editor.save())) return;
  editor.setView("article");
  emit("images", {
    context: {
      purpose: "cover",
      root: S.root,
      file: S.file,
      version: S.version,
    },
  });
}

export async function saveCover(path, alt, target) {
  if (S.busy || !latest() || S.editor) return false;
  if (
    target &&
    (target.root !== S.root ||
      target.file !== S.file ||
      target.version !== S.version)
  ) {
    toast(t("ui.99e52a7f0a84"), true);
    return false;
  }
  busy(true);
  try {
    let cover = null;
    if (path) {
      const prepared = await api("/api/prepare-image?" + query(), {
        method: "POST",
        body: { path, expected_revision: S.revision },
      });
      cover = { src: prepared.url, alt: alt || t("ui.3b855468a0eb") };
    }
    await api("/api/cover?" + query(), {
      method: "POST",
      body: {
        cover,
        base_version: S.version,
        expected_revision: S.revision,
        operation_id: crypto.randomUUID(),
      },
    });
    $("#imagegallery").close();
    await loadDocument();
    emit("sync");
    toast(cover ? t("ui.79233ee3afe6") : t("ui.2db507112079"));
    return true;
  } catch (error) {
    toast(
      error.conflict
        ? t("ui.0a66a439c56a")
        : error.message,
      true,
    );
    return false;
  } finally {
    busy(false);
  }
}

function render() {
  const cover = version()?.cover;
  $("#article-cover").hidden = !cover || S.view !== "article";
  localize($("#cover-btn"),"textContent",()=>cover ? t("ui.61029f9bb707") : t("ui.5bcd9613eb14"));
  if (cover) {
    const url =
      "/api/article-image?" + query() + "&src=" + encodeURIComponent(cover.src);
    $("#cover-preview").src = url;
    $("#cover-preview").alt = cover.alt;
    $("#cover-download").href = url;
    $("#cover-download").download = decodeURIComponent(cover.src)
      .split("/")
      .at(-1);
  } else $("#cover-preview").removeAttribute("src");
  controls();
}
function controls() {
  for (const id of ["#cover-btn", "#cover-change", "#cover-remove"])
    $(id).disabled = S.busy || !latest() || !!S.recording;
  $("#cover-remove").disabled ||= !!S.editor;
}
export function initCover() {
  $("#cover-btn").addEventListener("click", openCover);
  $("#cover-change").addEventListener("click", openCover);
  $("#cover-remove").addEventListener("click", () => saveCover(null));
  for (const event of ["document", "version", "view"])
    events.addEventListener(event, render);
  events.addEventListener("controls", controls);
}
