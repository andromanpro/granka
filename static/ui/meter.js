import { $, S, api, emit, events, query, version, toast } from "./core.js";
let sequence = 0,
  timer,
  animation,
  highlights = false;
const color = (n) =>
  n < 15 ? "#68d8b0" : n < 30 ? "#d8cf85" : n < 45 ? "#efb279" : "#ee8994";
export const meterHighlights = () =>
  highlights ? S.meter?.highlights || [] : [];
export function clearMeter() {
  ++sequence;
  clearTimeout(timer);
  cancelAnimationFrame(animation);
  S.meter = null;
  highlights = false;
  $("#meter-number").textContent = "—";
  $("#meter-orbit").style.setProperty("--value", 0);
  $("#meter-status").textContent = "Измеряю текущую версию";
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
    if (seq !== sequence || key !== query() || vid !== S.version) return;
    S.meter = result;
    drawMeter(result);
    if (highlights) emit("reader");
  } catch (error) {
    $("#meter-status").textContent = "Не удалось измерить";
    toast(error.message, true);
  } finally {
    if (seq === sequence) $("#meterwidget").classList.remove("measuring");
  }
}
function drawMeter(meter) {
  const n = meter.score,
    tone = n == null ? "#809aab" : color(n),
    orbit = $("#meter-orbit");
  orbit.style.setProperty("--tone", tone);
  cancelAnimationFrame(animation);
  if (n == null) $("#meter-number").textContent = "—";
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
        ? "Добавь свой корпус текстов"
        : "Недостаточно текста"
      : meter.profile_label + " · " + meter.words + " слов";
  $("#mobile-meter").textContent = "ИИМетр " + (n ?? "—");
  for (const [key, id] of [
    ["голос", "voice"],
    ["структура", "structure"],
    ["маркеры", "markers"],
  ]) {
    const value = n == null ? 0 : meter.axes[key].score;
    $("#axis-" + id + "-number").textContent = n == null ? "—" : value;
    $("#axis-" + id).style.width = value + "%";
  }
  const notes = $("#meterflags");
  notes.replaceChildren();
  const texts =
    n == null
      ? [
          meter.status === "uncalibrated"
            ? "Укажи voice.corpus_dir в конфигурации и добавь свои рукописные тексты. Итоговая оценка появится после калибровки."
            : "Для измерения нужна проза.",
        ]
      : [
          ...(meter.sample_warning ? [meter.sample_warning] : []),
          ...meter.axes["структура"].flags,
        ];
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
  const profile = localStorage.getItem("granka.voice-profile");
  if (profile === "talks" || profile === "blog")
    $("#meterprofile").value = profile;
  $("#meterrefresh").addEventListener("click", measure);
  $("#meterprofile").addEventListener("change", () => {
    localStorage.setItem("granka.voice-profile", $("#meterprofile").value);
    measure();
  });
  $("#meterhl").addEventListener("click", () => {
    if (S.editor) {
      toast("Сначала сохрани текст");
      return;
    }
    highlights = !highlights;
    $("#meterhl").textContent = highlights
      ? "Снять подсветку"
      : "Подсветить обороты";
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
    $("#meter-status").textContent =
      "Показатели сохранённой версии · есть правки";
    highlights = false;
  });
}
