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
  $("#preferences-summary").textContent =
    `Личных правил: ${summary.rules.length}. Уникальных решений: ${summary.decisions} (${summary.changed} правок, ${summary.kept} сохранённых фрагментов). До 16 подходящих примеров подбираются к каждому анализу.` +
    (summary.errors.length
      ? " Не удалось прочитать документов: " + summary.errors.length + "."
      : "");
  const rules = $("#preferences-rules");
  rules.replaceChildren();
  for (const rule of summary.rules) {
    const details = el("details", "preference-rule");
    details.append(
      el("summary", null, rule.title),
      el("p", null, rule.instruction),
    );
    details.appendChild(
      ruleButton("Отключить правило", () =>
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
        "Личные правила появятся здесь после добавления аллергенов или записи в corrections_file.",
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
        rule.state === "superseded"
          ? "Заменено уточнением"
          : rule.state === "stale"
            ? "Исходное правило изменилось вне Гранки"
            : "Отключено",
      ),
    );
    if (rule.state === "disabled")
      row.appendChild(
        ruleButton("Включить правило", () =>
          changeRule("/api/preferences/rule-toggle", {
            id: rule.id,
            enabled: true,
          }),
        ),
      );
    inactive.appendChild(row);
  }
  renderProposals();
  $("#benchmark-summary").textContent = summary.benchmark.cases
    ? "В наборе " +
      summary.benchmark.cases +
      " примеров · " +
      summary.benchmark.created
    : "Набор ещё не собран.";
  renderReport(summary.last_report);
  controls();
}
function renderReport(report) {
  const box = $("#benchmark-results");
  box.replaceChildren();
  if (!report) return;
  box.appendChild(
    el("h3", null, `Совпало решений: ${report.passed} из ${report.tested}`),
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
        "Прогон не подтверждает текущую редакцию правил. После изменений запусти проверку модели заново.",
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
        (item.passed ? "Совпало · " : "Разошлось · ") +
          item.before.slice(0, 100),
      ),
      el("blockquote", null, item.before),
      el(
        "p",
        null,
        "Решение автора: " +
          {
            keep: "сохранить",
            edit: "изменить",
            avoid_variants: "не использовать отвергнутые варианты",
          }[item.expected],
      ),
      el(
        "p",
        null,
        "Модель: " +
          (item.action === "keep" ? "сохранить" : item.text || "удалить"),
      ),
      el("p", "fine-print", item.reason),
    );
    if (item.author_text)
      details.appendChild(
        el("p", "fine-print", "Вариант автора: " + item.author_text),
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
  $("#benchmark-provider").textContent =
    S.settings.analysis_provider === "openrouter"
      ? "OpenRouter · проверка выполнит отдельный платный запрос."
      : "Codex · текущая модель анализа.";
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
    title.setAttribute("aria-label", "Название правила");
    const instruction = el("textarea");
    instruction.value = draft.instruction;
    instruction.setAttribute("aria-label", "Формулировка правила");
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
        proposal.previous_rule
          ? "Уточнение: " + proposal.previous_rule.title
          : "Новое правило",
      ),
      title,
      instruction,
      el("p", null, proposal.rationale),
    );
    const evidence = el("details");
    evidence.appendChild(
      el("summary", null, "Решения автора · " + proposal.evidence.length),
    );
    for (const example of proposal.evidence) {
      const row = el("div", "rule-evidence");
      row.append(
        el("blockquote", null, example.before),
        el(
          "p",
          null,
          example.expected === "keep"
            ? "Автор оставил текст"
            : example.after
              ? "Автор: " + example.after
              : "Автор изменил или убрал фрагмент",
        ),
      );
      evidence.appendChild(row);
    }
    card.appendChild(evidence);
    if (proposal.previous_rule) {
      const old = el("details");
      old.append(
        el("summary", null, "Действующая формулировка"),
        el("p", null, proposal.previous_rule.instruction),
      );
      card.appendChild(old);
    }
    const actions = el("div", "actions");
    actions.append(
      ruleButton("Принять правило", () =>
        changeRule("/api/preferences/rule-review", {
          id: proposal.id,
          action: "accepted",
          title: title.value,
          instruction: instruction.value,
        }),
      ),
      ruleButton("Отклонить", () =>
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
    box.appendChild(el("p", "fine-print", "Предложений на проверку пока нет."));
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
      toast("Проверочный набор обновлён");
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
      $("#rules-proposal-status").textContent =
        ruleJob.status === "succeeded"
          ? "Предложений: " + ruleJob.result.proposed
          : ruleJob.message;
      if (ruleJob.status === "succeeded" && ruleJob.id !== lastRuleJob) {
        lastRuleJob = ruleJob.id;
        if ($("#preferencesdialog").open)
          refresh().catch((error) => toast(error.message, true));
      }
    }
    const job = S.jobs.find((j) => j.kind === "benchmark");
    if (!job) return;
    $("#benchmark-status").textContent =
      job.message +
      (["queued", "running"].includes(job.status)
        ? " · " + Math.floor(Date.now() / 1000 - job.created) + " с"
        : "");
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
