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
    this._modalSlug = null;
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
  _gridColor() {
    return getComputedStyle(this).getPropertyValue("--helios-grid-color").trim() || "#7B1FA2";
  }

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
          <marker id="h-arr-gin"     markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto"><path id="h-arr-gin-path" d="M0,0 L5,2.5 L0,5Z" fill="#7B1FA2"/></marker>
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
        :host {
          display: block;
          --helios-grid-color: #7B1FA2;
        }
        @media (prefers-color-scheme: dark) {
          :host {
            --helios-grid-color: #be2afd;
          }
        }

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
        .fl-on {
          stroke-dasharray: 8 5;
          animation: flowDash linear infinite;
        }
        @keyframes flowDash { to { stroke-dashoffset: -26; } }

        /* ---- Daily energy ---- */
        .energy-row {
          display: flex;
          gap: 6px;
          margin: 4px 0 8px;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        .energy-chip {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          background: var(--secondary-background-color, #f0f0f0);
          border-radius: 6px;
          padding: 4px 4px 3px;
        }
        .energy-chip-lbl {
          font-size: 9px;
          color: var(--secondary-text-color);
          white-space: nowrap;
        }
        .energy-chip-val {
          font-size: 12px;
          font-weight: 700;
        }

        /* ---- Savings row ---- */
        .savings-row {
          display: flex;
          gap: 6px;
          margin: 0 0 8px;
          padding-bottom: 8px;
          border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        .savings-chip {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          background: #E8F5E9;
          border-radius: 6px;
          padding: 4px 4px 3px;
        }
        @media (prefers-color-scheme: dark) {
          .savings-chip { background: #1B5E20; }
        }
        .savings-chip-lbl {
          font-size: 9px;
          color: var(--secondary-text-color);
          white-space: nowrap;
        }
        .savings-chip-val {
          font-size: 12px;
          font-weight: 700;
          color: #2E7D32;
        }
        @media (prefers-color-scheme: dark) {
          .savings-chip-val { color: #81C784; }
        }

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
        .bar-wrap {
          flex: 1;
          position: relative;
        }
        .bar-bg {
          width: 100%;
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
        .dev-name-row {
          display: flex;
          align-items: center;
          gap: 5px;
        }
        .dev-name {
          font-size: 12px;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .dev-priority {
          font-size: 9px;
          font-weight: 700;
          color: var(--secondary-text-color);
          background: var(--divider-color, #e0e0e0);
          border-radius: 3px;
          padding: 1px 3px;
          flex-shrink: 0;
        }
        .dev-detail {
          font-size: 11px;
          color: var(--secondary-text-color);
          margin-top: 1px;
        }
        .dev-reason {
          font-size: 10px;
          font-weight: 600;
          color: var(--secondary-text-color);
          margin-top: 2px;
        }
        .dev-ready-btn {
          font-size: 10px;
          font-weight: 600;
          padding: 3px 8px;
          border-radius: 4px;
          border: none;
          background: var(--primary-color, #03a9f4);
          color: #fff;
          cursor: pointer;
          flex-shrink: 0;
          white-space: nowrap;
        }
        .dev-ready-btn:active { opacity: 0.7; }
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

        /* ---- Score decomposition ---- */
        .score-decomp {
          display: flex;
          gap: 4px;
          margin-top: 6px;
        }
        .score-factor {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          border-radius: 6px;
          padding: 3px 4px;
          background: var(--secondary-background-color, #f0f0f0);
          position: relative;
          overflow: hidden;
        }
        .score-factor-fill {
          position: absolute;
          bottom: 0; left: 0; right: 0;
          transition: height 0.6s ease, background 0.6s ease;
        }
        .score-factor-lbl {
          font-size: 9px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          position: relative;
        }
        .score-factor-val {
          font-size: 11px;
          font-weight: 700;
          position: relative;
        }
        .score-factor-w {
          font-size: 8px;
          color: var(--secondary-text-color);
          position: relative;
        }
        .score-sep {
          display: flex;
          align-items: center;
          font-size: 12px;
          font-weight: 700;
          color: var(--secondary-text-color);
          flex-shrink: 0;
          padding-bottom: 4px;
        }

        /* ---- Budget row ---- */
        .budget-row {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          margin-top: 6px;
        }
        .budget-chip {
          display: flex;
          flex-direction: column;
          align-items: center;
          flex: 1;
          min-width: 60px;
          background: var(--secondary-background-color, #f0f0f0);
          border-radius: 6px;
          padding: 3px 6px;
        }
        .budget-chip-lbl {
          font-size: 9px;
          color: var(--secondary-text-color);
          white-space: nowrap;
        }
        .budget-chip-val {
          font-size: 11px;
          font-weight: 700;
        }

        /* ---- Forecast section ---- */
        .forecast {
          margin-top: 6px;
        }
        .forecast-title {
          font-size: 10px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--secondary-text-color);
          margin-bottom: 4px;
        }
        .forecast-sub {
          font-size: 9px;
          color: var(--secondary-text-color);
          margin-bottom: 4px;
        }
        .forecast-chips {
          display: flex;
          gap: 4px;
          flex-wrap: wrap;
        }
        .forecast-chip {
          display: flex;
          flex-direction: column;
          align-items: center;
          flex: 1;
          min-width: 56px;
          background: var(--secondary-background-color, #f0f0f0);
          border-radius: 6px;
          padding: 3px 5px;
        }
        .forecast-chip-lbl {
          font-size: 8px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          text-align: center;
        }
        .forecast-chip-val {
          font-size: 11px;
          font-weight: 700;
        }

        /* ---- Modal overlay ---- */
        .modal-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0,0,0,0.45);
          z-index: 999;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .modal-overlay[hidden] { display: none; }
        .modal-box {
          background: var(--card-background-color, #fff);
          border-radius: 12px;
          padding: 16px;
          min-width: 260px;
          max-width: 380px;
          width: 90%;
          max-height: 80vh;
          overflow-y: auto;
          box-shadow: 0 8px 32px rgba(0,0,0,0.3);
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .hm-header {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .hm-icon { font-size: 22px; }
        .hm-title { flex: 1; font-size: 16px; font-weight: 700; }
        .hm-hdr-dot {
          width: 10px; height: 10px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .hm-hdr-status { font-size: 12px; color: var(--secondary-text-color); }
        .hm-close {
          margin-left: 4px;
          background: none;
          border: none;
          font-size: 16px;
          cursor: pointer;
          color: var(--secondary-text-color);
          padding: 2px 6px;
          border-radius: 4px;
        }
        .hm-close:hover { background: var(--secondary-background-color); }
        .hm-section {
          border-top: 1px solid var(--divider-color, #e0e0e0);
          padding-top: 10px;
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .hm-section-title {
          font-size: 10px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--secondary-text-color);
        }
        .hm-manual-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        .hm-manual-label { font-size: 13px; }
        .hm-manual-btn {
          font-size: 12px;
          font-weight: 600;
          padding: 5px 12px;
          border-radius: 6px;
          border: none;
          cursor: pointer;
          flex-shrink: 0;
        }
        .hm-manual-on  { background: #FF9800; color: #fff; }
        .hm-manual-off { background: var(--secondary-background-color, #f0f0f0); color: var(--primary-text-color); }
        .hm-factors-row {
          display: flex;
          gap: 4px;
          align-items: flex-end;
        }
        .hm-factor {
          flex: 1;
          display: flex;
          flex-direction: column;
          align-items: center;
          border-radius: 6px;
          padding: 3px 4px;
          background: var(--secondary-background-color, #f0f0f0);
          position: relative;
          overflow: hidden;
          min-height: 52px;
        }
        .hm-factor-fill {
          position: absolute;
          bottom: 0; left: 0; right: 0;
          transition: height 0.6s ease;
        }
        .hm-factor-lbl { font-size: 9px; color: var(--secondary-text-color); white-space: nowrap; position: relative; }
        .hm-factor-val { font-size: 11px; font-weight: 700; position: relative; }
        .hm-factor-w   { font-size: 8px;  color: var(--secondary-text-color); position: relative; }
        .hm-factor-sep {
          display: flex; align-items: center;
          font-size: 12px; font-weight: 700;
          color: var(--secondary-text-color);
          flex-shrink: 0; padding-bottom: 4px;
        }
        .hm-reason { font-size: 12px; color: var(--secondary-text-color); }
        .hm-stat   { font-size: 12px; }
        .hm-progress-wrap { display: flex; flex-direction: column; gap: 4px; }
        .hm-bar-bg {
          width: 100%; height: 8px;
          background: var(--secondary-background-color, #f0f0f0);
          border-radius: 4px; overflow: hidden;
        }
        .hm-bar-fill { height: 100%; border-radius: 4px; transition: width 0.6s ease; }
        .hm-bar-text  { font-size: 12px; }
        .hm-force-lbl { font-size: 11px; color: #FF9800; font-weight: 600; }
        .hm-window { font-size: 14px; font-weight: 600; }
        .dev-row { cursor: pointer; }
        .dev-row:hover { background: var(--divider-color, #e8e8e8); }
        #h-node-bat { cursor: pointer; }
      </style>

      <div class="card">
        <button class="info-btn" id="h-info-btn" hidden>ℹ️</button>
        <div class="flow-wrap">
          ${this._buildSvg()}
        </div>

        <div class="energy-row" id="h-energy-row" style="display:none">
          <div class="energy-chip">
            <span class="energy-chip-lbl">☀️ PV</span>
            <span class="energy-chip-val" id="h-en-pv">—</span>
          </div>
          <div class="energy-chip">
            <span class="energy-chip-lbl">⬇️ Import</span>
            <span class="energy-chip-val" id="h-en-import">—</span>
          </div>
          <div class="energy-chip">
            <span class="energy-chip-lbl">⬆️ Export</span>
            <span class="energy-chip-val" id="h-en-export">—</span>
          </div>
          <div class="energy-chip">
            <span class="energy-chip-lbl">🏠 Conso</span>
            <span class="energy-chip-val" id="h-en-conso">—</span>
          </div>
        </div>

        <div class="savings-row" id="h-savings-row" style="display:none">
          <div class="savings-chip">
            <span class="savings-chip-lbl">💰 Économies auj.</span>
            <span class="savings-chip-val" id="h-sav-daily">—</span>
          </div>
          <div class="savings-chip">
            <span class="savings-chip-lbl">💰 Économies totales</span>
            <span class="savings-chip-val" id="h-sav-total">—</span>
          </div>
        </div>

        <div class="footer" id="h-footer">
          <div class="score-row">
            <div class="lbl">Score</div>
            <div class="bar-wrap">
              <div class="bar-bg"><div class="bar-fill" id="h-score-bar"></div></div>
            </div>
            <div class="score-num" id="h-score-num">—</div>
          </div>
          <div class="score-decomp" id="h-score-decomp">
            <div class="score-factor" id="h-sf-surplus">
              <div class="score-factor-fill" id="h-sf-surplus-fill"></div>
              <span class="score-factor-lbl">☀️ Surplus</span>
              <span class="score-factor-val" id="h-sf-surplus-val">—</span>
              <span class="score-factor-w" id="h-sf-surplus-w"></span>
            </div>
            <span class="score-sep">+</span>
            <div class="score-factor" id="h-sf-tempo">
              <div class="score-factor-fill" id="h-sf-tempo-fill"></div>
              <span class="score-factor-lbl">🎨 Tempo</span>
              <span class="score-factor-val" id="h-sf-tempo-val">—</span>
              <span class="score-factor-w" id="h-sf-tempo-w"></span>
            </div>
            <span class="score-sep">+</span>
            <div class="score-factor" id="h-sf-solar">
              <div class="score-factor-fill" id="h-sf-solar-fill"></div>
              <span class="score-factor-lbl">🌞 Solaire</span>
              <span class="score-factor-val" id="h-sf-solar-val">—</span>
              <span class="score-factor-w" id="h-sf-solar-w"></span>
            </div>
          </div>
          <div class="budget-row" id="h-budget-row">
            <div class="budget-chip">
              <span class="budget-chip-lbl">Surplus</span>
              <span class="budget-chip-val" id="h-bud-surplus">—</span>
            </div>
            <div class="budget-chip">
              <span class="budget-chip-lbl">Surplus virt.</span>
              <span class="budget-chip-val" id="h-bud-vsurplus">—</span>
            </div>
            <div class="budget-chip">
              <span class="budget-chip-lbl">Bat. dispo.</span>
              <span class="budget-chip-val" id="h-bud-bat">—</span>
            </div>
            <div class="budget-chip">
              <span class="budget-chip-lbl">Remaining</span>
              <span class="budget-chip-val" id="h-bud-rem">—</span>
            </div>
          </div>
          <div class="forecast" id="h-forecast" style="display:none">
            <div class="forecast-title">Prévision journalière</div>
            <div class="forecast-sub" id="h-forecast-sub"></div>
            <div class="forecast-chips">
              <div class="forecast-chip"><span class="forecast-chip-lbl">☀️ PV prévu</span><span class="forecast-chip-val" id="h-fc-pv">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">🏠 Conso</span><span class="forecast-chip-val" id="h-fc-conso">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">⬇️ Import</span><span class="forecast-chip-val" id="h-fc-import">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">⬆️ Export</span><span class="forecast-chip-val" id="h-fc-export">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">🔄 Autoconso</span><span class="forecast-chip-val" id="h-fc-sc">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">🛡️ Autosuff.</span><span class="forecast-chip-val" id="h-fc-ss">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">💶 Coût</span><span class="forecast-chip-val" id="h-fc-cost">—</span></div>
              <div class="forecast-chip"><span class="forecast-chip-lbl">💰 Économie</span><span class="forecast-chip-val" id="h-fc-save">—</span></div>
            </div>
          </div>
        </div>

        <div class="devices" id="h-devices" style="display:none"></div>
      </div>

      <div class="modal-overlay" id="h-modal" hidden>
        <div class="modal-box" id="h-modal-box"></div>
      </div>

      <div class="modal-overlay" id="h-bat-modal" hidden>
        <div class="modal-box" id="h-bat-modal-box"></div>
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

    // Batterie — nœud SVG cliquable
    this.shadowRoot.getElementById("h-node-bat").addEventListener("click", () => {
      this._openBatModal();
    });

    // Device row click → open modal (delegation sur l'élément stable #h-devices)
    // Ignore les clics sur les boutons d'action inline (ex. "Prêt !")
    this.shadowRoot.getElementById("h-devices").addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      const row = e.target.closest(".dev-row[data-slug]");
      if (row) {
        if (row.dataset.slug === "__battery__") this._openBatModal();
        else this._openModal(row.dataset.slug);
      }
    });

    // Modal actions (delegation sur l'overlay, attaché une seule fois)
    const modal = this.shadowRoot.getElementById("h-modal");
    modal.addEventListener("click", (e) => {
      if (e.target === modal) { this._closeModal(); return; }
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === "close") { this._closeModal(); return; }
      if (action === "toggle-manual" && this._modalSlug) {
        const { devices } = this._resolveAll();
        const dev = devices.find(d => this._devSlug(d) === this._modalSlug);
        if (dev) this._toggleManual(dev);
      }
      if (action === "ready") {
        const readyEntity = btn.dataset.readyEntity;
        if (readyEntity && this._hass)
          this._hass.callService("input_boolean", "turn_on", { entity_id: readyEntity });
      }
      if (action === "start-appliance") {
        const deviceEntity = btn.dataset.deviceEntity;
        if (deviceEntity && this._hass)
          this._hass.callService("helios", "start_appliance", { device_entity: deviceEntity });
      }
      if (action === "device-power") {
        const sw = btn.dataset.switchEntity;
        const power = btn.dataset.power;
        if (sw && this._hass)
          this._hass.callService("homeassistant", power === "on" ? "turn_on" : "turn_off", { entity_id: sw });
      }
      if (action === "pool-force-toggle") {
        const forceEntity = btn.dataset.forceEntity;
        const isOn = btn.dataset.forceOn === "true";
        if (forceEntity && this._hass)
          this._hass.callService("homeassistant", isOn ? "turn_off" : "turn_on", { entity_id: forceEntity });
      }
      if (action === "pool-duration") {
        const durEntity = btn.dataset.durEntity;
        const option    = btn.dataset.option;
        if (durEntity && option && this._hass)
          this._hass.callService("select", "select_option", { entity_id: durEntity, option });
      }
    });

    // Bat modal actions
    const batModal = this.shadowRoot.getElementById("h-bat-modal");
    batModal.addEventListener("click", (e) => {
      if (e.target === batModal) { this._closeBatModal(); return; }
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const action = btn.dataset.action;
      if (action === "close-bat") { this._closeBatModal(); return; }
      if (action === "toggle-bat-manual") {
        const sw = this._batManualSwitchEntity();
        if (sw && this._hass) {
          const isOn = this._hass.states[sw]?.state === "on";
          this._hass.callService("homeassistant", isOn ? "turn_off" : "turn_on", { entity_id: sw });
        }
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

  // Résout les entity_id des capteurs d'énergie journalière.
  // Priorité : auto-découverte via entry_id → patterns d'états → config manuelle.
  _resolveEnergyIds() {
    const cfg = this._config?.energy ?? {};
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc) {
        return {
          pv:          cfg.pv          ?? disc["energy_pv"]          ?? null,
          import:      cfg.import      ?? disc["energy_import"]       ?? null,
          export:      cfg.export      ?? disc["energy_export"]       ?? null,
          consumption: cfg.consumption ?? disc["energy_consumption"]  ?? null,
        };
      }
    }
    // Fallback — pattern sur les states (sans entry_id)
    const states = this._hass?.states;
    const fb = key => states?.[`sensor.helios_${key}`] ? `sensor.helios_${key}` : null;
    return {
      pv:          cfg.pv          ?? fb("energy_pv"),
      import:      cfg.import      ?? fb("energy_import"),
      export:      cfg.export      ?? fb("energy_export"),
      consumption: cfg.consumption ?? fb("energy_consumption"),
    };
  }

  // Résout les entity_id des capteurs d'économies (journalier + total).
  _resolveSavingsIds() {
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc) {
        return {
          daily: disc["daily_savings"] ?? null,
          total: disc["total_savings"] ?? null,
        };
      }
    }
    const states = this._hass?.states;
    const fb = key => states?.[`sensor.helios_${key}`] ? `sensor.helios_${key}` : null;
    return {
      daily: fb("daily_savings"),
      total: fb("total_savings"),
    };
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
    const compact = !!this._config.compact;

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

    // Dark-mode adaptive colors via CSS custom properties
    const gridColor = this._gridColor();
    this._svgAttr("h-arr-gin-path", "fill", gridColor);
    const isDark = gridColor !== "#7B1FA2";
    this._svgAttr("h-node-house", "fill",   isDark ? "#1B5E20" : "#E8F5E9");
    this._svgAttr("h-node-house", "stroke", isDark ? "#66BB6A" : "#388E3C");

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
        active: true, power: gridAbs, color: this._gridColor(), marker: "h-arr-gin",
        x1: lg.x1, y1: lg.y1, x2: lg.x2, y2: lg.y2,
        lblX: lg.lblX, lblY: lg.lblY, lblAnchor: "middle", lblColor: this._gridColor(),
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

    // Score decomposition chips
    const factors = [
      { key: "surplus", fAttr: "f_surplus", wAttr: "w_surplus" },
      { key: "tempo",   fAttr: "f_tempo",   wAttr: "w_tempo"   },
      { key: "solar",   fAttr: "f_solar",   wAttr: "w_solar"   },
    ];
    for (const { key, fAttr, wAttr } of factors) {
      const f = this._attr(e.score, fAttr);
      const w = this._attr(e.score, wAttr);
      const fColor = f === null ? "#9E9E9E" : f > 0.6 ? "#4CAF50" : f > 0.3 ? "#FF9800" : "#F44336";
      this._txt(`h-sf-${key}-val`, f !== null ? f.toFixed(2) : "—");
      this._txt(`h-sf-${key}-w`,   w !== null ? `×${w}` : "");
      const fill = this.shadowRoot.getElementById(`h-sf-${key}-fill`);
      if (fill) {
        fill.style.height     = f !== null ? `${Math.round(f * 100)}%` : "0%";
        fill.style.background = fColor + "33"; // 20% opacity
      }
      const valEl = this.shadowRoot.getElementById(`h-sf-${key}-val`);
      if (valEl) valEl.style.color = fColor;
    }

    // Budget chips
    const surplusW   = this._attr(e.score, "surplus_w");
    const vSurplusW  = this._attr(e.score, "virtual_surplus_w");
    const batAvailW  = this._attr(e.score, "bat_available_w");
    const remainingW = this._attr(e.score, "remaining_w");
    this._txt("h-bud-surplus",  surplusW  !== null ? this._fmt(surplusW)  : "—");
    this._txt("h-bud-vsurplus", vSurplusW !== null ? this._fmt(vSurplusW) : "—");
    this._txt("h-bud-bat",      batAvailW !== null ? this._fmt(batAvailW) : "—");
    this._txt("h-bud-rem",      remainingW !== null ? this._fmt(remainingW) : "—");

    // Score bar — couleur dynamique selon niveau
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

    // Daily energy section
    const energyRow = this.shadowRoot.getElementById("h-energy-row");
    if (energyRow && !compact) {
      const energyIds = this._resolveEnergyIds();
      const hasEnergy = Object.values(energyIds).some(Boolean);
      energyRow.style.display = hasEnergy ? "" : "none";
      if (hasEnergy) {
        const fmtE = eid => {
          if (!eid) return "—";
          const v = this._num(eid, null);
          return v !== null ? `${parseFloat(v).toFixed(1)} kWh` : "—";
        };
        this._txt("h-en-pv",     fmtE(energyIds.pv));
        this._txt("h-en-import", fmtE(energyIds.import));
        this._txt("h-en-export", fmtE(energyIds.export));
        this._txt("h-en-conso",  fmtE(energyIds.consumption));
      }
    } else if (energyRow && compact) {
      energyRow.style.display = "none";
    }

    // Savings section
    const savingsRow = this.shadowRoot.getElementById("h-savings-row");
    if (savingsRow && !compact) {
      const savingsIds = this._resolveSavingsIds();
      const hasSavings = savingsIds.daily || savingsIds.total;
      savingsRow.style.display = hasSavings ? "" : "none";
      if (hasSavings) {
        const fmtEur = eid => {
          if (!eid) return "—";
          const v = this._num(eid, null);
          return v !== null ? `${parseFloat(v).toFixed(2)} €` : "—";
        };
        this._txt("h-sav-daily", fmtEur(savingsIds.daily));
        this._txt("h-sav-total", fmtEur(savingsIds.total));
      }
    } else if (savingsRow && compact) {
      savingsRow.style.display = "none";
    }

    // Forecast section
    const forecastEl = this.shadowRoot.getElementById("h-forecast");
    const forecastEid = (() => {
      const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
      if (entryId) {
        const disc = this._discoverEntities(entryId);
        if (disc?.["forecast"]) return disc["forecast"];
      }
      const fb = "sensor.helios_forecast";
      return this._hass?.states[fb] ? fb : null;
    })();
    const hasForecast = forecastEid && this._hass?.states[forecastEid]?.state !== "unavailable";
    if (forecastEl) forecastEl.style.display = hasForecast ? "" : "none";
    if (hasForecast) {
      const fmtKwh  = v => v !== null ? `${parseFloat(v).toFixed(1)} kWh` : "—";
      const fmtPct  = v => v !== null ? `${Math.round(v)} %` : "—";
      const fmtEur  = v => v !== null ? `${parseFloat(v).toFixed(2)} €` : "—";
      this._txt("h-fc-pv",     fmtKwh(this._attr(forecastEid, "forecast_pv_kwh")));
      this._txt("h-fc-conso",  fmtKwh(this._attr(forecastEid, "forecast_consumption_kwh")));
      this._txt("h-fc-import", fmtKwh(this._attr(forecastEid, "forecast_import_kwh")));
      this._txt("h-fc-export", fmtKwh(this._attr(forecastEid, "forecast_export_kwh")));
      this._txt("h-fc-sc",     fmtPct(this._attr(forecastEid, "forecast_self_consumption_pct")));
      this._txt("h-fc-ss",     fmtPct(this._attr(forecastEid, "forecast_self_sufficiency_pct")));
      this._txt("h-fc-cost",   fmtEur(this._attr(forecastEid, "forecast_cost")));
      this._txt("h-fc-save",   fmtEur(this._attr(forecastEid, "forecast_savings")));
      const lastFc = this._attr(forecastEid, "last_forecast");
      if (lastFc) {
        const d = new Date(lastFc);
        const sub = isNaN(d) ? lastFc
          : `Prévision du ${d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
        this._txt("h-forecast-sub", sub);
      }
    }

    // Compact: marge réduite + masquer footer + devices
    const cardEl   = this.shadowRoot.querySelector(".card");
    if (cardEl) compact ? cardEl.setAttribute("data-compact", "") : cardEl.removeAttribute("data-compact");
    const scoreDecomp = this.shadowRoot.getElementById("h-score-decomp");
    if (scoreDecomp) scoreDecomp.style.display = compact ? "none" : "";
    const budgetRow = this.shadowRoot.getElementById("h-budget-row");
    if (budgetRow) budgetRow.style.display = compact ? "none" : "";
    if (forecastEl && compact) forecastEl.style.display = "none";
    const devices = this.shadowRoot.getElementById("h-devices");
    if (devices && compact) devices.style.display = "none";

    // Devices section (full mode uniquement)
    if (!compact) this._updateDevices(discoveredDevices, e);

    // Rafraîchit les modales si elles sont ouvertes
    if (this._modalSlug) this._refreshModal();
    if (this._batModalOpen) this._refreshBatModal();
  }

  // ------------------------------------------------------------------ Devices
  _updateDevices(devCfgs, entityRefs = {}) {
    const devicesEl = this.shadowRoot.getElementById("h-devices");
    if (!devicesEl) return;
    const batRow = this._renderBatteryRow(entityRefs);
    if (devCfgs.length === 0 && !batRow) { devicesEl.style.display = "none"; return; }
    devicesEl.style.display = "flex";
    const sorted = [...devCfgs].sort((a, b) => {
      const sa = this._attr(a.entity, "last_effective_score") ?? -1;
      const sb = this._attr(b.entity, "last_effective_score") ?? -1;
      return sb - sa;
    });
    devicesEl.innerHTML = (batRow ?? "") + sorted.map(d => this._renderDevice(d)).join("");
    devicesEl.querySelectorAll(".dev-ready-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const entityId = btn.dataset.readyEntity;
        if (entityId && this._hass) {
          this._hass.callService("input_boolean", "turn_on", { entity_id: entityId });
        }
      });
    });
  }

  _renderBatteryRow(entityRefs) {
    const batEid = entityRefs?.battery;
    if (!batEid) return null;
    const enabled = this._attr(batEid, "battery_enabled");
    if (enabled === false) return null;

    const action  = this._str(batEid) ?? "idle";
    const soc     = this._attr(batEid, "soc");
    const powerW  = this._attr(batEid, "power_w");
    const manual  = this._hass?.states[this._batManualSwitchEntity()]?.state === "on";

    const actionLabel = { charge: "En charge", discharge: "Décharge", reserve: "Réserve", idle: "Veille" }[action] ?? action;
    const dotColor    = manual ? "#FF9800"
      : { charge: "#1565C0", discharge: "#0288D1", reserve: "#FF9800", idle: "#9E9E9E" }[action] ?? "#9E9E9E";
    const statusText  = manual ? "Manuel" : actionLabel;

    const socColor = soc === null ? "#9E9E9E" : soc > 60 ? "#4CAF50" : soc > 20 ? "#FF9800" : "#F44336";
    const detail   = soc !== null ? `SOC : <span style="color:${socColor};font-weight:700">${Math.round(soc)} %</span>` : "";
    const powerHtml = (action === "charge" || action === "discharge") && powerW !== null && Math.abs(powerW) > 5
      ? `<span class="dev-power">${this._fmt(Math.abs(powerW))}</span>` : "";

    return `
      <div class="dev-row" data-slug="__battery__">
        <div class="dev-icon">🔋</div>
        <div class="dev-info">
          <div class="dev-name-row"><span class="dev-name">Batterie</span></div>
          ${detail ? `<div class="dev-detail">${detail}</div>` : ""}
        </div>
        <div class="dev-status">
          <div class="dot" style="background:${dotColor}"></div>
          <span class="dev-status-text">${statusText}</span>
          ${powerHtml}
        </div>
      </div>`;
  }

  _renderDevice(dev) {
    const icon     = dev.icon || this._defaultIcon(dev.type);
    const isOn     = this._deviceIsOn(dev);
    const reason   = this._attr(dev.entity, "reason") ?? "";
    const priority = this._attr(dev.entity, "device_priority") ?? dev.priority ?? null;
    const detail   = this._deviceDetail(dev);

    // Status dot + label
    const { dotColor, statusText } = this._deviceStatus(dev, isOn);

    // Current power from sensor attribute
    let powerHtml = "";
    if (isOn) {
      const pw = this._attr(dev.entity, "power_w");
      if (pw !== null && pw > 5) powerHtml = `<span class="dev-power">${this._fmt(pw)}</span>`;
    }

    const priorityHtml = priority !== null ? `<span class="dev-priority">P${priority}</span>` : "";
    const reasonHtml   = reason ? `<div class="dev-reason">${this._reasonLabel(reason)}</div>` : "";

    // Appliance "ready" button — visible only when idle and ready_entity is configured
    const applianceState = dev.type === "appliance" ? this._attr(dev.entity, "appliance_state") : null;
    const readyEntity    = dev.type === "appliance" ? this._attr(dev.entity, "appliance_ready_entity") : null;
    const showReadyBtn   = this._str(dev.entity) === "off" && applianceState === "idle" && readyEntity;
    const readyBtnHtml   = showReadyBtn
      ? `<button class="dev-ready-btn" data-ready-entity="${readyEntity}">Prêt !</button>`
      : "";

    const slug = this._devSlug(dev);
    return `
      <div class="dev-row" data-slug="${slug}">
        <div class="dev-icon">${icon}</div>
        <div class="dev-info">
          <div class="dev-name-row">
            <span class="dev-name">${dev.name || ""}</span>
            ${priorityHtml}
          </div>
          ${detail ? `<div class="dev-detail">${detail}</div>` : ""}
          ${reasonHtml}
        </div>
        <div class="dev-status">
          <div class="dot" style="background:${dotColor}"></div>
          <span class="dev-status-text">${statusText}</span>
          ${powerHtml}
        </div>
        ${readyBtnHtml}
      </div>
    `;
  }

  _deviceIsOn(dev) {
    const st = this._str(dev.entity);
    return st === "on" || st === "running";
  }

  _deviceStatus(dev, _isOn) {
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
    const st2 = this._str(dev.entity);
    return (st2 === "on" || st2 === "running")
      ? { dotColor: "#4CAF50", statusText: "En marche" }
      : { dotColor: "#9E9E9E", statusText: "OFF" };
  }

  _deviceDetail(dev) {
    const parts = [];

    // Type-specific primary info
    switch (dev.type) {
      case "pool": {
        const doneMin = this._attr(dev.entity, "filtration_done_min");
        const reqMin  = this._attr(dev.entity, "filtration_required_min");
        if (doneMin !== null && reqMin !== null) {
          let s = `${(doneMin / 60).toFixed(1)}h / ${(reqMin / 60).toFixed(1)}h`;
          const forceRem = this._attr(dev.entity, "force_remaining_min") ?? 0;
          if (forceRem > 1) s += ` 🔒 ${Math.round(forceRem)} min`;
          parts.push(s);
        }
        break;
      }
      case "water_heater": {
        const temp = this._attr(dev.entity, "temperature");
        if (temp !== null) {
          const target = this._attr(dev.entity, "wh_temp_target");
          parts.push(target !== null ? `${temp.toFixed(1)}°C / ${target}°C` : `${temp.toFixed(1)}°C`);
        }
        break;
      }
      case "ev":
      case "ev_charger": {
        const soc = this._attr(dev.entity, "soc");
        if (soc !== null) parts.push(`SOC : ${Math.round(soc)}%`);
        if (this._attr(dev.entity, "plugged") === false) return parts.join(" · ");
        break;
      }
    }

    // Daily on-time (for non-pool types, pool already shows done/required)
    if (dev.type !== "pool") {
      const dailyMin = this._attr(dev.entity, "daily_on_minutes");
      if (dailyMin !== null && dailyMin > 0) {
        parts.push(`${(dailyMin / 60).toFixed(1)}h auj.`);
      }
    }

    // Allowed window (only if not the default full-day 00:00–24:00)
    const start = this._attr(dev.entity, "allowed_start");
    const end   = this._attr(dev.entity, "allowed_end");
    if (start && end && !(start === "00:00" && (end === "24:00" || end === "23:59"))) {
      parts.push(`${start}–${end}`);
    }

    return parts.join(" · ");
  }

  _defaultIcon(type) {
    const icons = { pool: "🏊", water_heater: "🌡️", appliance: "🫧", ev: "🚗", ev_charger: "🚗" };
    return icons[type] ?? "🔌";
  }

  _reasonLabel(reason) {
    const map = {
      urgency:        "Urgence",
      greedy:         "Surplus",
      satisfied:      "Satisfait",
      budget:         "Budget",
      off_hours:      "Hors plage",
      manual:         "Manuel",
      // rétrocompat (anciens logs)
      must_run:       "Forcé",
      dispatch:       "Surplus",
      no_budget:      "Budget",
      outside_window: "Hors plage",
    };
    return map[reason] ?? reason;
  }

  // ------------------------------------------------------------------ Modal
  _devSlug(dev) {
    return dev.entity.replace(/^sensor\.helios_/, "");
  }

  _poolForceEntity(dev) {
    const slug = this._devSlug(dev);
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc?.[`pool_${slug}_force`]) return disc[`pool_${slug}_force`];
    }
    const candidate = `switch.helios_${slug}_force`;
    return this._hass?.states[candidate] !== undefined ? candidate : null;
  }

  _poolDurationEntity(dev) {
    const slug = this._devSlug(dev);
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc?.[`pool_${slug}_force_duration`]) return disc[`pool_${slug}_force_duration`];
    }
    const candidate = `select.helios_${slug}_force_duration`;
    return this._hass?.states[candidate] !== undefined ? candidate : null;
  }

  _manualSwitchEntity(dev) {
    const slug = this._devSlug(dev);
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc?.[`device_${slug}_manual`]) return disc[`device_${slug}_manual`];
    }
    const candidate = `switch.helios_${slug}_manual`;
    return this._hass?.states[candidate] !== undefined ? candidate : null;
  }

  _toggleManual(dev) {
    const sw = this._manualSwitchEntity(dev);
    if (!sw || !this._hass) return;
    const isOn = this._hass.states[sw]?.state === "on";
    this._hass.callService("homeassistant", isOn ? "turn_off" : "turn_on", { entity_id: sw });
  }

  _openModal(slug) {
    this._modalSlug = slug;
    const modal = this.shadowRoot.getElementById("h-modal");
    if (modal) {
      modal.removeAttribute("hidden");
      this._refreshModal();
    }
  }

  _closeModal() {
    this._modalSlug = null;
    const modal = this.shadowRoot.getElementById("h-modal");
    if (modal) modal.setAttribute("hidden", "");
  }

  _refreshModal() {
    if (!this._modalSlug) return;
    const { devices } = this._resolveAll();
    const dev = devices.find(d => this._devSlug(d) === this._modalSlug);
    const box = this.shadowRoot.getElementById("h-modal-box");
    if (!dev || !box) return;
    box.innerHTML = this._buildModalContent(dev);
  }

  _buildModalContent(dev) {
    const icon    = dev.icon || this._defaultIcon(dev.type);
    const isOn    = this._deviceIsOn(dev);
    const { dotColor, statusText } = this._deviceStatus(dev, isOn);
    const manual  = this._attr(dev.entity, "manual_mode") === true;
    const swEntity = this._manualSwitchEntity(dev);

    // En-tête
    const headerHtml = `
      <div class="hm-header">
        <span class="hm-icon">${icon}</span>
        <span class="hm-title">${dev.name || ""}</span>
        <div class="hm-hdr-dot" style="background:${dotColor}"></div>
        <span class="hm-hdr-status">${statusText}</span>
        <button class="hm-close" data-action="close">✕</button>
      </div>`;

    // Contrôle manuel
    const switchEntity = this._attr(dev.entity, "switch_entity");
    const deviceIsOn   = this._deviceIsOn(dev);
    const onOffHtml = (manual && switchEntity) ? `
      <div class="hm-manual-row" style="margin-top:6px">
        <span class="hm-manual-label">Commande directe</span>
        <div style="display:flex;gap:6px">
          <button class="hm-manual-btn ${deviceIsOn ? "hm-manual-on" : "hm-manual-off"}"
            data-action="device-power" data-switch-entity="${switchEntity}" data-power="on">ON</button>
          <button class="hm-manual-btn ${!deviceIsOn ? "hm-manual-on" : "hm-manual-off"}"
            data-action="device-power" data-switch-entity="${switchEntity}" data-power="off">OFF</button>
        </div>
      </div>` : "";
    const manualHtml = swEntity ? `
      <div class="hm-section">
        <div class="hm-section-title">Contrôle</div>
        <div class="hm-manual-row">
          <span class="hm-manual-label">${manual ? "Mode manuel actif" : "Mode automatique"}</span>
          <button class="hm-manual-btn ${manual ? "hm-manual-on" : "hm-manual-off"}" data-action="toggle-manual">
            ${manual ? "Repasser en auto" : "Forcer manuel"}
          </button>
        </div>
        ${onOffHtml}
      </div>` : "";

    // Détail type-spécifique
    // Puissance actuelle — affichée pour tout appareil en marche
    const powerW   = this._attr(dev.entity, "power_w");
    const powerHtml = (isOn && powerW !== null && powerW > 5)
      ? `<div class="hm-stat" style="font-weight:700;color:#4CAF50">⚡ ${this._fmt(powerW)}</div>`
      : "";

    const detailBody = this._buildModalDetail(dev);
    const detailHtml = (detailBody || powerHtml) ? `
      <div class="hm-section">
        <div class="hm-section-title">État</div>
        ${powerHtml}
        ${detailBody}
      </div>` : "";

    // Score de l'appareil — composantes propres (pas les facteurs globaux)
    const effScore     = this._attr(dev.entity, "last_effective_score");
    const priorityScore= this._attr(dev.entity, "last_priority_score");
    const fit          = this._attr(dev.entity, "last_fit");
    const urgency      = this._attr(dev.entity, "last_urgency");
    const reason       = this._attr(dev.entity, "reason");
    const dailyMin     = this._attr(dev.entity, "daily_on_minutes");
    const priority     = this._attr(dev.entity, "device_priority") ?? dev.priority;

    const devFactors = [
      { label: "🎯 Priorité", val: priorityScore, w: 0.4 },
      { label: "⚡ Fit",      val: fit,           w: 0.3 },
      { label: "⏱ Urgence",  val: urgency,       w: 0.3 },
    ];
    const factorChips = devFactors.map(({ label, val, w }) => {
      const fc = val === null ? "#9E9E9E" : val > 0.6 ? "#4CAF50" : val > 0.3 ? "#FF9800" : "#F44336";
      return `
        <div class="hm-factor">
          <div class="hm-factor-fill" style="height:${val !== null ? Math.round(val * 100) : 0}%;background:${fc}33"></div>
          <span class="hm-factor-lbl">${label}</span>
          <span class="hm-factor-val" style="color:${fc}">${val !== null ? val.toFixed(2) : "—"}</span>
          <span class="hm-factor-w">×${w}</span>
        </div>`;
    }).join('<span class="hm-factor-sep">+</span>');

    const titleScore = effScore !== null
      ? `Score — effectif : <strong>${effScore.toFixed(3)}</strong>`
      : "Score";
    const reasonHtml = reason
      ? `<div class="hm-reason">Décision : ${this._reasonLabel(reason)}</div>` : "";
    const dailyHtml = dailyMin !== null && dailyMin > 0
      ? `<div class="hm-stat">Aujourd'hui : ${(dailyMin / 60).toFixed(1)} h ON</div>` : "";
    const priorityHtml = priority !== null
      ? `<div class="hm-stat">Priorité : P${priority} / 10</div>` : "";

    const scoreHtml = `
      <div class="hm-section">
        <div class="hm-section-title">${titleScore}</div>
        <div class="hm-factors-row">${factorChips}</div>
        ${reasonHtml}${dailyHtml}${priorityHtml}
      </div>`;

    // Plage horaire (uniquement si non 00:00–24:00)
    const start = this._attr(dev.entity, "allowed_start");
    const end   = this._attr(dev.entity, "allowed_end");
    const hasWindow = start && end && !(start === "00:00" && (end === "24:00" || end === "23:59"));
    const windowHtml = hasWindow ? `
      <div class="hm-section">
        <div class="hm-section-title">Plage autorisée</div>
        <div class="hm-window">🕐 ${start} → ${end}</div>
      </div>` : "";

    return headerHtml + manualHtml + detailHtml + scoreHtml + windowHtml;
  }

  _buildModalDetail(dev) {
    switch (dev.type) {
      case "pool": {
        const doneMin  = this._attr(dev.entity, "filtration_done_min");
        const reqMin   = this._attr(dev.entity, "filtration_required_min");
        const forceRem = this._attr(dev.entity, "force_remaining_min") ?? 0;
        if (doneMin === null || reqMin === null) return "";
        const pct      = Math.min(100, reqMin > 0 ? Math.round(doneMin / reqMin * 100) : 100);
        const barColor = pct >= 100 ? "#4CAF50" : "#2196F3";

        // Forçage — entités découvertes dynamiquement
        const forceEid    = this._poolForceEntity(dev);
        const durEid      = this._poolDurationEntity(dev);
        const forceIsOn   = forceEid ? this._hass?.states[forceEid]?.state === "on" : false;
        const curDuration = durEid ? (this._hass?.states[durEid]?.state ?? "2h") : "2h";
        const durOptions  = durEid ? (this._hass?.states[durEid]?.attributes?.options ?? ["1h","2h","4h","12h","24h"]) : ["1h","2h","4h","12h","24h"];

        const durChips = forceEid ? durOptions.map(opt => `
          <button class="hm-manual-btn ${opt === curDuration ? "hm-manual-on" : "hm-manual-off"}"
            data-action="pool-duration" data-dur-entity="${durEid}" data-option="${opt}"
            style="padding:3px 8px;font-size:11px">${opt}</button>`
        ).join("") : "";

        const forceToggleBtn = forceEid ? `
          <button class="hm-manual-btn ${forceIsOn ? "hm-manual-on" : "hm-manual-off"}"
            data-action="pool-force-toggle" data-force-entity="${forceEid}" data-force-on="${forceIsOn}">
            ${forceIsOn ? "🔒 Arrêter le forçage" : "🔒 Forcer maintenant"}
          </button>` : "";

        const forceHtml = forceEid ? `
          <div class="hm-section-title" style="margin-top:8px">Forçage</div>
          ${forceIsOn ? `<span class="hm-force-lbl">🔒 Forcé — ${Math.round(forceRem)} min restantes</span>` : ""}
          <div style="display:flex;gap:4px;flex-wrap:wrap;margin:4px 0">${durChips}</div>
          <div>${forceToggleBtn}</div>` : "";

        return `
          <div class="hm-progress-wrap">
            <div class="hm-bar-bg"><div class="hm-bar-fill" style="width:${pct}%;background:${barColor}"></div></div>
            <span class="hm-bar-text">Filtration : ${(doneMin / 60).toFixed(1)} h / ${(reqMin / 60).toFixed(1)} h (${pct} %)</span>
          </div>
          ${forceHtml}`;
      }
      case "water_heater": {
        const temp   = this._attr(dev.entity, "temperature");
        const target = this._attr(dev.entity, "wh_temp_target");
        if (temp === null) return "";
        const pct      = target ? Math.min(100, Math.round(temp / target * 100)) : null;
        const barColor = target
          ? (temp >= target ? "#4CAF50" : temp > target * 0.8 ? "#FF9800" : "#F44336")
          : "#2196F3";
        return `
          <div class="hm-progress-wrap">
            ${pct !== null ? `<div class="hm-bar-bg"><div class="hm-bar-fill" style="width:${pct}%;background:${barColor}"></div></div>` : ""}
            <span class="hm-bar-text">Température : ${temp.toFixed(1)} °C${target !== null ? ` / ${target} °C` : ""}</span>
          </div>`;
      }
      case "ev":
      case "ev_charger": {
        const soc     = this._attr(dev.entity, "soc");
        const plugged = this._attr(dev.entity, "plugged");
        const parts   = [];
        if (plugged === false) parts.push("Non branché");
        if (soc !== null) {
          const sc = soc > 60 ? "#4CAF50" : soc > 20 ? "#FF9800" : "#F44336";
          parts.push(`<span style="color:${sc}">SOC : ${Math.round(soc)} %</span>`);
        }
        return parts.length ? `<div class="hm-stat">${parts.join(" · ")}</div>` : "";
      }
      case "appliance": {
        const appState  = this._attr(dev.entity, "appliance_state");
        const readyEnt  = this._attr(dev.entity, "appliance_ready_entity");
        const showReady = this._str(dev.entity) === "off" && appState === "idle" && readyEnt;
        const stateLabel = {
          idle:      "Inactif",
          preparing: "En attente",
          running:   "En marche",
          done:      "Cycle terminé",
        }[appState] ?? appState ?? "—";

        // Deadline + bouton "Lancer maintenant" — uniquement en état preparing
        const isPreparing = appState === "preparing";
        const deadlineIso = isPreparing ? this._attr(dev.entity, "appliance_deadline") : null;
        let deadlineHtml = "";
        if (deadlineIso) {
          const d = new Date(deadlineIso);
          const timeStr = isNaN(d) ? deadlineIso : d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
          deadlineHtml = `<div class="hm-stat" style="color:#FF9800;font-weight:600">⏰ Avant ${timeStr}</div>`;
        }
        const startNowHtml = isPreparing
          ? `<button class="hm-manual-btn hm-manual-on" data-action="start-appliance" data-device-entity="${dev.entity}">▶ Lancer maintenant</button>`
          : "";

        return `
          <div class="hm-manual-row">
            <span class="hm-stat">${stateLabel}</span>
            ${showReady ? `<button class="dev-ready-btn" data-action="ready" data-ready-entity="${readyEnt}">Prêt !</button>` : ""}
            ${startNowHtml}
          </div>
          ${deadlineHtml}`;
      }
      default:
        return "";
    }
  }

  // ------------------------------------------------------------------ Bat modal
  _batManualSwitchEntity() {
    const entryId = this._config?.entry_id ?? this._autoDiscoverEntryId();
    if (entryId) {
      const disc = this._discoverEntities(entryId);
      if (disc?.["battery_manual"]) return disc["battery_manual"];
    }
    const candidate = "switch.helios_battery_manual";
    return this._hass?.states[candidate] !== undefined ? candidate : null;
  }

  _openBatModal() {
    this._batModalOpen = true;
    const modal = this.shadowRoot.getElementById("h-bat-modal");
    if (modal) {
      modal.removeAttribute("hidden");
      this._refreshBatModal();
    }
  }

  _closeBatModal() {
    this._batModalOpen = false;
    const modal = this.shadowRoot.getElementById("h-bat-modal");
    if (modal) modal.setAttribute("hidden", "");
  }

  _refreshBatModal() {
    const box = this.shadowRoot.getElementById("h-bat-modal-box");
    if (!box) return;
    box.innerHTML = this._buildBatModalContent();
  }

  _buildBatModalContent() {
    const { entityRefs: e } = this._resolveAll();
    const battAction = this._str(e.battery) ?? "idle";
    const soc        = this._attr(e.battery, "soc");
    const powerW     = this._attr(e.battery, "power_w");
    const availW     = this._attr(e.battery, "available_w");
    const urgency    = this._attr(e.battery, "urgency");
    const fit        = this._attr(e.battery, "fit");
    const priority   = this._attr(e.battery, "priority");
    const effScore   = this._attr(e.battery, "effective_score");

    const sw     = this._batManualSwitchEntity();
    const manual = sw ? this._hass?.states[sw]?.state === "on" : false;

    const actionDotColor = {
      charge:    "#1565C0",
      discharge: "#0288D1",
      reserve:   "#FF9800",
      idle:      "#9E9E9E",
    }[battAction] ?? "#9E9E9E";

    const actionLabel = {
      charge:    "En charge",
      discharge: "Décharge",
      reserve:   "Réserve",
      idle:      "Veille",
    }[battAction] ?? battAction;

    // SOC bar
    const socColor = soc === null ? "#9E9E9E" : soc > 60 ? "#4CAF50" : soc > 20 ? "#FF9800" : "#F44336";
    const socPct   = soc !== null ? Math.round(soc) : 0;
    const socBar   = `
      <div class="hm-progress-wrap">
        <div class="hm-bar-bg"><div class="hm-bar-fill" style="width:${socPct}%;background:${socColor}"></div></div>
        <span class="hm-bar-text">SOC : ${soc !== null ? socPct + " %" : "—"}</span>
      </div>`;

    const controlHtml = sw ? `
      <div class="hm-section">
        <div class="hm-section-title">Contrôle</div>
        <div class="hm-manual-row">
          <span class="hm-manual-label">${manual ? "Mode manuel actif" : "Mode automatique"}</span>
          <button class="hm-manual-btn ${manual ? "hm-manual-on" : "hm-manual-off"}" data-action="toggle-bat-manual">
            ${manual ? "Repasser en auto" : "Forcer manuel"}
          </button>
        </div>
      </div>` : "";

    // État — SOC + puissances
    const stateHtml = `
      <div class="hm-section">
        <div class="hm-section-title">État</div>
        ${socBar}
        <div class="hm-stat">Demande : ${powerW !== null ? this._fmt(powerW) : "—"}</div>
        <div class="hm-stat">Disponible : ${availW !== null ? this._fmt(availW) : "—"}</div>
      </div>`;

    // Score — même structure que les devices (3 facteurs + score effectif + priorité)
    const batFactors = [
      { label: "🎯 Priorité", val: priority !== null ? priority / 10 : null, w: 0.4 },
      { label: "⚡ Fit",      val: fit,                                      w: 0.3 },
      { label: "⏱ Urgence",  val: urgency,                                  w: 0.3 },
    ];
    const factorChips = batFactors.map(({ label, val, w }) => {
      const fc = val === null ? "#9E9E9E" : val > 0.6 ? "#4CAF50" : val > 0.3 ? "#FF9800" : "#F44336";
      return `
        <div class="hm-factor">
          <div class="hm-factor-fill" style="height:${val !== null ? Math.round(val * 100) : 0}%;background:${fc}33"></div>
          <span class="hm-factor-lbl">${label}</span>
          <span class="hm-factor-val" style="color:${fc}">${val !== null ? val.toFixed(2) : "—"}</span>
          <span class="hm-factor-w">×${w}</span>
        </div>`;
    }).join('<span class="hm-factor-sep">+</span>');

    const titleScore = effScore !== null
      ? `Score — effectif : <strong>${effScore.toFixed(3)}</strong>`
      : "Score";
    const priorityHtml = priority !== null
      ? `<div class="hm-stat">Priorité : P${priority} / 10</div>` : "";

    const scoreHtml = `
      <div class="hm-section">
        <div class="hm-section-title">${titleScore}</div>
        <div class="hm-factors-row">${factorChips}</div>
        ${priorityHtml}
      </div>`;

    return `
      <div class="hm-header">
        <span class="hm-icon">🔋</span>
        <span class="hm-title">Batterie</span>
        <div class="hm-hdr-dot" style="background:${actionDotColor}"></div>
        <span class="hm-hdr-status">${actionLabel}</span>
        <button class="hm-close" data-action="close-bat">✕</button>
      </div>
      ${controlHtml}
      ${stateHtml}
      ${scoreHtml}`;
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
