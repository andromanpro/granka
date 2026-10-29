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
  button.textContent = highlights
    ? "Снять подсветку" + (count == null ? "" : " · " + count)
    : "Подсветить обороты" + (count == null ? "" : " · " + count);
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
  $("#meter-status").textContent = S.document ? "Готовлю замер текущей версии" : "Открой статью для локального разбора";
  for (const name of ['voice', 'structure', 'markers']) {
    $('#axis-' + name + '-number').textContent = '—';
    $('#axis-' + name).style.width = '0%';
  }
  $('#meterflags').replaceChildren();
  $('#meterscale').textContent = '';
  $("#mobile-meter").textContent = "ИИМетр";
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
  $("#meter-status").textContent = "Локальный замер…";
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
    $("#meter-status").textContent = "Не удалось измерить";
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
  $("#meter-status").textContent =
    n == null
      ? meter.status === "uncalibrated"
        ? (meter.corpus_state?.reason || "Сравнение голоса не настроено")
        : "Недостаточно текста"
      : meter.profile_label + " · " + meter.words + " слов" + (meter.sample_warning ? ' · короткий текст' : '');
  $("#mobile-meter").textContent = "ИИМетр " + (n ?? "—");
  for (const [key, id] of [
    ["голос", "voice"],
    ["структура", "structure"],
    ["маркеры", "markers"],
  ]) {
    const available = meter.status !== "insufficient_text" && (key !== "голос" || n != null),
      value = available ? meter.axes[key].score : null;
    $("#axis-" + id + "-number").textContent = value ?? "—";
    $("#axis-" + id).style.width = (value ?? 0) + "%";
  }
  const notes = $("#meterflags");
  notes.replaceChildren();
  const texts = [
    ...(meter.status === "uncalibrated"
      ? [meter.corpus_state?.reason || "Сравнение голоса не настроено. Выбери папку своих текстов кнопкой ниже.", "Словарные обороты и структура доступны без корпуса."]
      : meter.status === "insufficient_text" ? ["Для измерения нужна проза."] : []),
    ...(meter.sample_warning ? [meter.sample_warning] : []),
    ...(meter.status !== "insufficient_text" ? meter.axes["структура"].flags : []),
    meter.axes["маркеры"].hits
      ? "Найдено словарных оборотов: " + meter.axes["маркеры"].hits + ". Подсветка показывает совпадения, которые стоит перечитать."
      : "Словарных оборотов не найдено. Это не исключает шаблонной подачи: словарь не оценивает смысл и развитие текста.",
  ];
  if (meter.axes["маркеры"].hits > meter.highlights.length)
    texts.push("Показаны первые " + meter.highlights.length + " совпадений из " + meter.axes["маркеры"].hits + ".");
  for (const text of texts) {
    const p = document.createElement("p");
    p.textContent = text;
    notes.appendChild(p);
  }
  const samples = (meter.calibration?.samples || [])
    .map((x) => x.score)
    .filter((x) => typeof x === "number");
  $("#meterscale").textContent =
    (samples.length
      ? "Диапазон проверенных рукописей: " +
        Math.min(...samples) +
        "–" +
        Math.max(...samples) +
        ". "
      : "") +
    "Корпус: " +
    meter.axes["голос"].corpus.length +
    " текстов, " +
    meter.axes["голос"].corpus_words +
    " слов. Это отклонение от выбранного корпуса, не вероятность участия ИИ.";
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
      toast("Сначала сохрани текст");
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
    $("#meter-status").textContent =
      "Показатели сохранённой версии · есть правки";
    highlights = false;
    drawHighlightButton();
  });
  drawHighlightButton();
}

function initCorpusSettings() {
  let revision = null, pending = false;
  const status = $('#meter-corpus-status');
  const load = async () => {
    status.textContent = 'Читаю настройки корпуса…';
    try {
      const value = await api('/api/voice-corpus?profile=' + $('#meterprofile').value);
      revision = value.revision; $('#meter-corpus-path').value = value.directory;
      status.textContent = value.status === 'ok' ? `${value.profile_label}: ${value.files} текстов, ${value.words} слов.` : value.reason;
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
      if (!revision) throw Error('Сначала открой настройку сравнения голоса');
      const value = await api('/api/voice-corpus', {method: 'POST', body: {directory: $('#meter-corpus-path').value, expected_revision: revision}});
      revision = value.revision; clearMeter(); await measure();
      status.textContent = S.meter?.corpus_state?.reason || 'Корпус подключён. Сравнение пересчитано локально.';
    } catch (error) { status.textContent = error.message; }
    finally { pending = false; $('#meter-corpus-save').disabled = false; }
  });
}
