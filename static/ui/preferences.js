import { t, localizedOption, locale, localize } from './i18n.js';
import { $, S, api, el, button, events, toast } from "./core.js";
import { startJob, workingJob } from "./jobs.js";
let summary = null,
  lastJob = null,
  loading = false;
let lastRuleJob = null;
const ruleDrafts = new Map();
function ruleButton(label, fn) {
  const b = button(label, fn, "quiet");
  b.dataset.ruleAction = "true";
  return b;
}
async function changeRule(route, body) {
  if (loading) return;
  loading = true;
  controls();
  try {
    await api(route, {
      method: "POST",
      body: { ...body, revision: summary.rules_revision },
    });
    await refresh();
  } catch (error) {
    toast(error.message, true);
    try {
      await refresh();
    } catch (_) {}
  } finally {
    loading = false;
    controls();
  }
}
async function refresh() {
  summary = await api("/api/preferences");
  localize($("#preferences-summary"),"textContent",()=>t("ui.82a0a8f22e32",summary.rules.length,summary.decisions,summary.changed,summary.kept) +
    (summary.errors.length
      ? t("ui.c2e1ff8f6660") + summary.errors.length + "."
      : ""));
  const rules = $("#preferences-rules");
  rules.replaceChildren();
  for (const rule of summary.rules) {
    const details = el("details", "preference-rule");
    details.append(
      el("summary", null, rule.title),
      el("p", null, rule.instruction),
    );
    details.appendChild(
      ruleButton(t("ui.5653cca0e4da"), () =>
        changeRule("/api/preferences/rule-toggle", {
          id: rule.id,
          enabled: false,
        }),
      ),
    );
    rules.appendChild(details);
  }
  if (!summary.rules.length)
    rules.appendChild(
      el(
        "p",
        "fine-print",
        ()=>t("ui.f87a3a02b0e6"),
      ),
    );
  const inactive = $("#inactive-rules");
  inactive.replaceChildren();
  for (const rule of summary.inactive_rules || []) {
    const row = el("div", "preference-rule");
    row.append(
      el("strong", null, rule.title),
      el(
        "p",
        "fine-print",
        ()=>rule.state === "superseded"
          ? t("ui.67543771e2eb")
          : rule.state === "stale"
            ? t("ui.56b5ba0d397c")
            : t("ui.3e95800065e0"),
      ),
    );
    if (rule.state === "disabled")
      row.appendChild(
        ruleButton(t("ui.86c0405f7faf"), () =>
          changeRule("/api/preferences/rule-toggle", {
            id: rule.id,
            enabled: true,
          }),
        ),
      );
    inactive.appendChild(row);
  }
  renderProposals();
  localize($("#benchmark-summary"),"textContent",()=>summary.benchmark.cases
    ? t("ui.eeb39a38f570") +
      summary.benchmark.cases +
      t("ui.b99001fa2e07") +
      summary.benchmark.created
    : t("ui.0017edfcb64f"));
  renderReport(summary.last_report);
  controls();
}
function renderReport(report) {
  const box = $("#benchmark-results");
  box.replaceChildren();
  if (!report) return;
  box.appendChild(
    el("h3", null, ()=>t("ui.a9d2b33bcfc7",report.passed,report.tested)),
  );
  box.appendChild(el("p", "fine-print", report.note));
  if (
    !report.rules_fingerprint ||
    report.rules_fingerprint !== summary?.rules_fingerprint
  )
    box.appendChild(
      el(
        "p",
        "inline-notice",
        ()=>t("ui.63ea5e52869e"),
      ),
    );
  for (const item of report.results) {
    const details = el(
      "details",
      "benchmark-case " + (item.passed ? "passed" : "failed"),
    );
    details.append(
      el(
        "summary",
        null,
        ()=>(item.passed ? t("ui.06a13d5b029d") : t("ui.1d1eb7f551a0")) +
          item.before.slice(0, 100),
      ),
      el("blockquote", null, item.before),
      el(
        "p",
        null,
        ()=>t("ui.73b198b3e856") +
          {
            keep: t("ui.eb41f2845e11"),
            edit: t("ui.706564218657"),
            avoid_variants: t("ui.2338ae7fd05b"),
          }[item.expected],
      ),
      el(
        "p",
        null,
        ()=>t("ui.40952bc27279") +
          (item.action === "keep" ? t("ui.eb41f2845e11") : item.text || t("ui.2977dad18584")),
      ),
      el("p", "fine-print", item.reason),
    );
    if (item.author_text)
      details.appendChild(
        el("p", "fine-print", ()=>t("ui.37d33951ebef") + item.author_text),
      );
    box.appendChild(details);
  }
}
function controls() {
  $("#benchmark-run").disabled =
    loading || !!workingJob() || !summary?.benchmark.cases;
  $("#benchmark-build").disabled = loading || !!workingJob();
  $("#rules-propose").disabled =
    loading || !!workingJob() || !summary?.decisions;
  document
    .querySelectorAll("[data-rule-action]")
    .forEach((b) => (b.disabled = loading || !!workingJob()));
  localize($("#benchmark-provider"),"textContent",()=>S.settings.analysis_provider === "openrouter"
      ? t("ui.a70225c38551")
      : t("ui.cfd2bbf04750"));
}
function renderProposals() {
  const box = $("#rule-proposals");
  box.replaceChildren();
  const pending = (summary.proposals || []).filter(
    (p) => p.status === "pending",
  );
  for (const proposal of pending) {
    const card = el("article", "rule-proposal");
    card.dataset.proposalId = proposal.id;
    const draft = ruleDrafts.get(proposal.id) || proposal;
    const title = el("input");
    title.value = draft.title;
    title.setAttribute("aria-label", t("ui.9e25d0859e87"));
    const instruction = el("textarea");
    instruction.value = draft.instruction;
    instruction.setAttribute("aria-label", t("ui.5799937961cd"));
    const remember = () =>
      ruleDrafts.set(proposal.id, {
        title: title.value,
        instruction: instruction.value,
      });
    title.addEventListener("input", remember);
    instruction.addEventListener("input", remember);
    card.append(
      el(
        "p",
        "fine-print",
        ()=>proposal.previous_rule
          ? t("ui.e6eb00c569cd") + proposal.previous_rule.title
          : t("ui.86885217ae72"),
      ),
      title,
      instruction,
      el("p", null, proposal.rationale),
    );
    const evidence = el("details");
    evidence.appendChild(
      el("summary", null, ()=>t("ui.8d587d83558a") + proposal.evidence.length),
    );
    for (const example of proposal.evidence) {
      const row = el("div", "rule-evidence");
      row.append(
        el("blockquote", null, example.before),
        el(
          "p",
          null,
          ()=>example.expected === "keep"
            ? t("ui.f758cda19335")
            : example.after
              ? t("ui.745b218447d3") + example.after
              : t("ui.39b2f6fbc34d"),
        ),
      );
      evidence.appendChild(row);
    }
    card.appendChild(evidence);
    if (proposal.previous_rule) {
      const old = el("details");
      old.append(
        el("summary", null, ()=>t("ui.71371bcea61c")),
        el("p", null, proposal.previous_rule.instruction),
      );
      card.appendChild(old);
    }
    const actions = el("div", "actions");
    actions.append(
      ruleButton(t("ui.ea00da281a92"), () =>
        changeRule("/api/preferences/rule-review", {
          id: proposal.id,
          action: "accepted",
          title: title.value,
          instruction: instruction.value,
        }),
      ),
      ruleButton(t("ui.899c5d6de891"), () =>
        changeRule("/api/preferences/rule-review", {
          id: proposal.id,
          action: "rejected",
        }),
      ),
    );
    card.appendChild(actions);
    box.appendChild(card);
  }
  if (!pending.length)
    box.appendChild(el("p", "fine-print", ()=>t("ui.7ac21faae3da")));
}
export function initPreferences() {
  $("#preferences-btn").addEventListener("click", async () => {
    $("#preferencesdialog").showModal();
    try {
      await refresh();
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#preferences-close").addEventListener("click", () =>
    $("#preferencesdialog").close(),
  );
  $("#benchmark-build").addEventListener("click", async () => {
    loading = true;
    controls();
    try {
      await api("/api/preferences/benchmark", { method: "POST", body: {} });
      await refresh();
      toast(t("ui.252028aca11e"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      loading = false;
      controls();
    }
  });
  $("#benchmark-run").addEventListener("click", async () => {
    const job = await startJob("benchmark", {
      limit: +$("#benchmark-limit").value,
    });
    if (job) {
      lastJob = null;
      $("#benchmark-status").textContent = job.message;
    }
  });
  $("#rules-propose").addEventListener("click", async () => {
    const job = await startJob("rules");
    if (job) {
      lastRuleJob = null;
      $("#rules-proposal-status").textContent = job.message;
    }
  });
  events.addEventListener("jobs", () => {
    controls();
    const ruleJob = S.jobs.find((j) => j.kind === "rules");
    if (ruleJob) {
      localize($("#rules-proposal-status"),"textContent",()=>ruleJob.status === "succeeded"
          ? t("ui.051d9e477c13") + ruleJob.result.proposed
          : ruleJob.message);
      if (ruleJob.status === "succeeded" && ruleJob.id !== lastRuleJob) {
        lastRuleJob = ruleJob.id;
        if ($("#preferencesdialog").open)
          refresh().catch((error) => toast(error.message, true));
      }
    }
    const job = S.jobs.find((j) => j.kind === "benchmark");
    if (!job) return;
    localize($("#benchmark-status"),"textContent",()=>job.message +
      (["queued", "running"].includes(job.status)
        ? " · " + Math.floor(Date.now() / 1000 - job.created) + t("ui.ddae8eb8bf21")
        : ""));
    if (job.status === "succeeded" && job.id !== lastJob) {
      lastJob = job.id;
      renderReport(job.result);
      if ($("#preferencesdialog").open)
        refresh().catch((error) => toast(error.message, true));
    }
  });
  events.addEventListener("settings", controls);
  events.addEventListener("controls", controls);
}
