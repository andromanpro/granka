import { t, localizedOption, locale, localize } from './i18n.js';
import {uiText} from './i18n.js';
import { $, S, api, emit, events, query, version, toast } from "./core.js";
let sequence = 0,
  timer,
  animation,
  highlights = false;
const color = (n) =>
  n < 15 ? "#68d8b0" : n < 30 ? "#d8cf85" : n < 45 ? "#efb279" : "#ee8994";
export const meterHighlights = () =>
  highlights ? S.meter?.highlights || [] : [];
function drawHighlightButton() {
  const button = $("#meterhl"),
    count = S.meter?.axes?.["маркеры"]?.hits;
  localize(button,"textContent",()=>highlights
    ? t("ui.e2f1b96f8f5f") + (count == null ? "" : " · " + count)
    : t("ui.9e1ffb77e222") + (count == null ? "" : " · " + count));
  button.setAttribute("aria-pressed", String(highlights));
}
export function clearMeter() {
  ++sequence;
  clearTimeout(timer);
  cancelAnimationFrame(animation);
  S.meter = null;
  $("#meterwidget").classList.remove("measuring");
  $("#meterwidget").setAttribute("aria-busy", "false");
  $("#meterrefresh").disabled = false;
  $("#meter-error").textContent = '';
  $("#meter-retry").hidden = true;
  highlights = false;
  drawHighlightButton();
  $("#meter-number").textContent = "—";
  $("#meter-orbit").style.setProperty("--value", 0);
  localize($("#meter-status"),"textContent",()=>S.document ? t("ui.5e396de77ce2") : t("ui.37b1ac53c4b4"));
  for (const name of ['voice', 'structure', 'markers']) {
    $('#axis-' + name + '-number').textContent = '—';
    $('#axis-' + name).style.width = '0%';
  }
  $('#meterflags').replaceChildren();
  $('#meterscale').textContent = '';
  localize($("#mobile-meter"),"textContent",()=>t("ui.e3c370fa4400"));
}
export function scheduleMeter() {
  clearTimeout(timer);
  if (S.document && !S.editor) timer = setTimeout(() => measure(), 120);
}
export async function measure() {
  if (!S.document || S.editor) return;
  const seq = ++sequence,
    key = query(),
    vid = S.version,
    profile = $("#meterprofile").value;
  $("#meterwidget").classList.add("measuring");
  $("#meterwidget").setAttribute("aria-busy", "true");
  $("#meterrefresh").disabled = true;
  $("#meter-error").textContent = '';
  $("#meter-retry").hidden = true;
  localize($("#meter-status"),"textContent",()=>t("ui.c318a94e24c7"));
  try {
    const result = await api(
      "/api/iimeter?" +
        key +
        "&version=" +
        encodeURIComponent(vid) +
        "&profile=" +
        profile,
    );
    if (seq !== sequence || key !== query() || vid !== S.version || profile !== $('#meterprofile').value || S.editor) return;
    S.meter = result;
    drawMeter(result);
    if (highlights) emit("reader");
  } catch (error) {
    if (seq !== sequence || key !== query() || vid !== S.version || profile !== $('#meterprofile').value || S.editor) return;
    S.meter = null;
    $('#meter-number').textContent = '—';
    $('#meter-orbit').style.setProperty('--value', 0);
    for (const name of ['voice', 'structure', 'markers']) {
      $('#axis-' + name + '-number').textContent = '—'; $('#axis-' + name).style.width = '0%';
    }
    $('#meterflags').replaceChildren(); $('#meterscale').textContent = '';
    $('#meter-error').textContent = error.message;
    $('#meter-retry').hidden = false;
    localize($("#meter-status"),"textContent",()=>t("ui.39d053b301fe"));
    toast(error.message, true);
  } finally {
    if (seq === sequence) {
      $("#meterwidget").classList.remove("measuring");
      $("#meterwidget").setAttribute("aria-busy", "false");
      $("#meterrefresh").disabled = false;
    }
  }
}
function drawMeter(meter) {
  drawHighlightButton();
  const n = meter.score,
    tone = n == null ? "#809aab" : color(n),
    orbit = $("#meter-orbit");
  orbit.style.setProperty("--tone", tone);
  cancelAnimationFrame(animation);
  if (n == null) { $("#meter-number").textContent = "—"; orbit.style.setProperty('--value', 0); }
  else {
    const start = performance.now(),
      duration = matchMedia("(prefers-reduced-motion: reduce)").matches
        ? 0
        : 750;
    const draw = (now) => {
      const t = duration ? Math.min(1, (now - start) / duration) : 1,
        value = n * (1 - (1 - t) ** 3);
      $("#meter-number").textContent = Math.round(value);
      orbit.style.setProperty("--value", value);
      if (t < 1) animation = requestAnimationFrame(draw);
    };
    animation = requestAnimationFrame(draw);
  }
  localize($("#meter-status"),"textContent",()=>n == null
      ? meter.status === 'unsupported_language' ? t("ui.c472d78cc622")
      : meter.status === "uncalibrated"
        ? (meter.corpus_state?.reason || t("ui.15ad401e77e9"))
        : t("ui.b1e6f3cae6a5")
      : uiText(meter.profile_label) + " · " + meter.words + t("ui.7b6787518b6d") + (meter.sample_warning ? t("ui.2df51f61b11a") : ''));
  localize($("#mobile-meter"),"textContent",()=>t("ui.e6961978df2a") + (n ?? "—"));
  for (const [key, id] of [
    ["голос", "voice"],
    ["структура", "structure"],
    ["маркеры", "markers"],
  ]) {
    const available = !['insufficient_text','unsupported_language'].includes(meter.status) && (key !== "голос" || n != null),
      value = available ? meter.axes[key].score : null;
    $("#axis-" + id + "-number").textContent = value ?? "—";
    $("#axis-" + id).style.width = (value ?? 0) + "%";
  }
  const notes = $("#meterflags");
  notes.replaceChildren();
  const texts = [
    ...(meter.status === "uncalibrated"
      ? [meter.corpus_state?.reason || t("ui.90e08ba4afd4"), t("ui.0556e0237e1f")]
      : meter.status === "insufficient_text" ? [t("ui.7bb7e0910840")] : []),
    ...(meter.sample_warning ? [meter.sample_warning] : []),
    ...(meter.status !== "insufficient_text" ? meter.axes["структура"].flags : []),
    meter.axes["маркеры"].hits
      ? t("ui.b611fa538d03") + meter.axes["маркеры"].hits + t("ui.f74fe0adfc3e")
      : t("ui.ff47c66ff3e7"),
  ];
  if (meter.axes["маркеры"].hits > meter.highlights.length)
    texts.push(t("ui.617bc984b7c0") + meter.highlights.length + t("ui.79dca9641ad7") + meter.axes["маркеры"].hits + ".");
  if(meter.status==='unsupported_language')texts.splice(0,texts.length,t("ui.f60babf44ab5"));
  for (const text of texts) {
    const p = document.createElement("p");
    localize(p,'textContent',()=>uiText(text));
    notes.appendChild(p);
  }
  const samples = (meter.calibration?.samples || [])
    .map((x) => x.score)
    .filter((x) => typeof x === "number");
  localize($("#meterscale"),"textContent",()=>(samples.length
      ? t("ui.a23989e65490") +
        Math.min(...samples) +
        "–" +
        Math.max(...samples) +
        ". "
      : "") +
    t("ui.e5c64e28e0a0") +
    meter.axes["голос"].corpus.length +
    t("ui.487a9408824c") +
    meter.axes["голос"].corpus_words +
    t("ui.8c7b847a61da"));
}
export function initMeter() {
  initCorpusSettings();
  const profile = localStorage.getItem("granka.voice-profile");
  if (profile === "talks" || profile === "blog")
    $("#meterprofile").value = profile;
  $("#meterrefresh").addEventListener("click", measure);
  $("#meterprofile").addEventListener("change", () => {
    localStorage.setItem("granka.voice-profile", $("#meterprofile").value);
    clearMeter();
    measure();
  });
  $("#meterhl").addEventListener("click", () => {
    if (S.editor) {
      toast(t("ui.7eb00c7c509f"));
      return;
    }
    highlights = !highlights;
    drawHighlightButton();
    emit("reader");
  });
  events.addEventListener("document", () => {
    clearMeter();
    scheduleMeter();
  });
  events.addEventListener("version", () => {
    clearMeter();
    scheduleMeter();
  });
  events.addEventListener("dirty", () => {
    ++sequence; clearTimeout(timer); cancelAnimationFrame(animation);
    $('#meterwidget').classList.remove('measuring'); $('#meterwidget').setAttribute('aria-busy', 'false');
    $('#meterrefresh').disabled = false;
    localize($("#meter-status"),"textContent",()=>t("ui.4db5fb74db45"));
    highlights = false;
    drawHighlightButton();
  });
  drawHighlightButton();
}

