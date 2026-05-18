/**
 * wjg-camera-card.js  –  Interaktiver Zoom für WJG XM-3820 in Home Assistant
 *
 * Lovelace-Ressource einmalig hinzufügen:
 *   Einstellungen → Dashboards → Ressourcen → + Ressource hinzufügen
 *   URL : /wjg_camera/wjg-camera-card.js
 *   Typ : JavaScript Modul
 *
 * Karte konfigurieren (YAML-Modus):
 *   type: custom:wjg-camera-card
 *   entity: camera.wjg_xm_3820
 */

const _tpl = document.createElement('template');
_tpl.innerHTML = `
<style>
  :host {
    display: block;
    background: #000;
    border-radius: var(--ha-card-border-radius, 4px);
    overflow: hidden;
    box-shadow: var(--ha-card-box-shadow, none);
  }
  #vp {
    position: relative;
    overflow: hidden;
    width: 100%;
    aspect-ratio: 16 / 9;
    cursor: grab;
    touch-action: none;
    background: #111;
  }
  #vp.drag { cursor: grabbing; }
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
  #bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 5px 8px;
    background: rgba(0,0,0,0.72);
  }
  .zb {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22);
    color: #fff;
    padding: 3px 12px;
    border-radius: 16px;
    cursor: pointer;
    font-size: 12px;
    font-family: sans-serif;
    transition: background 0.12s;
  }
  .zb:hover { background: rgba(255,255,255,0.28); }
  .zb.on {
    background: var(--primary-color, #03a9f4);
    border-color: var(--primary-color, #03a9f4);
  }
  #sp { flex: 1 }
  #lbl {
    color: #ccc;
    font-size: 12px;
    font-family: monospace;
    min-width: 40px;
    text-align: right;
  }
</style>
<div id="vp"><div id="inner"></div></div>
<div id="bar">
  <button class="zb on" data-z="1">1×</button>
  <button class="zb"    data-z="2">2×</button>
  <button class="zb"    data-z="4">4×</button>
  <button class="zb"    data-z="8">8×</button>
  <div id="sp"></div>
  <button class="zb" id="rb">↺</button>
  <span id="lbl">1.0×</span>
</div>`;

class WjgCameraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.appendChild(_tpl.content.cloneNode(true));
    // zoom/pan state
    this._z  = 1;
    this._px = 0;
    this._py = 0;
    // drag state
    this._drag = false;
    this._dx = 0; this._dy = 0;
    // pinch state
    this._pd = 0; this._pmx = 0; this._pmy = 0;
    // HA state
    this._config = null;
    this._hass   = null;
    this._ready  = false;     // stream element injected
    this._snapTimer = null;
    // bound handlers for removal
    this._mmove = (e) => this._onMouseMove(e);
    this._mup   = ()  => this._onMouseUp();
    this._bindEvents();
  }

  // ── Lovelace API ────────────────────────────────────────────────────────────

  setConfig(config) {
    if (!config.entity) throw new Error('wjg-camera-card: "entity" muss gesetzt sein');
    this._config = config;
    if (this._hass) this._ensureStream();
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureStream();
    // ha-camera-stream needs live hass + stateObj updates
    if (this._streamEl && this._streamEl.tagName === 'HA-CAMERA-STREAM') {
      this._streamEl.hass = hass;
      const s = hass.states[this._config.entity];
      if (s) this._streamEl.stateObj = s;
    }
  }

  getCardSize() { return 5; }

  static getStubConfig() { return { entity: 'camera.wjg_xm_3820' }; }

  // ── Stream Injektion ────────────────────────────────────────────────────────

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
      this._streamEl = el;
    } else {
      // Fallback: periodischer Snapshot-Refresh
      const img  = document.createElement('img');
      img.id     = 'snap';
      img.alt    = 'Kamera';
      inner.appendChild(img);
      this._streamEl  = img;
      this._startSnap();
    }
    this._ready = true;
  }

  _startSnap() {
    this._refreshSnap();
    this._snapTimer = setInterval(() => this._refreshSnap(), 1500);
  }

  _refreshSnap() {
    const img     = this._streamEl;
    const stateObj = this._hass?.states[this._config?.entity];
    if (img && stateObj?.attributes?.entity_picture) {
      img.src = stateObj.attributes.entity_picture + '&_t=' + Date.now();
    }
  }

  // ── Event-Binding ───────────────────────────────────────────────────────────

  _bindEvents() {
    const vp = this.shadowRoot.getElementById('vp');

    // Mausrad → Zoom um Mauszeiger
    vp.addEventListener('wheel', (e) => {
      e.preventDefault();
      const r  = vp.getBoundingClientRect();
      const cx = (e.clientX - r.left) / r.width;
      const cy = (e.clientY - r.top)  / r.height;
      this._zoomAt(cx, cy, this._z * (e.deltaY < 0 ? 1.25 : 0.8));
    }, { passive: false });

    // Maus-Drag → Verschieben (nur wenn gezoomt)
    vp.addEventListener('mousedown', (e) => {
      if (e.button !== 0 || this._z <= 1) return;
      this._drag = true;
      this._dx = e.clientX; this._dy = e.clientY;
      vp.classList.add('drag');
      window.addEventListener('mousemove', this._mmove);
      window.addEventListener('mouseup',   this._mup);
    });

    // Touch: Pinch-Zoom + 1-Finger-Drag
    vp.addEventListener('touchstart', (e) => {
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

    vp.addEventListener('touchmove', (e) => {
      e.preventDefault();
      if (e.touches.length === 2) {
        const d  = this._tdist(e.touches);
        const mx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const my = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        if (this._pd > 0) {
          const r  = vp.getBoundingClientRect();
          this._zoomAt((mx - r.left) / r.width, (my - r.top) / r.height, this._z * d / this._pd);
          this._panBy(mx - this._pmx, my - this._pmy);
        }
        this._pd = d; this._pmx = mx; this._pmy = my;
      } else if (this._drag && e.touches.length === 1) {
        this._panBy(e.touches[0].clientX - this._dx, e.touches[0].clientY - this._dy);
        this._dx = e.touches[0].clientX;
        this._dy = e.touches[0].clientY;
      }
    }, { passive: false });

    vp.addEventListener('touchend', () => {
      this._drag = false;
      this._pd   = 0;
    });

    // Preset-Buttons
    this.shadowRoot.querySelectorAll('[data-z]').forEach(b =>
      b.addEventListener('click', () => this._zoomAt(0.5, 0.5, parseFloat(b.dataset.z)))
    );
    this.shadowRoot.getElementById('rb').addEventListener('click', () => this._reset());
  }

  _onMouseMove(e) {
    if (!this._drag) return;
    this._panBy(e.clientX - this._dx, e.clientY - this._dy);
    this._dx = e.clientX; this._dy = e.clientY;
  }

  _onMouseUp() {
    this._drag = false;
    const vp = this.shadowRoot.getElementById('vp');
    if (vp) vp.classList.remove('drag');
    window.removeEventListener('mousemove', this._mmove);
    window.removeEventListener('mouseup',   this._mup);
  }

  _tdist(t) {
    const dx = t[0].clientX - t[1].clientX;
    const dy = t[0].clientY - t[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  // ── Zoom/Pan Mathematik ─────────────────────────────────────────────────────

  /**
   * Zoom um den Punkt (cx, cy) in Viewport-Koordinaten (0..1).
   * Formel: pan_neu = (cx*vw) * (1 - s) + s * pan_alt   (wobei s = nz/oz)
   */
  _zoomAt(cx, cy, nz) {
    nz = Math.max(1, Math.min(10, nz));
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth;
    const vh = vp.offsetHeight;
    const s  = nz / this._z;
    this._px = cx * vw * (1 - s) + s * this._px;
    this._py = cy * vh * (1 - s) + s * this._py;
    this._z  = nz;
    this._clamp();
    this._apply();
    // Server-seitigen digitalen Zoom synchronisieren
    this._syncServer(cx, cy, nz);
  }

  _panBy(dx, dy) {
    this._px += dx; this._py += dy;
    this._clamp();
    this._apply();
  }

  _clamp() {
    if (this._z <= 1) { this._px = 0; this._py = 0; return; }
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth;
    const vh = vp.offsetHeight;
    this._px = Math.min(0, Math.max(vw * (1 - this._z), this._px));
    this._py = Math.min(0, Math.max(vh * (1 - this._z), this._py));
  }

  _apply() {
    const inner = this.shadowRoot.getElementById('inner');
    const lbl   = this.shadowRoot.getElementById('lbl');
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
    this._apply();
    this._syncServer(0.5, 0.5, 1);
  }

  // ── Server-Sync: Koordinaten an HA-Service übergeben ────────────────────────
  // Dadurch stimmt der Pillow-Crop im Snapshot mit dem CSS-Zoom überein.

  _syncServer(cx, cy, zoom) {
    if (!this._hass || !this._config) return;
    // Bildmittelpunkt aus Pan/Zoom zurückrechnen
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth || 1;
    const vh = vp.offsetHeight || 1;
    // Der Viewport-Mittelpunkt (0.5, 0.5) entspricht diesem Bildbereich:
    const imgCx = (0.5 * vw - this._px) / (this._z * vw);
    const imgCy = (0.5 * vh - this._py) / (this._z * vh);
    this._hass.callService('wjg_camera', 'set_digital_zoom', {
      entity_id: this._config.entity,
      zoom:  this._z,
      cx:    Math.max(0, Math.min(1, imgCx)),
      cy:    Math.max(0, Math.min(1, imgCy)),
    }).catch(() => {});  // Fehler ignorieren – Service optional
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
    '%c WJG Camera Card 1.0 %c bereit',
    'color:#fff;background:#03a9f4;padding:2px 6px;border-radius:3px;font-weight:bold',
    ''
  );
}
