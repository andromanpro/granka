import { t, localizedOption, locale, localize } from './i18n.js';
import { $, S, el, button, events, emit, latest, operation, toast, providerName } from "./core.js";
import { startJob, workingJob } from "./jobs.js";
import { save } from "./editor.js";

const categories = {
  composition: t("ui.daad6ca4f251"), logic: t("ui.84710107ead5"), repetition: t("ui.9803b8ea2351"),
  rhetoric: t("ui.840542684e87"), personal_rules: t("ui.de39690e1f44"),
};
const statuses = { issue: t("ui.826964e6038f"), clear: t("ui.e39639b11809"), uncertain: t("ui.cc2c029148fe") };
const decisions = { consider: t("ui.3794a65e88ba"), dismiss: t("ui.3e8618e22021"), addressed: t("ui.bbc75acb96ce") };
const drafts = new Map();
let selectedReport = null;

function focus(report, findingId, anchorIndex, outlineIndex = null, signalIndex = null) {
  if (S.editor) { toast(t("ui.47dbff5333bf")); return; }
  S.deepFocus = { reportId: report.id, findingId, anchorIndex, outlineIndex, signalIndex };
  S.active = null;
  S.view = "article";
  emit("deep-selection");
}

async function decide(report, finding, action) {
  if (S.editor || S.busy || workingJob()) return;
  await operation("/api/deep-decision", {
    report_id: report.id, finding_id: finding.id,
    decision: action ? { action, note: drafts.get(finding.id) ?? finding.decision?.note ?? "" } : null,
  });
}

