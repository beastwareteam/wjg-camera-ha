/**
 * wjg-camera-card.js  v2.1
 * Einzel-Stream Kamera-Karte mit Zoom, Badges und Resize für WJG XM-3820
 *
 * Ersetzt picture-glance vollständig → NUR EINE Stream-Instanz im Dashboard.
 *
 * Lovelace-Ressource (einmalig):
 *   Einstellungen → Dashboards → Ressourcen → + Hinzufügen
 *   URL : /wjg_camera/wjg-camera-card.js   Typ: JavaScript Modul
 *
 * Karten-YAML:
 *   type: custom:wjg-camera-card
 *   entity: camera.wjg_xm_3820
 *   title: WJG XM-3820 Live       # optional
 *   show_zoom_bar: true            # default true
 *   badges:                        # optional Overlay-Badges
 *     - entity: binary_sensor.wjg_xm_3820_bewegung
 *       icon: mdi:motion-sensor
 *     - entity: binary_sensor.wjg_xm_3820_manipulation
 *       icon: mdi:shield-alert
 *     - entity: switch.wjg_xm_3820_aufnahme
 *       icon: mdi:record-circle
 */

/* ── Shadow-DOM Template ─────────────────────────────────────────────────── */
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
  }

  /* ── Titelzeile ── */
  #title-bar {
    display: none;
    align-items: center;
    padding: 6px 12px 4px;
    color: var(--primary-text-color, #fff);
    font-size: 14px;
    font-weight: 500;
    font-family: var(--paper-font-body1_-_font-family, sans-serif);
  }
  #title-bar.visible { display: flex; }

  /* ── Viewport (Kamerabild + Zoom-Transform) ── */
  #vp {
    position: relative;
    overflow: hidden;
    width: 100%;
    aspect-ratio: 16 / 9;
    cursor: grab;
    touch-action: none;
    background: #000;
  }
  #vp.drag { cursor: grabbing; }

  /* Durch Resize-Handle kann die Höhe überschrieben werden */
  #vp.custom-height { aspect-ratio: unset; }

  /* ── Zoom-Transform-Layer ── */
  #inner {
    position: absolute;
    inset: 0;
    transform-origin: 0 0;
    will-change: transform;
  }
  ha-camera-stream {
    display: block;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  #snap {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    pointer-events: none;
  }

  /* ── Overlay Badges (oben rechts) ── */
  #badges {
    position: absolute;
    top: 6px;
    right: 8px;
    display: flex;
    gap: 5px;
    pointer-events: none;
    z-index: 10;
  }
  .badge {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba(0,0,0,.55);
    backdrop-filter: blur(4px);
    transition: background .2s, color .2s;
  }
  .badge ha-icon {
    --mdc-icon-size: 16px;
    color: rgba(255,255,255,.55);
  }
  .badge.active { background: rgba(var(--rgb-state-active-color, 255,152,0), .85); }
  .badge.active ha-icon { color: #fff; }
  .badge.danger  { background: rgba(var(--rgb-error-color, 244,67,54), .85); }
  .badge.danger  ha-icon { color: #fff; }
  .badge.record  { background: rgba(220,50,50,.85); }
  .badge.record  ha-icon { color: #fff; }

  /* ── Resize-Handles ── */
  .rh {
    position: absolute;
    z-index: 20;
    background: transparent;
  }
  #rh-bottom {
    bottom: 0; left: 10%; width: 80%; height: 6px;
    cursor: ns-resize;
  }
  #rh-right {
    right: 0; top: 10%; width: 6px; height: 80%;
    cursor: ew-resize;
  }
  #rh-corner {
    bottom: 0; right: 0; width: 18px; height: 18px;
    cursor: nwse-resize;
  }
  /* Sichtbarer Grip-Indikator */
  #rh-corner::after {
    content: '';
    position: absolute;
    bottom: 3px; right: 3px;
    width: 10px; height: 10px;
    border-right: 2px solid rgba(255,255,255,.35);
    border-bottom: 2px solid rgba(255,255,255,.35);
  }

  /* ── Zoom-Leiste (unten) ── */
  #zoom-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 5px 8px;
    background: rgba(0,0,0,.7);
  }
  #zoom-bar.hidden { display: none; }
  .zb {
    background: rgba(255,255,255,.12);
    border: 1px solid rgba(255,255,255,.22);
    color: #fff;
    padding: 3px 10px;
    border-radius: 14px;
    cursor: pointer;
    font-size: 12px;
    font-family: sans-serif;
    transition: background .12s;
    line-height: 1.4;
  }
  .zb:hover { background: rgba(255,255,255,.28); }
  .zb.on {
    background: var(--primary-color, #03a9f4);
    border-color: var(--primary-color, #03a9f4);
  }
  #zsp { flex: 1 }
  #zlbl {
    color: #aaa;
    font-size: 12px;
    font-family: monospace;
    min-width: 38px;
    text-align: right;
  }
</style>

<div id="title-bar"></div>
<div id="vp">
  <div id="inner"></div>
  <div id="badges"></div>
  <div id="rh-bottom" class="rh"></div>
  <div id="rh-right"  class="rh"></div>
  <div id="rh-corner" class="rh"></div>
</div>
<div id="zoom-bar">
  <button class="zb on" data-z="1">1×</button>
  <button class="zb"    data-z="2">2×</button>
  <button class="zb"    data-z="4">4×</button>
  <button class="zb"    data-z="8">8×</button>
  <div id="zsp"></div>
  <button class="zb" id="zrb">↺</button>
  <span id="zlbl">1.0×</span>
</div>`;

/* ── Karten-Klasse ───────────────────────────────────────────────────────── */
class WjgCameraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.appendChild(_tpl.content.cloneNode(true));

    // Zoom/Pan
    this._z = 1; this._px = 0; this._py = 0;

    // Drag-Zoom
    this._drag = false; this._dx = 0; this._dy = 0;

    // Pinch
    this._pd = 0; this._pmx = 0; this._pmy = 0;

    // Resize
    this._resizing = null; // 'v' | 'h' | 'both'
    this._rsX = 0; this._rsY = 0;
    this._rsW = 0; this._rsH = 0;

    // State
    this._config  = null;
    this._hass    = null;
    this._ready   = false;
    this._snapTimer = null;
    this._storageKey = '';

    // Bound handlers
    this._mmove = e => this._onWinMouseMove(e);
    this._mup   = () => this._onWinMouseUp();
    this._bindZoomEvents();
    this._bindResizeEvents();
  }

  /* ── Breite sicherstellen sobald Element im DOM ist ─────────────────────── */

  connectedCallback() {
    this.style.width = '100%';
  }

  /* ── Lovelace API ───────────────────────────────────────────────────────── */

  setConfig(config) {
    if (!config.entity) throw new Error('wjg-camera-card: "entity" ist erforderlich');
    this._config = config;
    this._storageKey = `wjg_cam_${config.entity}`;
    this._applyConfig();
    if (this._hass) { this._ensureStream(); this._updateBadges(); }
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureStream();
    // ha-camera-stream benötigt aktuelle hass + stateObj bei jedem Update
    const s = this.shadowRoot.querySelector('ha-camera-stream');
    if (s) {
      s.hass = hass;
      const st = hass.states[this._config.entity];
      if (st) s.stateObj = st;
    }
    this._updateBadges();
  }

  getCardSize() { return 5; }
  static getStubConfig() { return { entity: 'camera.wjg_xm_3820' }; }

  /* ── Konfiguration anwenden ─────────────────────────────────────────────── */

  _applyConfig() {
    const cfg = this._config;

    // Titelzeile
    const titleBar = this.shadowRoot.getElementById('title-bar');
    if (cfg.title) {
      titleBar.textContent = cfg.title;
      titleBar.classList.add('visible');
    } else {
      titleBar.classList.remove('visible');
    }

    // Zoom-Leiste
    const zb = this.shadowRoot.getElementById('zoom-bar');
    if (cfg.show_zoom_bar === false) zb.classList.add('hidden');
    else zb.classList.remove('hidden');

    // Gespeicherte Größe wiederherstellen
    this._restoreSize();
  }

  /* ── Stream Injektion (einmalig) ─────────────────────────────────────────── */

  _ensureStream() {
    if (this._ready || !this._hass || !this._config) return;
    const stateObj = this._hass.states[this._config.entity];
    if (!stateObj) return;

    const inner = this.shadowRoot.getElementById('inner');
    if (!inner) return;

    if (window.customElements.get('ha-camera-stream')) {
      const el = document.createElement('ha-camera-stream');
      el.hass     = this._hass;
      el.stateObj = stateObj;
      el.controls = false;
      el.muted    = true;
      inner.appendChild(el);
    } else {
      // Fallback: periodischer Snapshot
      const img = document.createElement('img');
      img.id = 'snap'; img.alt = 'Kamera';
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

  /* ── Badges (Overlay) ───────────────────────────────────────────────────── */

  _updateBadges() {
    if (!this._hass || !this._config?.badges?.length) return;
    const container = this.shadowRoot.getElementById('badges');

    // Einmalig DOM aufbauen
    if (!container.children.length) {
      this._config.badges.forEach((b, i) => {
        const div  = document.createElement('div');
        div.className = 'badge';
        div.dataset.idx = i;
        const icon = document.createElement('ha-icon');
        icon.setAttribute('icon', b.icon || 'mdi:circle');
        div.appendChild(icon);
        container.appendChild(div);
      });
    }

    // Zustände aktualisieren
    this._config.badges.forEach((b, i) => {
      const div     = container.children[i];
      const stateObj = this._hass.states[b.entity];
      const state   = stateObj?.state;
      div.classList.remove('active', 'danger', 'record');
      if (state === 'on') {
        // Aufnahme-Switch → rot, Sensoren → orange
        if (b.entity.startsWith('switch.')) div.classList.add('record');
        else div.classList.add('active');
      }
    });
  }

  /* ── Zoom-Events ────────────────────────────────────────────────────────── */

  _bindZoomEvents() {
    const vp = this.shadowRoot.getElementById('vp');

    vp.addEventListener('wheel', e => {
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      this._zoomAt((e.clientX - r.left) / r.width,
                   (e.clientY - r.top)  / r.height,
                   this._z * (e.deltaY < 0 ? 1.25 : 0.8));
    }, { passive: false });

    vp.addEventListener('mousedown', e => {
      if (e.button !== 0 || this._resizing) return;
      if (this._z <= 1) return;
      this._drag = true;
      this._dx = e.clientX; this._dy = e.clientY;
      vp.classList.add('drag');
      window.addEventListener('mousemove', this._mmove);
      window.addEventListener('mouseup',   this._mup);
    });

    vp.addEventListener('touchstart', e => {
      if (e.touches.length === 2) {
        this._pd  = this._tdist(e.touches);
        this._pmx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        this._pmy = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      } else if (e.touches.length === 1 && this._z > 1) {
        this._drag = true;
        this._dx = e.touches[0].clientX;
        this._dy = e.touches[0].clientY;
      }
    }, { passive: true });

    vp.addEventListener('touchmove', e => {
      e.preventDefault();
      if (e.touches.length === 2) {
        const d = this._tdist(e.touches);
        const mx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const my = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        if (this._pd > 0) {
          const r = vp.getBoundingClientRect();
          this._zoomAt((mx - r.left) / r.width, (my - r.top) / r.height,
                       this._z * d / this._pd);
          this._panBy(mx - this._pmx, my - this._pmy);
        }
        this._pd = d; this._pmx = mx; this._pmy = my;
      } else if (this._drag && e.touches.length === 1) {
        this._panBy(e.touches[0].clientX - this._dx,
                    e.touches[0].clientY - this._dy);
        this._dx = e.touches[0].clientX;
        this._dy = e.touches[0].clientY;
      }
    }, { passive: false });

    vp.addEventListener('touchend', () => { this._drag = false; this._pd = 0; });

    this.shadowRoot.querySelectorAll('[data-z]').forEach(b =>
      b.addEventListener('click', () => this._zoomAt(0.5, 0.5, parseFloat(b.dataset.z)))
    );
    this.shadowRoot.getElementById('zrb').addEventListener('click', () => this._reset());
  }

  /* ── Resize-Events ──────────────────────────────────────────────────────── */

  _bindResizeEvents() {
    const starts = [
      { id: 'rh-bottom', mode: 'v'    },
      { id: 'rh-right',  mode: 'h'    },
      { id: 'rh-corner', mode: 'both' },
    ];
    starts.forEach(({ id, mode }) => {
      const el = this.shadowRoot.getElementById(id);
      // Mouse
      el.addEventListener('mousedown', e => {
        e.stopPropagation(); e.preventDefault();
        const vp = this.shadowRoot.getElementById('vp');
        const r  = vp.getBoundingClientRect();
        this._resizing = mode;
        this._rsX = e.clientX; this._rsY = e.clientY;
        this._rsW = r.width;   this._rsH = r.height;
        window.addEventListener('mousemove', this._mmove);
        window.addEventListener('mouseup',   this._mup);
      });
      // Touch
      el.addEventListener('touchstart', e => {
        e.stopPropagation();
        const vp = this.shadowRoot.getElementById('vp');
        const r  = vp.getBoundingClientRect();
        this._resizing = mode;
        this._rsX = e.touches[0].clientX;
        this._rsY = e.touches[0].clientY;
        this._rsW = r.width; this._rsH = r.height;
      }, { passive: true });
      el.addEventListener('touchmove', e => {
        if (!this._resizing) return;
        e.preventDefault();
        this._doResize(e.touches[0].clientX, e.touches[0].clientY);
      }, { passive: false });
      el.addEventListener('touchend', () => {
        this._resizing = null;
        this._saveSize();
      });
    });
  }

  /* ── Globale Mouse-Handler (Zoom-Drag + Resize) ─────────────────────────── */

  _onWinMouseMove(e) {
    if (this._resizing) {
      this._doResize(e.clientX, e.clientY);
    } else if (this._drag) {
      this._panBy(e.clientX - this._dx, e.clientY - this._dy);
      this._dx = e.clientX; this._dy = e.clientY;
    }
  }

  _onWinMouseUp() {
    if (this._resizing) {
      this._resizing = null;
      this._saveSize();
    }
    this._drag = false;
    const vp = this.shadowRoot.getElementById('vp');
    if (vp) vp.classList.remove('drag');
    window.removeEventListener('mousemove', this._mmove);
    window.removeEventListener('mouseup',   this._mup);
  }

  _doResize(x, y) {
    const vp = this.shadowRoot.getElementById('vp');
    const dx = x - this._rsX;
    const dy = y - this._rsY;
    if (this._resizing === 'v' || this._resizing === 'both') {
      const newH = Math.max(80, this._rsH + dy);
      vp.style.height = `${newH}px`;
      vp.classList.add('custom-height');
    }
    if (this._resizing === 'h' || this._resizing === 'both') {
      // Breite wird vom Grid kontrolliert – Höhe anpassen damit Aspekt bleibt
      const aspect = this._rsH / this._rsW;
      const newW   = Math.max(120, this._rsW + dx);
      const newH   = Math.max(80, newW * aspect);
      vp.style.height = `${newH}px`;
      vp.classList.add('custom-height');
    }
  }

  _saveSize() {
    const vp = this.shadowRoot.getElementById('vp');
    if (vp.style.height && this._storageKey)
      localStorage.setItem(this._storageKey + '_h', vp.style.height);
  }

  _restoreSize() {
    if (!this._storageKey) return;
    const saved = localStorage.getItem(this._storageKey + '_h');
    if (!saved) return;
    const vp = this.shadowRoot.getElementById('vp');
    if (vp) { vp.style.height = saved; vp.classList.add('custom-height'); }
  }

  /* ── Zoom-Mathematik ────────────────────────────────────────────────────── */

  _tdist(t) {
    const dx = t[0].clientX - t[1].clientX;
    const dy = t[0].clientY - t[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  _zoomAt(cx, cy, nz) {
    nz = Math.max(1, Math.min(10, nz));
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    const s  = nz / this._z;
    this._px = cx * vw * (1 - s) + s * this._px;
    this._py = cy * vh * (1 - s) + s * this._py;
    this._z  = nz;
    this._clamp();
    this._applyZoom();
    this._syncServer(cx, cy, nz);
  }

  _panBy(dx, dy) {
    this._px += dx; this._py += dy;
    this._clamp(); this._applyZoom();
  }

  _clamp() {
    if (this._z <= 1) { this._px = 0; this._py = 0; return; }
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    this._px = Math.min(0, Math.max(vw * (1 - this._z), this._px));
    this._py = Math.min(0, Math.max(vh * (1 - this._z), this._py));
  }

  _applyZoom() {
    const inner = this.shadowRoot.getElementById('inner');
    const lbl   = this.shadowRoot.getElementById('zlbl');
    if (!inner) return;
    inner.style.transform = `translate(${this._px}px,${this._py}px) scale(${this._z})`;
    lbl.textContent = `${this._z.toFixed(1)}×`;
    const zr = Math.round(this._z);
    this.shadowRoot.querySelectorAll('[data-z]').forEach(b =>
      b.classList.toggle('on', parseInt(b.dataset.z) === zr)
    );
  }

  _reset() {
    this._z = 1; this._px = 0; this._py = 0;
    this._applyZoom();
    this._syncServer(0.5, 0.5, 1);
  }

  _syncServer(cx, cy, zoom) {
    if (!this._hass || !this._config) return;
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth || 1, vh = vp.offsetHeight || 1;
    const imgCx = Math.max(0, Math.min(1, (0.5 * vw - this._px) / (this._z * vw)));
    const imgCy = Math.max(0, Math.min(1, (0.5 * vh - this._py) / (this._z * vh)));
    this._hass.callService('wjg_camera', 'set_digital_zoom', {
      entity_id: this._config.entity,
      zoom: this._z, cx: imgCx, cy: imgCy,
    }).catch(() => {});
  }

  disconnectedCallback() {
    if (this._snapTimer) clearInterval(this._snapTimer);
    window.removeEventListener('mousemove', this._mmove);
    window.removeEventListener('mouseup',   this._mup);
  }
}

if (!customElements.get('wjg-camera-card')) {
  customElements.define('wjg-camera-card', WjgCameraCard);
  console.info(
    '%c WJG Camera Card v2.1 %c bereit – 1 Stream, Zoom, Badges, Resize, Breiten-Fix',
    'color:#fff;background:#03a9f4;padding:2px 6px;border-radius:3px;font-weight:bold', ''
  );
}