function initCorpusSettings() {
  let revision = null, pending = false;
  const status = $('#meter-corpus-status');
  const load = async () => {
    localize(status,"textContent",()=>t("ui.150791a6ab59"));
    try {
      const value = await api('/api/voice-corpus?profile=' + $('#meterprofile').value);
      revision = value.revision; $('#meter-corpus-path').value = value.directory;
      localize(status,"textContent",()=>value.status === 'ok' ? t("ui.8d4363748128",value.profile_label,value.files,value.words) : value.reason);
    } catch (error) { status.textContent = error.message; }
  };
  $('#meter-configure').addEventListener('click', () => { if ($('#meter-corpus-settings').open) load(); else $('#meter-corpus-settings').open = true; });
  $('#meter-corpus-settings').addEventListener('toggle', () => { if ($('#meter-corpus-settings').open) load(); });
  $('#meter-retry').addEventListener('click', measure);
  $('#meter-corpus-pick').addEventListener('click', async () => {
    if (pending) return; pending = true; $('#meter-corpus-pick').disabled = true;
    try {
      const value = await api('/api/voice-corpus/pick', {method: 'POST', body: {}});
      if (value.directory) $('#meter-corpus-path').value = value.directory;
    } catch (error) { status.textContent = error.message; }
    finally { pending = false; $('#meter-corpus-pick').disabled = false; }
  });
  $('#meter-corpus-save').addEventListener('click', async () => {
    if (pending) return; pending = true; $('#meter-corpus-save').disabled = true;
    try {
      if (!revision) throw Error(t("ui.63c1563e0109"));
      const value = await api('/api/voice-corpus', {method: 'POST', body: {directory: $('#meter-corpus-path').value, expected_revision: revision}});
      revision = value.revision; clearMeter(); await measure();
      localize(status,"textContent",()=>S.meter?.corpus_state?.reason || t("ui.101745d112b5"));
    } catch (error) { status.textContent = error.message; }
    finally { pending = false; $('#meter-corpus-save').disabled = false; }
  });
}
