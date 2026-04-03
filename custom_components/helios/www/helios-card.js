/**
 * helios-card — Lovelace card for the Helios Energy Optimizer
 *
 * La resource est enregistrée automatiquement dans Lovelace au démarrage de HA.
 * URL : /helios/helios-card.js
 *
 * Test local : python3 -m http.server 8765 --directory custom_components/helios/www/
 *              → http://localhost:8765/test_card.html
 *
 * Config — mode automatique (recommandé) :
 *   type: custom:helios-card
 *   compact: true                                 # optionnel — vue flux condensée (sans section appareils)
 *   info_url: /lovelace/energie                  # optionnel — URL du bouton ℹ️
 *
 *   Aucun identifiant nécessaire : la carte détecte automatiquement l'intégration Helios
 *   via hass.entities (platform = "helios"), puis par pattern sur les entity_id en fallback.
 *
 * Config — mode manuel (rétrocompatible) :
 *   type: custom:helios-card
 *   entities:
 *     pv_power:      sensor.helios_pv_power
 *     grid_power:    sensor.helios_grid_power     # positif=import, négatif=export
 *     house_power:   sensor.helios_house_power
 *     battery_soc:   sensor.my_battery_soc        # optionnel — SOC batterie (%)
 *     battery_power: sensor.helios_battery_power  # optionnel — négatif=charge, positif=décharge
 *     score:         sensor.helios_global_score
 *   devices:                                      # optionnel — section appareils (mode full uniquement)
 *     - name: Piscine
 *       type: pool
 *       entity:              switch.helios_piscine_manuel
 *       filtration_done:     sensor.helios_pool_filtration_done     # en minutes
 *       filtration_required: sensor.helios_pool_filtration_required # en minutes
 *       force_remaining:     sensor.helios_pool_force_remaining     # optionnel, en minutes
 *     - name: Chauffe-eau
 *       type: water_heater
 *       entity:      switch.helios_chauffe_eau_manuel
 *       temp_entity: sensor.temperature_chauffe_eau
 *       temp_target: 61                           # optionnel — cible affichée en °C
 *     - name: Lave-vaisselle
 *       type: appliance
 *       entity:       switch.helios_lave_vaisselle_manuel
 *       state_entity: sensor.helios_lave_vaisselle_etat
 *     - name: Voiture
 *       type: ev_charger
 *       entity:         switch.helios_voiture_manuel
 *       soc_entity:     sensor.ev_soc             # optionnel
 *       plugged_entity: binary_sensor.ev_branche  # optionnel — on=branché, off=non branché
 */

class HeliosCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._initialized = false;
    this._hass = null;
    this._config = null;
    this._layout = this._makeLayout();
  }

  // ------------------------------------------------------------------ Layout (full vs compact)
  _makeLayout() {
    return {
      viewBox: "0 0 300 162", r: 20, fs: 17, fsVal: 9, ringR: 24, ringSW: 2.5,
      pv:   { cx: 150, cy: 32,  emojiY: 39  },
      house:{ cx: 150, cy: 114, emojiY: 121 },
      houseValBelowY: 151,
      grid: { cx: 45,  cy: 114, emojiY: 121 },
      bat:  { cx: 255, cy: 114 },
      bat_ico_y: 109, bat_soc_y: 126,
      linePv:  { x1:150, y1:56,  x2:150, y2:90,  lblX:157, lblY:75  },
      lineGrid:{ x1:70,  y1:114, x2:129, y2:114, lblX:95,  lblY:108 },
      lineBat: { x1:175, y1:114, x2:230, y2:114, lblX:204, lblY:108 },
    };
  }

  // ------------------------------------------------------------------ Ring helper
  _ringCirc() { return 2 * Math.PI * this._layout.ringR; }

  _updateRing(id, fraction, color) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    const circ = this._ringCirc();
    const off  = circ * (1 - Math.max(0, Math.min(1, fraction)));
    el.style.strokeDashoffset = off.toFixed(2);
    el.style.stroke = color;
  }

  _hideRing(id) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    el.style.strokeDashoffset = this._ringCirc().toFixed(2);
  }

  // ------------------------------------------------------------------ Config
  setConfig(config) {
    const prevCompact = this._config?.compact;
    this._config = config || {};
    const compact = !!this._config.compact;
    if (!this._initialized || prevCompact !== compact) {
      this._layout = this._makeLayout();
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

  // ------------------------------------------------------------------ SVG builder
  _buildSvg() {
    const L = this._layout;
    const { pv, house, grid, bat, r, fs, fsVal, ringR, ringSW } = L;
    const bcx = bat.cx, bcy = bat.cy;
    const circ = (2 * Math.PI * ringR).toFixed(2);
    const ring = (id, cx, cy, color) =>
      `<circle id="${id}" cx="${cx}" cy="${cy}" r="${ringR}"
        fill="none" stroke="${color}" stroke-width="${ringSW}" stroke-linecap="round"
        stroke-dasharray="${circ}" stroke-dashoffset="${circ}"
        transform="rotate(-90 ${cx} ${cy})" class="h-ring"/>`;
    return `
      <svg viewBox="${L.viewBox}" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <marker id="h-arr-pv"      markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5Z" fill="#F9A825"/></marker>
          <marker id="h-arr-gin"     markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5Z" fill="#7B1FA2"/></marker>
          <marker id="h-arr-gout"    markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5Z" fill="#388E3C"/></marker>
          <marker id="h-arr-bat-chg" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5Z" fill="#1565C0"/></marker>
          <marker id="h-arr-bat-dch" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path d="M0,0 L5,2.5 L0,5Z" fill="#0288D1"/></marker>
        </defs>
        <line id="h-line-pv"   class="fl" x1="${L.linePv.x1}"   y1="${L.linePv.y1}"   x2="${L.linePv.x2}"   y2="${L.linePv.y2}"/>
        <line id="h-line-grid" class="fl" x1="${L.lineGrid.x1}" y1="${L.lineGrid.y1}" x2="${L.lineGrid.x2}" y2="${L.lineGrid.y2}"/>
        <line id="h-line-bat"  class="fl" x1="${L.lineBat.x1}"  y1="${L.lineBat.y1}"  x2="${L.lineBat.x2}"  y2="${L.lineBat.y2}"/>
        <text id="h-lbl-pv"   x="${L.linePv.lblX}"   y="${L.linePv.lblY}"   font-size="${fsVal}" fill="#E65100" text-anchor="start"></text>
        <text id="h-lbl-grid" x="${L.lineGrid.lblX}" y="${L.lineGrid.lblY}" font-size="${fsVal}" text-anchor="middle"></text>
        <text id="h-lbl-bat"  x="${L.lineBat.lblX}"  y="${L.lineBat.lblY}"  font-size="${fsVal}" text-anchor="middle"></text>
        <circle cx="${pv.cx}" cy="${pv.cy}" r="${r}" fill="#FFF8E1" stroke="#F9A825" stroke-width="2"/>
        <text x="${pv.cx}" y="${pv.emojiY}" text-anchor="middle" font-size="${fs}">☀️</text>
        ${ring("h-ring-pv", pv.cx, pv.cy, "#F9A825")}
        <circle id="h-node-house" cx="${house.cx}" cy="${house.cy}" r="${r}" fill="#E8F5E9" stroke="#388E3C" stroke-width="2"/>
        <text x="${house.cx}" y="${house.emojiY}" text-anchor="middle" font-size="${fs}">🏠</text>
        <text id="h-val-house" x="${house.cx}" y="${L.houseValBelowY}" text-anchor="middle" font-size="${fsVal}" font-weight="600"></text>
        <circle id="h-node-grid" cx="${grid.cx}" cy="${grid.cy}" r="${r}" fill="#F3E5F5" stroke="#7B1FA2" stroke-width="2"/>
        <text x="${grid.cx}" y="${grid.emojiY}" text-anchor="middle" font-size="${fs}">⚡</text>
        ${ring("h-ring-grid", grid.cx, grid.cy, "#7B1FA2")}
        <circle id="h-node-bat" cx="${bcx}" cy="${bcy}" r="${r}" fill="#E3F2FD" stroke="#1565C0" stroke-width="2"/>
        <rect id="h-bat-body"     x="${bcx - 9}" y="${L.bat_ico_y - 6}" width="18" height="12" rx="2" fill="none" stroke="#1565C0" stroke-width="1.5"/>
        <rect id="h-bat-terminal" x="${bcx - 2.5}" y="${L.bat_ico_y - 9}" width="5" height="3" rx="1" fill="#1565C0"/>
        <rect id="h-bat-fill"     x="${bcx - 7}" y="${L.bat_ico_y - 4}" width="0" height="8" rx="1" fill="#1565C0"/>
        <text id="h-val-bat-soc" x="${bcx}" y="${L.bat_soc_y}" text-anchor="middle" font-size="${fsVal}" font-weight="600" fill="#0D47A1"></text>
        ${ring("h-ring-bat", bcx, bcy, "#4CAF50")}
      </svg>`;
  }

  // ------------------------------------------------------------------ Build DOM (once)
  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }

        .info-btn {
          position: absolute;
          top: 8px;
          right: 8px;
          z-index: 10;
          width: 22px;
          height: 22px;
          border-radius: 50%;
          border: none;
          background: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 16px;
          color: var(--secondary-text-color);
          padding: 0;
          opacity: 0.6;
          transition: opacity 0.2s;
        }
        .info-btn:hover { opacity: 1; }
        .info-btn[hidden] { display: none; }

        .card {
          position: relative;
          background: var(--card-background-color, #fff);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,0.1));
          padding: 16px;
          font-family: var(--primary-font-family, Roboto, sans-serif);
          color: var(--primary-text-color);
        }
        .card[data-compact] {
          padding: 4px;
        }

        /* ---- Power flow ---- */
        .flow-wrap { width: 100%; margin: 4px 0 8px; }
        .card[data-compact] .flow-wrap { margin: 0; }
        svg { width: 100%; height: auto; display: block; }

        .fl {
          fill: none;
          stroke: #e0e0e0;
          stroke-width: 2.5;
          stroke-linecap: round;
        }
        .h-ring {
          transition: stroke-dashoffset 0.8s ease, stroke 0.5s ease;
        }
        #h-val-house { fill: var(--primary-text-color, #212121); }
        @media (prefers-color-scheme: dark) {
          #h-node-house { fill: #1B5E20; stroke: #66BB6A; }
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
        .dev-power {
          font-size: 10px;
          font-weight: 600;
          color: #4CAF50;
          margin-left: 4px;
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
        <button class="info-btn" id="h-info-btn" hidden>ℹ️</button>
        <div class="flow-wrap">
          ${this._buildSvg()}
        </div>

        <div class="footer" id="h-footer">
          <div class="score-row">
            <div class="lbl">Score</div>
            <div class="bar-bg"><div class="bar-fill" id="h-score-bar"></div></div>
            <div class="score-num" id="h-score-num">—</div>
          </div>
        </div>

        <div class="devices" id="h-devices" style="display:none"></div>
      </div>
    `;

    this.shadowRoot.getElementById("h-info-btn").addEventListener("click", () => {
      const url = this._config?.info_url;
      if (!url) return;
      if (url.startsWith("http://") || url.startsWith("https://")) {
        window.open(url, "_blank");
      } else {
        history.pushState(null, "", url);
        window.dispatchEvent(new Event("location-changed", { bubbles: true, composed: true }));
      }
    });

    this._initialized = true;
  }

  // ------------------------------------------------------------------ Entity discovery
  // Finds the Helios config entry_id automatically from hass.entities by platform name.
  // Returns the first entry_id found for the "helios" platform, or null.
  _autoDiscoverEntryId() {
    if (!this._hass?.entities) return null;
    for (const info of Object.values(this._hass.entities)) {
      if (info.platform === "helios" && info.config_entry_id) return info.config_entry_id;
    }
    return null;
  }

  // Scans hass.entities (frontend registry) to find all entities belonging to
  // the given Helios config entry, keyed by their unique_id suffix.
  _discoverEntities(entryId) {
    if (!this._hass?.entities) return null;
    const map = {};
    for (const [entityId, info] of Object.entries(this._hass.entities)) {
      if (info.config_entry_id !== entryId) continue;
      const uid = info.unique_id ?? "";
      const suffix = uid.startsWith(entryId + "_") ? uid.slice(entryId.length + 1) : uid;
      map[suffix] = entityId;
    }
    return map;
  }

  // Build the device list from auto-discovered device state sensors, sorted by priority desc.
  // entityMap: suffix → entityId (output of _discoverEntities)
  _discoverDevices(entityMap) {
    const devices = [];
    for (const [suffix, entityId] of Object.entries(entityMap)) {
      const m = suffix.match(/^device_state_(.+)$/);
      if (!m) continue;
      const slug = m[1];
      const attrs = this._hass.states[entityId]?.attributes ?? {};
      devices.push({
        entity:   entityId,
        name:     attrs.device_name ?? slug,
        type:     attrs.device_type ?? "appliance",
        priority: attrs.device_priority ?? 5,
      });
    }
    devices.sort((a, b) => b.priority - a.priority);
    return devices;
  }

  // Scans hass.states directly — no entry_id or hass.entities needed.
  // Detects Helios entities by naming convention and by the helios_device_on attribute marker.
  // Returns { entityRefs, devices } in the same format used by _resolveEntityRefs / _updateDevices.
  _discoverFromStates() {
    const states = this._hass?.states;
    if (!states) return null;

    // System entities — fixed HA entity_id names generated by Helios
    const entityRefs = { _soc_from_attr: true };
    const SYSTEM = {
      pv_power:       "sensor.helios_pv_power",
      grid_power:     "sensor.helios_grid_power",
      house_power:    "sensor.helios_house_power",
      score:   "sensor.helios_global_score",
      battery: "sensor.helios_battery",
    };
    for (const [key, eid] of Object.entries(SYSTEM)) {
      if (states[eid]) entityRefs[key] = eid;
    }

    // Device discovery — sensor.helios_{slug} with device_type attribute
    const devices = [];
    for (const [entityId, state] of Object.entries(states)) {
      if (!entityId.startsWith("sensor.helios_")) continue;
      const attrs = state.attributes ?? {};
      if (!attrs.device_type) continue;
      const slug = entityId.replace(/^sensor\.helios_/, "");
      devices.push({
        entity:   entityId,
        name:     attrs.device_name ?? slug,
        type:     attrs.device_type ?? "appliance",
        priority: attrs.device_priority ?? 5,
      });
    }
    devices.sort((a, b) => b.priority - a.priority);

    return { entityRefs, devices };
  }

  // Résout les entités ET les appareils en un seul passage.
  // Priorité : entry_id explicite → auto-découverte → patterns état → config manuelle.
  _resolveAll() {
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc?.["global_score"]) {
        return {
          entityRefs: {
            pv_power:       disc["pv_power"],
            grid_power:     disc["grid_power"],
            house_power:    disc["house_power"],
            score:          disc["global_score"],
            battery:        disc["battery"],
            _soc_from_attr: true,
          },
          devices: this._discoverDevices(disc),
        };
      }
    }
    const stateDisc = this._discoverFromStates();
    if (stateDisc?.entityRefs.score) return stateDisc; // { entityRefs, devices }
    return { entityRefs: this._config?.entities || {}, devices: this._config?.devices ?? [] };
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
    const { entityRefs: e, devices: discoveredDevices } = this._resolveAll();

    const pv         = this._num(e.pv_power);
    const grid       = this._num(e.grid_power);
    const house      = this._num(e.house_power);
    const score      = this._num(e.score);
    // Battery data — all from sensor.helios_battery attributes
    const battAction   = this._str(e.battery) ?? "idle";
    const soc          = this._attr(e.battery, "soc") ?? this._attr(e.score, "battery_soc");
    const batPowerRaw  = this._attr(e.battery, "power_w");
    const tempo      = this._attr(e.score, "tempo_color");
    // Node values
    this._txt("h-val-house", this._fmt(house));

    const tempoFill   = tempo === "red" ? "#FFEBEE" : tempo === "white" ? "#F5F5F5" : "#E3F2FD";
    const tempoStroke = tempo === "red" ? "#F44336" : tempo === "white" ? "#9E9E9E" : "#2196F3";
    this._svgAttr("h-node-grid", "fill",   tempoFill);
    this._svgAttr("h-node-grid", "stroke", tempoStroke);

    const L = this._layout;
    this._txt("h-val-bat-soc", soc !== null ? `${Math.round(soc)}%` : "—");
    const batColor = soc !== null
      ? (soc > 60 ? "#4CAF50" : soc > 20 ? "#FF9800" : "#F44336")
      : "#1565C0";
    this._svgAttr("h-bat-body",     "stroke", batColor);
    this._svgAttr("h-bat-terminal", "fill",   batColor);
    this._svgAttr("h-bat-fill",     "fill",   batColor);
    this._svgAttr("h-bat-fill",     "width",  soc !== null ? Math.max(1, 14 * soc / 100).toFixed(1) : "0");
    this._svgAttr("h-val-bat-soc",  "fill",   batColor);
    this._svgAttr("h-node-bat", "stroke", battAction === "discharge" ? "#0288D1" : "#1565C0");

    // PV → House
    const lp = L.linePv;
    this._flow("h-line-pv", "h-lbl-pv", {
      active: pv > 10, power: pv, color: "#F9A825", marker: "h-arr-pv",
      x1: lp.x1, y1: lp.y1, x2: lp.x2, y2: lp.y2,
      lblX: lp.lblX, lblY: lp.lblY, lblAnchor: "start", lblColor: "#E65100",
    });

    // Grid flow
    const lg = L.lineGrid;
    const gridAbs = Math.abs(grid);
    if (grid > 10) {
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#7B1FA2", marker: "h-arr-gin",
        x1: lg.x1, y1: lg.y1, x2: lg.x2, y2: lg.y2,
        lblX: lg.lblX, lblY: lg.lblY, lblAnchor: "middle", lblColor: "#7B1FA2",
      });
    } else if (grid < -10) {
      this._flow("h-line-grid", "h-lbl-grid", {
        active: true, power: gridAbs, color: "#388E3C", marker: "h-arr-gout",
        x1: lg.x2, y1: lg.y1, x2: lg.x1, y2: lg.y2,
        lblX: lg.lblX, lblY: lg.lblY, lblAnchor: "middle", lblColor: "#388E3C",
      });
    } else {
      this._flowOff("h-line-grid", "h-lbl-grid", lg.x1, lg.y1, lg.x2, lg.y2);
    }

    // Battery flow — négatif = charge, positif = décharge
    const lb = L.lineBat;
    let batIsCharge, batIsDischarge, batPow;
    if (batPowerRaw !== null && batPowerRaw !== undefined) {
      batPow         = Math.abs(batPowerRaw);
      batIsCharge    = batPowerRaw < -10;
      batIsDischarge = batPowerRaw > 10;
    } else {
      batPow         = Math.abs(pv - house - grid);
      batIsCharge    = battAction === "charge"    && batPow > 10;
      batIsDischarge = battAction === "discharge" && batPow > 10;
    }
    if (batIsCharge) {
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPow, color: "#1565C0", marker: "h-arr-bat-chg",
        x1: lb.x1, y1: lb.y1, x2: lb.x2, y2: lb.y2,
        lblX: lb.lblX, lblY: lb.lblY, lblAnchor: "middle", lblColor: "#1565C0",
      });
    } else if (batIsDischarge) {
      this._flow("h-line-bat", "h-lbl-bat", {
        active: true, power: batPow, color: "#0288D1", marker: "h-arr-bat-dch",
        x1: lb.x2, y1: lb.y1, x2: lb.x1, y2: lb.y2,
        lblX: lb.lblX, lblY: lb.lblY, lblAnchor: "middle", lblColor: "#0288D1",
      });
    } else {
      this._flowOff("h-line-bat", "h-lbl-bat", lb.x1, lb.y1, lb.x2, lb.y2);
    }

    // Score bar
    const scoreColor = score > 0.6 ? "#4CAF50" : score > 0.3 ? "#FF9800" : "#F44336";
    const bar = this.shadowRoot.getElementById("h-score-bar");
    if (bar) {
      bar.style.width      = `${Math.round(score * 100)}%`;
      bar.style.background = scoreColor;
    }
    this._txt("h-score-num", this._hass ? score.toFixed(2) : "—");

    // Progress rings
    const maxPow  = this._attr(e.score, "peak_pv_w") ?? 6000;
    const maxGrid = this._attr(e.score, "grid_subscription_w") ?? maxPow;

    // PV ring — amber, 0…peak_pv_w (from Helios config, via score sensor attribute)
    this._updateRing("h-ring-pv", pv / maxPow, "#F9A825");

    // Grid ring — color by tempo, 0…grid_subscription_w
    if (Math.abs(grid) > 5) {
      const tempoRingColor = tempo === "red" ? "#F44336" : tempo === "white" ? "#9E9E9E" : "#2196F3";
      this._updateRing("h-ring-grid", Math.abs(grid) / maxGrid, tempoRingColor);
    } else {
      this._hideRing("h-ring-grid");
    }

    // Battery ring — SOC, color by level (batColor already encodes the same logic)
    if (soc !== null) {
      this._updateRing("h-ring-bat", soc / 100, batColor);
    } else {
      this._hideRing("h-ring-bat");
    }

    // Bouton info
    const infoBtn = this.shadowRoot.getElementById("h-info-btn");
    if (infoBtn) infoBtn.hidden = !this._config.info_url;

    // Compact: marge réduite + masquer footer + devices
    const compact  = !!this._config.compact;
    const cardEl   = this.shadowRoot.querySelector(".card");
    if (cardEl) compact ? cardEl.setAttribute("data-compact", "") : cardEl.removeAttribute("data-compact");
    const devices = this.shadowRoot.getElementById("h-devices");
    if (devices && compact) devices.style.display = "none";

    // Devices section (full mode uniquement)
    if (!compact) this._updateDevices(discoveredDevices);
  }

  // ------------------------------------------------------------------ Devices
  _updateDevices(devCfgs) {
    const devicesEl = this.shadowRoot.getElementById("h-devices");
    if (!devicesEl) return;
    if (devCfgs.length === 0) { devicesEl.style.display = "none"; return; }
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

    // Current power from sensor attribute
    let powerHtml = "";
    if (isOn) {
      const pw = this._attr(dev.entity, "power_w");
      if (pw !== null && pw > 5) powerHtml = `<span class="dev-power">${this._fmt(pw)}</span>`;
    }

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
          ${powerHtml}
        </div>
        <div class="dev-score-col">${scoreHtml}</div>
      </div>
    `;
  }

  _deviceIsOn(dev) {
    return this._str(dev.entity) === "running";
  }

  _deviceStatus(dev, isOn) {
    const manual = this._attr(dev.entity, "manual_mode") === true;
    if (manual) return { dotColor: "#FF9800", statusText: "Manuel" };
    const st = this._str(dev.entity);
    if (dev.type === "appliance") {
      const map = {
        running: { dotColor: "#4CAF50", statusText: "En marche" },
        waiting: { dotColor: "#FF9800", statusText: "En attente" },
        off:     { dotColor: "#9E9E9E", statusText: "Arrêt" },
      };
      return map[st] ?? { dotColor: "#9E9E9E", statusText: st ?? "—" };
    }
    if (dev.type === "ev" || dev.type === "ev_charger") {
      if (this._attr(dev.entity, "plugged") === false) return { dotColor: "#9E9E9E", statusText: "Non branché" };
    }
    return isOn
      ? { dotColor: "#4CAF50", statusText: "ON" }
      : { dotColor: "#9E9E9E", statusText: "OFF" };
  }

  _deviceDetail(dev) {
    switch (dev.type) {
      case "pool": {
        const doneMin = this._attr(dev.entity, "filtration_done_min");
        const reqMin  = this._attr(dev.entity, "filtration_required_min");
        if (doneMin === null || reqMin === null) return "";
        let s = `${(doneMin / 60).toFixed(1)}h / ${(reqMin / 60).toFixed(1)}h`;
        const forceRem = this._attr(dev.entity, "force_remaining_min") ?? 0;
        if (forceRem > 1) s += ` 🔒 ${Math.round(forceRem)} min`;
        return s;
      }
      case "water_heater": {
        const temp = this._attr(dev.entity, "temperature");
        if (temp === null) return "";
        const target = this._attr(dev.entity, "wh_temp_target");
        return target !== null ? `${temp.toFixed(1)}°C / ${target}°C` : `${temp.toFixed(1)}°C`;
      }
      case "ev":
      case "ev_charger": {
        if (this._attr(dev.entity, "plugged") === false) return "";
        const soc = this._attr(dev.entity, "soc");
        return soc !== null ? `SOC : ${Math.round(soc)}%` : "";
      }
      default:
        return "";
    }
  }

  _defaultIcon(type) {
    const icons = { pool: "🏊", water_heater: "🌡️", appliance: "🫧", ev: "🚗", ev_charger: "🚗" };
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
      line.style.stroke = color;
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
      line.style.stroke = "var(--divider-color, #e0e0e0)";
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
        pv_power:   "sensor.helios_pv_power",
        grid_power: "sensor.helios_grid_power",
        house_power:"sensor.helios_house_power",
        score:      "sensor.helios_global_score",
        battery:    "sensor.helios_battery",
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
