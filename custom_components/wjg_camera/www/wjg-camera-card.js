/**
 * wjg-camera-card.js  v3.2  — Maximum Edition
 * Einzel-Stream Kamera-Karte für Home Assistant / WJG XM-3820
 *
 * NEU in v3.2:
 *  • PTZ-Overlay unterstützt jetzt echte button.press Entities (ptz_entities Map)
 *  • Rückwärtskompatibel: ptz_service funktioniert weiterhin als Fallback
 *
 * NEU in v3.1:
 *  • Stufenloser Zoom: von technischem Minimum (0.25×) bis Maximum (64×)
 *  • Kein fester Stufen-Raster mehr — Slider ist vollständig kontinuierlich
 *  • Zoom-Preset-Buttons ersetzt durch −/+ Buttons + Reset + Freitext-Eingabe
 *  • Video-Download-Button (Stream-Recording via MediaRecorder API)
 *  • Keyboard-Hilfe-Panel (?) mit allen Kürzeln sichtbar im UI
 *  • Minimap-Toggle-Button im Toolbar sichtbar
 *  • Overlay-Modus wird als Text-Label im Zoom-Bar angezeigt
 *  • Zoom-Anzeige mit 2 Dezimalstellen für feine Kontrolle
 *  • max_zoom / min_zoom per Config oder unbegrenzt (Standardwert 64×)
 *  • Alle vorhandenen Features nun über UI erreichbar (kein verstecktes Feature mehr)
 *
 * Bestehende Features (v3.0):
 *  • Unified Pointer Events API (Mouse + Touch + Stylus)
 *  • Momentum-Panning mit Trägheit (velocity EMA)
 *  • Doppelklick / Doppel-Tap zum Zoom an Cursor-Position
 *  • Sanfte Zoom-Animation (ease-out cubic, requestAnimationFrame)
 *  • Vollbild-Modus (Fullscreen API, ESC-Taste)
 *  • Snapshot / Screenshot (Canvas-Capture, Fallback entity_picture)
 *  • Vollständige Tastatursteuerung (Pfeiltasten, +/−, R, F, S, G, P, ?)
 *  • ResizeObserver für reaktionsfähiges Layout
 *  • PTZ-Overlay mit 8 Richtungen + Zoom In/Out
 *  • Badge-System mit Tooltip, Click-Action, state_class
 *  • Ladezustand / Fehler / Offline Overlay mit Spinner
 *  • Status-Dot (grün/rot/orange)
 *  • Overlay-Modi: Fadenkreuz | Raster | Drittel-Regel
 *  • Minimap bei Zoom > 1.5×
 *  • Throttled _syncServer (100 ms)
 *  • Debounced localStorage (300 ms)
 *  • Resize-Handles (Höhe, Breite, Ecke)
 *  • Vollständige Speicherbereinigung in disconnectedCallback
 *
 * Konfigurationsbeispiel (YAML):
 *   type: custom:wjg-camera-card
 *   entity: camera.wjg_xm_3820
 *   title: Einfahrt
 *   show_zoom_bar: true
 *   show_minimap: true
 *   show_ptz: false
 *   min_zoom: 0.5
 *   max_zoom: 64
 *   aspect_ratio: "16/9"
 *   ptz_entities:
 *     up:         button.wjg_xm_3820_ptz_up
 *     down:       button.wjg_xm_3820_ptz_down
 *     left:       button.wjg_xm_3820_ptz_left
 *     right:      button.wjg_xm_3820_ptz_right
 *     up_left:    button.wjg_xm_3820_ptz_up
 *     up_right:   button.wjg_xm_3820_ptz_up
 *     down_left:  button.wjg_xm_3820_ptz_down
 *     down_right: button.wjg_xm_3820_ptz_down
 *     home:       button.wjg_xm_3820_ptz_home
 *     zoom_in:    button.wjg_xm_3820_ptz_zoom_in
 *     zoom_out:   button.wjg_xm_3820_ptz_zoom_out
 *   badges:
 *     - entity: binary_sensor.motion_einfahrt
 *       icon: mdi:motion-sensor
 *       label: Bewegung
 *     - entity: switch.aufnahme
 *       icon: mdi:record-circle
 *       label: Aufnahme
 *       action: switch.toggle
 */

