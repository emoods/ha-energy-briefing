/* Energy Briefing card – shipped with the energy_briefing integration. */
const CARD_VERSION = "0.2.1";

const STRINGS = {
  en: {
    refresh: "Refresh",
    send: "Refresh + send",
    empty: "No briefing yet – tap Refresh.",
    settings: "Settings",
    name: "Energy Briefing",
    description: "Shows the latest energy briefing with traffic lights, outlook and buttons.",
    labels: {
      entity: "Briefing sensor",
      show_lights: "Traffic lights",
      show_outlook: "Outlook",
      show_details: "Details",
      show_buttons: "Buttons",
      show_footer: "Footer (time and source)",
    },
  },
  de: {
    refresh: "Neu erstellen",
    send: "Erstellen + senden",
    empty: "Noch kein Bericht – auf „Neu erstellen“ tippen.",
    settings: "Einstellungen",
    name: "Energy Briefing",
    description: "Zeigt den aktuellen Energiebericht mit Ampeln, Ausblick und Knöpfen.",
    labels: {
      entity: "Bericht-Sensor",
      show_lights: "Ampeln",
      show_outlook: "Ausblick",
      show_details: "Details",
      show_buttons: "Knöpfe",
      show_footer: "Fußzeile (Zeit und Quelle)",
    },
  },
};

const SETTINGS_PATH = "/config/integrations/integration/energy_briefing";

const langOf = (hass) => {
  const l = (hass && ((hass.locale && hass.locale.language) || hass.language)) || navigator.language || "en";
  return l.startsWith("de") ? "de" : "en";
};
const strings = (hass) => STRINGS[langOf(hass || document.querySelector("home-assistant")?.hass)];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

