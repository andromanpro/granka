import {
  $,
  S,
  api,
  query,
  version,
  latest,
  events,
  el,
  button,
  toast,
  operation,
} from "./core.js";
import { editor } from "./editor.js";
import { startJob } from "./jobs.js";

let context = null,
  prepared = null,
  settings = {},
  generating = null,
  displayed = null,
  locked = false;
const key = () => `granka.telegram:${S.root}|${S.file}`;
const payload = () => ({
  text: $("#tg-text").value,
  url: $("#tg-url").value.trim(),
  include_cover: $("#tg-cover").checked,
  version: context.version,
  expected_revision: S.revision,
});
const assembled = () =>
  $("#tg-text").value.trim() +
  ($("#tg-url").value.trim() ? "\n\n" + $("#tg-url").value.trim() : "");
function tab(name) {
  $("#tg-compose-panel").hidden = name !== "compose";
  $("#tg-bot-panel").hidden = name !== "bot";
  document
    .querySelectorAll("[data-tg-tab]")
    .forEach((b) => b.classList.toggle("selected", b.dataset.tgTab === name));
}
function store() {
  try {
    localStorage.setItem(
      key(),
      JSON.stringify({ ...payload(), direction: $("#tg-direction").value }),
    );
  } catch (_) {}
}
function render() {
  const value = prepared?.text ?? assembled();
  $("#tg-preview").textContent = value || "Здесь появится текст анонса.";
  $("#tg-count").textContent =
    `${value.length} / ${$("#tg-cover").checked ? 1024 : 4096} символов со ссылкой`;
  $("#tg-count").classList.toggle(
    "error",
    value.length > ($("#tg-cover").checked ? 1024 : 4096),
  );
  const photo = prepared?.with_cover
    ? "/api/telegram/photo?" + query() + "&id=" + prepared.id
    : !prepared && $("#tg-cover").checked && version()?.cover
      ? "/api/article-image?" +
        query() +
        "&src=" +
        encodeURIComponent(version().cover.src)
      : null;
  $("#tg-photo").hidden = !photo;
  if (photo) $("#tg-photo").src = photo;
  else $("#tg-photo").removeAttribute("src");
  $("#tg-channel").textContent = prepared
    ? prepared.channel.title +
      " · " +
      (prepared.channel.username
        ? "@" + prepared.channel.username
        : prepared.channel.chat_id)
    : settings.chat_id
      ? "Канал: " + settings.chat_id
      : "Предпросмотр · канал ещё не настроен";
  $("#tg-compose").inert = !!prepared || locked;
  for (const id of ["#tg-save", "#tg-copy", "#tg-prepare"])
    $(id).hidden = !!prepared;
  $("#tg-edit").hidden = !prepared;
  $("#tg-publish").hidden = !prepared;
  $("#tg-publish").disabled =
    locked || !prepared || !["prepared", "failed"].includes(prepared.status);
  $("#tg-publish").textContent =
    prepared?.status === "sent"
      ? "Опубликовано"
      : prepared?.status === "failed"
        ? "Повторить отправку"
        : "Опубликовать в «" + (prepared?.channel.title || "канал") + "»";
  for (const id of ["#tg-save", "#tg-prepare", "#tg-edit", "#telegram-close"])
    $(id).disabled = locked;
  if (prepared) {
    const status = $("#tg-status");
    status.replaceChildren();
    if (prepared.status === "sent") {
      const a = el("a", null, "Открыть опубликованный пост");
      a.href = prepared.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      status.appendChild(a);
    } else
      status.textContent =
        prepared.error ||
        (prepared.status === "sending"
          ? "Отправка выполняется."
          : "Проверь текст, обложку и канал. Кнопка ниже отправит именно этот пост.");
  }
}
async function refreshSettings() {
  settings = await api("/api/telegram/settings");
  $("#tg-chat-id").value = settings.chat_id;
  $("#tg-token-file").value = settings.token_file;
  $("#tg-token").value = "";
  $("#tg-bot-status").textContent = settings.has_token
    ? "Токен настроен."
    : "Токен ещё не настроен.";
}
async function publications() {
  const result = await api("/api/telegram/publications?" + query()),
    list = $("#tg-publications");
  list.replaceChildren();
  for (const record of result.publications) {
    const row = el("p", "fine-print");
    row.textContent =
      record.channel.title +
      " · " +
      ({
        prepared: "Подготовлен",
        sent: "Опубликован",
        failed: "Ошибка",
        sending: "Отправляется",
        unknown: "Проверь канал",
      }[record.status] || record.status);
    if (record.url) {
      const a = el("a", null, " Открыть пост");
      a.href = record.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      row.appendChild(a);
    }
    list.appendChild(row);
  }
  if (!list.children.length) list.textContent = "Публикаций пока нет.";
}
export async function openTelegram() {
  if (S.busy || !latest() || S.recording) return;
  if (S.editor && !(await editor.save())) return;
  context = { root: S.root, file: S.file, version: S.version };
  prepared = null;
  displayed = null;
  let draft = S.document.announcement || {};
  try {
    draft = JSON.parse(localStorage.getItem(key())) || draft;
  } catch (_) {}
  $("#tg-text").value = draft.text || "";
  $("#tg-url").value = draft.url || "";
  $("#tg-direction").value = draft.direction || "";
  $("#tg-cover").disabled = !version().cover;
  $("#tg-cover").checked = !!version().cover && (draft.include_cover ?? true);
  $("#tg-status").textContent =
    draft.version && draft.version !== S.version
      ? "Черновик анонса от " +
        draft.version +
        ". Проверь его по текущей версии статьи."
      : "";
  $("#tg-variants").replaceChildren();
  tab("compose");
  render();
  $("#telegramdialog").showModal();
  try {
    await Promise.all([refreshSettings(), publications()]);
    render();
    renderGeneration();
  } catch (error) {
    toast(error.message, true);
  }
}
function renderGeneration() {
  if (!context || !$("#telegramdialog").open) return;
  const job = S.jobs.find(
    (j) => j.kind === "announcement" && j.version === context.version,
  );
  const running = job && ["queued", "running"].includes(job.status);
  $("#tg-generate").disabled = !!running || generating;
  $("#tg-generation").textContent = job
    ? running
      ? job.message + " · " + Math.floor(Date.now() / 1000 - job.created) + " с"
      : job.status === "succeeded"
        ? "Выбери вариант, затем поправь текст под себя."
        : job.message
    : "";
  if (job?.status === "succeeded" && job.id !== displayed) {
    displayed = job.id;
    const box = $("#tg-variants");
    box.replaceChildren();
    for (const variant of job.result.variants)
      box.appendChild(
        button(variant.label, () => {
          $("#tg-text").value = variant.text;
          store();
          render();
        }),
      );
  }
}
async function action(fn) {
  if (locked) return;
  locked = true;
  render();
  try {
    await fn();
  } catch (error) {
    toast(error.message, true);
    $("#tg-status").textContent = error.message;
  } finally {
    locked = false;
    render();
  }
}
export function initTelegram() {
  $("#announcement-btn").addEventListener("click", openTelegram);
  $("#telegram-close").addEventListener("click", () =>
    $("#telegramdialog").close(),
  );
  $("#telegramdialog").addEventListener("cancel", (e) => {
    if (locked) e.preventDefault();
  });
  document.querySelectorAll("[data-tg-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      if (!locked) tab(b.dataset.tgTab);
    }),
  );
  for (const id of ["#tg-text", "#tg-url", "#tg-cover", "#tg-direction"])
    $(id).addEventListener("input", () => {
      store();
      render();
    });
  $("#tg-generate").addEventListener("click", async () => {
    generating = true;
    renderGeneration();
    try {
      await startJob("announcement", { prompt: $("#tg-direction").value });
    } finally {
      generating = false;
      renderGeneration();
    }
  });
  $("#tg-save").addEventListener("click", () =>
    action(async () => {
      const result = await operation("/api/announcement", payload(), {
        reload: false,
      });
      if (result) {
        S.document.announcement = result.announcement;
        store();
        $("#tg-status").textContent = "Черновик анонса сохранён.";
      }
    }),
  );
  $("#tg-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(assembled());
      toast("Анонс скопирован");
    } catch (_) {
      toast("Выдели и скопируй текст анонса вручную");
    }
  });
  $("#tg-prepare").addEventListener("click", () =>
    action(async () => {
      if (!settings.has_token || !settings.chat_id) {
        tab("bot");
        toast("Сначала настрой бота и канал");
        return;
      }
      prepared = await api("/api/telegram/prepare?" + query(), {
        method: "POST",
        body: payload(),
      });
    }),
  );
  $("#tg-edit").addEventListener("click", () => {
    prepared = null;
    $("#tg-status").textContent = "";
    render();
  });
  $("#tg-publish").addEventListener("click", () =>
    action(async () => {
      prepared = await api("/api/telegram/publish?" + query(), {
        method: "POST",
        body: { id: prepared.id, confirmed: true },
      });
      await publications();
    }),
  );
  $("#tg-settings-save").addEventListener("click", () =>
    action(async () => {
      await api("/api/telegram/settings", {
        method: "POST",
        body: {
          chat_id: $("#tg-chat-id").value,
          token: $("#tg-token").value,
          token_file: $("#tg-token-file").value,
        },
      });
      prepared = null;
      await refreshSettings();
      $("#tg-bot-status").textContent = "Настройки сохранены.";
    }),
  );
  $("#tg-token-clear").addEventListener("click", () =>
    action(async () => {
      await api("/api/telegram/settings", {
        method: "POST",
        body: { clear_token: true },
      });
      prepared = null;
      await refreshSettings();
    }),
  );
  $("#tg-check").addEventListener("click", () =>
    action(async () => {
      const result = await api("/api/telegram/check", {
        method: "POST",
        body: {},
      });
      $("#tg-bot-status").textContent =
        "@" +
        result.bot_username +
        " может публиковать в «" +
        result.title +
        "».";
    }),
  );
  events.addEventListener("jobs", renderGeneration);
  events.addEventListener("controls", () => {
    $("#announcement-btn").disabled = S.busy || !latest() || !!S.recording;
  });
}
