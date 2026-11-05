import { t, localizedOption, locale, localize } from './i18n.js';
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
      error.conflict ? t("ui.23b626fd607a") : error.message,
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
    toast(t("ui.f7056d9c8f89"));
    return;
  }
  const pending = annotations().filter(
    (a) => a.type !== "protected" && !a.decision,
  ).length;
  if (
    pending &&
    !confirm(t("ui.d9740bb6789c") + pending + t("ui.deccb4660c0e"))
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
        toast(t("ui.1b4b89230191"));
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
    draft: t("ui.06553f21aedb"),
    ready: t("ui.0103b62f6c2e"),
    applied: t("ui.0e21e606620f"),
  };
  const publication = S.publication;
  const changed = publication?.changed || !!S.editor;
  localize($("#stage-label"),"textContent",()=>publication ? publication.label + (S.editor && !publication.changed ? t("ui.c072be35e6bc") : '') : titles[state] || state);
  localize($("#stage-label"),"title",()=>publication ? publication.title + (S.editor ? t("ui.f38e62f3a96e") : '') + '\n' + (titles[state] || state) : '');
  $("#stage-dot").className = "stage-dot " + (publication ? changed ? 'published changed' : 'published' : state);
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
    localize($("#job-title"),"textContent",()=>current.result?.warning
      ? t("ui.fb6299e294d2")
      : current.message);
    localize($("#job-description"),"textContent",()=>current.result?.warning ||
      (running
        ? providerName(current.provider) +
          " · " +
          Math.floor(Date.now() / 1000 - current.created) +
          t("ui.b5461c51f095")
        : t("ui.8d3e38d93c2e")));
  }
  const report = $("#review-result");
  report.replaceChildren();
  const changes = version()?.applied_decisions || [];
  if (changes.length) {
    const summary = document.createElement("p");
    localize(summary,"textContent",()=>t("ui.c2ce5cc3b32e") +
      changes.length +
      t("ui.53f82a57bbde"));
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
  events.addEventListener('publication', renderJobStatus);
  setInterval(pollJobs, 1500);
}
