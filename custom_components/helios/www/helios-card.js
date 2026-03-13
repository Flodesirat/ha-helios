/**
 * helios-card — Lovelace card for the Helios Energy Optimizer
 *
 * Shows real-time energy flow between Solar, Grid, Battery and House,
 * along with the global optimization score and current mode.
 *
 * Config example:
 *   type: custom:helios-card
 *   entities:
 *     pv_power:       sensor.eo_pv_power
 *     grid_power:     sensor.eo_grid_power      # positive=import, negative=export
 *     house_power:    sensor.eo_house_power
 *     battery_soc:    sensor.my_battery_soc     # optional (any SOC sensor)
 *     score:          sensor.eo_global_score
 *     battery_action: sensor.eo_battery_action
 *     auto_mode:      switch.eo_auto_mode
 */

class HeliosCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._initialized = false;
  }

  // ------------------------------------------------------------------ Config
  setConfig(config) {
    if (!config.entities) {
      throw new Error("helios-card: 'entities' is required");
    }
    this._config = config;
    if (!this._initialized) {
      this._build();
    }
  }

  // ------------------------------------------------------------------ hass
  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  // ------------------------------------------------------------------ Helpers
  _num(entityId, fallback = 0) {
    if (!entityId || !this._hass) return fallback;
    const s = this._hass.states[entityId];
    if (!s || s.state === "unavailable" || s.state === "unknown") {
      return fallback;
    }
    const n = parseFloat(s.state);
    return isNaN(n) ? fallback : n;
  }

  _str(entityId, fallback = null) {
    if (!entityId || !this._hass) return fallback;
    const s = this._hass.states[entityId];
    if (!s || s.state === "unavailable" || s.state === "unknown") {
      return fallback;
    }
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

        ha-card {
          padding: 16px;
          font-family: var(--primary-font-family, Roboto, sans-serif);
          color: var(--primary-text-color);
        }

        /* ---- Header ---- */
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 4px;
        }
        .title {
          font-size: 15px;
          font-weight: 600;
          letter-spacing: 0.3px;
        }
        .mode-btn {
          padding: 3px 11px;
          border-radius: 12px;
          font-size: 11px;
          font-weight: 700;
          color: #fff;
          cursor: pointer;
          border: none;
          transition: background 0.3s;
        }

        /* ---- SVG energy flow ---- */
        .flow-wrap {
          width: 100%;
          margin: 4px 0 8px;
        }
        svg { width: 100%; height: auto; display: block; }

        /* Flow line base */
        .fl {
          fill: none;
          stroke: #e0e0e0;
          stroke-width: 2.5;
          stroke-linecap: round;
        }
        /* Active animated flow */
        .fl-on {
          stroke-dasharray: 8 5;
          animation: flowDash linear infinite;
        }
        @keyframes flowDash {
          to { stroke-dashoffset: -26; }
        }

        /* ---- Footer ---- */
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
        }
        .score-num {
          font-size: 12px;
          font-weight: 600;
          min-width: 34px;
          text-align: right;
        }

        /* Chips row */
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
          width: 8px;
          height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
        }
      </style>

      <ha-card>
        <!-- Header -->
        <div class="header">
          <div class="title">⚡ Helios</div>
          <button class="mode-btn" id="mode-btn">—</button>
        </div>

        <!-- SVG energy flow diagram -->
        <!--
          Layout (300×185 viewBox):
                     ☀️ PV  (150, 38)
                      |
          ⚡ Grid — 🏠 House — 🔋 Bat
           (45,125)   (150,125)  (255,125)
        -->
        <div class="flow-wrap">
          <svg viewBox="0 0 300 185" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <!-- Arrow markers, one per flow colour -->
              <marker id="h-arr-pv"      markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#F9A825"/></marker>
              <marker id="h-arr-gin"     markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#7B1FA2"/></marker>
              <marker id="h-arr-gout"    markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#388E3C"/></marker>
              <marker id="h-arr-bat-chg" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#1565C0"/></marker>
              <marker id="h-arr-bat-dch" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7Z" fill="#0288D1"/></marker>
            </defs>

            <!-- ===== Flow lines ===== -->
            <!-- PV → House (vertical) -->
            <line id="h-line-pv" class="fl" x1="150" y1="64" x2="150" y2="97"/>
            <text id="h-lbl-pv" x="157" y="83" font-size="9" fill="#E65100" text-anchor="start"></text>

            <!-- Grid ↔ House (horizontal left) -->
            <line id="h-line-grid" class="fl" x1="73" y1="125" x2="122" y2="125"/>
            <text id="h-lbl-grid" x="97" y="119" font-size="9" text-anchor="middle"></text>

            <!-- Battery ↔ House (horizontal right) -->
            <line id="h-line-bat" class="fl" x1="178" y1="125" x2="227" y2="125"/>
            <text id="h-lbl-bat" x="203" y="119" font-size="9" text-anchor="middle"></text>

            <!-- ===== Nodes ===== -->
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

        <!-- Footer: score bar + chips -->
        <div class="footer">
          <div class="score-row">
            <div class="lbl">Score</div>
            <div class="bar-bg"><div class="bar-fill" id="h-score-bar"></div></div>
            <div class="score-num" id="h-score-num">—</div>
          </div>
          <div class="chips" id="h-chips"></div>
        </div>
      </ha-card>
    `;

    // Mode button click → toggle the switch entity
    this.shadowRoot.getElementById("mode-btn").addEventListener("click", () => {
      const entityId = this._config.entities?.auto_mode;
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

  // ------------------------------------------------------------------ Update (on every hass change)
  _update() {
    if (!this._initialized || !this._hass || !this._config) return;

    const e = this._config.entities || {};

    // --- Read values ---
    const pv         = this._num(e.pv_power);
    const grid        = this._num(e.grid_power);   // >0 import, <0 export
    const house       = this._num(e.house_power);
    const soc         = e.battery_soc ? this._num(e.battery_soc, null) : null;
    const score       = this._num(e.score);
    const battAction  = this._str(e.battery_action) ?? "idle";
    const tempo       = this._attr(e.score, "tempo_color");
    const modeAttr    = this._attr(e.score, "mode");
    const switchState = this._str(e.auto_mode);
    const mode        = modeAttr ?? (switchState === "on" ? "auto" : switchState === "off" ? "off" : null);

    // --- Node values ---
    this._txt("h-val-pv",    this._fmt(pv));
    this._txt("h-val-house", this._fmt(house));

    const gridSign = grid > 0 ? "+" : "";
    this._txt("h-val-grid", `${gridSign}${this._fmt(grid)}`);
    this._css("h-node-grid", {
      fill:   grid < 0 ? "#E8F5E9" : "#F3E5F5",
      stroke: grid < 0 ? "#388E3C" : "#7B1FA2",
    });
    this._txtAttr("h-val-grid", "fill", grid < 0 ? "#2E7D32" : "#6A1B9A");

    this._txt("h-val-bat-soc", soc !== null ? `${Math.round(soc)}%` : "—");
    const batActionLabel = { charge: "↑ charge", discharge: "↓ décharge", reserve: "🔒 réserve", idle: "—" };
    this._txt("h-val-bat-action", batActionLabel[battAction] ?? battAction);

    // Battery node border pulses when active
    this._css("h-node-bat", {
      stroke: battAction === "charge" ? "#1565C0" : battAction === "discharge" ? "#0288D1" : "#1565C0",
    });

    // --- PV → House flow ---
    this._flow("h-line-pv", "h-lbl-pv", {
      active: pv > 10,
      power: pv,
      color: "#F9A825",
      marker: "h-arr-pv",
      x1: 150, y1: 64, x2: 150, y2: 97,
      lblX: 157, lblY: 83, lblAnchor: "start",
      lblColor: "#E65100",
    });

    // --- Grid flow (direction depends on sign) ---
    const gridAbs = Math.abs(grid);
    if (grid > 10) {
      // Import: Grid → House
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#7B1FA2", marker: "h-arr-gin",
        x1: 73, y1: 125, x2: 122, y2: 125,
        lblX: 97, lblY: 119, lblAnchor: "middle", lblColor: "#7B1FA2",
      });
    } else if (grid < -10) {
      // Export: House → Grid
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#388E3C", marker: "h-arr-gout",
        x1: 122, y1: 125, x2: 73, y2: 125,
        lblX: 97, lblY: 119, lblAnchor: "middle", lblColor: "#388E3C",
      });
    } else {
      this._flowOff("h-line-grid", "h-lbl-grid", 73, 125, 122, 125);
    }

    // --- Battery flow ---
    // Estimate battery power from energy balance: bat ≈ pv - house - grid
    const batPowerEst = Math.abs(pv - house - grid);
    if (battAction === "charge" && batPowerEst > 10) {
      // House/PV → Battery
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPowerEst, color: "#1565C0", marker: "h-arr-bat-chg",
        x1: 178, y1: 125, x2: 227, y2: 125,
        lblX: 203, lblY: 119, lblAnchor: "middle", lblColor: "#1565C0",
      });
    } else if (battAction === "discharge" && batPowerEst > 10) {
      // Battery → House
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPowerEst, color: "#0288D1", marker: "h-arr-bat-dch",
        x1: 227, y1: 125, x2: 178, y2: 125,
        lblX: 203, lblY: 119, lblAnchor: "middle", lblColor: "#0288D1",
      });
    } else {
      this._flowOff("h-line-bat", "h-lbl-bat", 178, 125, 227, 125);
    }

    // --- Score bar ---
    const scoreColor = score > 0.6 ? "#4CAF50" : score > 0.3 ? "#FF9800" : "#F44336";
    const bar = this.shadowRoot.getElementById("h-score-bar");
    if (bar) {
      bar.style.width    = `${Math.round(score * 100)}%`;
      bar.style.background = scoreColor;
    }
    this._txt("h-score-num", score.toFixed(2));

    // --- Mode button ---
    const btn = this.shadowRoot.getElementById("mode-btn");
    if (btn) {
      const modeColors = { auto: "#4CAF50", off: "#F44336", manual: "#FF9800" };
      btn.style.background = modeColors[mode] ?? "#9E9E9E";
      btn.textContent = mode ? mode.toUpperCase() : "—";
    }

    // --- Chips ---
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
  }

  // ------------------------------------------------------------------ SVG helpers
  _flow(lineId, lblId, { active, power, color, marker, x1, y1, x2, y2, lblX, lblY, lblAnchor, lblColor }) {
    const line = this.shadowRoot.getElementById(lineId);
    const lbl  = this.shadowRoot.getElementById(lblId);
    if (!line) return;

    if (active && power > 10) {
      // Speed: faster = more power (clamp between 0.4s and 3s)
      const speed = Math.max(0.4, Math.min(3.0, 2000 / Math.max(power, 100)));
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
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
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
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

  _txtAttr(id, attr, value) {
    const el = this.shadowRoot.getElementById(id);
    if (el) el.setAttribute(attr, value);
  }

  _css(id, styles) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    for (const [k, v] of Object.entries(styles)) el.setAttribute(k, v);
  }

  // ------------------------------------------------------------------ Card metadata (Lovelace UI)
  static getStubConfig() {
    return {
      entities: {
        pv_power:       "sensor.eo_pv_power",
        grid_power:     "sensor.eo_grid_power",
        house_power:    "sensor.eo_house_power",
        battery_soc:    "",               // e.g. sensor.solaredge_battery_level
        score:          "sensor.eo_global_score",
        battery_action: "sensor.eo_battery_action",
        auto_mode:      "switch.eo_auto_mode",
      },
    };
  }
}

customElements.define("helios-card", HeliosCard);

// Register card in Lovelace UI picker
window.customCards = window.customCards || [];
window.customCards.push({
  type:        "helios-card",
  name:        "Helios Energy Flow",
  description: "Flux d'énergie solaire, batterie et réseau avec score de décision.",
  preview:     true,
});