function render() {
  const box = $("#deep-review");
  box.replaceChildren();
  if (S.contractErrors?.some((error) => error.startsWith("deep_analyses:"))) {
    box.hidden = false;
    box.append(el("p", "inline-notice", ()=>t("ui.9603fb4c57b6")));
    return;
  }
  const reports = (S.document?.deep_analyses || []).filter((r) => r.version === S.version);
  const older = S.document?.deep_analyses?.at(-1);
  box.hidden = !reports.length && !older;
  if (!reports.length && older) box.append(el("p", "inline-notice", ()=>t("ui.136947ac728c",older.version,S.version)));
  if (!reports.length) return;
  const report = reports.find((r) => r.id === selectedReport) || reports.at(-1);
  selectedReport = report.id;
  box.append(el("h2", "panel-title", ()=>t("ui.f926ce9e2529")));
  if (reports.length > 1) {
    const select = el("select");
    select.setAttribute("aria-label", t("ui.e7493c4455e6"));
    for (const r of reports) select.append(localizedOption(()=>r.ts.replace("T", " ") + " · " + r.findings.length + t("ui.bf00dbe2c178"),r.id));
    select.value = report.id;
    select.addEventListener("change", () => {
      selectedReport = select.value;
      S.deepFocus = null;
      emit("deep-selection");
      render();
    });
    box.append(select);
  }
  box.append(el("small", "muted", ()=>t("ui.5c351e0400dd",report.version,providerName(report.provider),report.receipts.length)));
  box.append(el("p", "muted", ()=>t("ui.50f208b3a96e",report.findings.filter((f) => f.decision).length,report.findings.length)));
  box.append(el("p", "deep-summary", report.summary));
  const outline = el("details", "deep-map");
  outline.append(el("summary", null, ()=>t("ui.ea6e94848629") + report.map.outline.length + t("ui.db3056bc96a3")));
  outline.append(el("p", null, ()=>t("ui.e61320af03b7") + report.map.thesis));
  outline.append(el("p", null, ()=>t("ui.a9f63fe161dd") + report.map.reader_task));
  outline.append(el("p", null, report.map.trajectory));
  for (const [i, block] of report.map.outline.entries()) {
    const row = el("div", "deep-map-block");
    row.append(button(`${i + 1}. ${block.role}`, () => focus(report, null, 0, i), "review-quote"), el("p", null, block.point));
    outline.append(row);
  }
  box.append(outline);
  const coverage = el("details", "deep-coverage");
  coverage.append(el("summary", null, ()=>t("ui.67a72dae1b78")));
  for (const [key, value] of Object.entries(report.coverage)) {
    coverage.append(el("strong", null, `${categories[key]} · ${statuses[value.status]}`), el("p", null, value.reason));
  }
  box.append(coverage);
  if (report.local_signals?.clues.length) {
    const signals = el("details", "deep-signals");
    signals.append(el("summary", null, ()=>t("ui.c832c58b553e") + report.local_signals.clues.length));
    signals.append(el("p", "muted", report.local_signals.notice));
    for (const [i, clue] of report.local_signals.clues.entries()) {
      signals.append(el("p", null, clue.why_check));
      for (const [j, a] of clue.anchors.entries()) signals.append(button("«" + a.quote + "»", () => focus(report, null, j, null, i), "review-quote deep-quote"));
    }
    box.append(signals);
  }
  if (report.skipped.length) {
    const skipped = el("details", "inline-notice deep-skipped");
    skipped.append(el("summary", null, ()=>t("ui.e69f7c10aa0e",report.skipped.length)));
    for (const item of report.skipped) skipped.append(el("p", null, `${item.candidate_id}: ${item.reason}`));
    box.append(skipped);
  }
  if (report.challenges.length) {
    const challenges = el("details", "deep-challenges");
    challenges.append(el("summary", null, ()=>t("ui.1a646e2e5806") + report.challenges.filter((c) => c.verdict === "drop").length));
    for (const item of report.challenges) {
      challenges.append(el("strong", null, ()=>item.title + " · " + ({ keep: t("ui.239c0751fb56"), drop: t("ui.6e46da78d37d"), invalid: t("ui.cd636f01f126") }[item.verdict])), el("p", null, item.reason));
    }
    box.append(challenges);
  }
  if (!report.findings.length) box.append(el("p", "empty", ()=>t("ui.b9f2883df829")));
  for (const finding of report.findings) {
    const card = el("article", "deep-card" + (finding.decision ? " decided" : ""));
    card.dataset.findingId = finding.id;
    card.append(el("small", "muted", ()=>`${categories[finding.category]} · ${finding.severity} · ${finding.confidence === "high" ? t("ui.d86c6cb0f9fb") : t("ui.0dcf85a9b900")}`));
    card.append(el("h3", null, finding.title));
    for (const [index, anchor] of finding.anchors.entries()) {
      const quote = button(`${index + 1}. «${anchor.quote}»`, () => focus(report, finding.id, index), "review-quote deep-quote");
      localize(quote,"title",()=>anchor.role === "context" ? t("ui.b9e94cd995d4") : t("ui.c8db133c303a"));
      quote.dataset.anchorIndex = index;
      card.append(quote);
    }
    card.append(el("p", "explanation", finding.why), el("p", "deep-suggestion", finding.suggestion));
    const objection = el("details");
    objection.append(el("summary", null, ()=>t("ui.99d8b53ea63f")), el("p", null, finding.counterargument), el("p", "muted", ()=>t("ui.686ea819b440") + finding.verification));
    card.append(objection);
    const note = el("textarea");
    localize(note,"placeholder",()=>t("ui.a1fa953e7da4"));
    note.setAttribute("aria-label", t("ui.e79229e52375"));
    note.maxLength = 2000;
    note.value = drafts.get(finding.id) ?? finding.decision?.note ?? "";
    note.addEventListener("input", () => drafts.set(finding.id, note.value));
    card.append(note);
    const actions = el("div", "actions deep-actions");
    if (finding.decision) {
      card.append(el("p", "decision-text", decisions[finding.decision.action] + (finding.decision.note ? ": " + finding.decision.note : "")));
      actions.append(button(()=>t("ui.d95b37588046"), () => decide(report, finding, null), "quiet"));
    } else {
      for (const [action, label] of Object.entries(decisions)) actions.append(button(label, () => decide(report, finding, action), "quiet"));
    }
    card.append(actions);
    box.append(card);
  }
  box.append(el("small", "muted", ()=>t("ui.b9efb2912af3")));
  controls();
}

function controls() {
  const locked = S.busy || !!S.editor || !!workingJob() || !latest();
  $("#deep-analysis-btn").disabled = S.busy || !!workingJob() || !latest();
  localize($("#deep-analysis-btn"),"textContent",()=>S.editor ? t("ui.944245a967dc") : t("ui.82a4ef844fa6"));
  for (const node of document.querySelectorAll(".deep-actions button, .deep-card textarea")) node.disabled = locked;
}

export function initDeepReview() {
  events.addEventListener("document", render);
  events.addEventListener("version", render);
  events.addEventListener("controls", controls);
  events.addEventListener("deep-result", (event) => {
    selectedReport = event.detail.reportId;
    render();
    const report = S.document?.deep_analyses?.find((r) => r.id === selectedReport);
    const first = report?.findings[0];
    if (first) focus(report, first.id, Math.max(0, first.anchors.findIndex((a) => a.role === "revise")));
    else { S.deepFocus = null; S.active = null; emit("deep-selection"); }
    $("#deep-review").scrollIntoView({ block: "start" });
  });
  $("#deep-analysis-btn").addEventListener("click", async () => {
    if (S.editor && !(await save())) return;
    await startJob("deep-analysis");
  });
}
