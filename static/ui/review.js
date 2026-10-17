import {
  $,
  S,
  el,
  button,
  events,
  emit,
  annotations,
  version,
  operation,
  toast,
  latest,
} from "./core.js";
import { editor } from "./editor.js";
import { applyDecisions, workingJob, startJob } from "./jobs.js";
const drafts = new Map(),
  choices = new Map();
function requestFor(a) {
  return a.type === "candidate"
    ? S.editorialRequests[S.version]?.find(
        (r) => r.start === a.start && r.end === a.end && r.quote === a.quote,
      )
    : null;
}
function usableVariants(a) {
  const request = requestFor(a);
  if (request && request.kind !== "creative") return [];
  return (a.variants || [])
    .map((value, index) => ({ value, index }))
    .filter(
      ({ value }) =>
        value?.trim() &&
        (!request ||
          ![request.quote, request.instruction].includes(value.trim())),
    );
}
function choose(id, scroll = true) {
  S.active = id;
  emit("selection", { scroll });
}
async function decide(a, action, reason) {
  if (S.editor || S.busy) return;
  let decision = null;
  if (action === "accept") {
    const variants = usableVariants(a);
    const chosen =
      variants.find((v) => v.index === choices.get(a.id)) || variants[0];
    if (!chosen) {
      toast("Сначала выполни поручение или напиши свой вариант");
      return;
    }
    decision = {
      action,
      text: chosen.value,
      intent: "replace",
    };
  }
  if (action === "delete")
    decision = { action: "custom", text: "", intent: "delete" };
  if (action === "custom") {
    const text = (drafts.get(a.id) || "").trim();
    if (!text) {
      toast("Напиши свой вариант");
      return;
    }
    decision = { action, text, intent: "replace" };
  }
  if (action === "comment") {
    const text = (drafts.get(a.id) || a.note || "").trim();
    if (!text) {
      toast("Напиши комментарий");
      return;
    }
    decision = { action: "custom", text, intent: "comment" };
  }
  if (action === "reject") decision = { action, reason };
  if (action === "allergen") decision = { action };
  const result = await operation("/api/decision", {
    annotation_id: a.id,
    decision,
  });
  if (result) {
    drafts.delete(a.id);
    const next = annotations().find(
      (x) => x.type !== "protected" && !x.decision,
    );
    S.active = next?.id || a.id;
    emit("selection", { scroll: !!next });
  }
}
function textarea(a, placeholder) {
  const input = el("textarea");
  input.id = "cust-" + a.id;
  input.placeholder = placeholder;
  input.value = drafts.get(a.id) ?? a.note ?? "";
  input.addEventListener("input", () => drafts.set(a.id, input.value));
  return input;
}
function card(a, compact = false) {
  const root = el("article", "review-card" + (compact ? " compact" : ""));
  const request = requestFor(a);
  root.id = "card-" + a.id;
  const label =
    a.type === "protected"
      ? "Голос автора"
      : a.type === "note"
        ? a.kind === "image"
          ? "Картинка"
          : "Пометка"
        : request
          ? {
              creative: "Поручение ИИ",
              author: "Вопрос автору",
              unsupported: "Проверить поручение",
            }[request.kind]
          : a.class || "Замечание";
  root.appendChild(
    el(
      "span",
      "tag " + (a.severity || a.type),
      label + (a.severity ? " · " + a.severity : ""),
    ),
  );
  const quote = button("«" + a.quote + "»", () => choose(a.id), "review-quote");
  root.appendChild(quote);
  if (compact) {
    if (a.decision) root.appendChild(el("small", "muted", "Решено"));
    return root;
  }
  if (a.why) root.appendChild(el("p", "explanation", a.why));
  if (a.decision) {
    const d = a.decision,
      labels = { reject: "Оставлено", allergen: "В аллергенах" };
    root.appendChild(
      el(
        "p",
        "decision-text",
        labels[d.action] ||
          (d.intent === "delete"
            ? "Вставка будет удалена"
            : (d.intent === "comment" ? "Комментарий: " : "Замена: ") +
              (d.text || "")),
      ),
    );
    root.appendChild(button("Переиграть", () => decide(a, null), "quiet"));
    return root;
  }
  if (a.type === "protected") {
    root.appendChild(
      button("Это не мой голос", () => markAllergen(a), "quiet"),
    );
    return root;
  }
  if (a.type === "note" && a.kind === "image") {
    root.appendChild(
      button(
        "Выбрать или сгенерировать картинку",
        () => emit("images", { context: editor.contextForAnnotation(a) }),
        "primary",
      ),
    );
    root.appendChild(button("Удалить пометку", () => remove(a), "quiet"));
    return root;
  }
  if (a.type === "candidate") {
    const usable = usableVariants(a);
    if (request?.kind === "creative") {
      const generate = button(
        usable.length ? "Ещё варианты" : "Выполнить поручение",
        () => startJob("editorial", { annotation_id: a.id }),
        "editorial-generate " + (usable.length ? "quiet" : "primary"),
      );
      generate.disabled = !latest() || !!workingJob();
      root.appendChild(generate);
      if (!usable.length)
        root.appendChild(
          el(
            "p",
            "muted",
            "ИИ предложит готовый текст для этой вставки. Выбери вариант, затем примени решения.",
          ),
        );
    } else if (request?.kind === "author") {
      root.appendChild(
        el(
          "p",
          "muted",
          "Здесь нужны твои сведения. Впиши ответ — он заменит вставку в скобках.",
        ),
      );
    }
    const variants = el("div", "variants");
    const selected = usable.some((v) => v.index === choices.get(a.id))
      ? choices.get(a.id)
      : usable[0]?.index;
    usable.forEach(({ value, index: i }) => {
      const label = el("label", "variant"),
        radio = el("input");
      radio.type = "radio";
      radio.name = "v-" + a.id;
      radio.value = i;
      radio.checked = selected === i;
      radio.addEventListener("change", () => choices.set(a.id, i));
      label.append(radio, el("span", null, value));
      variants.appendChild(label);
    });
    root.appendChild(variants);
    root.appendChild(
      textarea(
        a,
        request?.kind === "author"
          ? "Твой ответ для текста статьи"
          : "Свой вариант",
      ),
    );
    const actions = el("div", "actions");
    if (usable.length)
      actions.appendChild(
        button("Принять", () => decide(a, "accept"), "primary"),
      );
    actions.appendChild(
      button(request ? "Вставить свой текст" : "Своё", () =>
        decide(a, "custom"),
      ),
    );
    if (request)
      actions.appendChild(
        button("Убрать вставку", () => decide(a, "delete"), "quiet"),
      );
    root.appendChild(actions);
    const reasons = el("details", "reject-reasons");
    reasons.appendChild(el("summary", null, "Оставить как есть…"));
    for (const [label, reason] of [
      ["Мой голос", "voice"],
      ["Уместно здесь", "ok-here"],
      ["Ложное срабатывание", "fp"],
      ["Варианты хуже", "worse"],
    ])
      reasons.appendChild(
        button(label, () => decide(a, "reject", reason), "quiet"),
      );
    root.append(
      reasons,
      button("В аллергены", () => markAllergen(a), "quiet danger"),
    );
  } else {
    root.appendChild(textarea(a, "Что изменить или уточнить"));
    const actions = el("div", "actions");
    actions.append(
      button("Заменить фрагмент", () => decide(a, "custom"), "primary"),
      button("Комментарий", () => decide(a, "comment")),
      button("Удалить пометку", () => remove(a), "quiet"),
    );
    root.appendChild(actions);
  }
  return root;
}
async function remove(a) {
  if (await operation("/api/note-remove", { annotation_id: a.id })) {
    S.active = null;
    emit("selection");
  }
}
async function markAllergen(a) {
  if (S.editor) return;
  const saved = await operation("/api/decision", {
    annotation_id: a.id,
    decision: { action: "allergen" },
  });
  if (!saved) return;
  await operation(
    "/api/allergen",
    {
      pattern: a.class || "Оборот",
      example: a.quote,
      note: "Помечено автором в Гранке.",
    },
    { reload: false },
  );
}
export async function addNote() {
  const range = editor.selection();
  $("#selpop").hidden = true;
  if (!range) {
    toast(
      S.editor ? "Сначала сохрани правку текста" : "Выдели фрагмент статьи",
    );
    return;
  }
  const quote = [...version().text].slice(range.start, range.end).join("");
  const out = await operation("/api/note", {
    note: { version: S.version, ...range, quote, kind: "note" },
  });
  if (out) {
    S.active = out.id;
    getSelection().removeAllRanges();
    emit("pane", { pane: "review" });
    emit("selection");
    $("#cust-" + out.id)?.focus();
  }
}
export function renderReview() {
  if (!S.document) return;
  const all = annotations(),
    judged = all.filter((a) => a.type !== "protected"),
    done = judged.filter((a) => a.decision).length;
  const deep = Array.isArray(S.document.deep_analyses) && !S.contractErrors?.some((error) => error.startsWith("deep_analyses:"))
    ? S.document.deep_analyses.filter((r) => r?.version === S.version).at(-1)?.findings || []
    : [];
  const deepPending = Array.isArray(deep) ? deep.filter((f) => !f.decision).length : 0;
  $("#review-count").textContent = `${done} из ${judged.length}`;
  $("#review-progress").style.width =
    (judged.length ? (done / judged.length) * 100 : 0) + "%";
  $("#review-tab-count").textContent = judged.length - done + deepPending;
  $("#review-tab-count").title = `${judged.length - done} локальных пометок и ${deepPending} замечаний к статье целиком`;
  if (!all.some((a) => a.id === S.active))
    S.active =
      judged.find((a) => !a.decision)?.id || judged[0]?.id || all[0]?.id;
  const root = $("#cards");
  root.replaceChildren();
  const active = all.find((a) => a.id === S.active);
  if (active) root.appendChild(card(active));
  else
    root.appendChild(
      el(
        "p",
        "empty",
        deep.length ? "Локальных замен нет. Замечания к статье целиком показаны выше." : "Замечаний нет. Можно править текст или запустить анализ.",
      ),
    );
  const pending = judged.filter((a) => !a.decision && a.id !== S.active);
  if (pending.length) {
    root.appendChild(
      el("h3", "section-label", "Следующие · " + pending.length),
    );
    for (const a of pending) root.appendChild(card(a, true));
  }
  for (const [name, items] of [
    [
      "Голос автора",
      all.filter((a) => a.type === "protected" && a.id !== S.active),
    ],
    ["Решённые", judged.filter((a) => a.decision && a.id !== S.active)],
  ]) {
    if (!items.length) continue;
    const details = el("details", "review-group");
    details.appendChild(el("summary", null, name + " · " + items.length));
    for (const a of items) details.appendChild(card(a, true));
    root.appendChild(details);
  }
  controls();
}
function controls() {
  const disabled = S.busy || !!S.editor;
  $("#review-locked").hidden = !S.editor;
  $("#cards").inert = disabled;
  for (const generate of document.querySelectorAll(".editorial-generate"))
    generate.disabled = disabled || !latest() || !!workingJob();
  $("#readybtn").disabled =
    disabled ||
    !latest() ||
    !!workingJob() ||
    !annotations().some(
      (a) =>
        a.decision &&
        a.decision.action !== "reject" &&
        a.decision.intent !== "comment",
    );
}
const actions = {
  decide: "Решение",
  undecide: "Возврат на проверку",
  "note-add": "Пометка",
  "note-remove": "Удаление",
  "text-edit": "Правка текста",
  "image-insert": "Картинка",
  "cover-edit": "Обложка",
  "decisions-batch": "Пакет решений",
  "batch-undo": "Отмена пакета",
  "apply-decisions": "Применение",
  analysis: "Анализ",
  "deep-analysis": "Разбор статьи целиком",
  "deep-decision": "Решение по разбору",
  status: "Статус",
  "voice-session": "Голос",
};
function renderHistory() {
  const list = $("#history-list");
  list.replaceChildren();
  for (const event of (S.document?.history || []).slice(-80).reverse()) {
    const row = el("article", "history-item");
    row.append(
      el("time", null, (event.ts || "").replace("T", " ")),
      el("strong", null, actions[event.action] || "Изменение"),
    );
    if (event.quote) row.appendChild(el("p", null, event.quote));
    else if (event.version)
      row.appendChild(
        el(
          "p",
          null,
          (event.base_version ? event.base_version + " → " : "") +
            event.version,
        ),
      );
    list.appendChild(row);
  }
  if (!list.children.length)
    list.appendChild(
      el("p", "empty", "История появится после первого действия."),
    );
}
export function initReview() {
  events.addEventListener("document", () => {
    renderReview();
    renderHistory();
  });
  events.addEventListener("version", renderReview);
  events.addEventListener("selection", renderReview);
  events.addEventListener("controls", controls);
  $("#readybtn").addEventListener("click", applyDecisions);
  $("#addann").addEventListener("click", addNote);
  $("#note-btn").addEventListener("click", addNote);
  document.addEventListener("keydown", (e) => {
    if (
      S.busy ||
      S.editor ||
      document.querySelector("dialog[open]") ||
      document.activeElement?.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
    )
      return;
    const a = annotations().find((x) => x.id === S.active),
      key = e.key.toLowerCase();
    if (!a) return;
    if (key === "j" || key === "k") {
      const pending = annotations().filter(
        (x) => x.type !== "protected" && !x.decision,
      );
      if (!pending.length) return;
      const at = pending.findIndex((x) => x.id === S.active),
        next = (at + (key === "j" ? 1 : -1) + pending.length) % pending.length;
      choose(pending[next].id);
      e.preventDefault();
    } else if (
      /^[1-9]$/.test(key) &&
      a.type === "candidate" &&
      +key <= usableVariants(a).length
    ) {
      choices.set(a.id, usableVariants(a)[+key - 1].index);
      renderReview();
      e.preventDefault();
    } else if (e.key === "Enter" && a.type === "candidate" && !a.decision) {
      decide(a, "accept");
      e.preventDefault();
    }
  });
}