// ─────────────────────────────── Template ────────────────────────────────────
const _tpl = document.createElement('template');
_tpl.innerHTML = `
<style>
  :host {
    display: block;
    width: 100%;
    background: var(--ha-card-background, #1c1c1e);
    border-radius: var(--ha-card-border-radius, 12px);
    overflow: hidden;
    box-shadow: var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,.4));
    position: relative;
    user-select: none;
    contain: layout style;
  }

  /* ── Title Bar ─────────────────────────────────────────────── */
  #title-bar {
    display: none;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px 4px;
    color: var(--primary-text-color, #fff);
    font-size: 14px;
    font-weight: 500;
    font-family: var(--paper-font-body1_-_font-family, sans-serif);
    gap: 8px;
  }
  #title-bar.visible { display: flex; }
  #title-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #status-dot {
    flex-shrink: 0;
    width: 8px; height: 8px; border-radius: 50%;
    background: #444;
    transition: background .4s, box-shadow .4s;
  }
  #status-dot.online  { background: #4caf50; box-shadow: 0 0 6px #4caf5088; }
  #status-dot.offline { background: #f44336; }
  #status-dot.loading { background: #ff9800; animation: _pulse .8s ease-in-out infinite alternate; }

  @keyframes _pulse { from { opacity: .35 } to { opacity: 1 } }
  @keyframes _spin  { to { transform: rotate(360deg) } }
  @keyframes _blink { 0%,100%{opacity:1} 50%{opacity:.35} }
  @keyframes _fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }

  /* ── Viewport ──────────────────────────────────────────────── */
  #vp {
    position: relative;
    overflow: hidden;
    width: 100%;
    aspect-ratio: 16 / 9;
    cursor: grab;
    touch-action: none;
    background: #000;
    outline: none;
  }
  #vp:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: -2px; }
  #vp.dragging      { cursor: grabbing; }
  #vp.zoom1         { cursor: default; }
  #vp.custom-height { aspect-ratio: unset; }

  /* ── Inner (transformed layer) ─────────────────────────────── */
  #inner {
    position: absolute; inset: 0;
    transform-origin: 0 0;
    will-change: transform;
  }
  ha-camera-stream {
    display: block; width: 100%; height: 100%; pointer-events: none;
  }
  #snap {
    display: block; width: 100%; height: 100%;
    object-fit: contain; pointer-events: none;
  }

  /* ── Status Overlay ────────────────────────────────────────── */
  #status-overlay {
    position: absolute; inset: 0; z-index: 30;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    background: rgba(0,0,0,.65); backdrop-filter: blur(2px);
    color: #fff; font-family: sans-serif;
    gap: 12px; pointer-events: none;
    opacity: 0; transition: opacity .3s;
  }
  #status-overlay.visible { opacity: 1; }
  #so-spinner {
    width: 32px; height: 32px;
    border: 3px solid rgba(255,255,255,.15);
    border-top-color: var(--primary-color, #03a9f4);
    border-radius: 50%;
    animation: _spin .75s linear infinite;
  }
  #so-icon   { font-size: 30px; display: none; }
  #so-text   { font-size: 13px; color: #bbb; }

  /* ── Overlay Canvas ────────────────────────────────────────── */
  #overlay-canvas {
    position: absolute; inset: 0; z-index: 5;
    pointer-events: none; opacity: .4; display: none;
  }
  #overlay-canvas.visible { display: block; }

  /* ── Minimap ───────────────────────────────────────────────── */
  #minimap {
    position: absolute; bottom: 8px; left: 8px; z-index: 15;
    width: 120px; height: 68px;
    border: 1px solid rgba(255,255,255,.25);
    border-radius: 5px;
    background: rgba(0,0,0,.45);
    overflow: hidden;
    display: none; pointer-events: none;
  }
  #minimap.visible { display: block; }
  #minimap-thumb {
    position: absolute;
    border: 1.5px solid var(--primary-color, #03a9f4);
    background: rgba(3,169,244,.18);
    box-sizing: border-box;
    border-radius: 2px;
  }

  /* ── KBD Hint ──────────────────────────────────────────────── */
  #kbd-hint {
    position: absolute; top: 50%; left: 50%; z-index: 25;
    transform: translate(-50%,-50%);
    background: rgba(0,0,0,.78); color: #fff;
    font-size: 13px; font-family: monospace; letter-spacing: .5px;
    padding: 6px 16px; border-radius: 8px;
    pointer-events: none; opacity: 0; transition: opacity .2s;
    white-space: nowrap;
  }
  #kbd-hint.show { opacity: 1; }

  /* ── Keyboard Help Panel ───────────────────────────────────── */
  #kbd-panel {
    position: absolute; inset: 0; z-index: 40;
    background: rgba(0,0,0,.88); backdrop-filter: blur(4px);
    color: #fff; font-family: sans-serif;
    display: none; flex-direction: column;
    padding: 20px; gap: 6px; overflow-y: auto;
    animation: _fadeIn .18s ease;
  }
  #kbd-panel.visible { display: flex; }
  #kbd-panel h3 {
    margin: 0 0 8px; font-size: 14px; font-weight: 600;
    color: var(--primary-color, #03a9f4); letter-spacing: .5px;
  }
  .kbd-row {
    display: flex; align-items: center; gap: 10px;
    font-size: 12px; color: #ccc; padding: 3px 0;
    border-bottom: 1px solid rgba(255,255,255,.06);
  }
  .kbd-key {
    background: rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.25);
    border-radius: 4px; padding: 2px 7px;
    font-family: monospace; font-size: 11px; color: #fff;
    min-width: 28px; text-align: center; flex-shrink: 0;
  }
  #kbd-close {
    margin-top: 10px; align-self: flex-end;
    background: var(--primary-color, #03a9f4);
    border: none; color: #fff; padding: 5px 18px;
    border-radius: 8px; cursor: pointer; font-size: 13px;
  }

  /* ── Badges ────────────────────────────────────────────────── */
  #badges {
    position: absolute; top: 6px; right: 8px;
    display: flex; gap: 5px; z-index: 10; pointer-events: none;
  }
  .badge {
    display: flex; align-items: center; justify-content: center;
    width: 28px; height: 28px; border-radius: 50%;
    background: rgba(0,0,0,.55); backdrop-filter: blur(4px);
    transition: background .2s, transform .15s;
    cursor: pointer; pointer-events: auto;
    position: relative;
  }
  .badge:hover { transform: scale(1.18); }
  .badge ha-icon { --mdc-icon-size: 16px; color: rgba(255,255,255,.55); }
  .badge.active  { background: rgba(var(--rgb-state-active-color, 255,152,0), .85); }
  .badge.danger  { background: rgba(var(--rgb-error-color, 244,67,54), .85); }
  .badge.warn    { background: rgba(255,193,7,.85); }
  .badge.record  { background: rgba(220,50,50,.85); animation: _blink 1.2s infinite; }
  .badge.active ha-icon, .badge.danger ha-icon,
  .badge.warn ha-icon,   .badge.record ha-icon { color: #fff; }
  .badge::after {
    content: attr(data-tip); position: absolute;
    bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
    background: rgba(0,0,0,.82); color: #fff;
    font-size: 11px; font-family: sans-serif; padding: 3px 8px;
    border-radius: 4px; white-space: nowrap;
    opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 100;
  }
  .badge:hover::after { opacity: 1; }

  /* ── PTZ Overlay ───────────────────────────────────────────── */
  #ptz {
    position: absolute; bottom: 44px; right: 8px; z-index: 15;
    display: none; flex-direction: column; align-items: center; gap: 2px;
  }
  #ptz.visible { display: flex; }
  .ptz-row { display: flex; gap: 2px; }
  .ptz-btn {
    width: 32px; height: 32px;
    background: rgba(0,0,0,.6); backdrop-filter: blur(4px);
    border: 1px solid rgba(255,255,255,.2); border-radius: 6px;
    color: rgba(255,255,255,.85); font-size: 15px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .12s, transform .08s; user-select: none;
  }
  .ptz-btn:hover  { background: rgba(255,255,255,.22); }
  .ptz-btn:active { transform: scale(.86); background: rgba(255,255,255,.38); }

  /* ── Resize Handles ────────────────────────────────────────── */
  .rh { position: absolute; z-index: 20; background: transparent; transition: background .15s; }
  #rh-bottom { bottom: 0; left: 10%; width: 80%; height: 8px; cursor: ns-resize; }
  #rh-right  { right: 0; top: 10%; width: 8px; height: 80%; cursor: ew-resize; }
  #rh-corner { bottom: 0; right: 0; width: 22px; height: 22px; cursor: nwse-resize; }
  #rh-bottom:hover  { background: rgba(3,169,244,.35); }
  #rh-right:hover   { background: rgba(3,169,244,.35); }
  #rh-corner::after {
    content: ''; position: absolute;
    bottom: 4px; right: 4px; width: 12px; height: 12px;
    border-right: 2.5px solid rgba(255,255,255,.45);
    border-bottom: 2.5px solid rgba(255,255,255,.45);
    border-radius: 0 0 2px 0; transition: border-color .15s;
  }
  #rh-corner:hover::after { border-color: var(--primary-color, #03a9f4); }

  /* ── Zoom Bar ──────────────────────────────────────────────── */
  #zoom-bar {
    display: flex; align-items: center; gap: 4px;
    padding: 5px 8px;
    background: rgba(0,0,0,.35);
    border-top: 1px solid rgba(255,255,255,.07);
    flex-wrap: nowrap;
  }
  #zoom-bar.hidden { display: none; }

  /* Zoom step buttons */
  .zb {
    background: rgba(255,255,255,.11);
    border: 1px solid rgba(255,255,255,.2);
    color: #fff; width: 26px; height: 26px; border-radius: 8px;
    cursor: pointer; font-size: 16px; font-family: sans-serif;
    transition: background .12s, border-color .12s;
    line-height: 1; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
  }
  .zb:hover { background: rgba(255,255,255,.26); }
  .zb:active { background: rgba(255,255,255,.4); transform: scale(.9); }

  /* Zoom value input */
  #zoom-input-wrap {
    display: flex; align-items: center; gap: 2px; flex-shrink: 0;
  }
  #zoom-val-input {
    width: 46px; background: rgba(255,255,255,.08);
    border: 1px solid rgba(255,255,255,.2); border-radius: 5px;
    color: #fff; font-size: 12px; font-family: monospace;
    text-align: right; padding: 2px 4px; outline: none;
  }
  #zoom-val-input:focus { border-color: var(--primary-color, #03a9f4); background: rgba(255,255,255,.14); }
  #zoom-unit { color: #777; font-size: 11px; font-family: monospace; }

  /* Slider */
  #z-slider-wrap { flex: 1; display: flex; align-items: center; min-width: 40px; }
  #z-slider {
    flex: 1; -webkit-appearance: none; appearance: none;
    height: 3px; background: rgba(255,255,255,.18); border-radius: 2px;
    outline: none; cursor: pointer;
  }
  #z-slider::-webkit-slider-thumb {
    -webkit-appearance: none; width: 14px; height: 14px;
    border-radius: 50%; background: var(--primary-color, #03a9f4);
    cursor: pointer; transition: transform .1s;
    box-shadow: 0 1px 4px rgba(0,0,0,.5);
  }
  #z-slider::-webkit-slider-thumb:hover { transform: scale(1.4); }
  #z-slider::-moz-range-thumb {
    width: 14px; height: 14px; border-radius: 50%; border: none;
    background: var(--primary-color, #03a9f4); cursor: pointer;
  }

  /* Overlay mode label */
  #overlay-label {
    font-size: 10px; font-family: monospace; color: #666;
    flex-shrink: 0; min-width: 54px; text-align: center;
    background: rgba(255,255,255,.06); border-radius: 4px;
    padding: 2px 5px; transition: color .2s;
  }
  #overlay-label.active { color: var(--primary-color, #03a9f4); }

  /* ── Toolbar ───────────────────────────────────────────────── */
  #toolbar {
    display: flex; align-items: center; gap: 2px; flex-shrink: 0;
  }
  .tb {
    width: 26px; height: 26px;
    background: rgba(255,255,255,.09);
    border: 1px solid rgba(255,255,255,.16);
    color: rgba(255,255,255,.7); border-radius: 6px;
    cursor: pointer; font-size: 13px;
    display: flex; align-items: center; justify-content: center;
    transition: background .12s, color .12s, border-color .12s;
    position: relative; flex-shrink: 0;
  }
  .tb:hover  { background: rgba(255,255,255,.24); color: #fff; }
  .tb.active { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
  .tb ha-icon { --mdc-icon-size: 14px; }
  .tb::after {
    content: attr(data-tip); position: absolute;
    bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
    background: rgba(0,0,0,.82); color: #fff;
    font-size: 11px; font-family: sans-serif; padding: 3px 8px;
    border-radius: 4px; white-space: nowrap;
    opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 100;
  }
  .tb:hover::after { opacity: 1; }

  /* Recording indicator */
  #rec-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #f44336; flex-shrink: 0;
    animation: _blink .9s infinite;
    display: none;
  }
  #rec-dot.visible { display: block; }
</style>

<!-- ── Title Bar ──────────────────────────────────────────────────── -->
<div id="title-bar">
  <span id="title-text"></span>
  <div id="status-dot"></div>
</div>

<!-- ── Viewport ───────────────────────────────────────────────────── -->
<div id="vp" tabindex="0" role="img" aria-label="Kamera-Stream">

  <div id="inner"></div>

  <canvas id="overlay-canvas"></canvas>

  <div id="status-overlay">
    <div id="so-spinner"></div>
    <div id="so-icon"></div>
    <div id="so-text">Verbinde…</div>
  </div>

  <div id="minimap"><div id="minimap-thumb"></div></div>
  <div id="kbd-hint"></div>

  <!-- Keyboard Help Panel -->
  <div id="kbd-panel">
    <h3>⌨ Tastaturkürzel</h3>
    <div class="kbd-row"><span class="kbd-key">+</span><span>Zoom rein</span></div>
    <div class="kbd-row"><span class="kbd-key">−</span><span>Zoom raus</span></div>
    <div class="kbd-row"><span class="kbd-key">R</span><span>Zoom &amp; Pan zurücksetzen</span></div>
    <div class="kbd-row"><span class="kbd-key">F</span><span>Vollbild umschalten</span></div>
    <div class="kbd-row"><span class="kbd-key">S</span><span>Snapshot speichern</span></div>
    <div class="kbd-row"><span class="kbd-key">G</span><span>Overlay-Modus wechseln</span></div>
    <div class="kbd-row"><span class="kbd-key">P</span><span>PTZ-Steuerung ein/aus</span></div>
    <div class="kbd-row"><span class="kbd-key">M</span><span>Minimap ein/aus</span></div>
    <div class="kbd-row"><span class="kbd-key">?</span><span>Diese Hilfe</span></div>
    <div class="kbd-row"><span class="kbd-key">←→↑↓</span><span>Pan (bei Zoom &gt; 1×)</span></div>
    <div class="kbd-row"><span class="kbd-key">Esc</span><span>Reset / Vollbild beenden</span></div>
    <div class="kbd-row"><span class="kbd-key">Doppelklick</span><span>Zoom an Cursor-Position</span></div>
    <div class="kbd-row"><span class="kbd-key">Scrollen</span><span>Zoom an Mausposition</span></div>
    <div class="kbd-row"><span class="kbd-key">Pinch</span><span>Touch-Zoom (2 Finger)</span></div>
    <button id="kbd-close">Schließen</button>
  </div>

  <!-- Badges -->
  <div id="badges"></div>

  <!-- PTZ Controls -->
  <div id="ptz">
    <div class="ptz-row">
      <button class="ptz-btn" data-ptz="up_left"   title="Oben Links">↖</button>
      <button class="ptz-btn" data-ptz="up"         title="Hoch">↑</button>
      <button class="ptz-btn" data-ptz="up_right"   title="Oben Rechts">↗</button>
    </div>
    <div class="ptz-row">
      <button class="ptz-btn" data-ptz="left"       title="Links">←</button>
      <button class="ptz-btn" data-ptz="home"       title="Startposition">⊙</button>
      <button class="ptz-btn" data-ptz="right"      title="Rechts">→</button>
    </div>
    <div class="ptz-row">
      <button class="ptz-btn" data-ptz="down_left"  title="Unten Links">↙</button>
      <button class="ptz-btn" data-ptz="down"       title="Runter">↓</button>
      <button class="ptz-btn" data-ptz="down_right" title="Unten Rechts">↘</button>
    </div>
    <div class="ptz-row" style="margin-top:4px">
      <button class="ptz-btn" data-ptz="zoom_in"   title="Optischer Zoom +">+</button>
      <button class="ptz-btn" data-ptz="zoom_out"  title="Optischer Zoom −">−</button>
    </div>
  </div>

  <!-- Resize Handles -->
  <div id="rh-bottom" class="rh"></div>
  <div id="rh-right"  class="rh"></div>
  <div id="rh-corner" class="rh"></div>
</div>

<!-- ── Zoom Bar + Toolbar ─────────────────────────────────────────── -->
<div id="zoom-bar">
  <!-- Zoom out -->
  <button class="zb" id="zb-out" title="Zoom raus (−)">−</button>

  <!-- Continuous slider -->
  <div id="z-slider-wrap">
    <input id="z-slider" type="range" min="0" max="100" value="0" step="0.05">
  </div>

  <!-- Zoom in -->
  <button class="zb" id="zb-in" title="Zoom rein (+)">+</button>

  <!-- Editable zoom value -->
  <div id="zoom-input-wrap">
    <input id="zoom-val-input" type="number" value="1.00" min="0.1" step="0.01">
    <span id="zoom-unit">×</span>
  </div>

  <!-- Overlay mode label -->
  <span id="overlay-label">Kein Raster</span>

  <!-- Recording dot -->
  <div id="rec-dot" title="Aufnahme läuft"></div>

  <!-- Toolbar -->
  <div id="toolbar">
    <button class="tb" id="tb-reset"    data-tip="Reset (R)">↺</button>
    <button class="tb" id="tb-overlay"  data-tip="Overlay (G)">⊞</button>
    <button class="tb" id="tb-minimap"  data-tip="Minimap (M)">🗺</button>
    <button class="tb" id="tb-ptz"      data-tip="PTZ (P)">🎮</button>
    <button class="tb" id="tb-snap"     data-tip="Snapshot (S)">📷</button>
    <button class="tb" id="tb-record"   data-tip="Video aufnehmen">⏺</button>
    <button class="tb" id="tb-fs"       data-tip="Vollbild (F)">⛶</button>
    <button class="tb" id="tb-help"     data-tip="Tastaturhilfe (?)">?</button>
  </div>
</div>`;

