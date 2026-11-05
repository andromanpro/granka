import { t, localizedOption, locale, localize } from './i18n.js';
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
import { openSettings } from './settings.js';
let plan = null,
  review = null,
  info = null,
  locked = false,
  jobSignature = null,
  refreshSequence = 0;
const sequence = () => info?.pipeline || [];
const stageName = stage => `${sequence().indexOf(stage)+1} · ${info?.environments?.[stage]?.label || stage}`;
const previous = stage => sequence()[sequence().indexOf(stage)-1];
const final = stage => stage === info?.final_stage;
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
  localize($("#site-btn"),"textContent",()=>publication ? t("ui.1f93bbc61fbd") : t("ui.bd2cc7e95b17"));
  localize($("#site-title"),"textContent",()=>publication ? t("ui.f3db1ae9bbe3") : t("ui.935b6e6ee2ed"));
  $("#site-slug").readOnly = !!publication;
  $("#site-date").readOnly = !!publication;
  if (!publication) return;
  localize($("#publication-state"),"textContent",()=>publication.post_status === "publish" ? t("ui.f3024c1f148c") : t("ui.1531bdafa08d"));
  localize($("#publication-description"),"textContent",()=>`${publication.version} · ${publication.date} · ` +
    (publication.changed || S.editor ? t("ui.c2dd0286296d") : t("ui.5761cdd334a0")));
  $("#publication-link").href = publication.url;
}
function running() {
  return S.jobs.find(
    (j) => j.kind === "site" && ["queued", "running"].includes(j.status),
  );
}
function render() {
  const active = running();
  const stale = plan && (plan.source_hash !== info?.source_hash ||
    JSON.stringify(plan.pipeline || ['nas','staging','prod']) !== JSON.stringify(sequence()) ||
    (plan.destination_signature && plan.destination_signature !== info?.configuration_signature));
  $("#site-fields").inert = locked || !!active;
  $("#site-prepare").disabled = !info?.configured;
  localize($("#site-domain"),"textContent",()=>info?.environments?.[info?.final_stage]?.url || t("ui.3176f42e1622"));
  $("#site-close").disabled = locked;
  $("#site-open-settings").disabled = locked || !!active;
  $("#site-review").hidden = !review || stale;
  $("#site-confirm").disabled =
    locked ||
    !!active ||
    (review?.previous && !$("#site-approval").checked);
  localize($("#site-snapshot"),"textContent",()=>plan
    ? t("ui.51065323f695",plan.version,plan.file,plan.assets.length) +
      (stale ? t("ui.b898f105b1ee") : "")
    : t("ui.d88b99fec325"));
  const box = $("#site-stages");
  box.replaceChildren();
  for (const stage of sequence()) {
    const state = plan?.stages[stage],
      row = el("article", "site-stage");
    row.appendChild(el("strong", null, stageName(stage)));
    const predecessor = previous(stage);
    const enabled =
      plan &&
      !stale &&
      !active &&
      !locked &&
      (!predecessor || plan.stages[predecessor]?.status === "succeeded");
    if (state?.status === "succeeded") {
      const a = el(
        "a",
        null,
        ()=>state.post_status === "draft"
          ? t("ui.6fb4e9965ba1")
          : t("ui.6ee247bf8669"),
      );
      a.href = state.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      row.appendChild(a);
      if (stage === sequence()[0] && plan.gitea) {
        const git = el("a", null, ()=>t("ui.729e65df5f6c"));
        git.href = plan.gitea.url;
        git.target = "_blank";
        git.rel = "noreferrer";
        row.appendChild(git);
      }
    } else {
      const next = button(
        ()=>state?.status === "failed" || state?.status === "unknown"
          ? t("ui.b8f067556aa5")
          : t("ui.44cdcce6a36e"),
        () => inspect(stage),
      );
      next.disabled = !enabled;
      row.appendChild(next);
      if (state?.error) row.appendChild(el("p", "fine-print", state.error));
    }
    box.appendChild(row);
  }
  if (active)
    localize($("#site-operation"),"textContent",()=>active.message +
      " · " +
      Math.floor(Date.now() / 1000 - active.created) +
      t("ui.ddae8eb8bf21"));
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
    localize($("#site-operation"),"textContent",()=>t("ui.4cb1abadd7f4") + stageName(stage) + t("ui.dfcc12a52705"));
    review = await api("/api/site/review?" + query(), {
      method: "POST",
      body: { id: plan.id, stage },
    });
    $("#site-review-title").textContent = stageName(stage);
    localize($("#site-review-description"),"textContent",()=>(review.existing
        ? t("ui.24b1fd1cf478") +
          review.existing.title +
          t("ui.810830f756b5")
        : t("ui.cb775f1793e3") + plan.metadata.title + "».") +
      (final(stage)
        ? t("ui.84faf28b95b4") +
          (plan.metadata.status === "publish"
            ? t("ui.de5f7f789987")
            : t("ui.946dbf50fc69"))
        : t("ui.cf37bf2a96ed")));
    $("#site-existing").hidden = !review.existing;
    if (review.existing) $("#site-existing").href = review.existing.url;
    $("#site-approval-row").hidden = !review.previous;
    $("#site-approval").checked = false;
    localize($("#site-confirm"),"textContent",()=>!final(stage)
        ? t("ui.58fe5ecdfaf3") + (info.environments[stage]?.label || stage) + "»"
          : plan.metadata.status === "publish"
            ? (review.existing ? t("ui.77c51c7a3a0b") : t("ui.c1d7ea84961b")) + (info.environments[stage]?.url || t("ui.0deb3221fa93"))
            : review.existing?.status === "publish"
              ? t("ui.9dd66cdae1e1")
              : t("ui.13bb8b0e4428"));
    localize($("#site-operation"),"textContent",()=>t("ui.96edab4a0736"));
  });
}
export async function openSite() {
  if (S.busy || !latest() || S.recording) return;
  if (S.editor && !(await editor.save())) return;
  plan = null;
  review = null;
  info = null;
  localize($("#site-operation"),"textContent",()=>t("ui.1679ed866916"));
  $("#sitedialog").showModal();
  render();
  await action(async () => {
    await refresh();
    plan = info.plans.find((p) => p.source_hash === info.source_hash) || null;
    fill(plan?.metadata || info.metadata);
    localize($("#site-operation"),"textContent",()=>info.configured
      ? ""
      : t("ui.fb4f60753260"));
  });
}
export function initSite() {
  $("#site-open-settings").addEventListener('click',()=>{$('#sitedialog').close();openSettings();});
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
      localize($("#site-operation"),"textContent",()=>t("ui.32ebe4bbf32d"));
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
        localize($("#site-operation"),"textContent",()=>job.status === "succeeded"
            ? t("ui.183a217f4059")
            : job.message);
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
