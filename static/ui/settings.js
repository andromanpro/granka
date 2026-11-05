import { t, localizedOption, locale, localize } from './i18n.js';
import { $, S, api, emit, toast, providerName } from "./core.js";
import { showWorkspacePage, readAppearance, applyAppearance } from './workspace.js';
import { renderSiteSettings, siteSettingsValue, initSiteSettings } from './site-settings.js';
import {language,setLanguage} from './i18n.js';
const fields = {
  "analysis-provider": "analysis_provider",
  "image-provider": "image_provider",
  "codex-model": "codex_model",
  "or-text-model": "openrouter_text_model",
  "or-image-model": "openrouter_image_model",
  "image-aspect": "image_aspect",
  "spice-view": "spice_view",
  "spice-motion": "spice_motion",
  "material-language": "material_language",
  "comments-language": "comments_language",
};
let connections = null;
function renderConnections(value) {
  connections = value || null;
  $("#connections-error").hidden = true;
  renderSiteSettings(value);
}
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
  renderConnections(info?.connections);
  for (const [id, key] of Object.entries(fields))
    $("#" + id).value = S.settings[key] || "";
  localize($("#settingsnote"),"textContent",()=>t("ui.53a1755f9f7f") +
    (info?.codex_default_model ? " · " + info.codex_default_model : "") +
    t("ui.eb3c4c7c1dca") +
    (info?.openrouter_available ? t("ui.2c6f0dc8aa5b") : t("ui.338ec7d8207d")));
  const appearance = readAppearance();
  $('#ui-language').value=language();
  $("#reading-font").value = appearance.font;
  $("#reading-size").value = String(appearance.size);
  $("#reading-motion").checked = appearance.motion;
  showWorkspacePage("#settingsdialog", "settings");
}
export function initSettings() {
  $('#ui-language').addEventListener('change',()=>setLanguage($('#ui-language').value));
  initSiteSettings();
  $("#generation-settings").addEventListener("click", openSettings);
  $("#settingsclose").addEventListener("click", () =>
    $("#settingsdialog").close(),
  );
  for (const id of ["analysis-provider", "image-provider"])
    $("#" + id).addEventListener("change", () => {
      if ($("#" + id).value === "openrouter") $("#settingsdialog .settings-extra").open = true;
    });
  $("#settingssave").addEventListener("click", async () => {
    const button = $("#settingssave"); button.disabled = true;
    const values = {};
    for (const [id, key] of Object.entries(fields))
      values[key] = $("#" + id).value;
    try {
      if (!connections) throw new Error(t("ui.fdf301985ccd"));
      const saved = await api("/api/settings", {
          method: "POST",
          body: { settings: values, site: siteSettingsValue(), config_revision: connections.revision },
        });
      S.settings = saved.settings; connections = saved.connections;
      applyAppearance({font: $("#reading-font").value, size: Number($("#reading-size").value), motion: $("#reading-motion").checked}, {persist: true});
      emit("settings");
      $("#settingsdialog").close();
      toast(t("ui.babdb58139be"));
    } catch (error) {
      $("#connections-error").textContent = error.message; $("#connections-error").hidden = false;
      toast(error.message, true);
    } finally { button.disabled = false; }
  });
  $("#load-models").addEventListener("click", async () => {
    const b = $("#load-models");
    b.disabled = true;
    localize(b,"textContent",()=>t("ui.1679ed866916"));
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
      toast(t("ui.32322ab46b06"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      b.disabled = false;
      localize(b,"textContent",()=>t("ui.278c54608e25"));
    }
  });
  loadSettings();
}