// ─────────────────────────────── Class ───────────────────────────────────────
class WjgCameraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.appendChild(_tpl.content.cloneNode(true));

    // ── Zoom / Pan state ──────────────────────────────────────────
    this._z  = 1;
    this._px = 0;
    this._py = 0;

    // ── Pointer state ─────────────────────────────────────────────
    this._ptrs      = new Map();
    this._drag      = false;
    this._lastX     = 0;  this._lastY     = 0;
    this._velX      = 0;  this._velY      = 0;
    this._lastMoveT = 0;

    // ── Pinch state ───────────────────────────────────────────────
    this._pinchD  = 0;
    this._pinchCX = 0; this._pinchCY = 0;

    // ── Animation RAFs ────────────────────────────────────────────
    this._momentumRAF = null;
    this._zoomRAF     = null;

    // ── Resize state ──────────────────────────────────────────────
    this._resizing  = null;
    this._rsX = 0; this._rsY = 0;
    this._rsW = 0; this._rsH = 0;
    this._saveDebounce = null;

    // ── Config / HASS ─────────────────────────────────────────────
    this._config     = null;
    this._hass       = null;
    this._ready      = false;
    this._snapTimer  = null;
    this._storageKey = '';

    // ── Feature state ─────────────────────────────────────────────
    this._overlayMode   = 'none';
    this._ptzVisible    = false;
    this._minimapForced = null;   // null=auto, true=force on, false=force off
    this._kbdTimer      = null;
    this._syncTimer     = null;

    // ── Video recording state ──────────────────────────────────────
    this._mediaRecorder = null;
    this._recChunks     = [];

    // ── ResizeObserver ────────────────────────────────────────────
    this._resizeObs = null;

    // Bind handlers
    this._onPtrDown  = this._onPtrDown.bind(this);
    this._onPtrMove  = this._onPtrMove.bind(this);
    this._onPtrUp    = this._onPtrUp.bind(this);
    this._onWheel    = this._onWheel.bind(this);
    this._onKeyDown  = this._onKeyDown.bind(this);
    this._onDblClick = this._onDblClick.bind(this);
    this._onFsChange = this._onFsChange.bind(this);
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  connectedCallback() {
    if (!this._storageKey || !localStorage.getItem(this._storageKey + '_w'))
      this.style.width = '100%';

    this._bindVpEvents();
    this._bindZoomBarEvents();
    this._bindResizeHandleEvents();

    this._resizeObs = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      this._clamp();
      this._applyZoom(false);
      this._updateMinimap();
      this._redrawOverlay(width, height);
    });
    this._resizeObs.observe(this._$('vp'));

    document.addEventListener('fullscreenchange', this._onFsChange);
  }

  disconnectedCallback() {
    this._resizeObs?.disconnect();
    document.removeEventListener('fullscreenchange', this._onFsChange);
    if (this._snapTimer)    clearInterval(this._snapTimer);
    if (this._momentumRAF)  cancelAnimationFrame(this._momentumRAF);
    if (this._zoomRAF)      cancelAnimationFrame(this._zoomRAF);
    if (this._kbdTimer)     clearTimeout(this._kbdTimer);
    if (this._saveDebounce) clearTimeout(this._saveDebounce);
    if (this._syncTimer)    clearTimeout(this._syncTimer);
    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive')
      this._mediaRecorder.stop();
    const vp = this._$('vp');
    vp.removeEventListener('pointerdown',   this._onPtrDown);
    vp.removeEventListener('pointermove',   this._onPtrMove);
    vp.removeEventListener('pointerup',     this._onPtrUp);
    vp.removeEventListener('pointercancel', this._onPtrUp);
    vp.removeEventListener('wheel',         this._onWheel);
    vp.removeEventListener('keydown',       this._onKeyDown);
    vp.removeEventListener('dblclick',      this._onDblClick);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  setConfig(config) {
    if (!config.entity) throw new Error('wjg-camera-card: "entity" ist erforderlich');
    this._config     = config;
    this._storageKey = `wjg_cam_${config.entity}`;
    this._applyConfig();
    if (this._hass) { this._ensureStream(); this._updateBadges(); }
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureStream();
    const s = this.shadowRoot.querySelector('ha-camera-stream');
    if (s) {
      s.hass = hass;
      const st = hass.states[this._config?.entity];
      if (st) s.stateObj = st;
    }
    this._updateBadges();
    this._updateStatusDot();
  }

  getCardSize() { return 5; }
  static getStubConfig() { return { entity: 'camera.wjg_xm_3820' }; }

  // ── Config ─────────────────────────────────────────────────────────────────
  _applyConfig() {
    const cfg = this._config;

    // Title
    const tb = this._$('title-bar'), tt = this._$('title-text');
    if (cfg.title) { tt.textContent = cfg.title; tb.classList.add('visible'); }
    else             tb.classList.remove('visible');

    // Zoom bar visibility
    this._$('zoom-bar').classList.toggle('hidden', cfg.show_zoom_bar === false);

    // Aspect ratio
    if (cfg.aspect_ratio) {
      const [w, h] = cfg.aspect_ratio.split('/').map(Number);
      if (w && h) this._$('vp').style.aspectRatio = `${w} / ${h}`;
    }

    // PTZ initial state
    if (cfg.show_ptz) this._setPTZ(true);

    // Update slider range based on min/max_zoom
    this._updateSliderRange();

    this._restoreSize();
  }

  _minZoom() { return Math.max(0.1, this._config?.min_zoom || 0.25); }
  _maxZoom() { return Math.min(256, this._config?.max_zoom || 64); }

  _updateSliderRange() {
    // Slider is always 0–100, mapped logarithmically to [minZoom, maxZoom]
    // No step buttons needed — slider is fully continuous
  }

  // ── Stream / Snapshot refresh ──────────────────────────────────────────────
  _ensureStream() {
    if (this._ready || !this._hass || !this._config) return;
    const st = this._hass.states[this._config.entity];
    if (!st) return;
    const inner = this._$('inner');
    if (!inner) return;

    this._showStatus('loading', 'Verbinde…');

    if (window.customElements.get('ha-camera-stream')) {
      const el = document.createElement('ha-camera-stream');
      el.hass = this._hass; el.stateObj = st;
      el.controls = false; el.muted = true;
      const hide = () => this._hideStatus();
      el.addEventListener('load', hide, { once: true });
      setTimeout(hide, 4000);
      inner.appendChild(el);
    } else {
      const img = document.createElement('img');
      img.id = 'snap'; img.alt = 'Kamera';
      img.addEventListener('load', () => this._hideStatus(), { once: true });
      inner.appendChild(img);
      this._snapTimer = setInterval(() => this._refreshSnap(), 1500);
      this._refreshSnap();
    }
    this._ready = true;
  }

  _refreshSnap() {
    const img = this.shadowRoot.querySelector('#snap');
    const st  = this._hass?.states[this._config?.entity];
    if (img && st?.attributes?.entity_picture)
      img.src = st.attributes.entity_picture + '&_t=' + Date.now();
  }

  // ── Status helpers ─────────────────────────────────────────────────────────
  _showStatus(type, text) {
    const ol = this._$('status-overlay');
    if (!ol) return;
    ol.classList.add('visible');
    this._$('so-spinner').style.display = type === 'loading' ? 'block' : 'none';
    const icon = this._$('so-icon');
    icon.style.display = type !== 'loading' ? 'block' : 'none';
    icon.textContent = type === 'error' ? '⚠️' : type === 'offline' ? '📵' : '';
    this._$('so-text').textContent = text;
  }

  _hideStatus() {
    this._$('status-overlay')?.classList.remove('visible');
    this._updateStatusDot();
  }

  _updateStatusDot() {
    const dot = this._$('status-dot');
    if (!dot || !this._hass || !this._config) return;
    const state = this._hass.states[this._config.entity]?.state;
    dot.className = (state === 'idle' || state === 'streaming') ? 'online'
                  : (state === 'unavailable')                    ? 'offline'
                  : 'loading';
  }

  // ── Badges ─────────────────────────────────────────────────────────────────
  _updateBadges() {
    if (!this._hass || !this._config?.badges?.length) return;
    const container = this._$('badges');
    if (!container.children.length) {
      this._config.badges.forEach((b, i) => {
        const div  = document.createElement('div');
        div.className = 'badge';
        div.dataset.idx = i;
        div.setAttribute('data-tip', b.label || b.entity || '');
        div.addEventListener('click', () => {
          if (!b.action || !this._hass) return;
          const [domain, svc] = b.action.split('.');
          this._hass.callService(domain, svc, { entity_id: b.entity }).catch(() => {});
        });
        const icon = document.createElement('ha-icon');
        icon.setAttribute('icon', b.icon || 'mdi:circle');
        div.appendChild(icon);
        container.appendChild(div);
      });
    }
    this._config.badges.forEach((b, i) => {
      const div   = container.children[i];
      const state = this._hass.states[b.entity]?.state;
      div.classList.remove('active', 'danger', 'warn', 'record');
      if      (state === 'on')          div.classList.add(b.state_class || (b.entity.startsWith('switch.') ? 'record' : 'active'));
      else if (state === 'unavailable') div.classList.add('danger');
      else if (state === 'warning')     div.classList.add('warn');
    });
  }

  // ── Event binding ──────────────────────────────────────────────────────────
  _bindVpEvents() {
    const vp = this._$('vp');
    vp.addEventListener('pointerdown',   this._onPtrDown);
    vp.addEventListener('pointermove',   this._onPtrMove);
    vp.addEventListener('pointerup',     this._onPtrUp);
    vp.addEventListener('pointercancel', this._onPtrUp);
    vp.addEventListener('wheel',         this._onWheel, { passive: false });
    vp.addEventListener('keydown',       this._onKeyDown);
    vp.addEventListener('dblclick',      this._onDblClick);
    vp.addEventListener('contextmenu',   e => e.preventDefault());
  }

  _bindZoomBarEvents() {
    // Zoom −/+ buttons
    this._$('zb-out').addEventListener('click', () =>
      this._animZoomAt(0.5, 0.5, this._z / 1.35));
    this._$('zb-in').addEventListener('click', () =>
      this._animZoomAt(0.5, 0.5, this._z * 1.35));

    // Continuous slider
    const slider = this._$('z-slider');
    slider.addEventListener('input', () => {
      const t  = parseFloat(slider.value) / 100;
      const nz = this._sliderToZoom(t);
      this._zoomAt(0.5, 0.5, nz);
    });

    // Editable zoom input
    const zinput = this._$('zoom-val-input');
    zinput.addEventListener('change', () => {
      const v = parseFloat(zinput.value);
      if (!isNaN(v) && v > 0)
        this._animZoomAt(0.5, 0.5, v);
    });
    zinput.addEventListener('keydown', e => {
      if (e.key === 'Enter') zinput.blur();
      e.stopPropagation(); // prevent vp key handler
    });

    // Toolbar buttons
    this._$('tb-reset')   .addEventListener('click', () => { this._reset(); this._showKbd('↺ Reset'); });
    this._$('tb-overlay') .addEventListener('click', () => this._cycleOverlay());
    this._$('tb-minimap') .addEventListener('click', () => this._toggleMinimapForced());
    this._$('tb-ptz')     .addEventListener('click', () => this._togglePTZ());
    this._$('tb-snap')    .addEventListener('click', () => this._snapshot());
    this._$('tb-record')  .addEventListener('click', () => this._toggleRecord());
    this._$('tb-fs')      .addEventListener('click', () => this._toggleFullscreen());
    this._$('tb-help')    .addEventListener('click', () => this._toggleKbdPanel());
    this._$('kbd-close')  .addEventListener('click', () => this._toggleKbdPanel(false));

    // PTZ buttons
    this.shadowRoot.querySelectorAll('[data-ptz]').forEach(b =>
      b.addEventListener('click', () => this._callPTZ(b.dataset.ptz))
    );
  }

  _bindResizeHandleEvents() {
    [['rh-bottom','v'], ['rh-right','h'], ['rh-corner','both']].forEach(([id, mode]) => {
      const el = this._$(id);
      el.addEventListener('pointerdown', e => {
        e.stopPropagation(); e.preventDefault();
        el.setPointerCapture(e.pointerId);
        const r = this._$('vp').getBoundingClientRect();
        this._resizing = mode;
        this._rsX = e.clientX; this._rsY = e.clientY;
        this._rsW = r.width;   this._rsH = r.height;

        const onMove = ev => this._doResize(ev.clientX, ev.clientY);
        const onUp   = ()  => {
          this._resizing = null;
          this._debounceSave();
          el.removeEventListener('pointermove', onMove);
          el.removeEventListener('pointerup',   onUp);
          el.removeEventListener('pointercancel', onUp);
        };
        el.addEventListener('pointermove',  onMove);
        el.addEventListener('pointerup',    onUp);
        el.addEventListener('pointercancel', onUp);
      });
    });
  }

  // ── Pointer Handlers ───────────────────────────────────────────────────────
  _onPtrDown(e) {
    if (e.target.closest?.('.rh') || e.target.closest?.('.ptz-btn')) return;
    const vp = this._$('vp');
    vp.setPointerCapture(e.pointerId);
    this._ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this._ptrs.size === 1) {
      if (this._momentumRAF) { cancelAnimationFrame(this._momentumRAF); this._momentumRAF = null; }
      this._drag   = true;
      this._lastX  = e.clientX; this._lastY = e.clientY;
      this._velX   = 0; this._velY = 0;
      this._lastMoveT = performance.now();
      if (this._z > 1) vp.classList.add('dragging');
      vp.focus({ preventScroll: true });
    } else if (this._ptrs.size === 2) {
      this._drag = false;
      vp.classList.remove('dragging');
      const [a, b] = [...this._ptrs.values()];
      this._pinchD  = this._dist(a, b);
      this._pinchCX = (a.x + b.x) / 2;
      this._pinchCY = (a.y + b.y) / 2;
    }
  }

  _onPtrMove(e) {
    if (!this._ptrs.has(e.pointerId)) return;
    this._ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this._ptrs.size >= 2) {
      const [a, b] = [...this._ptrs.values()];
      const d  = this._dist(a, b);
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      if (this._pinchD > 0) {
        const vp = this._$('vp');
        const r  = vp.getBoundingClientRect();
        this._zoomAt((cx - r.left) / r.width, (cy - r.top) / r.height,
                     this._z * d / this._pinchD);
        this._panBy(cx - this._pinchCX, cy - this._pinchCY);
      }
      this._pinchD = d; this._pinchCX = cx; this._pinchCY = cy;

    } else if (this._drag && this._z > 1) {
      const dx  = e.clientX - this._lastX;
      const dy  = e.clientY - this._lastY;
      const now = performance.now();
      const dt  = Math.max(1, now - this._lastMoveT);
      this._velX = this._velX * 0.6 + (dx / dt) * 0.4;
      this._velY = this._velY * 0.6 + (dy / dt) * 0.4;
      this._panBy(dx, dy);
      this._lastX = e.clientX; this._lastY = e.clientY;
      this._lastMoveT = now;
    }
  }

  _onPtrUp(e) {
    this._ptrs.delete(e.pointerId);
    const vp = this._$('vp');
    if (this._ptrs.size === 0) {
      if (this._drag && this._z > 1 &&
          (Math.abs(this._velX) > 0.05 || Math.abs(this._velY) > 0.05)) {
        this._startMomentum();
      }
      this._drag = false;
      vp.classList.remove('dragging');
    }
    if (this._ptrs.size < 2) this._pinchD = 0;
  }

  _onWheel(e) {
    e.preventDefault();
    const vp = this._$('vp');
    const r  = vp.getBoundingClientRect();
    const f  = e.deltaY < 0 ? 1.12 : (1 / 1.12);
    this._zoomAt((e.clientX - r.left) / r.width,
                 (e.clientY - r.top)  / r.height,
                 this._z * f);
  }

  _onDblClick(e) {
    const vp = this._$('vp');
    const r  = vp.getBoundingClientRect();
    const cx = (e.clientX - r.left) / r.width;
    const cy = (e.clientY - r.top)  / r.height;
    this._animZoomAt(cx, cy, this._z > 1 ? 1 : 2.5);
  }

  _onKeyDown(e) {
    // Don't steal keys when zoom input is focused
    if (e.target === this._$('zoom-val-input')) return;

    const step = 40, zs = 1.25;
    switch (e.key) {
      case 'ArrowLeft':  this._panBy( step, 0);  this._showKbd('← Pan'); break;
      case 'ArrowRight': this._panBy(-step, 0);  this._showKbd('→ Pan'); break;
      case 'ArrowUp':    this._panBy(0,  step);  this._showKbd('↑ Pan'); break;
      case 'ArrowDown':  this._panBy(0, -step);  this._showKbd('↓ Pan'); break;
      case '+': case '=': this._animZoomAt(0.5, 0.5, this._z * zs); this._showKbd('Zoom +'); break;
      case '-': case '_': this._animZoomAt(0.5, 0.5, this._z / zs); this._showKbd('Zoom −'); break;
      case 'r': case 'R': this._reset(); this._showKbd('↺ Reset'); break;
      case 'f': case 'F': this._toggleFullscreen(); break;
      case 's': case 'S': this._snapshot(); this._showKbd('📷 Snap'); break;
      case 'g': case 'G': this._cycleOverlay(); break;
      case 'p': case 'P': this._togglePTZ(); break;
      case 'm': case 'M': this._toggleMinimapForced(); break;
      case '?': case '/': this._toggleKbdPanel(); break;
      case 'Home':        this._reset(); break;
      case 'Escape':
        if (this._$('kbd-panel').classList.contains('visible'))
          this._toggleKbdPanel(false);
        else if (document.fullscreenElement)
          document.exitFullscreen();
        else { this._reset(); this._showKbd('↺ Reset'); }
        break;
      default: return;
    }
    e.preventDefault();
  }

  _onFsChange() {
    const isFs = document.fullscreenElement === this || this.contains(document.fullscreenElement);
    this._$('tb-fs')?.classList.toggle('active', isFs);
  }

  // ── Zoom ───────────────────────────────────────────────────────────────────
  _sliderToZoom(t) {
    // Logarithmic: t=0 → minZoom, t=1 → maxZoom
    const mn = Math.log(this._minZoom());
    const mx = Math.log(this._maxZoom());
    return Math.exp(mn + t * (mx - mn));
  }

  _zoomToSlider(z) {
    const mn = Math.log(this._minZoom());
    const mx = Math.log(this._maxZoom());
    return (Math.log(Math.max(this._minZoom(), z)) - mn) / (mx - mn);
  }

  _zoomAt(cx, cy, nz) {
    nz = Math.max(this._minZoom(), Math.min(this._maxZoom(), nz));
    const vp = this._$('vp');
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    const s  = nz / this._z;
    this._px = cx * vw * (1 - s) + s * this._px;
    this._py = cy * vh * (1 - s) + s * this._py;
    this._z  = nz;
    this._clamp();
    this._applyZoom();
    this._syncServer();
  }

  _animZoomAt(cx, cy, nz) {
    nz = Math.max(this._minZoom(), Math.min(this._maxZoom(), nz));
    const vp = this._$('vp');
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    const s  = nz / this._z;
    const tPx = cx * vw * (1 - s) + s * this._px;
    const tPy = cy * vh * (1 - s) + s * this._py;
    const sZ  = this._z, sPx = this._px, sPy = this._py;
    const t0  = performance.now(), dur = 220;

    if (this._zoomRAF) cancelAnimationFrame(this._zoomRAF);
    const tick = t => {
      const p = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      this._z  = sZ  + (nz  - sZ)  * e;
      this._px = sPx + (tPx - sPx) * e;
      this._py = sPy + (tPy - sPy) * e;
      this._clamp(); this._applyZoom();
      if (p < 1) {
        this._zoomRAF = requestAnimationFrame(tick);
      } else {
        this._z = nz; this._px = tPx; this._py = tPy;
        this._clamp(); this._applyZoom();
        this._zoomRAF = null;
        this._syncServer();
      }
    };
    this._zoomRAF = requestAnimationFrame(tick);
  }

  _reset() {
    this._animZoomAt_raw(1, 0, 0);
  }

  _animZoomAt_raw(tz, tPx, tPy) {
    if (this._zoomRAF) cancelAnimationFrame(this._zoomRAF);
    const sZ = this._z, sPx = this._px, sPy = this._py;
    const t0 = performance.now(), dur = 220;
    const tick = t => {
      const p = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      this._z  = sZ  + (tz  - sZ)  * e;
      this._px = sPx + (tPx - sPx) * e;
      this._py = sPy + (tPy - sPy) * e;
      this._clamp(); this._applyZoom();
      if (p < 1) {
        this._zoomRAF = requestAnimationFrame(tick);
      } else {
        this._z = tz; this._px = tPx; this._py = tPy;
        this._clamp(); this._applyZoom();
        this._zoomRAF = null;
        this._syncServer();
      }
    };
    this._zoomRAF = requestAnimationFrame(tick);
  }

  _panBy(dx, dy) {
    this._px += dx; this._py += dy;
    this._clamp(); this._applyZoom();
  }

  _clamp() {
    if (this._z <= 1) { this._px = 0; this._py = 0; return; }
    const vp = this._$('vp');
    if (!vp) return;
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    this._px = Math.min(0, Math.max(vw * (1 - this._z), this._px));
    this._py = Math.min(0, Math.max(vh * (1 - this._z), this._py));
  }

  _applyZoom(updateMinimap = true) {
    const inner = this._$('inner');
    const vp    = this._$('vp');
    if (!inner) return;

    inner.style.transform = `translate(${this._px}px,${this._py}px) scale(${this._z})`;

    // Update zoom value display (editable input)
    const zinput = this._$('zoom-val-input');
    if (zinput !== document.activeElement)
      zinput.value = this._z.toFixed(2);

    // Update slider (logarithmic, continuous)
    const slider = this._$('z-slider');
    slider.value = (this._zoomToSlider(this._z) * 100).toFixed(3);

    // Cursor
    vp.classList.toggle('zoom1', this._z <= 1);

    if (updateMinimap) this._updateMinimap();
  }

  // ── Momentum ───────────────────────────────────────────────────────────────
  _startMomentum() {
    const decay = 0.915;
    let vx = this._velX * 16;
    let vy = this._velY * 16;
    const step = () => {
      vx *= decay; vy *= decay;
      this._px += vx; this._py += vy;
      this._clamp(); this._applyZoom();
      if (Math.abs(vx) > 0.4 || Math.abs(vy) > 0.4) {
        this._momentumRAF = requestAnimationFrame(step);
      } else {
        this._momentumRAF = null;
      }
    };
    this._momentumRAF = requestAnimationFrame(step);
  }

  // ── Minimap ────────────────────────────────────────────────────────────────
  _updateMinimap() {
    const mm    = this._$('minimap');
    const thumb = this._$('minimap-thumb');
    const vp    = this._$('vp');
    if (!mm || !thumb || !vp) return;

    const autoShow = (this._config?.show_minimap !== false) && this._z > 1.5;
    const show = this._minimapForced !== null ? this._minimapForced : autoShow;
    mm.classList.toggle('visible', show);
    this._$('tb-minimap')?.classList.toggle('active', show);
    if (!show) return;

    const mW = mm.offsetWidth, mH = mm.offsetHeight;
    const vW = vp.offsetWidth, vH = vp.offsetHeight;
    if (!mW || !mH || !vW || !vH) return;

    const x = -this._px / (this._z * vW);
    const y = -this._py / (this._z * vH);
    const w = 1 / this._z;
    const h = 1 / this._z;

    thumb.style.left   = `${Math.max(0, x * mW)}px`;
    thumb.style.top    = `${Math.max(0, y * mH)}px`;
    thumb.style.width  = `${Math.min(mW, w * mW)}px`;
    thumb.style.height = `${Math.min(mH, h * mH)}px`;
  }

  _toggleMinimapForced() {
    if (this._minimapForced === null)
      this._minimapForced = !(this._z > 1.5);
    else
      this._minimapForced = !this._minimapForced;
    this._updateMinimap();
    this._showKbd(this._minimapForced ? '🗺 Minimap an' : '🗺 Minimap aus');
  }

  // ── Overlay ────────────────────────────────────────────────────────────────
  _cycleOverlay() {
    const modes = ['none', 'crosshair', 'grid', 'thirds'];
    this._overlayMode = modes[(modes.indexOf(this._overlayMode) + 1) % modes.length];
    const canvas = this._$('overlay-canvas');
    const btn    = this._$('tb-overlay');
    const lbl    = this._$('overlay-label');
    const vp     = this._$('vp');

    canvas.classList.toggle('visible', this._overlayMode !== 'none');
    btn.classList.toggle('active',     this._overlayMode !== 'none');

    const names = { none: 'Kein Raster', crosshair: '⊕ Fadenkreuz', grid: '⊞ Raster', thirds: '⅓ Drittel' };
    lbl.textContent = names[this._overlayMode];
    lbl.classList.toggle('active', this._overlayMode !== 'none');

    this._showKbd(names[this._overlayMode]);
    this._redrawOverlay(vp.offsetWidth, vp.offsetHeight);
  }

  _redrawOverlay(w, h) {
    const canvas = this._$('overlay-canvas');
    if (!canvas || this._overlayMode === 'none' || !w || !h) return;
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 1;

    if (this._overlayMode === 'crosshair') {
      ctx.beginPath();
      ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h);
      ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(w / 2, h / 2, Math.min(w, h) * 0.04, 0, 2 * Math.PI);
      ctx.stroke();
    } else if (this._overlayMode === 'grid') {
      const cols = 8;
      const rows = Math.round(cols * h / w);
      for (let i = 1; i < cols; i++) {
        ctx.beginPath();
        ctx.moveTo(i * w / cols, 0); ctx.lineTo(i * w / cols, h); ctx.stroke();
      }
      for (let j = 1; j < rows; j++) {
        ctx.beginPath();
        ctx.moveTo(0, j * h / rows); ctx.lineTo(w, j * h / rows); ctx.stroke();
      }
    } else if (this._overlayMode === 'thirds') {
      ctx.strokeStyle = 'rgba(255,220,50,0.9)';
      for (const t of [1/3, 2/3]) {
        ctx.beginPath();
        ctx.moveTo(t * w, 0); ctx.lineTo(t * w, h);
        ctx.moveTo(0, t * h); ctx.lineTo(w, t * h);
        ctx.stroke();
      }
    }
  }

  // ── PTZ ────────────────────────────────────────────────────────────────────
  _setPTZ(show) {
    this._ptzVisible = show;
    this._$('ptz')   ?.classList.toggle('visible', show);
    this._$('tb-ptz')?.classList.toggle('active',  show);
  }

  _togglePTZ() {
    this._setPTZ(!this._ptzVisible);
    this._showKbd(this._ptzVisible ? 'PTZ An' : 'PTZ Aus');
  }

  _callPTZ(direction) {
    if (!this._hass || !this._config) return;

    // Modus 1: ptz_entities Map → ruft button.press auf der jeweiligen Entity auf
    // Konfiguration in YAML:
    //   ptz_entities:
    //     up:        button.wjg_xm_3820_ptz_up
    //     down:      button.wjg_xm_3820_ptz_down
    //     left:      button.wjg_xm_3820_ptz_left
    //     right:     button.wjg_xm_3820_ptz_right
    //     up_left:   button.wjg_xm_3820_ptz_up        # optional Diagonalen
    //     up_right:  button.wjg_xm_3820_ptz_up
    //     down_left: button.wjg_xm_3820_ptz_down
    //     down_right:button.wjg_xm_3820_ptz_down
    //     home:      button.wjg_xm_3820_ptz_home
    //     zoom_in:   button.wjg_xm_3820_ptz_zoom_in   # optional
    //     zoom_out:  button.wjg_xm_3820_ptz_zoom_out  # optional
    // Debug: zeigt im Browser-Konsole was HA tatsaechlich uebergibt
    console.debug('[wjg-camera-card] _callPTZ direction=' + direction +
      ' ptz_entities=' + JSON.stringify(this._config.ptz_entities));

    // Modus 1: ptz_entities Map → button.press, OHNE move-Parameter
    const _ptz = this._config.ptz_entities;
    const _hasPtz = _ptz !== null &&
                    _ptz !== undefined &&
                    typeof _ptz === 'object' &&
                    !Array.isArray(_ptz) &&
                    Object.keys(_ptz).length > 0;
    if (_hasPtz) {
      const entityId = String(_ptz[direction] || '').trim();
      if (!entityId) return; // Richtung nicht konfiguriert → ignorieren
      // Nur entity_id! button.press erlaubt keinen move-Parameter.
      this._hass.callService('button', 'press', { entity_id: entityId }).catch(() => {});
      return;
    }

    // Modus 2: ptz_service (Legacy) → ruft einen einzelnen Service mit move-Parameter auf
    const service = this._config.ptz_service || 'wjg_camera.ptz';
    const [domain, svc] = service.split('.');
    this._hass.callService(domain, svc, {
      entity_id: this._config.entity,
      move: direction,
    }).catch(() => {});
  }

  // ── Keyboard Help Panel ────────────────────────────────────────────────────
  _toggleKbdPanel(forceState) {
    const panel = this._$('kbd-panel');
    const show  = forceState !== undefined ? forceState : !panel.classList.contains('visible');
    panel.classList.toggle('visible', show);
    this._$('tb-help')?.classList.toggle('active', show);
  }

  // ── Fullscreen ─────────────────────────────────────────────────────────────
  _toggleFullscreen() {
    if (!document.fullscreenElement)
      this.requestFullscreen().catch(() => {});
    else
      document.exitFullscreen();
  }

  // ── Snapshot ───────────────────────────────────────────────────────────────
  _snapshot() {
    const stream = this.shadowRoot.querySelector('ha-camera-stream');
    if (stream?.shadowRoot) {
      const video = stream.shadowRoot.querySelector('video');
      if (video?.videoWidth) {
        const canvas = document.createElement('canvas');
        canvas.width  = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0);
        canvas.toBlob(blob => {
          const url = URL.createObjectURL(blob);
          const a   = Object.assign(document.createElement('a'), {
            href: url,
            download: `snapshot_${this._config.entity}_${Date.now()}.jpg`,
          });
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 2000);
        }, 'image/jpeg', 0.92);
        this._showKbd('📷 Gespeichert');
        return;
      }
    }
    const pic = this._hass?.states[this._config?.entity]?.attributes?.entity_picture;
    if (pic) window.open(pic, '_blank');
    this._showKbd('📷 Snap');
  }

  // ── Video Recording ────────────────────────────────────────────────────────
  _toggleRecord() {
    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive') {
      this._mediaRecorder.stop();
      return;
    }

    // Find video element
    const stream = this.shadowRoot.querySelector('ha-camera-stream');
    const video  = stream?.shadowRoot?.querySelector('video');
    if (!video) {
      this._showKbd('⏺ Kein Stream');
      return;
    }

    // Capture stream from video element
    let captureStream;
    try {
      captureStream = video.captureStream
        ? video.captureStream()
        : video.mozCaptureStream?.();
    } catch(e) {}

    if (!captureStream) {
      this._showKbd('⏺ Nicht unterstützt');
      return;
    }

    const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4']
      .find(t => MediaRecorder.isTypeSupported(t)) || '';

    this._recChunks = [];
    this._mediaRecorder = new MediaRecorder(captureStream, mimeType ? { mimeType } : undefined);

    this._mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) this._recChunks.push(e.data);
    };

    this._mediaRecorder.onstop = () => {
      const blob = new Blob(this._recChunks, { type: mimeType || 'video/webm' });
      const url  = URL.createObjectURL(blob);
      const a    = Object.assign(document.createElement('a'), {
        href: url,
        download: `video_${this._config.entity}_${Date.now()}.webm`,
      });
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      this._recChunks = [];
      this._$('tb-record').classList.remove('active');
      this._$('rec-dot').classList.remove('visible');
      this._showKbd('⏺ Video gespeichert');
    };

    this._mediaRecorder.start(1000); // chunk every 1s
    this._$('tb-record').classList.add('active');
    this._$('rec-dot').classList.add('visible');
    this._showKbd('⏺ Aufnahme läuft…');
  }

  // ── KBD Hint ───────────────────────────────────────────────────────────────
  _showKbd(text) {
    const el = this._$('kbd-hint');
    if (!el) return;
    el.textContent = text;
    el.classList.add('show');
    if (this._kbdTimer) clearTimeout(this._kbdTimer);
    this._kbdTimer = setTimeout(() => el.classList.remove('show'), 1300);
  }

  // ── Resize Handles ─────────────────────────────────────────────────────────
  _doResize(x, y) {
    const vp  = this._$('vp');
    const dx  = x - this._rsX;
    const dy  = y - this._rsY;
    const cfg = this._config || {};
    const minW = cfg.min_width  || 160, maxW = cfg.max_width  || 4000;
    const minH = cfg.min_height || 80,  maxH = cfg.max_height || 3000;

    if (this._resizing === 'v' || this._resizing === 'both') {
      vp.style.height = `${Math.max(minH, Math.min(maxH, this._rsH + dy))}px`;
      vp.classList.add('custom-height');
    }
    if (this._resizing === 'h' || this._resizing === 'both') {
      const newW = Math.max(minW, Math.min(maxW, this._rsW + dx));
      this.style.width = `${newW}px`;
      if (this._resizing === 'h') {
        const newH = Math.max(minH, newW * (this._rsH / this._rsW));
        vp.style.height = `${newH}px`;
        vp.classList.add('custom-height');
      }
    }
  }

  _debounceSave() {
    if (this._saveDebounce) clearTimeout(this._saveDebounce);
    this._saveDebounce = setTimeout(() => this._saveSize(), 300);
  }

  _saveSize() {
    if (!this._storageKey) return;
    const vp = this._$('vp');
    if (vp?.style.height)   localStorage.setItem(this._storageKey + '_h', vp.style.height);
    if (this.style.width)   localStorage.setItem(this._storageKey + '_w', this.style.width);
  }

  _restoreSize() {
    if (!this._storageKey) return;
    const vp   = this._$('vp');
    const savH = localStorage.getItem(this._storageKey + '_h');
    const savW = localStorage.getItem(this._storageKey + '_w');
    if (savH && vp) { vp.style.height = savH; vp.classList.add('custom-height'); }
    if (savW)       { this.style.width = savW; }
  }

  // ── Server Sync (throttled 100 ms) ─────────────────────────────────────────
  _syncServer() {
    if (this._syncTimer) return;
    this._syncTimer = setTimeout(() => {
      this._syncTimer = null;
      this._doSyncServer();
    }, 100);
  }

  _doSyncServer() {
    if (!this._hass || !this._config) return;
    const vp  = this._$('vp');
    const vW  = vp.offsetWidth || 1, vH = vp.offsetHeight || 1;
    const cX  = Math.max(0, Math.min(1, (0.5 * vW - this._px) / (this._z * vW)));
    const cY  = Math.max(0, Math.min(1, (0.5 * vH - this._py) / (this._z * vH)));
    this._hass.callService('wjg_camera', 'set_digital_zoom', {
      entity_id: this._config.entity,
      zoom: this._z, cx: cX, cy: cY,
    }).catch(() => {});
  }

  // ── Helpers ────────────────────────────────────────────────────────────────
  _$(id)      { return this.shadowRoot.getElementById(id); }
  _dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
}

// ─────────────────────────────── Registration ────────────────────────────────
if (!customElements.get('wjg-camera-card')) {
  customElements.define('wjg-camera-card', WjgCameraCard);
  console.info(
    '%c WJG Camera Card v3.1 %c Stufenloser Zoom (0.25×–64×) · Video-Download · Minimap-Toggle · Kbd-Hilfe-Panel · Overlay-Label · Alle Features im UI sichtbar',
    'color:#fff;background:#03a9f4;padding:2px 8px;border-radius:3px;font-weight:bold', ''
  );
}