function markdown(text) {
  const out = [];
  let list = false;
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    const item = line.match(/^[-*•]\s+(.*)$/);
    if (item) {
      if (!list) {
        out.push("<ul>");
        list = true;
      }
      out.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (list) {
      out.push("</ul>");
      list = false;
    }
    if (line) out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}

const STYLE = `
  ha-card { padding: 16px; }
  .head { display: flex; align-items: flex-start; gap: 8px; }
  .title { flex: 1; font-size: 1.25em; font-weight: 500; line-height: 1.3; }
  .icon-btn { background: none; border: none; color: var(--secondary-text-color); cursor: pointer;
              padding: 4px; border-radius: 50%; line-height: 0; }
  .icon-btn:hover { background: var(--secondary-background-color); }
  .summary { margin: 8px 0 12px; line-height: 1.45; }
  .lights { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .chip { padding: 4px 10px; border-radius: 16px; background: var(--secondary-background-color); white-space: nowrap; }
  .chip small { color: var(--secondary-text-color); }
  .outlook { display: grid; grid-template-columns: repeat(auto-fit, minmax(48px, 1fr)); gap: 4px;
             margin-bottom: 10px; text-align: center; }
  .day { padding: 6px 2px; border-radius: 10px; background: var(--secondary-background-color); }
  .day.great, .day.good { background: color-mix(in srgb, var(--success-color, #4caf50) 20%, transparent); }
  .day.poor { opacity: 0.7; }
  .d, .k { font-size: 0.78em; color: var(--secondary-text-color); }
  .i { font-size: 1.4em; line-height: 1.5; }
  .details { line-height: 1.4; }
  .details ul { margin: 4px 0; padding-left: 20px; }
  .details li { margin: 3px 0; }
  .details p { margin: 4px 0; }
  .buttons { display: flex; gap: 8px; margin-top: 12px; }
  .btn { flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px; padding: 8px;
         border-radius: 10px; border: 1px solid var(--divider-color); background: none;
         color: var(--primary-text-color); cursor: pointer; font: inherit; }
  .btn:hover { background: var(--secondary-background-color); }
  .btn[disabled] { opacity: 0.5; cursor: progress; }
  .footer { margin-top: 8px; font-size: 0.75em; color: var(--secondary-text-color); }
  .empty { color: var(--secondary-text-color); margin: 8px 0; }
`;

class EnergyBriefingCard extends HTMLElement {
  static getStubConfig(hass) {
    const ids = Object.keys(hass.states).filter((e) => e.startsWith("sensor."));
    const id =
      ids.find((e) => hass.entities?.[e]?.platform === "energy_briefing") ||
      ids.find((e) => "lights" in hass.states[e].attributes && "outlook" in hass.states[e].attributes);
    return { entity: id || "sensor.energy_briefing_briefing" };
  }

  static getConfigForm() {
    const bool = (name) => ({ name, selector: { boolean: {} } });
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { filter: { domain: "sensor", integration: "energy_briefing" } } } },
        {
          type: "grid",
          name: "",
          flatten: true,
          schema: ["show_lights", "show_outlook", "show_details", "show_buttons", "show_footer"].map(bool),
        },
      ],
      computeLabel: (schema) => strings().labels[schema.name] || schema.name,
    };
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Please choose the briefing sensor (entity)");
    this._config = {
      show_lights: true,
      show_outlook: true,
      show_details: true,
      show_buttons: true,
      show_footer: true,
      ...config,
    };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._key = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config?.entity];
    const key = `${st ? st.last_updated : "none"}|${langOf(hass)}|${this._busy || ""}`;
    if (key !== this._key) {
      this._key = key;
      this._render();
    }
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _buttons() {
    const hass = this._hass;
    const reg = hass.entities || {};
    const device = reg[this._config.entity]?.device_id;
    const find = (key, suffix) =>
      Object.values(reg).find((e) => e.platform === "energy_briefing" && e.device_id === device && e.translation_key === key)
        ?.entity_id ||
      Object.keys(hass.states).find((e) => e.startsWith("button.energy_briefing") && e.endsWith(suffix));
    return { refresh: find("generate", "_create_briefing"), send: find("generate_and_notify", "_send_briefing") };
  }

  async _press(which) {
    const entity = this._buttons()[which];
    if (!entity || this._busy) return;
    this._busy = which;
    this._key = undefined;
    this._render();
    try {
      await this._hass.callService("button", "press", { entity_id: entity });
    } finally {
      this._busy = undefined;
      this._key = undefined;
      this._render();
    }
  }

  _openSettings() {
    history.pushState(null, "", SETTINGS_PATH);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }

  _render() {
    if (!this.shadowRoot || !this._config || !this._hass) return;
    const hass = this._hass;
    const c = this._config;
    const t = strings(hass);
    const st = hass.states[c.entity];
    const a = (st && st.attributes) || {};
    const valid = st && st.state && !["unknown", "unavailable"].includes(st.state);

    const gear = hass.user?.is_admin
      ? `<button class="icon-btn" id="settings" title="${esc(t.settings)}"><ha-icon icon="mdi:cog-outline"></ha-icon></button>`
      : "";
    let body = `<div class="head"><div class="title">${valid ? esc(st.state) : esc(t.name)}</div>${gear}</div>`;

    if (!valid) {
      body += `<div class="empty">${esc(t.empty)}</div>`;
    } else {
      if (a.summary) body += `<div class="summary">${inline(a.summary)}</div>`;
      const lights = a.lights || [];
      if (c.show_lights && lights.length) {
        body += `<div class="lights">${lights
          .map(
            (l) =>
              `<span class="chip" title="${esc(l.label)}">${esc(l.icon)} ${esc(l.level_icon)}${
                l.hint && l.level !== "red" ? ` <small>${esc(l.hint)}</small>` : ""
              }</span>`
          )
          .join("")}</div>`;
      }
      const outlook = a.outlook || [];
      if (c.show_outlook && outlook.length) {
        body += `<div class="outlook">${outlook
          .map((o) => {
            const sub =
              o.pv_kwh != null ? `${Math.round(o.pv_kwh)} kWh` : o.temp_max != null ? `${Math.round(o.temp_max)}°` : "";
            return `<div class="day ${esc(o.rating)}" title="${esc(o.condition)}"><div class="d">${esc(o.day)}</div><div class="i">${esc(
              o.icon
            )}</div><div class="k">${esc(sub)}</div></div>`;
          })
          .join("")}</div>`;
      }
      if (c.show_details && a.details) body += `<div class="details">${markdown(a.details)}</div>`;
    }

    if (c.show_buttons) {
      const b = this._buttons();
      const btn = (key, icon, label) =>
        b[key]
          ? `<button class="btn" id="${key}" ${this._busy ? "disabled" : ""}><ha-icon icon="${
              this._busy === key ? "mdi:timer-sand" : icon
            }"></ha-icon>${esc(label)}</button>`
          : "";
      const html = btn("refresh", "mdi:refresh", t.refresh) + btn("send", "mdi:cellphone-arrow-down", t.send);
      if (html) body += `<div class="buttons">${html}</div>`;
    }

    if (valid && c.show_footer && a.generated_at) {
      const when = new Date(a.generated_at).toLocaleString(hass.locale?.language || undefined, {
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
      body += `<div class="footer">${esc(when)}${a.source ? ` · ${esc(a.source)}` : ""}</div>`;
    }

    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${body}</ha-card>`;
    this.shadowRoot.getElementById("refresh")?.addEventListener("click", () => this._press("refresh"));
    this.shadowRoot.getElementById("send")?.addEventListener("click", () => this._press("send"));
    this.shadowRoot.getElementById("settings")?.addEventListener("click", () => this._openSettings());
  }
}

if (!customElements.get("energy-briefing-card")) {
  customElements.define("energy-briefing-card", EnergyBriefingCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "energy-briefing-card",
    name: STRINGS[langOf()].name,
    description: STRINGS[langOf()].description,
    preview: true,
    documentationURL: "https://github.com/emoods/ha-energy-briefing",
  });
  console.info(`%c ENERGY-BRIEFING-CARD %c ${CARD_VERSION} `, "background:#f9a825;color:#000", "background:#333;color:#fff");
}
