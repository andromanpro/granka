import {render as renderMarkdown} from '../markdown-view.js';
import {
  $,
  S,
  api,
  query,
  version,
  latest,
  events,
  emit,
  el,
  button,
  toast,
  operation,
} from "./core.js";
import { editor } from "./editor.js";
import { startJob } from "./jobs.js";

import { showWorkspacePage } from './workspace.js';
let context = null,
  prepared = null,
  settings = {},
  generating = null,
  displayed = null,
  locked = false,
  attachments = [],
  drafts = {},
  currentMode = "announcement";
const key = () => `granka.telegram:${S.root}|${S.file}`;
const payload = () => ({
  text: $("#tg-text").value,
  url: $("#tg-url").value.trim(),
  include_cover: $("#tg-cover").checked,
  mode: $("#tg-mode").value,
  attachments: $("#tg-mode").value === "article" ? [] : attachments,
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
  drafts[currentMode] = payload();
  try {
    localStorage.setItem(
      key(),
      JSON.stringify({ ...payload(), drafts, direction: $("#tg-direction").value }),
    );
  } catch (_) {}
}
function render() {
  const rich = $("#tg-mode").value === "article";
  $("#tg-announcement-options").hidden = rich;
  $("#tg-use-article").hidden = !rich;
  $("#tg-cover").closest("label").hidden = rich;
  $("#tg-text").maxLength = rich ? 60000 : 4096;
  document.querySelector('label[for="tg-text"]').textContent = rich ? "Текст статьи · Markdown" : "Текст анонса";
  $("#tg-format-help").textContent = rich
    ? "Картинки и видео размещаются между абзацами. Добавление файла вставит его в текст. Отправка использует Rich Messages; вид зависит от версии Telegram."
    : "До 10 фото и видео, включая обложку. С вложениями подпись до 1024 символов; без них — до 4096.";
  const value = prepared?.text ?? assembled();
  if(rich && value) renderMarkdown($("#tg-preview"),value,[],{imageUrl:src=>{
    const index=prepared?.attachments?.findIndex(item=>item.name===decodeURIComponent(src).split('/').at(-1))??-1;
    return index>=0 ? "/api/telegram/media?"+query()+"&id="+prepared.id+"&index="+index : mediaURL(src);
  }});
  else $("#tg-preview").textContent = value || "Здесь появится текст.";
  const limit=rich?60000:$("#tg-cover").checked||attachments.length?1024:4096;
  $("#tg-count").textContent =
    `${value.length} / ${limit} символов со ссылкой`;
  $("#tg-count").classList.toggle(
    "error",
    value.length > limit,
  );
  const photo = prepared?.with_cover && !prepared?.attachments?.length
    ? "/api/telegram/photo?" + query() + "&id=" + prepared.id
    : !prepared && !rich && $("#tg-cover").checked && version()?.cover
      ? "/api/article-image?" +
        query() +
        "&src=" +
        encodeURIComponent(version().cover.src)
      : null;
  $("#tg-photo").hidden = !photo;
  if (photo) $("#tg-photo").src = photo;
  else $("#tg-photo").removeAttribute("src");
  const gallery=$("#tg-media-preview");gallery.replaceChildren();
  if(!rich)for(const [i,item]of(prepared?.attachments||attachments).entries()){
    const node=el(item.type==="video"?"video":"img");
    node.src=prepared?"/api/telegram/media?"+query()+"&id="+prepared.id+"&index="+i:mediaURL(item.src);
    if(item.type==="video"){node.controls=true;node.preload="auto";}else node.alt=item.name;
    gallery.appendChild(node);
  }
  const list=$("#tg-media-list");list.replaceChildren();
  if(!rich)attachments.forEach((item,index)=>{
    const row=el("div","tg-media-row");row.appendChild(el("span",null,(item.type==="video"?"Видео · ":"Фото · ")+item.name));
    const move=delta=>{const to=index+delta;if(to<0||to>=attachments.length)return;[attachments[index],attachments[to]]=[attachments[to],attachments[index]];store();render();};
    for(const [label,fn]of[["↑",()=>move(-1)],["↓",()=>move(1)],["Убрать",()=>{attachments.splice(index,1);store();render();}]])row.appendChild(button(label,fn));
    list.appendChild(row);
  });
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
          : "Проверь текст, вложения и канал. Кнопка ниже отправит сохранённый снимок этого поста.");
  }
}
const mediaURL=src=>"/api/article-image?"+query()+"&src="+encodeURIComponent(src);
function addMedia(item){
  if($("#tg-mode").value==="article"){
    const area=$("#tg-text"),start=area.selectionStart,end=area.selectionEnd;
    const alt=item.type==="video"?"Видео":"Иллюстрация";
    area.setRangeText("\n\n!["+alt+"]("+item.src+")\n\n",start,end,"end");
  }else{
    if(attachments.length+Number($("#tg-cover").checked)>=10)throw Error("В одном альбоме до10вложений,включая обложку");
    if(!attachments.some(a=>a.src===item.src))attachments.push(item);
  }
  store();render();
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
  drafts={...(S.document.telegram_drafts||{}),...(draft.drafts||{})};
  currentMode=draft.mode||"announcement";$("#tg-mode").value=currentMode;
  draft=drafts[currentMode]||draft;attachments=draft.attachments||[];
  $("#tg-text").value = draft.text || "";
  $("#tg-url").value = draft.url || "";
  $("#tg-direction").value = draft.direction || "";
  $("#tg-cover").disabled = !version().cover;
  $("#tg-cover").checked = currentMode!=="article" && !!version().cover && (draft.include_cover ?? true);
  $("#tg-assets").hidden=true;
  $("#tg-status").textContent =
    draft.version && draft.version !== S.version
      ? "Черновик анонса от " +
        draft.version +
        ". Проверь его по текущей версии статьи."
      : "";
  $("#tg-variants").replaceChildren();
  tab("compose");
  render();
  showWorkspacePage("#telegramdialog", "telegram");
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
  S.workspaceLocked = true;
  events.dispatchEvent(new Event("workspace-lock"));
  render();
  try {
    await fn();
  } catch (error) {
    toast(error.message, true);
    $("#tg-status").textContent = error.message;
  } finally {
    locked = false;
    S.workspaceLocked = false;
    events.dispatchEvent(new Event("workspace-lock"));
    render();
  }
}
export function initTelegram() {
  $("#tg-mode").addEventListener("change",()=>{
    const selected=$("#tg-mode").value;$("#tg-mode").value=currentMode;store();currentMode=selected;$("#tg-mode").value=selected;
    const draft=drafts[selected]||{};$("#tg-text").value=draft.text||"";$("#tg-url").value=draft.url||"";
    $("#tg-cover").checked=selected!=="article"&&!!version().cover&&!!draft.include_cover;attachments=draft.attachments||[];store();render();
  });
  $("#tg-use-article").addEventListener("click",()=>{
    if($("#tg-text").value.trim()&&!confirm("Заменить текст Telegram текущей статьёй? Черновик статьи в редакторе не изменится."))return;
    $("#tg-text").value=version().text.replace(/^\ufeff?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/,"").trim();store();render();
  });
  $("#tg-add-media").addEventListener("click",()=>$("#tg-upload").click());
  $("#tg-upload").addEventListener("change",()=>action(async()=>{
    const files=[...$("#tg-upload").files];$("#tg-upload").value="";
    for(const file of files){const r=await api("/api/telegram/upload?"+query()+"&name="+encodeURIComponent(file.name),{method:"POST",body:file,headers:{"X-Granka-Revision":S.revision}});addMedia(r.asset);}
    $("#tg-status").textContent="Материалы добавлены в папку статьи. Сохрани черновик Telegram.";
  }));
  $("#tg-article-assets").addEventListener("click",()=>action(async()=>{
    const {assets}=await api("/api/telegram/assets?"+query()),box=$("#tg-assets");box.replaceChildren();box.hidden=false;
    for(const item of assets)box.appendChild(button((item.type==="video"?"Видео · ":"Фото · ")+item.name,()=>{try{addMedia(item);}catch(error){toast(error.message,true);}}));
    if(!assets.length)box.textContent="В папке статьи пока нет фото и видео.";
  }));
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
        $("#tg-status").textContent = "Черновик Telegram сохранён.";
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
      emit('publication-sent');
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
