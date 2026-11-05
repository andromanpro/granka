import { t, localizedOption, locale, localize } from './i18n.js';
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
import { initScheduledPosts, openSchedule } from './scheduled-posts.js';
let context = null,
  prepared = null,
  settings = {},
  generating = null,
  displayed = null,
  locked = false,
  checkingAccess = false,
  attachments = [],
  drafts = {},
  currentMode = "announcement",
  publishedRecords = [],
  deliveryChoice = false,
  publicationsReady = false,
  actionError = "";
const key = () => `granka.telegram:${S.root}|${S.file}`;
const manualPublication = () => settings.publication_mode === "manual";
function renderPublicationMode() {
  const manual = $("#tg-publication-mode").value === "manual";
  $("#tg-bot-credentials").hidden = manual;
  $("#tg-check").hidden = $("#tg-token-clear").hidden = manual;
  localize($("#tg-publication-mode-help"),"textContent",()=>manual
    ? t("ui.90b6b64013fe")
    : t("ui.288ca096eb84"));
}
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
function targets() {
  const seen = new Set();
  return publishedRecords.filter(r => {
    const channel = settings.chat_id;
    if (r.status !== "sent" || r.is_post_head === false || (r.channel.chat_id !== channel && "@" + r.channel.username !== channel)) return false;
    const id = r.channel.chat_id + ":" + (r.message_ids || [r.message_id]).join(",");
    if (seen.has(id)) return false;
    seen.add(id); return true;
  });
}
function deliveryPayload() {
  const action = $("#tg-delivery-action").value;
  const sourceHead = publishedRecords.find(r => r.is_source_head &&
    (r.channel.chat_id === settings.chat_id || "@" + r.channel.username === settings.chat_id));
  return {...payload(), action, target_id: action === "update" ? $("#tg-target").value : null, expected_source_id: sourceHead?.id || null};
}
function renderDelivery() {
  const options = targets(), select = $("#tg-target"), previous = select.value;
  select.replaceChildren();
  for (const item of options) {
    const option = el("option", null, new Date((item.finished || item.created) * 1000).toLocaleString(locale()) + " · " + item.version);
    option.value = item.id; select.appendChild(option);
  }
  if (options.some(r => r.id === previous)) select.value = previous;
  $("#tg-delivery-action").querySelector('[value="update"]').disabled = !options.length;
  if (!deliveryChoice) $("#tg-delivery-action").value = options.length ? "update" : "create";
  const updating = $("#tg-delivery-action").value === "update";
  select.hidden = $("#tg-target-label").hidden = !updating;
  for (const element of [select, $("#tg-delivery-action")]) element.disabled = locked || !!prepared || !publicationsReady;
  const hint = $("#tg-delivery-hint"); hint.replaceChildren();
  const target = options.find(r => r.id === select.value);
  localize(hint,"textContent",()=>prepared?.unchanged ? t("ui.7667337cbab9") : updating
    ? t("ui.a253967b0938")
    : options.length ? t("ui.d8073533bdb5") : t("ui.bb5bce8cb694"));
  const url = prepared?.target_url || target?.url;
  if (url && (updating || options.length)) {
    const link = el("a", null, ()=>t("ui.a7b84ea7d964")); link.href = url; link.target = "_blank"; link.rel = "noreferrer"; hint.appendChild(link);
  }
  if (!publicationsReady) localize(hint,"textContent",()=>t("ui.ff8201e9f696"));
  if (manualPublication()) {
    localize(hint,"textContent",()=>t("ui.8bb1dfb44d86"));
    select.disabled = $("#tg-delivery-action").disabled = true;
  }
}
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
  renderDelivery();
  const rich = $("#tg-mode").value === "article";
  $("#tg-announcement-options").hidden = rich;
  $("#tg-use-article").hidden = !rich;
  $("#tg-cover").closest("label").hidden = rich;
  $("#tg-text").maxLength = rich ? 60000 : 4096;
  localize(document.querySelector('label[for="tg-text"]'),"textContent",()=>rich ? t("ui.e3243273323a") : t("ui.3cc3431b9237"));
  localize($("#tg-format-help"),"textContent",()=>rich
    ? t("ui.fb6141ff4171")
    : t("ui.5a9cb9bcdcc5"));
  const value = prepared?.text ?? assembled();
  if(rich && value) renderMarkdown($("#tg-preview"),value,[],{imageUrl:src=>{
    const index=prepared?.attachments?.findIndex(item=>item.name===decodeURIComponent(src).split('/').at(-1))??-1;
    return index>=0 ? "/api/telegram/media?"+query()+"&id="+prepared.id+"&index="+index : mediaURL(src);
  }});
  else localize($("#tg-preview"),"textContent",()=>value || t("ui.e7e2b6c0e73c"));
  const limit=rich?60000:$("#tg-cover").checked||attachments.length?1024:4096;
  localize($("#tg-count"),"textContent",()=>t("ui.c77769eaa79b",value.length,limit));
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
    const row=el("div","tg-media-row");row.appendChild(el("span",null,()=>(item.type==="video"?t("ui.b6381a3b195f"):t("ui.cbf06126fede"))+item.name));
    const move=delta=>{const to=index+delta;if(to<0||to>=attachments.length)return;[attachments[index],attachments[to]]=[attachments[to],attachments[index]];store();render();};
    for(const [label,fn]of[["↑",()=>move(-1)],["↓",()=>move(1)],[t("ui.07cb65087477"),()=>{attachments.splice(index,1);store();render();}]])row.appendChild(button(label,fn));
    list.appendChild(row);
  });
  localize($("#tg-channel"),"textContent",()=>prepared
    ? prepared.channel.title +
      " · " +
      (prepared.channel.username
        ? "@" + prepared.channel.username
        : prepared.channel.chat_id)
    : settings.chat_id
      ? t("ui.bcdd7ac2129e") + settings.chat_id
      : t("ui.9d39d251ff19"));
  localize($("#tg-sender"),"textContent",()=>manualPublication()
    ? t("ui.c34f4e4bc897")
    : t("ui.7f8588297e82") + (prepared ? " «" + prepared.channel.title + "»" : settings.chat_id ? " · " + settings.chat_id : t("ui.422bb4ba559d")) + t("ui.1f788026bd09"));
  $("#tg-compose").inert = !!prepared || locked;
  for (const id of ["#tg-save", "#tg-copy", "#tg-prepare"])
    $(id).hidden = !!prepared;
  $("#tg-edit").hidden = !prepared;
  $("#tg-publish").hidden = !prepared;
  $("#tg-publish").disabled =
    locked || manualPublication() || !prepared || !["prepared", "failed"].includes(prepared.status) || !!prepared.schedule && prepared.schedule.state !== 'cancelled';
  $('#tg-schedule').hidden = !prepared || !['prepared','failed'].includes(prepared.status);
  $('#tg-schedule').disabled = locked || manualPublication() || !!prepared?.schedule && prepared.schedule.state !== 'cancelled';
  localize($("#tg-publish"),"textContent",()=>prepared?.status === "sent"
      ? prepared.action === "update" ? t("ui.798343da0b45") : t("ui.382ded9c6877")
      : prepared?.status === "failed"
        ? prepared.action === "update" ? t("ui.976e03d39c01") : t("ui.0c2b4e7e894e")
        : prepared?.action === "update" ? t("ui.e331183a1168") : t("ui.8f74b6d49df7") + (prepared?.channel.title || t("ui.e88e1a759ff5")) + "»");
  for (const id of ["#tg-save", "#tg-prepare", "#tg-edit", "#telegram-close", "#tg-back-article", "#tg-check", "#tg-settings-save", "#tg-token-clear"])
    $(id).disabled = locked;
  localize($("#tg-prepare"),"textContent",()=>t("ui.e0facbf1c5e5"));
  $("#tg-prepare").disabled = locked || manualPublication() || !publicationsReady;
  $("#tg-publication-mode").disabled = locked;
  const step = $("#tg-publish-step"), channel = prepared?.channel.title || t("ui.e88e1a759ff5");
  if (!prepared && manualPublication()) localize(step,"textContent",()=>t("ui.515652d7f657"));
  else if (!prepared) localize(step,"textContent",()=>t("ui.6370f30c6f5a"));
  else if (prepared.status === "sent") localize(step,"textContent",()=>prepared.action === "update"
    ? t("ui.3ab103c7069b") + channel + t("ui.675ddaa253ce")
    : t("ui.a9719f1a30ad") + channel + t("ui.7800e01c1474"));
  else if (prepared.status === "sending") localize(step,"textContent",()=>t("ui.bc540efd31f6") + channel + t("ui.bf5cdcf9f0dc"));
  else if (prepared.status === "unknown") localize(step,"textContent",()=>t("ui.59691c762776"));
  else localize(step,"textContent",()=>t("ui.aa2895263479") + (prepared.action === "update" ? t("ui.f65199a3eace") : t("ui.ef620035049a")) +
    t("ui.8a30a3ae6597") + channel + t("ui.99a9cbc1f261"));
  localize($("#tg-check"),"textContent",()=>checkingAccess ? t("ui.74d0346f0cf4") : t("ui.2e7fd36e3cfa"));
  document.querySelectorAll("[data-tg-tab]").forEach((b) => b.disabled = locked);
  if (prepared) {
    const status = $("#tg-status");
    status.replaceChildren();
    if (prepared.status === "sent") {
      const a = el("a", null, ()=>t("ui.808b950a21f8"));
      a.href = prepared.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      status.appendChild(a);
    } else
      localize(status,"textContent",()=>actionError || prepared.error ||
        (prepared.status === "sending"
          ? t("ui.90cd93f26ef6")
          : t("ui.93abb86c6498")));
  }
}
const mediaURL=src=>"/api/article-image?"+query()+"&src="+encodeURIComponent(src);
function addMedia(item){
  if($("#tg-mode").value==="article"){
    const area=$("#tg-text"),start=area.selectionStart,end=area.selectionEnd;
    const alt=item.type==="video"?t("ui.f92fafb4c00a"):t("ui.524c2d2663e6");
    area.setRangeText("\n\n!["+alt+"]("+item.src+")\n\n",start,end,"end");
  }else{
    if(attachments.length+Number($("#tg-cover").checked)>=10)throw Error(t("ui.7dc77cf0e799"));
    if(!attachments.some(a=>a.src===item.src))attachments.push(item);
  }
  store();render();
}
async function refreshSettings() {
  settings = await api("/api/telegram/settings");
  $("#tg-chat-id").value = settings.chat_id;
  $("#tg-token-file").value = settings.token_file;
  $("#tg-token").value = "";
  $("#tg-publication-mode").value = settings.publication_mode || "channel";
  renderPublicationMode();
  delete $("#tg-bot-status").dataset.state;
  $("#tg-bot-status").classList.remove("error");
  localize($("#tg-bot-status"),"textContent",()=>manualPublication()
    ? t("ui.e454b0159f4d")
    : settings.has_token
    ? t("ui.33283e7d72b9")
    : t("ui.52e198928786"));
}
async function publications() {
  const result = await api("/api/telegram/publications?" + query()),
    list = $("#tg-publications");
  list.replaceChildren();
  publishedRecords = result.publications;
  publicationsReady = true;
  for (const record of result.publications) {
    const row = el("p", "fine-print");
    localize(row,"textContent",()=>record.channel.title +
      " · " +
      ({
        prepared: t("ui.61ca6b0c7e22"),
        sent: record.action === "update" ? t("ui.8e6cd0d8c983") : t("ui.5202b1f290af"),
        failed: t("ui.bf22dfeeba8b"),
        sending: t("ui.408d1167e921"),
        unknown: t("ui.076b3991e23f"),
      }[record.status] || record.status));
    if (record.url) {
      const a = el("a", null, ()=>t("ui.95b2abf80f26"));
      a.href = record.url;
      a.target = "_blank";
      a.rel = "noreferrer";
      row.appendChild(a);
    }
    list.appendChild(row);
  }
  if (!list.children.length) localize(list,"textContent",()=>t("ui.e40e6445c42a"));
}
export async function openTelegram() {
  if (S.busy || !latest() || S.recording) return;
  if (S.editor && !(await editor.save())) return;
  context = { root: S.root, file: S.file, version: S.version };
  prepared = null;
  deliveryChoice = false;
  publishedRecords = [];
  publicationsReady = false;
  displayed = null;
  actionError = "";
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
  localize($("#tg-status"),"textContent",()=>draft.version && draft.version !== S.version
      ? t("ui.f7cc2eadb434") +
        draft.version +
        t("ui.2234af29f6bc")
      : "");
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
  localize($("#tg-generation"),"textContent",()=>job
    ? running
      ? job.message + " · " + Math.floor(Date.now() / 1000 - job.created) + t("ui.ddae8eb8bf21")
      : job.status === "succeeded"
        ? t("ui.d2bfc436414e")
        : job.message
    : "");
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
  actionError = "";
  locked = true;
  S.workspaceLocked = true;
  events.dispatchEvent(new Event("workspace-lock"));
  render();
  try {
    await fn();
  } catch (error) {
    actionError = error.message;
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
  initScheduledPosts();
  $('#tg-schedule').addEventListener('click',()=>{if(prepared)openSchedule(prepared);});
  events.addEventListener('schedule-changed',async()=>{prepared=null;await publications();render();});
  $("#tg-publication-mode").addEventListener("change", () => {
    renderPublicationMode();
    localize($("#tg-bot-status"),"textContent",()=>t("ui.9ecd99544957"));
  });
  for (const id of ["#tg-delivery-action", "#tg-target"]) $(id).addEventListener("change", () => { deliveryChoice = true; render(); });
  $("#tg-mode").addEventListener("change",()=>{
    const selected=$("#tg-mode").value;$("#tg-mode").value=currentMode;store();currentMode=selected;$("#tg-mode").value=selected;
    const draft=drafts[selected]||{};$("#tg-text").value=draft.text||"";$("#tg-url").value=draft.url||"";
    $("#tg-cover").checked=selected!=="article"&&!!version().cover&&!!draft.include_cover;attachments=draft.attachments||[];store();render();
  });
  $("#tg-use-article").addEventListener("click",()=>{
    if($("#tg-text").value.trim()&&!confirm(t("ui.d8508559d970")))return;
    $("#tg-text").value=version().text.replace(/^\ufeff?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/,"").trim();store();render();
  });
  $("#tg-add-media").addEventListener("click",()=>$("#tg-upload").click());
  $("#tg-upload").addEventListener("change",()=>action(async()=>{
    const files=[...$("#tg-upload").files];$("#tg-upload").value="";
    for(const file of files){const r=await api("/api/telegram/upload?"+query()+"&name="+encodeURIComponent(file.name),{method:"POST",body:file,headers:{"X-Granka-Revision":S.revision}});addMedia(r.asset);}
    localize($("#tg-status"),"textContent",()=>t("ui.8d122fb4dd1d"));
  }));
  $("#tg-article-assets").addEventListener("click",()=>action(async()=>{
    const {assets}=await api("/api/telegram/assets?"+query()),box=$("#tg-assets");box.replaceChildren();box.hidden=false;
    for(const item of assets)box.appendChild(button(()=>(item.type==="video"?t("ui.b6381a3b195f"):t("ui.cbf06126fede"))+item.name,()=>{try{addMedia(item);}catch(error){toast(error.message,true);}}));
    if(!assets.length)localize(box,"textContent",()=>t("ui.c4b3a1238897"));
  }));
  $("#announcement-btn").addEventListener("click", openTelegram);
  for (const id of ["#telegram-close", "#tg-back-article"]) $(id).addEventListener("click", () => {
    if (locked) return; store(); $("#telegramdialog").close();
  });
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
        localize($("#tg-status"),"textContent",()=>t("ui.6da31bfb8b5b"));
      }
    }),
  );
  $("#tg-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(assembled());
      toast(t("ui.d984065bbaa0"));
    } catch (_) {
      toast(t("ui.99011b176661"));
    }
  });
  $("#tg-prepare").addEventListener("click", async () => {
    await action(async () => {
      if (manualPublication()) throw new Error(t("ui.b82b44e1ba8c"));
      if (!settings.has_token || !settings.chat_id) {
        tab("bot");
        toast(t("ui.3d80e388dc72"));
        return;
      }
      prepared = await api("/api/telegram/prepare?" + query(), {
        method: "POST",
        body: deliveryPayload(),
      });
    });
    if (prepared) $("#tg-publish-step").focus();
  });
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
          publication_mode: $("#tg-publication-mode").value,
        },
      });
      prepared = null;
      await refreshSettings();
      localize($("#tg-bot-status"),"textContent",()=>t("ui.db022e4a824b"));
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
      const status = $("#tg-bot-status");
      const controller = new AbortController();
      let timer;
      checkingAccess = true;
      status.classList.remove("error");
      status.dataset.state = "pending";
      localize(status,"textContent",()=>t("ui.b799e27c5ec4"));
      render();
      try {
        if ($("#tg-token").value.trim() ||
            $("#tg-chat-id").value.trim() !== (settings.chat_id || "") ||
            $("#tg-publication-mode").value !== (settings.publication_mode || "channel") ||
            $("#tg-token-file").value.trim() !== (settings.token_file || ""))
          throw new Error(t("ui.f4ab3c51e36c"));
        timer = setTimeout(() => controller.abort(), 45000);
        const result = await api("/api/telegram/check", {
          method: "POST", body: {}, signal: controller.signal,
        });
        status.dataset.state = "success";
        localize(status,"textContent",()=>t("ui.2f6f78b2a0af") + result.bot_username +
          t("ui.68051d66f7f8") + result.title + t("ui.a2d1cbf35c55") + result.chat_id + ".");
      } catch (error) {
        if (error.name === "AbortError")
          error = new Error(t("ui.8f003081ea2b"));
        status.dataset.state = "error";
        status.classList.add("error");
        status.textContent = error.message;
        throw error;
      } finally {
        clearTimeout(timer);
        checkingAccess = false;
      }
    }),
  );
  events.addEventListener("jobs", renderGeneration);
  events.addEventListener("controls", () => {
    $("#announcement-btn").disabled = S.busy || !latest() || !!S.recording;
  });
}
