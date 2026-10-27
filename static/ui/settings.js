import { $, S, api, emit, toast, providerName } from "./core.js";
import { showWorkspacePage, readAppearance, applyAppearance } from './workspace.js';
const fields = {
  "analysis-provider": "analysis_provider",
  "image-provider": "image_provider",
  "codex-model": "codex_model",
  "or-text-model": "openrouter_text_model",
  "or-image-model": "openrouter_image_model",
  "image-aspect": "image_aspect",
  "spice-view": "spice_view",
  "spice-motion": "spice_motion",
};
export async function loadSettings() {
  try {
    const data = await api("/api/settings");
    S.settings = data.settings;
    emit("settings", data);
    return data;
  } catch (_) {
    return null;
  }
}
export async function openSettings() {
  const info = await loadSettings();
  for (const [id, key] of Object.entries(fields))
    $("#" + id).value = S.settings[key] || "";
  $("#settingsnote").textContent =
    "Codex использует текущий вход в аккаунт" +
    (info?.codex_default_model ? " · " + info.codex_default_model : "") +
    ". OpenRouter — отдельные платные запросы. Ключ " +
    (info?.openrouter_available ? "найден." : "не найден.");
  const appearance = readAppearance();
  $("#reading-font").value = appearance.font;
  $("#reading-size").value = String(appearance.size);
  $("#reading-motion").checked = appearance.motion;
  showWorkspacePage("#settingsdialog", "settings");
}
export function initSettings() {
  $("#generation-settings").addEventListener("click", openSettings);
  $("#settingsclose").addEventListener("click", () =>
    $("#settingsdialog").close(),
  );
  for (const id of ["analysis-provider", "image-provider"])
    $("#" + id).addEventListener("change", () => {
      if ($("#" + id).value === "openrouter") $("#settingsdialog .settings-extra").open = true;
    });
  $("#settingssave").addEventListener("click", async () => {
    const values = {};
    for (const [id, key] of Object.entries(fields))
      values[key] = $("#" + id).value;
    try {
      S.settings = (
        await api("/api/settings", {
          method: "POST",
          body: { settings: values },
        })
      ).settings;
      applyAppearance({font: $("#reading-font").value, size: Number($("#reading-size").value), motion: $("#reading-motion").checked}, {persist: true});
      emit("settings");
      $("#settingsdialog").close();
      toast("Настройки сохранены");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("#load-models").addEventListener("click", async () => {
    const b = $("#load-models");
    b.disabled = true;
    b.textContent = "Загружаю…";
    try {
      const models = await api("/api/models");
      for (const kind of ["text", "image"]) {
        const list = $("#or-" + kind + "-models");
        list.replaceChildren();
        for (const m of models[kind] || []) {
          const o = document.createElement("option");
          o.value = m.id;
          o.label = m.name;
          list.appendChild(o);
        }
      }
      toast("Модели загружены");
    } catch (error) {
      toast(error.message, true);
    } finally {
      b.disabled = false;
      b.textContent = "Загрузить модели OpenRouter";
    }
  });
  loadSettings();
}
