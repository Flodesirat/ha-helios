/**
 * helios-card — Lovelace card for the Helios Energy Optimizer
 *
 * Config example:
 *   type: custom:helios-card
 *   entities:
 *     pv_power:       sensor.helios_pv_power
 *     grid_power:     sensor.helios_grid_power    # positive=import, negative=export
 *     house_power:    sensor.helios_house_power
 *     battery_soc:    sensor.my_battery_soc       # optional
 *     score:          sensor.helios_global_score
 *     battery_action: sensor.helios_battery_action
 *     auto_mode:      switch.helios_auto_mode
 *   devices:                                      # optional
 *     - name: Piscine
 *       type: pool
 *       entity: switch.helios_piscine_manuel
 *       filtration_done:     sensor.helios_pool_filtration_done
 *       filtration_required: sensor.helios_pool_filtration_required
 *       force_remaining:     sensor.helios_pool_force_remaining  # optional
 *     - name: Chauffe-eau
 *       type: water_heater
 *       entity: switch.helios_chauffe_eau_manuel
 *       temp_entity:   sensor.temperature_chauffe_eau
 *       temp_target:   61   # optional — fallback if no entity
 *     - name: Lave-vaisselle
 *       type: appliance
 *       entity:       switch.helios_lave_vaisselle_manuel
 *       state_entity: sensor.helios_lave_vaisselle_etat
 *     - name: Voiture
 *       type: ev
 *       entity:         switch.helios_voiture_manuel
 *       soc_entity:     sensor.ev_soc
 *       plugged_entity: binary_sensor.ev_branche   # optional
 */

class HeliosCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._initialized = false;
    this._hass = null;
    this._config = null;
  }

  // ------------------------------------------------------------------ Config
  setConfig(config) {
    this._config = config || {};
    if (!this._initialized) {
      this._build();
    }
    this._update();
  }

  // ------------------------------------------------------------------ hass
  set hass(hass) {
    this._hass = hass;
    if (this._initialized) {
      this._update();
    }
  }

  // ------------------------------------------------------------------ Helpers
  _num(entityId, fallback = 0) {
    if (!entityId || !this._hass) return fallback;
    const s = this._hass.states[entityId];
    if (!s || s.state === "unavailable" || s.state === "unknown") return fallback;
    const n = parseFloat(s.state);
    return isNaN(n) ? fallback : n;
  }

  _str(entityId, fallback = null) {
    if (!entityId || !this._hass) return fallback;
    const s = this._hass.states[entityId];
    if (!s || s.state === "unavailable" || s.state === "unknown") return fallback;
    return s.state;
  }

  _attr(entityId, attribute, fallback = null) {
    if (!entityId || !this._hass) return fallback;
    return this._hass.states[entityId]?.attributes?.[attribute] ?? fallback;
  }

  _fmt(w) {
    const abs = Math.abs(w);
    if (abs >= 1000) return `${(w / 1000).toFixed(1)} kW`;
    return `${Math.round(w)} W`;
  }

  // ------------------------------------------------------------------ Build DOM (once)
  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }

        .card {
          background: var(--card-background-color, #fff);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,0.1));
          padding: 16px;
          font-family: var(--primary-font-family, Roboto, sans-serif);
          color: var(--primary-text-color);
        }

        /* ---- Header ---- */
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
        }
        .title { font-size: 15px; font-weight: 600; letter-spacing: 0.3px; }

        .mode-btn {
          padding: 3px 11px;
          border-radius: 12px;
          font-size: 11px;
          font-weight: 700;
          color: #fff;
          cursor: pointer;
          border: none;
          background: #9E9E9E;
          transition: background 0.3s;
        }

        /* ---- Power flow ---- */
        .flow-wrap { width: 100%; margin: 4px 0 8px; }
        svg { width: 100%; height: auto; display: block; }

        .fl {
          fill: none;
          stroke: #e0e0e0;
          stroke-width: 2.5;
          stroke-linecap: round;
        }
        .fl-on {
          stroke-dasharray: 8 5;
          animation: flowDash linear infinite;
        }
        @keyframes flowDash { to { stroke-dashoffset: -26; } }

        /* ---- Score / chips ---- */
        .footer {
          padding-top: 10px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        .score-row {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 7px;
        }
        .lbl {
          font-size: 12px;
          color: var(--secondary-text-color);
          min-width: 42px;
        }
        .bar-bg {
          flex: 1;
          height: 7px;
          background: var(--secondary-background-color, #f0f0f0);
          border-radius: 4px;
          overflow: hidden;
        }
        .bar-fill {
          height: 100%;
          border-radius: 4px;
          transition: width 0.6s ease, background 0.6s ease;
          width: 0%;
          background: #9E9E9E;
        }
        .score-num {
          font-size: 12px;
          font-weight: 600;
          min-width: 34px;
          text-align: right;
        }
        .chips { display: flex; gap: 7px; flex-wrap: wrap; }
        .chip {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 11px;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color, #f5f5f5);
          padding: 3px 9px;
          border-radius: 12px;
        }
        .dot {
          width: 8px; height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
        }

        /* ---- Devices section ---- */
        .devices {
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .dev-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 8px;
          border-radius: 8px;
          background: var(--secondary-background-color, #f5f5f5);
        }
        .dev-icon {
          font-size: 17px;
          width: 24px;
          text-align: center;
          flex-shrink: 0;
        }
        .dev-info {
          flex: 1;
          min-width: 0;
        }
        .dev-name {
          font-size: 12px;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .dev-detail {
          font-size: 11px;
          color: var(--secondary-text-color);
          margin-top: 1px;
        }
        .dev-status {
          display: flex;
          align-items: center;
          gap: 5px;
          flex-shrink: 0;
        }
        .dev-status-text {
          font-size: 11px;
          font-weight: 600;
          min-width: 54px;
        }
        .dev-score-col {
          text-align: right;
          flex-shrink: 0;
          min-width: 38px;
        }
        .dev-score-val {
          font-size: 12px;
          font-weight: 700;
        }
        .dev-reason {
          font-size: 9px;
          color: var(--secondary-text-color);
          margin-top: 1px;
        }
      </style>

      <div class="card">
        <div class="header">
          <div class="title">⚡ Helios</div>
          <button class="mode-btn" id="h-mode-btn">—</button>
        </div>

        <div class="flow-wrap">
          <svg viewBox="0 0 300 185" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="h-arr-pv"      markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#F9A825"/></marker>
              <marker id="h-arr-gin"     markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#7B1FA2"/></marker>
              <marker id="h-arr-gout"    markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#388E3C"/></marker>
              <marker id="h-arr-bat-chg" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#1565C0"/></marker>
              <marker id="h-arr-bat-dch" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#0288D1"/></marker>
            </defs>

            <!-- Flow lines -->
            <line id="h-line-pv"   class="fl" x1="150" y1="64"  x2="150" y2="97"/>
            <line id="h-line-grid" class="fl" x1="73"  y1="125" x2="122" y2="125"/>
            <line id="h-line-bat"  class="fl" x1="178" y1="125" x2="227" y2="125"/>

            <!-- Power labels on lines -->
            <text id="h-lbl-pv"   x="157" y="83"  font-size="9" fill="#E65100" text-anchor="start"></text>
            <text id="h-lbl-grid" x="97"  y="119" font-size="9" text-anchor="middle"></text>
            <text id="h-lbl-bat"  x="203" y="119" font-size="9" text-anchor="middle"></text>

            <!-- PV — top center -->
            <circle cx="150" cy="38" r="27" fill="#FFF8E1" stroke="#F9A825" stroke-width="2"/>
            <text x="150" y="32" text-anchor="middle" font-size="18">☀️</text>
            <text id="h-val-pv" x="150" y="48" text-anchor="middle" font-size="9" font-weight="600" fill="#E65100"></text>

            <!-- House — center -->
            <circle cx="150" cy="125" r="27" fill="#E8F5E9" stroke="#388E3C" stroke-width="2"/>
            <text x="150" y="120" text-anchor="middle" font-size="18">🏠</text>
            <text id="h-val-house" x="150" y="133" text-anchor="middle" font-size="9" font-weight="600" fill="#1B5E20"></text>

            <!-- Grid — left -->
            <circle id="h-node-grid" cx="45" cy="125" r="27" fill="#F3E5F5" stroke="#7B1FA2" stroke-width="2"/>
            <text x="45" y="120" text-anchor="middle" font-size="18">⚡</text>
            <text id="h-val-grid" x="45" y="133" text-anchor="middle" font-size="9" font-weight="600" fill="#6A1B9A"></text>

            <!-- Battery — right -->
            <circle id="h-node-bat" cx="255" cy="125" r="27" fill="#E3F2FD" stroke="#1565C0" stroke-width="2"/>
            <text x="255" y="118" text-anchor="middle" font-size="16">🔋</text>
            <text id="h-val-bat-soc"    x="255" y="130" text-anchor="middle" font-size="9" font-weight="600" fill="#0D47A1"></text>
            <text id="h-val-bat-action" x="255" y="141" text-anchor="middle" font-size="8"  fill="#1565C0"></text>
          </svg>
        </div>

        <div class="footer">
          <div class="score-row">
            <div class="lbl">Score</div>
            <div class="bar-bg"><div class="bar-fill" id="h-score-bar"></div></div>
            <div class="score-num" id="h-score-num">—</div>
          </div>
          <div class="chips" id="h-chips"></div>
        </div>

        <div class="devices" id="h-devices" style="display:none"></div>
      </div>
    `;

    this.shadowRoot.getElementById("h-mode-btn").addEventListener("click", () => {
      const entityId = this._config?.entities?.auto_mode;
      if (!entityId || !this._hass) return;
      const state = this._hass.states[entityId];
      if (!state) return;
      this._hass.callService(
        "switch",
        state.state === "on" ? "turn_off" : "turn_on",
        { entity_id: entityId }
      );
    });

    this._initialized = true;
  }

  // ------------------------------------------------------------------ Update
  _update() {
    if (!this._initialized) return;
    try {
      this._doUpdate();
    } catch (e) {
      console.error("helios-card: error during update", e);
    }
  }

  _doUpdate() {
    const e = this._config?.entities || {};

    const pv         = this._num(e.pv_power);
    const grid       = this._num(e.grid_power);
    const house      = this._num(e.house_power);
    const soc        = e.battery_soc ? this._num(e.battery_soc, null) : null;
    const score      = this._num(e.score);
    const battAction = this._str(e.battery_action) ?? "idle";
    const tempo      = this._attr(e.score, "tempo_color");
    const modeAttr   = this._attr(e.score, "mode");
    const switchSt   = this._str(e.auto_mode);
    const mode       = modeAttr ?? (switchSt === "on" ? "auto" : switchSt === "off" ? "off" : null);

    // Node values
    this._txt("h-val-pv",    this._fmt(pv));
    this._txt("h-val-house", this._fmt(house));

    const gridSign = grid > 0 ? "+" : "";
    this._txt("h-val-grid", `${gridSign}${this._fmt(grid)}`);
    this._svgAttr("h-node-grid", "fill",   grid < 0 ? "#E8F5E9" : "#F3E5F5");
    this._svgAttr("h-node-grid", "stroke", grid < 0 ? "#388E3C" : "#7B1FA2");
    this._svgAttr("h-val-grid",  "fill",   grid < 0 ? "#2E7D32" : "#6A1B9A");

    this._txt("h-val-bat-soc", soc !== null ? `${Math.round(soc)}%` : "—");
    const batLabels = { charge: "↑ charge", discharge: "↓ décharge", reserve: "🔒 réserve", idle: "—" };
    this._txt("h-val-bat-action", batLabels[battAction] ?? battAction);
    this._svgAttr("h-node-bat", "stroke", battAction === "discharge" ? "#0288D1" : "#1565C0");

    // PV → House
    this._flow("h-line-pv", "h-lbl-pv", {
      active: pv > 10, power: pv, color: "#F9A825", marker: "h-arr-pv",
      x1: 150, y1: 64, x2: 150, y2: 97,
      lblX: 157, lblY: 83, lblAnchor: "start", lblColor: "#E65100",
    });

    // Grid flow
    const gridAbs = Math.abs(grid);
    if (grid > 10) {
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#7B1FA2", marker: "h-arr-gin",
        x1: 73, y1: 125, x2: 122, y2: 125,
        lblX: 97, lblY: 119, lblAnchor: "middle", lblColor: "#7B1FA2",
      });
    } else if (grid < -10) {
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#388E3C", marker: "h-arr-gout",
        x1: 122, y1: 125, x2: 73, y2: 125,
        lblX: 97, lblY: 119, lblAnchor: "middle", lblColor: "#388E3C",
      });
    } else {
      this._flowOff("h-line-grid", "h-lbl-grid", 73, 125, 122, 125);
    }

    // Battery flow
    const batPow = Math.abs(pv - house - grid);
    if (battAction === "charge" && batPow > 10) {
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPow, color: "#1565C0", marker: "h-arr-bat-chg",
        x1: 178, y1: 125, x2: 227, y2: 125,
        lblX: 203, lblY: 119, lblAnchor: "middle", lblColor: "#1565C0",
      });
    } else if (battAction === "discharge" && batPow > 10) {
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPow, color: "#0288D1", marker: "h-arr-bat-dch",
        x1: 227, y1: 125, x2: 178, y2: 125,
        lblX: 203, lblY: 119, lblAnchor: "middle", lblColor: "#0288D1",
      });
    } else {
      this._flowOff("h-line-bat", "h-lbl-bat", 178, 125, 227, 125);
    }

    // Score bar
    const scoreColor = score > 0.6 ? "#4CAF50" : score > 0.3 ? "#FF9800" : "#F44336";
    const bar = this.shadowRoot.getElementById("h-score-bar");
    if (bar) {
      bar.style.width      = `${Math.round(score * 100)}%`;
      bar.style.background = scoreColor;
    }
    this._txt("h-score-num", this._hass ? score.toFixed(2) : "—");

    // Mode button
    const btn = this.shadowRoot.getElementById("h-mode-btn");
    if (btn) {
      const modeColors = { auto: "#4CAF50", off: "#F44336", manual: "#FF9800" };
      btn.style.background = modeColors[mode] ?? "#9E9E9E";
      btn.textContent = mode ? mode.toUpperCase() : "—";
    }

    // Chips
    const chips = [];
    if (tempo) {
      const tempoMap = { blue: ["#2196F3", "Bleu"], white: ["#9E9E9E", "Blanc"], red: ["#F44336", "Rouge"] };
      const [dotColor, tempoLabel] = tempoMap[tempo] ?? ["#9E9E9E", tempo];
      chips.push(`<div class="chip"><div class="dot" style="background:${dotColor}"></div>Tempo ${tempoLabel}</div>`);
    }
    if (battAction && battAction !== "idle") {
      const batChipLabel = { charge: "🔋 Charge", discharge: "🔋 Décharge", reserve: "🔋 Réserve" };
      chips.push(`<div class="chip">${batChipLabel[battAction] ?? battAction}</div>`);
    }
    if (soc !== null) {
      const socColor = soc > 60 ? "#4CAF50" : soc > 20 ? "#FF9800" : "#F44336";
      chips.push(`<div class="chip">SOC <b style="color:${socColor}">${Math.round(soc)}%</b></div>`);
    }
    const chipsEl = this.shadowRoot.getElementById("h-chips");
    if (chipsEl) chipsEl.innerHTML = chips.join("");

    // Devices section
    this._updateDevices();
  }

  // ------------------------------------------------------------------ Devices
  _updateDevices() {
    const devicesEl = this.shadowRoot.getElementById("h-devices");
    if (!devicesEl) return;

    const devCfgs = this._config?.devices;
    if (!devCfgs || devCfgs.length === 0) {
      devicesEl.style.display = "none";
      return;
    }

    devicesEl.style.display = "flex";
    devicesEl.innerHTML = devCfgs.map(d => this._renderDevice(d)).join("");
  }

  _renderDevice(dev) {
    const icon   = dev.icon || this._defaultIcon(dev.type);
    const isOn   = this._deviceIsOn(dev);
    const score  = this._attr(dev.entity, "last_effective_score") ?? null;
    const reason = this._attr(dev.entity, "last_decision_reason") ?? "";
    const detail = this._deviceDetail(dev);

    // Status dot + label
    const { dotColor, statusText } = this._deviceStatus(dev, isOn);

    // Score color
    const scoreColor = score === null ? "#9E9E9E"
                     : score > 0.6   ? "#4CAF50"
                     : score > 0.3   ? "#FF9800"
                     : "#F44336";
    const scoreHtml = score !== null
      ? `<div class="dev-score-val" style="color:${scoreColor}">${score.toFixed(2)}</div>
         ${reason ? `<div class="dev-reason">${this._reasonLabel(reason)}</div>` : ""}`
      : "";

    return `
      <div class="dev-row">
        <div class="dev-icon">${icon}</div>
        <div class="dev-info">
          <div class="dev-name">${dev.name || ""}</div>
          ${detail ? `<div class="dev-detail">${detail}</div>` : ""}
        </div>
        <div class="dev-status">
          <div class="dot" style="background:${dotColor}"></div>
          <span class="dev-status-text">${statusText}</span>
        </div>
        <div class="dev-score-col">${scoreHtml}</div>
      </div>
    `;
  }

  _deviceIsOn(dev) {
    if (dev.type === "appliance") {
      return this._str(dev.state_entity) === "en_route";
    }
    // For all other types, read the helios_device_on attribute from the manual switch
    const attr = this._attr(dev.entity, "helios_device_on");
    if (attr !== null) return attr === true || attr === "true";
    return false;
  }

  _deviceStatus(dev, isOn) {
    if (dev.type === "appliance") {
      const state = this._str(dev.state_entity);
      const map = {
        en_route:   { dotColor: "#4CAF50", statusText: "En route" },
        en_attente: { dotColor: "#FF9800", statusText: "En attente" },
        stop:       { dotColor: "#9E9E9E", statusText: "Arrêt" },
      };
      return map[state] ?? { dotColor: "#9E9E9E", statusText: state ?? "—" };
    }
    if (dev.type === "ev") {
      const pluggedEntity = dev.plugged_entity ? this._str(dev.plugged_entity) : null;
      if (pluggedEntity === "off") return { dotColor: "#9E9E9E", statusText: "Non branché" };
    }
    return isOn
      ? { dotColor: "#4CAF50", statusText: "ON" }
      : { dotColor: "#9E9E9E", statusText: "OFF" };
  }

  _deviceDetail(dev) {
    switch (dev.type) {
      case "pool": {
        const done = this._num(dev.filtration_done, null);
        const req  = this._num(dev.filtration_required, null);
        if (done === null || req === null) return "";
        let s = `${done.toFixed(1)}h / ${req.toFixed(1)}h`;
        const forceRem = dev.force_remaining ? this._num(dev.force_remaining, 0) : 0;
        if (forceRem > 1) s += ` 🔒 ${Math.round(forceRem)} min`;
        return s;
      }
      case "water_heater": {
        const temp = this._num(dev.temp_entity, null);
        if (temp === null) return "";
        const target = dev.temp_target ?? null;
        return target !== null
          ? `${temp.toFixed(1)}°C / ${target}°C`
          : `${temp.toFixed(1)}°C`;
      }
      case "ev": {
        const pluggedEntity = dev.plugged_entity ? this._str(dev.plugged_entity) : null;
        if (pluggedEntity === "off") return "";
        const soc = this._num(dev.soc_entity, null);
        return soc !== null ? `SOC : ${Math.round(soc)}%` : "";
      }
      case "appliance":
        // Status already shown in statusText
        return "";
      default:
        return "";
    }
  }

  _defaultIcon(type) {
    const icons = { pool: "🏊", water_heater: "🌡️", appliance: "🫧", ev: "🚗" };
    return icons[type] ?? "🔌";
  }

  _reasonLabel(reason) {
    const map = {
      dispatch:       "Surplus",
      must_run:       "Forcé",
      satisfied:      "Satisfait",
      score_too_low:  "Score faible",
      no_budget:      "Budget",
      fit_negligible: "Fit faible",
      outside_window: "Hors plage",
      manual:         "Manuel",
    };
    return map[reason] ?? reason;
  }

  // ------------------------------------------------------------------ SVG helpers
  _flow(lineId, lblId, { active, power, color, marker, x1, y1, x2, y2, lblX, lblY, lblAnchor, lblColor }) {
    const line = this.shadowRoot.getElementById(lineId);
    const lbl  = this.shadowRoot.getElementById(lblId);
    if (!line) return;

    if (active && power > 10) {
      const speed = Math.max(0.4, Math.min(3.0, 2000 / Math.max(power, 100)));
      line.setAttribute("x1", x1); line.setAttribute("y1", y1);
      line.setAttribute("x2", x2); line.setAttribute("y2", y2);
      line.setAttribute("stroke", color);
      line.setAttribute("marker-end", `url(#${marker})`);
      line.classList.add("fl-on");
      line.style.animationDuration = `${speed}s`;
      if (lbl) {
        lbl.setAttribute("x", lblX);
        lbl.setAttribute("y", lblY);
        lbl.setAttribute("text-anchor", lblAnchor);
        lbl.setAttribute("fill", lblColor);
        lbl.textContent = this._fmt(power);
      }
    } else {
      this._flowOff(lineId, lblId, x1, y1, x2, y2);
    }
  }

  _flowOff(lineId, lblId, x1, y1, x2, y2) {
    const line = this.shadowRoot.getElementById(lineId);
    if (line) {
      line.setAttribute("x1", x1); line.setAttribute("y1", y1);
      line.setAttribute("x2", x2); line.setAttribute("y2", y2);
      line.setAttribute("stroke", "#e0e0e0");
      line.removeAttribute("marker-end");
      line.classList.remove("fl-on");
      line.style.animationDuration = "";
    }
    const lbl = this.shadowRoot.getElementById(lblId);
    if (lbl) lbl.textContent = "";
  }

  _txt(id, text) {
    const el = this.shadowRoot.getElementById(id);
    if (el) el.textContent = text;
  }

  _svgAttr(id, attr, value) {
    const el = this.shadowRoot.getElementById(id);
    if (el) el.setAttribute(attr, value);
  }

  // ------------------------------------------------------------------ Card metadata
  static getStubConfig() {
    return {
      entities: {
        pv_power:       "sensor.helios_pv_power",
        grid_power:     "sensor.helios_grid_power",
        house_power:    "sensor.helios_house_power",
        battery_soc:    "",
        score:          "sensor.helios_global_score",
        battery_action: "sensor.helios_battery_action",
        auto_mode:      "switch.helios_auto_mode",
      },
      devices: [],
    };
  }

  getCardSize() { return 4; }
}

// Guard against double registration (script loaded twice)
if (!customElements.get("helios-card")) {
  customElements.define("helios-card", HeliosCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "helios-card")) {
  window.customCards.push({
    type:        "helios-card",
    name:        "Helios Energy Flow",
    description: "Flux d'énergie solaire, batterie et réseau avec score et état des appareils.",
  });
}
