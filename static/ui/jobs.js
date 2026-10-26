import {
  $,
  S,
  api,
  events,
  emit,
  query,
  version,
  annotations,
  loadDocument,
  providerName,
  toast,
} from "./core.js";
const seen = new Set();
const submitted = new Set();
let submitting = false;
export const workingJob = () =>
  S.jobs.find(
    (j) => j.kind !== "image" && ["queued", "running"].includes(j.status),
  );
export async function startJob(kind, extra = {}) {
  if (submitting || !S.document) return null;
  submitting = true;
  emit("controls");
  try {
    const result = await api("/api/jobs?" + query(), {
      method: "POST",
      body: {
        kind,
        version: S.version,
        expected_revision: S.revision,
        operation_id: crypto.randomUUID(),
        ...extra,
      },
    });
    submitted.add(result.job.id);
    S.jobs = [result.job, ...S.jobs.filter((j) => j.id !== result.job.id)];
    emit("jobs");
    return result.job;
  } catch (error) {
    toast(
      error.conflict ? "Версия изменилась. Перечитай статью." : error.message,
      true,
    );
    return null;
  } finally {
    submitting = false;
    emit("controls");
  }
}
export async function applyDecisions() {
  if (S.editor) {
    toast("Сначала сохрани правку текста");
    return;
  }
  const pending = annotations().filter(
    (a) => a.type !== "protected" && !a.decision,
  ).length;
  if (
    pending &&
    !confirm("Ещё " + pending + " пометок без решения. Применить уже принятые?")
  )
    return;
  await startJob("apply");
}
export async function pollJobs() {
  if (!S.document) return;
  const key = query();
  try {
    const result = await api("/api/jobs?" + key);
    if (key !== query()) return;
    S.jobs = result.jobs || [];
    emit("jobs");
    const done = S.jobs.find(
      (j) =>
        !["image", "announcement", "site", "benchmark", "rules", "spice"].includes(
          j.kind,
        ) &&
        j.status === "succeeded" &&
        (submitted.has(j.id) || j.finished > S.loadedAt) &&
        !seen.has(j.id),
    );
    if (done) {
      seen.add(done.id);
      submitted.delete(done.id);
      if (!S.editor && !document.querySelector("dialog[open]")) {
        if (["analysis", "editorial", "deep-analysis"].includes(done.kind)) S.view = "article";
        else if (!done.result?.unchanged) S.view = "diff";
        await loadDocument();
        if (["analysis", "editorial", "deep-analysis"].includes(done.kind)) {
          emit("pane", { pane: "review" });
          if (done.kind === "deep-analysis") {
            emit("deep-result", { reportId: done.result.report_id });
          } else {
            const analysis = S.document.analyses?.at(-1);
            S.active = done.result?.annotation_id || analysis?.added?.[0] || analysis?.updated?.[0] || S.active;
            emit("selection", { scroll: true });
          }
        }
      } else
        toast("Результат готов. Открой его после завершения текущей правки.");
    }
  } catch (_) {}
}
export function renderJobStatus() {
  const current = S.jobs.find(
      (j) =>
        !["image", "announcement", "site", "benchmark", "rules"].includes(
          j.kind,
        ),
    ),
    banner = $("#job-banner");
  const state = S.document?.status || "draft";
  const titles = {
    draft: "Правка и замечания",
    ready: "Решения приняты · можно применить",
    applied: "Новая версия готова · проверь изменения",
  };
  $("#stage-label").textContent = titles[state] || state;
  $("#stage-dot").className = "stage-dot " + state;
  banner.hidden =
    !current ||
    (!current.result?.warning &&
      !["queued", "running", "failed", "conflict", "interrupted"].includes(
        current.status,
      ));
  if (!banner.hidden) {
    const running = ["queued", "running"].includes(current.status);
    banner.classList.toggle("running", running);
    banner.classList.toggle("error", !running);
    $("#job-title").textContent = current.result?.warning
      ? "Нужно проверить исходный файл"
      : current.message;
    $("#job-description").textContent =
      current.result?.warning ||
      (running
        ? providerName(current.provider) +
          " · " +
          Math.floor(Date.now() / 1000 - current.created) +
          " с. Результат появится здесь."
        : "Подробности сохранены в журнале задания. Автоматического повторного запроса нет.");
  }
  const report = $("#review-result");
  report.replaceChildren();
  const changes = version()?.applied_decisions || [];
  if (changes.length) {
    const summary = document.createElement("p");
    summary.textContent =
      "В этой версии применено " +
      changes.length +
      " решений. Их можно проверить во вкладке «Изменения».";
    report.appendChild(summary);
  }
  const analysis = S.document?.analyses
    ?.filter((a) => a.version === S.version)
    .at(-1);
  if (analysis) {
    const p = document.createElement("p");
    p.textContent = analysis.summary;
    report.appendChild(p);
  }
  emit("controls");
}
export function initJobs() {
  events.addEventListener("document", () => {
    renderJobStatus();
    pollJobs();
  });
  events.addEventListener("jobs", renderJobStatus);
  events.addEventListener("version", renderJobStatus);
  setInterval(pollJobs, 1500);
}
