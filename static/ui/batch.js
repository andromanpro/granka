import {
  $,
  S,
  annotations,
  latest,
  el,
  button,
  events,
  emit,
  operation,
  toast,
} from "./core.js";
import { workingJob } from "./jobs.js";
let current = null,
  locked = false;
function groups() {
  const result = new Map();
  for (const a of annotations()) {
    if (
      a.type !== "candidate" ||
      a.decision ||
      !a.rule_id ||
      a.editorial_request
    )
      continue;
    const key = a.rule_id + "|" + a.rule_title;
    if (!result.has(key)) result.set(key, []);
    result.get(key).push(a);
  }
  return [...result.values()].filter((items) => items.length > 1);
}
function controls() {
  $("#batch-groups").inert =
    S.busy || !!S.editor || !latest() || !!workingJob();
  $("#batch-save").disabled = locked;
}
function render() {
  const box = $("#batch-groups");
  box.replaceChildren();
  for (const items of groups()) {
    const row = el("div", "batch-group");
    row.appendChild(
      el(
        "span",
        null,
        (items[0].rule_title || items[0].class) + " · " + items.length,
      ),
    );
    row.append(
      button("Принять варианты", () => open(items, "accept"), "quiet"),
      button("Оставить", () => open(items, "reject"), "quiet"),
    );
    box.appendChild(row);
  }
  const last = [...(S.document?.history || [])]
    .reverse()
    .find((e) => e.action === "decisions-batch" && e.version === S.version);
  if (
    last &&
    last.annotations.every((id) =>
      annotations().some(
        (a) => a.id === id && a.decision?.batch_id === last.batch_id,
      ),
    )
  ) {
    box.appendChild(
      button(
        "Отменить последний пакет · " + last.count,
        async () => {
          await operation("/api/decisions-batch/undo", {
            version: S.version,
            batch_id: last.batch_id,
          });
        },
        "quiet",
      ),
    );
  }
  controls();
}
function open(items, action) {
  current = {
    items,
    action,
    version: S.version,
    revision: S.revision,
    root: S.root,
    file: S.file,
  };
  $("#batch-title").textContent = items[0].rule_title || items[0].class;
  $("#batch-reason-row").hidden = action !== "reject";
  $("#batch-status").textContent = "";
  const box = $("#batch-items");
  box.replaceChildren();
  for (const a of items) {
    const row = el("article", "batch-item");
    row.dataset.id = a.id;
    const label = el("label", "tg-checkbox"),
      check = el("input");
    check.type = "checkbox";
    check.checked = true;
    label.append(
      check,
      el(
        "strong",
        null,
        action === "accept" ? "Заменить фрагмент" : "Оставить фрагмент",
      ),
    );
    row.appendChild(label);
    row.appendChild(el("blockquote", null, a.quote));
    if (action === "accept") {
      const select = el("select");
      select.setAttribute("aria-label", "Вариант замены");
      (a.variants || []).forEach((v, i) => {
        if (v.trim() && v.trim() !== a.quote.trim())
          select.appendChild(new Option("Вариант " + (i + 1), String(i)));
      });
      const after = el("p", "batch-after");
      after.textContent = a.variants[+select.value] || "";
      select.addEventListener(
        "change",
        () => (after.textContent = a.variants[+select.value]),
      );
      row.append(select, after);
      if (!select.options.length) {
        check.checked = false;
        check.disabled = true;
        after.textContent =
          "Нет готового варианта — разбери эту карточку отдельно.";
      }
    }
    box.appendChild(row);
  }
  $("#batchdialog").showModal();
}
export function initBatch() {
  for (const name of ["document", "version"])
    events.addEventListener(name, render);
  events.addEventListener("controls", controls);
  $("#batch-close").addEventListener("click", () => {
    if (!locked) $("#batchdialog").close();
  });
  $("#batchdialog").addEventListener("cancel", (event) => {
    if (locked) event.preventDefault();
  });
  $("#batch-save").addEventListener("click", async () => {
    if (locked || !current) return;
    if (
      current.version !== S.version ||
      current.revision !== S.revision ||
      current.root !== S.root ||
      current.file !== S.file
    ) {
      toast("Статья изменилась. Открой пакет заново.", true);
      return;
    }
    const items = [...$("#batch-items").children]
      .filter((row) => row.querySelector("input").checked)
      .map((row) => ({
        id: row.dataset.id,
        action: current.action,
        ...(current.action === "accept"
          ? { variant: +row.querySelector("select").value }
          : { reason: $("#batch-reason").value }),
      }));
    if (!items.length) {
      toast("Выбери хотя бы один фрагмент");
      return;
    }
    locked = true;
    controls();
    try {
      const result = await operation("/api/decisions-batch", {
        version: current.version,
        rule_id: current.items[0].rule_id,
        items,
      });
      if (result) {
        $("#batchdialog").close();
        toast("Сохранено решений: " + result.count);
        emit("selection");
      }
    } finally {
      locked = false;
      controls();
    }
  });
}
