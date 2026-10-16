import {
  $,
  S,
  api,
  query,
  latest,
  events,
  el,
  button,
  emit,
  toast,
} from "./core.js";
import { editor } from "./editor.js";
let plan = null,
  review = null,
  info = null,
  locked = false,
  jobSignature = null,
  refreshSequence = 0;
const names = {
  nas: "1 · Git и предпросмотр",
  staging: "2 · Стейдж",
  prod: "3 · Прод",
};
function metadata() {
  const result = {};
  for (const [key, id] of Object.entries({
    title: "post-title",
    slug: "slug",
    date: "date",
    status: "status",
    excerpt: "excerpt",
  }))
    result[key] = $("#site-" + id).value;
  for (const key of ["categories", "tags"])
    result[key] = $("#site-" + key)
      .value.split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  return result;
}
function fill(value) {
  for (const [key, id] of Object.entries({
    title: "post-title",
    slug: "slug",
    date: "date",
    status: "status",
    excerpt: "excerpt",
  }))
    $("#site-" + id).value = value[key] || "";
  for (const key of ["categories", "tags"])
    $("#site-" + key).value = (value[key] || []).join(", ");
}
async function refresh() {
  const sequence = ++refreshSequence, source = query();
  const result = await api("/api/site?" + source);
  if (sequence !== refreshSequence || source !== query()) return;
  info = result;
  if (plan) plan = info.plans.find((p) => p.id === plan.id) || plan;
  renderPublication();
  render();
}
function renderPublication() {
  const publication = info?.publication;
  $("#publication-banner").hidden = !publication;
  $("#site-btn").textContent = publication ? "Обновить на сайте" : "На сайт";
  $("#site-title").textContent = publication ? "Обновление статьи" : "Публикация статьи";
  $("#site-slug").readOnly = !!publication;
  $("#site-date").readOnly = !!publication;
  if (!publication) return;
  $("#publication-state").textContent = publication.post_status === "publish" ? "Опубликовано на сайте" : "Черновик на сайте";
  $("#publication-description").textContent = `${publication.version} · ${publication.date} · ` +
    (publication.changed || S.editor ? "Есть неопубликованные изменения" : "Текущая версия на сайте");
  $("#publication-link").href = publication.url;
}
function running() {
  return S.jobs.find(
    (j) => j.kind === "site" && ["queued", "running"].includes(j.status),
  );
}
function render() {
  const active = running();
  const stale = plan && plan.source_hash !== info?.source_hash;
  $("#site-fields").inert = locked || !!active;
  $("#site-prepare").disabled = !info?.configured;
  $("#site-domain").textContent =
    info?.environments?.prod?.url || "WordPress · настрой подключение";
  $("#site-close").disabled = locked;
  $("#site-review").hidden = !review || stale;
  $("#site-confirm").disabled =
    locked ||
    !!active ||
    (review?.stage !== "nas" && !$("#site-approval").checked);
  $("#site-snapshot").textContent = plan
    ? `${plan.version} · ${plan.file} · ${plan.assets.length} картинок` +
      (stale ? " · Статья изменилась: подготовь новый снимок." : "")
    : "Подготовь снимок текста, обложки и картинок для публикации.";
  const box = $("#site-stages");
  box.replaceChildren();
  for (const stage of ["nas", "staging", "prod"]) {
    const state = plan?.stages[stage],
      row = el("article", "site-stage");
    row.appendChild(el("strong", null, names[stage]));
    const previous = { staging: "nas", prod: "staging" }[stage];
    const enabled =
      plan &&
      !stale &&
      !active &&
      !locked &&
      (!previous || plan.stages[previous]?.status === "succeeded");
    if (state?.status === "succeeded") {
      const a = el(
        "a",
        null,
        state.post_status === "draft"
          ? "Открыть черновик (нужен вход)"
          : "Открыть страницу",
      );
      a.href = state.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      row.appendChild(a);
      if (stage === "nas" && plan.gitea) {
        const git = el("a", null, "Исходник в Git");
        git.href = plan.gitea.url;
        git.target = "_blank";
        git.rel = "noreferrer";
        row.appendChild(git);
      }
    } else {
      const next = button(
        state?.status === "failed" || state?.status === "unknown"
          ? "Проверить и повторить"
          : "Подготовить переход",
        () => inspect(stage),
      );
      next.disabled = !enabled;
      row.appendChild(next);
      if (state?.error) row.appendChild(el("p", "fine-print", state.error));
    }
    box.appendChild(row);
  }
  if (active)
    $("#site-operation").textContent =
      active.message +
      " · " +
      Math.floor(Date.now() / 1000 - active.created) +
      " с";
}
async function action(fn) {
  if (locked) return;
  locked = true;
  render();
  try {
    await fn();
  } catch (error) {
    $("#site-operation").textContent = error.message;
    toast(error.message, true);
  } finally {
    locked = false;
    render();
  }
}
async function inspect(stage) {
  await action(async () => {
    review = await api("/api/site/review?" + query(), {
      method: "POST",
      body: { id: plan.id, stage },
    });
    $("#site-review-title").textContent = names[stage];
    $("#site-review-description").textContent =
      (review.existing
        ? "Обновится существующая статья «" +
          review.existing.title +
          "». Текст из WordPress будет заменён подготовленным снимком."
        : "Будет создана статья «" + plan.metadata.title + "».") +
      (stage === "prod"
        ? " На проде: " +
          (plan.metadata.status === "publish"
            ? "публичная публикация."
            : "черновик WordPress.")
        : " Страница будет доступна для просмотра на этом контуре.");
    $("#site-existing").hidden = !review.existing;
    if (review.existing) $("#site-existing").href = review.existing.url;
    $("#site-approval-row").hidden = stage === "nas";
    $("#site-approval").checked = false;
    $("#site-confirm").textContent =
      stage === "nas"
        ? "Сохранить в Git и открыть предпросмотр"
        : stage === "staging"
          ? "Перенести на стейдж"
          : plan.metadata.status === "publish"
            ? (review.existing ? "Обновить статью на " : "Опубликовать на ") + (info.environments.prod.url || "сайте")
            : review.existing?.status === "publish"
              ? "Снять с публикации и сохранить черновиком"
              : "Сохранить черновик на проде";
  });
}
export async function openSite() {
  if (S.busy || !latest() || S.recording) return;
  if (S.editor && !(await editor.save())) return;
  plan = null;
  review = null;
  info = null;
  $("#site-operation").textContent = "Загружаю…";
  $("#sitedialog").showModal();
  render();
  await action(async () => {
    await refresh();
    plan = info.plans.find((p) => p.source_hash === info.source_hash) || null;
    fill(plan?.metadata || info.metadata);
    $("#site-operation").textContent = info.configured
      ? ""
      : "Публикация выключена. Заполни раздел site в локальном файле конфигурации и перезапусти Гранку.";
  });
}
export function initSite() {
  $("#site-btn").addEventListener("click", openSite);
  $("#publication-edit").addEventListener("click", openSite);
  events.addEventListener("document", () => {
    plan = null; review = null; info = null; renderPublication();
    refresh().catch((error) => toast(error.message, true));
  });
  $("#site-close").addEventListener("click", () => $("#sitedialog").close());
  $("#sitedialog").addEventListener("cancel", (e) => {
    if (locked) e.preventDefault();
  });
  for (const input of $("#site-fields").querySelectorAll(
    "input,select,textarea",
  ))
    input.addEventListener("input", () => {
      plan = null;
      review = null;
      render();
    });
  $("#site-approval").addEventListener("change", render);
  $("#site-prepare").addEventListener("click", () =>
    action(async () => {
      plan = await api("/api/site/prepare?" + query(), {
        method: "POST",
        body: {
          metadata: metadata(),
          version: S.version,
          expected_revision: S.revision,
        },
      });
      review = null;
      $("#site-operation").textContent =
        "Снимок готов. Первый шаг — Git и предпросмотр.";
    }),
  );
  $("#site-confirm").addEventListener("click", () =>
    action(async () => {
      const result = await api("/api/site/deploy?" + query(), {
        method: "POST",
        body: {
          id: plan.id,
          review_id: review.id,
          confirmed: true,
          approved_previous: $("#site-approval").checked,
        },
      });
      review = null;
      S.jobs = [result.job, ...S.jobs.filter((j) => j.id !== result.job.id)];
      emit("jobs");
    }),
  );
  events.addEventListener("jobs", () => {
    const job = S.jobs.find((j) => j.kind === "site"),
      signature = job?.id + ":" + job?.status;
    render();
    if (job && signature !== jobSignature) {
      jobSignature = signature;
      if (!["queued", "running"].includes(job.status)) {
        $("#site-operation").textContent =
          job.status === "succeeded"
            ? "Готово. Открой страницу и проверь результат."
            : job.message;
        refresh().catch((error) => toast(error.message, true));
      }
    }
  });
  events.addEventListener("controls", () => {
    $("#site-btn").disabled = S.busy || !latest() || !!S.recording;
    $("#publication-edit").disabled = $("#site-btn").disabled;
    renderPublication();
  });
}
