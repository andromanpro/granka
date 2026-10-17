import { $, S, el, button, events, emit, latest, operation, toast, providerName } from "./core.js";
import { startJob, workingJob } from "./jobs.js";
import { save } from "./editor.js";

const categories = {
  composition: "Композиция", logic: "Логика и конкретика", repetition: "Повторы мысли",
  rhetoric: "Риторические шаблоны", personal_rules: "Личные правила",
};
const statuses = { issue: "Есть замечания", clear: "Замечаний нет", uncertain: "Недостаточно оснований" };
const decisions = { consider: "В работу", dismiss: "Не согласен", addressed: "Учтено" };
const drafts = new Map();
let selectedReport = null;

function focus(report, findingId, anchorIndex, outlineIndex = null, signalIndex = null) {
  if (S.editor) { toast("Сначала сохрани текст, чтобы перейти к цитате"); return; }
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
    box.append(el("p", "inline-notice", "В сохранённом разборе есть ошибки разметки. Проверь исходный файл; новые решения пока не сохраняются."));
    return;
  }
  const reports = (S.document?.deep_analyses || []).filter((r) => r.version === S.version);
  const older = S.document?.deep_analyses?.at(-1);
  box.hidden = !reports.length && !older;
  if (!reports.length && older) box.append(el("p", "inline-notice", `Последний разбор относится к ${older.version}. Для ${S.version} запусти новый.`));
  if (!reports.length) return;
  const report = reports.find((r) => r.id === selectedReport) || reports.at(-1);
  selectedReport = report.id;
  box.append(el("h2", "panel-title", "Разбор статьи целиком"));
  if (reports.length > 1) {
    const select = el("select");
    select.setAttribute("aria-label", "Сохранённый разбор");
    for (const r of reports) select.append(new Option(r.ts.replace("T", " ") + " · " + r.findings.length + " замечаний", r.id));
    select.value = report.id;
    select.addEventListener("change", () => {
      selectedReport = select.value;
      S.deepFocus = null;
      emit("deep-selection");
      render();
    });
    box.append(select);
  }
  box.append(el("small", "muted", `${report.version} · ${providerName(report.provider)} · ${report.receipts.length} прохода`));
  box.append(el("p", "muted", `Рассмотрено: ${report.findings.filter((f) => f.decision).length} из ${report.findings.length}`));
  box.append(el("p", "deep-summary", report.summary));
  const outline = el("details", "deep-map");
  outline.append(el("summary", null, "Ход статьи · " + report.map.outline.length + " блоков"));
  outline.append(el("p", null, "Тезис: " + report.map.thesis));
  outline.append(el("p", null, "Задача читателя: " + report.map.reader_task));
  outline.append(el("p", null, report.map.trajectory));
  for (const [i, block] of report.map.outline.entries()) {
    const row = el("div", "deep-map-block");
    row.append(button(`${i + 1}. ${block.role}`, () => focus(report, null, 0, i), "review-quote"), el("p", null, block.point));
    outline.append(row);
  }
  box.append(outline);
  const coverage = el("details", "deep-coverage");
  coverage.append(el("summary", null, "Что проверено"));
  for (const [key, value] of Object.entries(report.coverage)) {
    coverage.append(el("strong", null, `${categories[key]} · ${statuses[value.status]}`), el("p", null, value.reason));
  }
  box.append(coverage);
  if (report.local_signals?.clues.length) {
    const signals = el("details", "deep-signals");
    signals.append(el("summary", null, "Локальные подсказки для чтения · " + report.local_signals.clues.length));
    signals.append(el("p", "muted", report.local_signals.notice));
    for (const [i, clue] of report.local_signals.clues.entries()) {
      signals.append(el("p", null, clue.why_check));
      for (const [j, a] of clue.anchors.entries()) signals.append(button("«" + a.quote + "»", () => focus(report, null, j, null, i), "review-quote deep-quote"));
    }
    box.append(signals);
  }
  if (report.skipped.length) {
    const skipped = el("details", "inline-notice deep-skipped");
    skipped.append(el("summary", null, `Ограничения и пропуски · ${report.skipped.length}`));
    for (const item of report.skipped) skipped.append(el("p", null, `${item.candidate_id}: ${item.reason}`));
    box.append(skipped);
  }
  if (report.challenges.length) {
    const challenges = el("details", "deep-challenges");
    challenges.append(el("summary", null, "Перепроверка той же моделью · отклонено " + report.challenges.filter((c) => c.verdict === "drop").length));
    for (const item of report.challenges) {
      challenges.append(el("strong", null, item.title + " · " + ({ keep: "оставлено", drop: "отклонено", invalid: "не прошло проверку цитат" }[item.verdict])), el("p", null, item.reason));
    }
    box.append(challenges);
  }
  if (!report.findings.length) box.append(el("p", "empty", "После перепроверки замечаний нет. Это результат редакторского разбора, а не проверка авторства."));
  for (const finding of report.findings) {
    const card = el("article", "deep-card" + (finding.decision ? " decided" : ""));
    card.dataset.findingId = finding.id;
    card.append(el("small", "muted", `${categories[finding.category]} · ${finding.severity} · ${finding.confidence === "high" ? "уверенное замечание" : "гипотеза"}`));
    card.append(el("h3", null, finding.title));
    for (const [index, anchor] of finding.anchors.entries()) {
      const quote = button(`${index + 1}. «${anchor.quote}»`, () => focus(report, finding.id, index), "review-quote deep-quote");
      quote.title = anchor.role === "context" ? "Контекст; пересмотр не предлагается" : "Место для пересмотра";
      quote.dataset.anchorIndex = index;
      card.append(quote);
    }
    card.append(el("p", "explanation", finding.why), el("p", "deep-suggestion", finding.suggestion));
    const objection = el("details");
    objection.append(el("summary", null, "Возражение в пользу автора"), el("p", null, finding.counterargument), el("p", "muted", "После перепроверки: " + finding.verification));
    card.append(objection);
    const note = el("textarea");
    note.placeholder = "Моё замечание к разбору…";
    note.setAttribute("aria-label", "Комментарий к замечанию");
    note.maxLength = 2000;
    note.value = drafts.get(finding.id) ?? finding.decision?.note ?? "";
    note.addEventListener("input", () => drafts.set(finding.id, note.value));
    card.append(note);
    const actions = el("div", "actions deep-actions");
    if (finding.decision) {
      card.append(el("p", "decision-text", decisions[finding.decision.action] + (finding.decision.note ? ": " + finding.decision.note : "")));
      actions.append(button("Переиграть", () => decide(report, finding, null), "quiet"));
    } else {
      for (const [action, label] of Object.entries(decisions)) actions.append(button(label, () => decide(report, finding, action), "quiet"));
    }
    card.append(actions);
    box.append(card);
  }
  box.append(el("small", "muted", "Решения сохраняют отношение к замечаниям. Текст правится в редакторе; кнопка «Применить решения» этот разбор не переписывает."));
  controls();
}

function controls() {
  const locked = S.busy || !!S.editor || !!workingJob() || !latest();
  $("#deep-analysis-btn").disabled = S.busy || !!workingJob() || !latest();
  $("#deep-analysis-btn").textContent = S.editor ? "Сохранить и разобрать целиком" : "Разобрать статью целиком";
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
