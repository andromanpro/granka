import {
  $,
  S,
  el,
  button,
  api,
  events,
  emit,
  annotations,
  version,
  operation,
  toast,
} from "./core.js";
import { editor } from "./editor.js";
let recorder,
  stream,
  context,
  frame,
  start = 0,
  chunks = [],
  pins = [],
  review = null;
async function microphones() {
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        (d) => d.kind === "audioinput",
      ),
      select = $("#micsel"),
      saved = localStorage.getItem("granka.mic") || "";
    select.replaceChildren(new Option("Микрофон по умолчанию", ""));
    devices.forEach((d, i) =>
      select.appendChild(
        new Option(d.label || "Микрофон " + (i + 1), d.deviceId),
      ),
    );
    if (devices.some((d) => d.deviceId === saved)) select.value = saved;
  } catch (_) {}
}
function stopLevel() {
  cancelAnimationFrame(frame);
  try {
    context?.close();
  } catch (_) {}
  context = null;
  $("#reclevel").hidden = true;
}
function level() {
  try {
    context = new AudioContext();
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    const data = new Uint8Array(512),
      canvas = $("#reclevel"),
      c = canvas.getContext("2d");
    canvas.hidden = false;
    const draw = () => {
      analyser.getByteTimeDomainData(data);
      const rms = Math.sqrt(
        data.reduce((sum, x) => sum + ((x - 128) / 128) ** 2, 0) / data.length,
      );
      c.clearRect(0, 0, canvas.width, canvas.height);
      c.fillStyle = "#61d3c6";
      c.fillRect(0, 0, Math.min(1, rms * 4) * canvas.width, canvas.height);
      frame = requestAnimationFrame(draw);
    };
    draw();
  } catch (_) {}
}
function pin() {
  if (!S.recording) return;
  const off = editor.selection();
  if (!off) return;
  const at = (performance.now() - start) / 1000;
  if (pins.some((p) => !(off.end <= p.start || off.start >= p.end))) return;
  pins.push({
    ...off,
    at,
    quote: [...version().text].slice(off.start, off.end).join(""),
  });
  getSelection().removeAllRanges();
  $("#voice-status").textContent =
    "Запись · закреплено фрагментов: " + pins.length;
}
function showReview(result) {
  const box = $("#voice-review");
  box.replaceChildren();
  review = {
    session: {
      version: S.version,
      transcript: result.text || "",
      segments: result.segments || [],
      marks: pins,
    },
    pairs: [],
  };
  const buckets = pins.map((pin) => ({ pin, text: [] })),
    general = [];
  for (const segment of result.segments || []) {
    let near = -1,
      distance = Infinity;
    const t = (segment.start + segment.end) / 2;
    pins.forEach((p, i) => {
      const d = Math.abs(p.at - t);
      if (d < distance) {
        near = i;
        distance = d;
      }
    });
    if (near >= 0 && distance <= 10) buckets[near].text.push(segment.text);
    else general.push(segment.text);
  }
  for (const { pin, text } of buckets) {
    const row = el("div", "voice-pair");
    row.appendChild(el("p", null, "«" + pin.quote + "»"));
    const ta = el("textarea");
    ta.value = text.join(" ");
    row.appendChild(ta);
    const skip = annotations().some(
      (a) => !(pin.end <= a.start || pin.start >= a.end),
    );
    if (skip)
      row.appendChild(
        el("small", "muted", "Пересекается с пометкой — пропущено"),
      );
    review.pairs.push({ pin, ta, skip });
    box.appendChild(row);
  }
  if (general.length) {
    const ta = el("textarea");
    ta.value = general.join(" ");
    box.append(el("p", "muted", "Общий комментарий"), ta);
    review.general = ta;
  }
  $("#voice-actions").hidden = false;
  $("#voice-status").textContent = "Проверь распознанный текст";
}
async function record() {
  if (S.recording) {
    recorder.stop();
    return;
  }
  if (S.editor) {
    toast("Сначала сохрани текст");
    return;
  }
  try {
    const device = localStorage.getItem("granka.mic") || "";
    stream = await navigator.mediaDevices.getUserMedia({
      audio: device ? { deviceId: { exact: device } } : true,
    });
    recorder = new MediaRecorder(stream, {
      mimeType: "audio/webm;codecs=opus",
    });
    chunks = [];
    pins = [];
    review = null;
    start = performance.now();
    S.recording = true;
    emit("controls");
    microphones();
    recorder.ondataavailable = (e) => {
      if (e.data.size) chunks.push(e.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      stopLevel();
      S.recording = false;
      $("#recbtn").textContent = "Записать голос";
      emit("controls");
      const blob = new Blob(chunks, { type: "audio/webm" });
      $("#voice-status").textContent = "Распознаю локально…";
      $("#recbtn").disabled = true;
      try {
        const result = await api("/api/transcribe", {
          method: "POST",
          body: blob,
          headers: { "Content-Type": "audio/webm" },
        });
        if (pins.length) showReview(result);
        else {
          $("#fbtext").value +=
            ($("#fbtext").value ? " " : "") + (result.text || "");
          $("#voice-status").textContent = result.text
            ? "Распознано. Можно отправить комментарий."
            : "Речь не распознана.";
        }
      } catch (error) {
        $("#voice-status").textContent = error.message;
        toast(error.message, true);
      } finally {
        $("#recbtn").disabled = false;
      }
    };
    recorder.start();
    level();
    $("#recbtn").textContent = "Остановить";
    $("#voice-status").textContent = "Говори и выделяй фрагменты в статье.";
  } catch (error) {
    stream?.getTracks().forEach((t) => t.stop());
    S.recording = false;
    emit("controls");
    toast("Микрофон недоступен: " + error.message, true);
  }
}
export function initVoice() {
  S.recording = false;
  microphones();
  events.addEventListener("voice-pin", pin);
  $("#recbtn").addEventListener("click", record);
  $("#micsel").addEventListener("change", () =>
    localStorage.setItem("granka.mic", $("#micsel").value),
  );
  $("#fbsend").addEventListener("click", async () => {
    const text = $("#fbtext").value.trim();
    if (!text) return;
    if (await operation("/api/feedback", { text, context: S.active || "" })) {
      $("#fbtext").value = "";
      toast("Комментарий сохранён для анализа");
    }
  });
  $("#voice-apply").addEventListener("click", async () => {
    if (!review) return;
    const create = review.pairs
      .filter((p) => !p.skip && p.ta.value.trim())
      .map((p) => ({ ...p.pin, note: p.ta.value.trim() }));
    const result = await operation("/api/voice-session", {
      session: review.session,
      create,
    });
    if (result) {
      if (review.general?.value.trim())
        await operation("/api/feedback", {
          text: review.general.value.trim(),
          context: "Голос",
        });
      $("#voice-review").replaceChildren();
      $("#voice-actions").hidden = true;
      review = null;
      toast("Голосовые пометки сохранены");
    }
  });
  $("#voice-cancel").addEventListener("click", () => {
    review = null;
    $("#voice-review").replaceChildren();
    $("#voice-actions").hidden = true;
  });
}
