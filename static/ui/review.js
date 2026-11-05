import { t, localizedOption, locale, localize } from './i18n.js';
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
      toast(t("ui.0e3e47c55628"));
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
      toast(t("ui.1bebc27da4de"));
      return;
    }
    decision = { action, text, intent: "replace" };
  }
  if (action === "comment") {
    const text = (drafts.get(a.id) || a.note || "").trim();
    if (!text) {
      toast(t("ui.4c38060e4456"));
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
      ? t("ui.ea1e59aa398e")
      : a.type === "note"
        ? a.kind === "image"
          ? t("ui.a02924da692d")
          : t("ui.809b4fa27561")
        : request
          ? {
              creative: t("ui.bf8177194d6a"),
              author: t("ui.c508f19f8321"),
              unsupported: t("ui.8ba3dac232ff"),
            }[request.kind]
          : a.class || t("ui.fc919c794f45");
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
    if (a.decision) root.appendChild(el("small", "muted", ()=>t("ui.63f6302a4dc9")));
    return root;
  }
  if (a.why) root.appendChild(el("p", "explanation", a.why));
  if (a.decision) {
    const d = a.decision,
      labels = { reject: t("ui.508d93760b8b"), allergen: t("ui.a966b8082c52") };
    root.appendChild(
      el(
        "p",
        "decision-text",
        ()=>labels[d.action] ||
          (d.intent === "delete"
            ? t("ui.1d04f346fda8")
            : (d.intent === "comment" ? t("ui.67cb5488d26f") : t("ui.9528af9527d2")) +
              (d.text || "")),
      ),
    );
    root.appendChild(button(()=>t("ui.d95b37588046"), () => decide(a, null), "quiet"));
    return root;
  }
  if (a.type === "protected") {
    root.appendChild(
      button(()=>t("ui.d236c9e85b31"), () => markAllergen(a), "quiet"),
    );
    return root;
  }
  if (a.type === "note" && a.kind === "image") {
    root.appendChild(
      button(
        ()=>t("ui.d02ba4fe6dcf"),
        () => emit("images", { context: editor.contextForAnnotation(a) }),
        "primary",
      ),
    );
    root.appendChild(button(()=>t("ui.b78043b784de"), () => remove(a), "quiet"));
    return root;
  }
  if (a.type === "candidate") {
    const usable = usableVariants(a);
    if (request?.kind === "creative") {
      const generate = button(
        ()=>usable.length ? t("ui.d2c8a33499ee") : t("ui.7cc0eab1a141"),
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
            ()=>t("ui.d3f7cadcca38"),
          ),
        );
    } else if (request?.kind === "author") {
      root.appendChild(
        el(
          "p",
          "muted",
          ()=>t("ui.4470bc27736c"),
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
          ? t("ui.571595e4d053")
          : t("ui.a78f0004f45e"),
      ),
    );
    const actions = el("div", "actions");
    if (usable.length)
      actions.appendChild(
        button(()=>t("ui.9659ad64c284"), () => decide(a, "accept"), "primary"),
      );
    actions.appendChild(
      button(()=>request ? t("ui.0bd6bf25fb0f") : t("ui.ecd3b2a11b19"), () =>
        decide(a, "custom"),
      ),
    );
    if (request)
      actions.appendChild(
        button(()=>t("ui.1053f9999714"), () => decide(a, "delete"), "quiet"),
      );
    root.appendChild(actions);
    const reasons = el("details", "reject-reasons");
    reasons.appendChild(el("summary", null, ()=>t("ui.65e407d4ed71")));
    for (const [label, reason] of [
      [t("ui.b4489c968fb2"), "voice"],
      [t("ui.612fb27ef064"), "ok-here"],
      [t("ui.8aa0d5a1a7e1"), "fp"],
      [t("ui.1202d0cd3b3c"), "worse"],
    ])
      reasons.appendChild(
        button(label, () => decide(a, "reject", reason), "quiet"),
      );
    root.append(
      reasons,
      button(()=>t("ui.de9dc9600c10"), () => markAllergen(a), "quiet danger"),
    );
  } else {
    root.appendChild(textarea(a, t("ui.6eefecf9e487")));
    const actions = el("div", "actions");
    actions.append(
      button(()=>t("ui.db90b38f1429"), () => decide(a, "custom"), "primary"),
      button(()=>t("ui.d89b31028652"), () => decide(a, "comment")),
      button(()=>t("ui.b78043b784de"), () => remove(a), "quiet"),
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
      pattern: a.class || t("ui.003a5c06e58b"),
      example: a.quote,
      note: t("ui.71b7ef2c0b22"),
    },
    { reload: false },
  );
}
export async function addNote() {
  const range = editor.selection();
  $("#selpop").hidden = true;
  if (!range) {
    toast(
      S.editor ? t("ui.f7056d9c8f89") : t("ui.d6e1e4018325"),
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
  localize($("#review-count"),"textContent",()=>t("ui.0191332365c7",done,judged.length));
  $("#review-progress").style.width =
    (judged.length ? (done / judged.length) * 100 : 0) + "%";
  $("#review-tab-count").textContent = judged.length - done + deepPending;
  localize($("#review-tab-count"),"title",()=>t("ui.1b54b2d8b9b5",judged.length - done,deepPending));
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
        ()=>deep.length ? t("ui.6e8ed1397467") : t("ui.092212986867"),
      ),
    );
  const pending = judged.filter((a) => !a.decision && a.id !== S.active);
  if (pending.length) {
    root.appendChild(
      el("h3", "section-label", ()=>t("ui.e88ba068d15f") + pending.length),
    );
    for (const a of pending) root.appendChild(card(a, true));
  }
  for (const [name, items] of [
    [
      t("ui.ea1e59aa398e"),
      all.filter((a) => a.type === "protected" && a.id !== S.active),
    ],
    [t("ui.bb4572f133b3"), judged.filter((a) => a.decision && a.id !== S.active)],
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
  decide: t("ui.199d6db958cd"),
  undecide: t("ui.94d970fcc66a"),
  "note-add": t("ui.809b4fa27561"),
  "note-remove": t("ui.303c2b490a51"),
  "text-edit": t("ui.666f751366cb"),
  "image-insert": t("ui.a02924da692d"),
  "cover-edit": t("ui.5bcd9613eb14"),
  "spice-edit": t("ui.1c85fff09d68"),
  "version-restore": t("ui.90d4deec7cc1"),
  "decisions-batch": t("ui.d569874a11e8"),
  "batch-undo": t("ui.a0b86377ba67"),
  "apply-decisions": t("ui.94c9a70a782f"),
  analysis: t("ui.d7327abdc0fe"),
  "deep-analysis": t("ui.f926ce9e2529"),
  "deep-decision": t("ui.22836fbcdaf7"),
  status: t("ui.225077c6d479"),
  "voice-session": t("ui.24a0d703afd4"),
};
function renderHistory() {
  const list = $("#history-list");
  list.replaceChildren();
  for (const event of (S.document?.history || []).slice(-80).reverse()) {
    const row = el("article", "history-item");
    row.append(
      el("time", null, (event.ts || "").replace("T", " ")),
      el("strong", null, ()=>actions[event.action] || t("ui.9f0cda93578f")),
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
      el("p", "empty", ()=>t("ui.55f452b8ecab")),
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
