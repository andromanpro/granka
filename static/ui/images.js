import {
  $,
  S,
  el,
  api,
  events,
  emit,
  latest,
  providerName,
  toast,
} from "./core.js";
import { editor, imagesIn, removeImages, clearPicked } from "./editor.js";
import { startJob } from "./jobs.js";
import { saveCover } from "./cover.js";
let purpose = "inline";
const promptDrafts = {
  inline: "",
  cover: "Выразительная иллюстрация главной идеи статьи. Без надписей.",
};
let images = [],
  target = null,
  selected = null,
  contextTarget = null,
  contextImage = null,
  pickedFigures = [],
  shownJob = null;
function showSelectionMenu(rect, figures) {
  pickedFigures = figures;
  const pop = $("#selpop"),
    remove = $("#delimg");
  pop.hidden = false;
  $("#addann").disabled = !!S.editor || !editor.selection();
  remove.hidden = !figures.length;
  remove.textContent =
    figures.length > 1 ? "Убрать картинки" : "Убрать картинку";
  pop.style.left =
    Math.max(8, Math.min(rect.left, innerWidth - pop.offsetWidth - 8)) + "px";
  pop.style.top =
    Math.min(rect.bottom + 8, innerHeight - pop.offsetHeight - 8) + "px";
}
const preview = (path) => "/api/image-preview?path=" + encodeURIComponent(path);
async function loadImages() {
  try {
    images = (await api("/api/images")).images || [];
  } catch (error) {
    toast(error.message, true);
    images = [];
  }
}
function choose(item) {
  selected = item;
  $("#imagepreview").src = preview(item.path);
  $("#imagepreview").hidden = false;
  $("#imageinfo").textContent =
    item.name + (item.kb ? " · " + item.kb + " КБ" : "");
  $("#imagealt").value = item.name.replace(/\.[^.]+$/, "");
  $("#imageinsert").disabled = false;
  document
    .querySelectorAll(".image-tile")
    .forEach((n) =>
      n.classList.toggle("selected", n.dataset.path === item.path),
    );
}
function grid() {
  const box = $("#imagegrid"),
    q = $("#imagefilter").value.toLowerCase();
  box.replaceChildren();
  for (const item of images.filter((i) => i.name.toLowerCase().includes(q))) {
    const b = el("button", "image-tile");
    b.dataset.path = item.path;
    b.title = item.name;
    const image = el("img");
    image.src = preview(item.path);
    image.alt = item.name;
    image.loading = "lazy";
    b.append(image, el("span", null, item.name));
    b.addEventListener("click", () => choose(item));
    box.appendChild(b);
  }
  if (!box.children.length)
    box.appendChild(
      el("p", "empty", "Картинки не найдены. Можно сгенерировать новую."),
    );
}
function tab(name) {
  $("#image-library").hidden = name !== "library";
  $("#image-generation").hidden = name !== "generate";
  document
    .querySelectorAll("[data-image-tab]")
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.imageTab === name),
    );
}
export async function openImages(context) {
  if (S.busy || !latest() || S.view !== "article") {
    toast("Открой последнюю версию статьи");
    return;
  }
  target = context || editor.context();
  purpose = context?.purpose || "inline";
  const cover = purpose === "cover";
  $("#image-title").textContent = cover ? "Обложка статьи" : "Выбрать картинку";
  $("#image-kicker").textContent = cover
    ? "Первое впечатление · 16:9"
    : "Иллюстрация для статьи";
  $("#imageinsert").textContent = cover
    ? "Сделать обложкой"
    : "Вставить в статью";
  $("#generation-prompt").value = promptDrafts[purpose];
  selected = null;
  shownJob = null;
  $("#selpop").hidden = true;
  $("#docmenu").hidden = true;
  $("#imagefilter").value = "";
  $("#imagepreview").hidden = true;
  $("#imageinsert").disabled = true;
  $("#imageinfo").textContent = "Выбери картинку слева";
  $("#imagealt").value = "";
  $("#imagewhere").textContent = cover
    ? "Отдельная обложка этой статьи. Сохранится новой версией."
    : "Вставка на месте курсора, после выделенного текста.";
  tab(cover ? "generate" : "library");
  if (!$("#imagegallery").open) $("#imagegallery").showModal();
  $("#imagegrid").textContent = "Загружаю…";
  await loadImages();
  grid();
  renderGeneration();
}
function renderGeneration() {
  const provider = S.settings.image_provider;
  $("#generation-note").textContent =
    provider === "codex"
      ? "Исполнитель: Codex · текущий вход в аккаунт"
      : "OpenRouter · " +
        (S.settings.openrouter_image_model || "выбери модель в настройках") +
        " · платный запрос";
  if (purpose === "cover")
    $("#generation-note").textContent +=
      " · Обложка 16:9, с учётом сохранённого текста статьи";
  const job = S.jobs.find(
      (j) => j.kind === "image" && (j.purpose || "inline") === purpose,
    ),
    label = $("#generation-status");
  if (!job) {
    $("#generate-image").disabled = false;
    label.textContent = "";
    return;
  }
  const running = ["queued", "running"].includes(job.status);
  $("#generate-image").disabled = running;
  label.classList.toggle("running", running);
  label.textContent =
    providerName(job.provider) +
    " · " +
    job.message +
    (running ? " · " + Math.floor(Date.now() / 1000 - job.created) + " с" : "");
  if (job.status === "succeeded" && job.result) {
    label.textContent =
      purpose === "cover"
        ? "Обложка готова. Проверь её и нажми «Сделать обложкой»."
        : "Готово. Проверь изображение и нажми «Вставить в статью».";
    if (
      $("#imagegallery").open &&
      shownJob !== job.id &&
      !$("#image-generation").hidden
    ) {
      shownJob = job.id;
      choose({ path: job.result.path, name: job.result.name });
      $("#imagealt").value = (job.result.prompt || "Иллюстрация").slice(0, 140);
      loadImages();
    }
  }
}
export function initImages() {
  events.addEventListener("images", (e) => openImages(e.detail.context));
  events.addEventListener("image-picked", (e) =>
    showSelectionMenu(e.detail.figure.getBoundingClientRect(), [
      e.detail.figure,
    ]),
  );
  $("#delimg").addEventListener("click", () => removeImages(pickedFigures));
  $("#contextdelimage").addEventListener("click", () => {
    $("#docmenu").hidden = true;
    removeImages(contextImage ? [contextImage] : []);
  });
  events.addEventListener("settings", renderGeneration);
  events.addEventListener("jobs", renderGeneration);
  $("#image-btn").addEventListener("mousedown", (e) => e.preventDefault());
  $("#image-btn").addEventListener("click", () => openImages());
  $("#addimg").addEventListener("click", () => openImages());
  $("#contextimage").addEventListener("click", () => openImages(contextTarget));
  $("#imagefilter").addEventListener("input", grid);
  $("#generation-prompt").addEventListener("input", () => {
    promptDrafts[purpose] = $("#generation-prompt").value;
  });
  $("#imageclose").addEventListener("click", () => $("#imagegallery").close());
  $("#imageinsert").addEventListener("click", () => {
    if (selected)
      purpose === "cover"
        ? saveCover(selected.path, $("#imagealt").value, target)
        : editor.insertImage(selected.path, $("#imagealt").value, target);
  });
  document.querySelectorAll("[data-image-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      tab(b.dataset.imageTab);
      renderGeneration();
    }),
  );
  $("#generate-image").addEventListener("click", async () => {
    const prompt = $("#generation-prompt").value.trim();
    if (!prompt) {
      toast("Опиши картинку");
      return;
    }
    if (purpose === "cover" && target.version !== S.version) {
      toast("Версия изменилась. Открой обложку заново.", true);
      return;
    }
    const job = await startJob("image", { prompt, purpose });
    if (job) {
      shownJob = null;
      selected = null;
      $("#imagepreview").hidden = true;
      $("#imageinsert").disabled = true;
      $("#imageinfo").textContent = "Готовлю новый вариант";
      renderGeneration();
    }
  });
  $("#doc").addEventListener("contextmenu", (e) => {
    if (S.view !== "article" || !latest()) return;
    e.preventDefault();
    const sel = getSelection(),
      range = sel.rangeCount ? sel.getRangeAt(0) : null;
    const inside =
      range &&
      !range.collapsed &&
      [...range.getClientRects()].some(
        (r) =>
          e.clientX >= r.left &&
          e.clientX <= r.right &&
          e.clientY >= r.top &&
          e.clientY <= r.bottom,
      );
    contextTarget = editor.context(
      inside ? range : document.caretRangeFromPoint(e.clientX, e.clientY),
    );
    const figure = e.target.closest?.(".md-image");
    contextImage = figure && $("#doc").contains(figure) ? figure : null;
    $("#contextdelimage").hidden = !contextImage;
    if (!contextImage) clearPicked();
    $("#selpop").hidden = true;
    const menu = $("#docmenu");
    menu.hidden = false;
    menu.style.left =
      Math.max(8, Math.min(e.clientX, innerWidth - menu.offsetWidth - 8)) +
      "px";
    menu.style.top =
      Math.max(8, Math.min(e.clientY, innerHeight - menu.offsetHeight - 8)) +
      "px";
  });
  $("#docmenu").addEventListener("mousedown", (e) => e.preventDefault());
  $("#selpop").addEventListener("mousedown", (e) => e.preventDefault());
  document.addEventListener("mousedown", (e) => {
    if (!e.target.closest?.("#docmenu")) $("#docmenu").hidden = true;
  });
  document.addEventListener("mouseup", (e) => {
    if (
      e.target.closest?.("#selpop,#docmenu") ||
      document.querySelector("dialog[open]")
    )
      return;
    const sel = getSelection(),
      box = $("#doc");
    if (
      !sel.isCollapsed &&
      box.contains(sel.anchorNode) &&
      box.contains(sel.focusNode) &&
      S.view === "article"
    ) {
      if (S.recording) {
        emit("voice-pin");
        return;
      }
      const range = sel.getRangeAt(0);
      showSelectionMenu(range.getBoundingClientRect(), imagesIn(range));
    } else $("#selpop").hidden = true;
  });
}
