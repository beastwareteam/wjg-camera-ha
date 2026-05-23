/**
 * wjg-camera-card.js  v3.0
 *
 * NEU in v3.0:
 *   show_ptz: true       → PTZ D-Pad direkt auf dem Video (hover/touch)
 *   show_minimap: true   → Minimap mit Zoom-Viewport-Indikator (oben links)
 *   min_zoom / max_zoom  → konfigurierbarer Zoom-Bereich (default 1 / 8)
 *   aspect_ratio         → z.B. "16/9" (default), "4/3", "1/1"
 *   ptz_service          → z.B. "button.press" (default)
 *
 * v2.2 Features bleiben erhalten:
 *   Zoom-Bar, Badges, Resize-Handles, localStorage-Persistenz
 */

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

  /* ── Titelleiste ── */
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

  /* ── Viewport ── */
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
  #vp.custom-height { aspect-ratio: unset; }

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

  /* ── Badges (oben rechts) ── */
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
  .badge ha-icon { --mdc-icon-size: 16px; color: rgba(255,255,255,.55); }
  .badge.active { background: rgba(var(--rgb-state-active-color, 255,152,0), .85); }
  .badge.active ha-icon { color: #fff; }
  .badge.danger  { background: rgba(var(--rgb-error-color, 244,67,54), .85); }
  .badge.danger  ha-icon { color: #fff; }
  .badge.record  { background: rgba(220,50,50,.85); }
  .badge.record  ha-icon { color: #fff; }
  .badge.warn    { background: rgba(255,140,0,.85); }
  .badge.warn    ha-icon { color: #fff; }

  /* ── Minimap (oben links) ── */
  #minimap {
    position: absolute;
    top: 8px;
    left: 8px;
    width: 108px;
    height: 62px;
    border: 1px solid rgba(255,255,255,.28);
    border-radius: 5px;
    overflow: hidden;
    background: rgba(0,0,0,.55);
    z-index: 25;
    opacity: 0;
    transition: opacity .3s;
    pointer-events: none;
  }
  #minimap.mm-always { opacity: .8; }
  #vp:hover #minimap  { opacity: .92; }
  #minimap.mm-hidden  { display: none !important; }
  #mm-img {
    width: 100%; height: 100%;
    object-fit: cover;
    display: block;
    opacity: .65;
  }
  #mm-rect {
    position: absolute;
    inset: 0;
    border: 2px solid var(--primary-color, #03a9f4);
    border-radius: 2px;
    background: rgba(3,169,244,.12);
    box-sizing: border-box;
    transition: all .12s;
    pointer-events: none;
  }

  /* ── PTZ Overlay (unten links) ── */
  #ptz-overlay {
    position: absolute;
    bottom: 10px;
    left: 10px;
    z-index: 30;
    display: flex;
    align-items: flex-end;
    gap: 5px;
    opacity: 0;
    transition: opacity .25s;
    pointer-events: none;
  }
  #ptz-overlay.ptz-hidden  { display: none !important; }
  #vp:hover #ptz-overlay   { opacity: 1; pointer-events: auto; }
  #ptz-overlay.ptz-touch   { opacity: .85; pointer-events: auto; }

  #ptz-pad {
    display: grid;
    grid-template-columns: repeat(3, 38px);
    grid-template-rows: repeat(3, 38px);
    gap: 3px;
  }
  .pb {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,.22);
    background: rgba(0,0,0,.62);
    backdrop-filter: blur(6px);
    color: #fff;
    font-size: 17px;
    cursor: pointer;
    transition: background .12s, transform .08s;
    touch-action: manipulation;
    -webkit-tap-highlight-color: transparent;
    box-sizing: border-box;
  }
  .pb:hover  { background: rgba(3,169,244,.7); border-color: rgba(3,169,244,.8); }
  .pb:active { transform: scale(.9); background: rgba(3,169,244,.9); }
  .pb.pb-empty { background: transparent; border-color: transparent; cursor: default; pointer-events: none; }
  .pb.pb-stop  { font-size: 13px; letter-spacing: -.5px; }

  #ptz-zoom-col {
    display: flex;
    flex-direction: column;
    gap: 3px;
    justify-content: center;
    margin-bottom: 2px;
  }
  .pb-z {
    width: 38px;
    height: 38px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,.22);
    background: rgba(0,0,0,.62);
    backdrop-filter: blur(6px);
    color: #fff;
    font-size: 22px;
    font-weight: 600;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background .12s, transform .08s;
    touch-action: manipulation;
  }
  .pb-z:hover  { background: rgba(3,169,244,.7); border-color: rgba(3,169,244,.8); }
  .pb-z:active { transform: scale(.9); }

  /* ── Resize-Handles ── */
  .rh { position: absolute; z-index: 20; background: transparent; }
  #rh-bottom { bottom: 0; left: 10%; width: 80%; height: 6px; cursor: ns-resize; }
  #rh-right  { right: 0; top: 10%; width: 6px; height: 80%; cursor: ew-resize; }
  #rh-corner { bottom: 0; right: 0; width: 18px; height: 18px; cursor: nwse-resize; }
  #rh-corner::after {
    content: '';
    position: absolute;
    bottom: 3px; right: 3px;
    width: 10px; height: 10px;
    border-right: 2px solid rgba(255,255,255,.35);
    border-bottom: 2px solid rgba(255,255,255,.35);
  }

  /* ── Zoom-Bar ── */
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
  #zsp { flex: 1; }
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

  <!-- Minimap (oben links, nur bei show_minimap: true) -->
  <div id="minimap" class="mm-hidden">
    <img id="mm-img" alt="" />
    <div id="mm-rect"></div>
  </div>

  <!-- PTZ Overlay (unten links, nur bei show_ptz: true) -->
  <div id="ptz-overlay" class="ptz-hidden">
    <div id="ptz-pad">
      <div class="pb pb-empty"></div>
      <button class="pb" id="pb-up">▲</button>
      <div class="pb pb-empty"></div>

      <button class="pb" id="pb-left">◄</button>
      <button class="pb pb-stop" id="pb-stop">⏹</button>
      <button class="pb" id="pb-right">►</button>

      <div class="pb pb-empty"></div>
      <button class="pb" id="pb-down">▼</button>
      <div class="pb pb-empty"></div>
    </div>
    <div id="ptz-zoom-col">
      <button class="pb-z" id="pb-zin" title="Zoom +">+</button>
      <button class="pb-z" id="pb-zout" title="Zoom −">−</button>
    </div>
  </div>

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

/* ══════════════════════════════════════════════════════════════════════════ */

class WjgCameraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.appendChild(_tpl.content.cloneNode(true));

    // Zoom-State
    this._z   = 1;
    this._px  = 0;
    this._py  = 0;
    this._minZ = 1;
    this._maxZ = 8;

    // Drag / Pinch
    this._drag = false; this._dx = 0; this._dy = 0;
    this._pd   = 0;     this._pmx = 0; this._pmy = 0;

    // Resize
    this._resizing = null;
    this._rsX = 0; this._rsY = 0; this._rsH = 0; this._rsW = 0;

    // State
    this._config     = null;
    this._hass       = null;
    this._ready      = false;
    this._snapTimer  = null;
    this._mmTimer    = null;
    this._storageKey = '';
    this._isTouchDev = false;

    // Bound handlers
    this._mmove = e => this._onWinMouseMove(e);
    this._mup   = () => this._onWinMouseUp();

    this._bindZoomEvents();
    this._bindResizeEvents();
    this._bindPtzEvents();
  }

  /* ── Lifecycle ── */

  connectedCallback() {
    const savedW = this._storageKey
      ? localStorage.getItem(this._storageKey + '_w') : null;
    if (!savedW) this.style.width = '100%';

    // Touch-Gerät erkennen (PTZ-Overlay immer sichtbar)
    this._isTouchDev = window.matchMedia('(pointer: coarse)').matches;
    this._applyPtzTouchMode();
  }

  disconnectedCallback() {
    if (this._snapTimer) clearInterval(this._snapTimer);
    if (this._mmTimer)   clearInterval(this._mmTimer);
    window.removeEventListener('mousemove', this._mmove);
    window.removeEventListener('mouseup',   this._mup);
  }

  /* ── Config & HASS ── */

  setConfig(config) {
    if (!config.entity) throw new Error('wjg-camera-card: "entity" ist erforderlich');
    this._config = config;
    this._storageKey = `wjg_cam_${config.entity}`;
    this._minZ = parseFloat(config.min_zoom) || 1;
    this._maxZ = parseFloat(config.max_zoom) || 8;
    this._applyConfig();
    if (this._hass) { this._ensureStream(); this._updateBadges(); }
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureStream();
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
  static get version() { return '3.0'; }

  /* ── Config anwenden ── */

  _applyConfig() {
    const cfg = this._config;

    // Titelleiste
    const titleBar = this.shadowRoot.getElementById('title-bar');
    if (cfg.title) {
      titleBar.textContent = cfg.title;
      titleBar.classList.add('visible');
    } else {
      titleBar.classList.remove('visible');
    }

    // Aspect-Ratio
    const vp = this.shadowRoot.getElementById('vp');
    if (cfg.aspect_ratio) {
      const ar = String(cfg.aspect_ratio).replace('/', ' / ');
      vp.style.aspectRatio = ar;
    }

    // Zoom-Bar
    const zb = this.shadowRoot.getElementById('zoom-bar');
    if (cfg.show_zoom_bar === false) zb.classList.add('hidden');
    else zb.classList.remove('hidden');

    // PTZ Overlay
    const ptz = this.shadowRoot.getElementById('ptz-overlay');
    if (cfg.show_ptz) ptz.classList.remove('ptz-hidden');
    else              ptz.classList.add('ptz-hidden');

    // Minimap
    const mm = this.shadowRoot.getElementById('minimap');
    if (cfg.show_minimap) {
      mm.classList.remove('mm-hidden');
      this._startMmRefresh();
    } else {
      mm.classList.add('mm-hidden');
    }

    this._restoreSize();
    this._applyPtzTouchMode();
  }

  _applyPtzTouchMode() {
    const ptz = this.shadowRoot.getElementById('ptz-overlay');
    if (!ptz) return;
    if (this._isTouchDev && this._config?.show_ptz) {
      ptz.classList.add('ptz-touch');
    } else {
      ptz.classList.remove('ptz-touch');
    }
  }

  /* ── Stream ── */

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

  /* ── Badges ── */

  _updateBadges() {
    if (!this._hass || !this._config?.badges?.length) return;
    const container = this.shadowRoot.getElementById('badges');
    if (!container.children.length) {
      this._config.badges.forEach((b, i) => {
        const div  = document.createElement('div');
        div.className  = 'badge';
        div.dataset.idx = i;
        const icon = document.createElement('ha-icon');
        icon.setAttribute('icon', b.icon || 'mdi:circle');
        div.appendChild(icon);
        container.appendChild(div);
      });
    }
    this._config.badges.forEach((b, i) => {
      const div      = container.children[i];
      const stateObj = this._hass.states[b.entity];
      const state    = stateObj?.state;
      const cls      = b.state_class || 'active';
      div.classList.remove('active', 'danger', 'record', 'warn');
      if (state === 'on') div.classList.add(cls);
    });
  }

  /* ── PTZ ── */

  _ptzEntityId(action) {
    // camera.wjg_xm_3820 → wjg_xm_3820
    const suffix = this._config.entity.replace(/^[^.]+\./, '');
    const map = {
      up:       'ptz_up',
      down:     'ptz_down',
      left:     'ptz_left',
      right:    'ptz_right',
      stop:     'ptz_stopp',
      zoom_in:  'ptz_zoom_in',
      zoom_out: 'ptz_zoom_out',
      home:     'ptz_home',
    };
    return `button.${suffix}_${map[action] || action}`;
  }

  _pressPtz(action) {
    if (!this._hass) return;
    const svc = String(this._config?.ptz_service || 'button.press');
    const [domain, service] = svc.split('.');
    const entityId = this._ptzEntityId(action);
    this._hass.callService(domain, service, { entity_id: entityId }).catch(() => {});
  }

  _bindPtzEvents() {
    const btns = {
      'pb-up':   'up',
      'pb-down': 'down',
      'pb-left': 'left',
      'pb-right':'right',
      'pb-stop': 'stop',
      'pb-zin':  'zoom_in',
      'pb-zout': 'zoom_out',
    };
    for (const [id, action] of Object.entries(btns)) {
      const el = this.shadowRoot.getElementById(id);
      if (!el) continue;
      el.addEventListener('click', e => {
        e.stopPropagation();
        this._pressPtz(action);
      });
      // Touch: verhindere Zoom-Gesten auf den Buttons
      el.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
    }
  }

  /* ── Minimap ── */

  _startMmRefresh() {
    if (this._mmTimer) return;
    this._refreshMmImg();
    this._mmTimer = setInterval(() => this._refreshMmImg(), 5000);
  }

  _refreshMmImg() {
    const mmImg = this.shadowRoot.getElementById('mm-img');
    if (!mmImg) return;
    const st = this._hass?.states[this._config?.entity];
    if (st?.attributes?.entity_picture)
      mmImg.src = st.attributes.entity_picture + '&_t=' + Date.now();
  }

  _updateMmRect() {
    const mm = this.shadowRoot.getElementById('minimap');
    if (!mm || mm.classList.contains('mm-hidden')) return;
    const rect = this.shadowRoot.getElementById('mm-rect');
    if (!rect) return;

    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth  || 1;
    const vh = vp.offsetHeight || 1;
    const mw = mm.offsetWidth  || 108;
    const mh = mm.offsetHeight || 62;

    if (this._z <= 1) {
      rect.style.left   = '0';
      rect.style.top    = '0';
      rect.style.width  = mw + 'px';
      rect.style.height = mh + 'px';
      return;
    }

    const rw = Math.min(mw, mw / this._z);
    const rh = Math.min(mh, mh / this._z);
    const rx = Math.max(0, Math.min(mw - rw, (-this._px / vw) * (mw / this._z)));
    const ry = Math.max(0, Math.min(mh - rh, (-this._py / vh) * (mh / this._z)));

    rect.style.left   = rx + 'px';
    rect.style.top    = ry + 'px';
    rect.style.width  = rw + 'px';
    rect.style.height = rh + 'px';
  }

  /* ── Zoom-Events ── */

  _bindZoomEvents() {
    const vp = this.shadowRoot.getElementById('vp');

    // Mausrad
    vp.addEventListener('wheel', e => {
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      this._zoomAt(
        (e.clientX - r.left) / r.width,
        (e.clientY - r.top)  / r.height,
        this._z * (e.deltaY < 0 ? 1.25 : 0.8)
      );
    }, { passive: false });

    // Maus-Drag
    vp.addEventListener('mousedown', e => {
      if (e.button !== 0 || this._resizing) return;
      if (this._z <= 1) return;
      this._drag = true;
      this._dx = e.clientX; this._dy = e.clientY;
      vp.classList.add('drag');
      window.addEventListener('mousemove', this._mmove);
      window.addEventListener('mouseup',   this._mup);
    });

    // Touch: Pinch + Pan
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
        const d  = this._tdist(e.touches);
        const mx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const my = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        if (this._pd > 0) {
          const r = vp.getBoundingClientRect();
          this._zoomAt(
            (mx - r.left) / r.width, (my - r.top) / r.height,
            this._z * d / this._pd
          );
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

    // Zoom-Bar Buttons
    this.shadowRoot.querySelectorAll('[data-z]').forEach(b =>
      b.addEventListener('click', () =>
        this._zoomAt(0.5, 0.5, parseFloat(b.dataset.z))
      )
    );
    this.shadowRoot.getElementById('zrb')
      .addEventListener('click', () => this._reset());
  }

  /* ── Resize-Handles ── */

  _bindResizeEvents() {
    [
      { id: 'rh-bottom', mode: 'v'    },
      { id: 'rh-right',  mode: 'h'    },
      { id: 'rh-corner', mode: 'both' },
    ].forEach(({ id, mode }) => {
      const el = this.shadowRoot.getElementById(id);

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

  _onWinMouseMove(e) {
    if (this._resizing) {
      this._doResize(e.clientX, e.clientY);
    } else if (this._drag) {
      this._panBy(e.clientX - this._dx, e.clientY - this._dy);
      this._dx = e.clientX; this._dy = e.clientY;
    }
  }

  _onWinMouseUp() {
    if (this._resizing) { this._resizing = null; this._saveSize(); }
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
    if (this._resizing === 'v') {
      vp.style.height = Math.max(80, this._rsH + dy) + 'px';
      vp.classList.add('custom-height');
    }
    if (this._resizing === 'h') {
      const newW = Math.max(120, this._rsW + dx);
      this.style.width = newW + 'px';
      vp.style.height  = Math.max(80, newW * (this._rsH / this._rsW)) + 'px';
      vp.classList.add('custom-height');
    }
    if (this._resizing === 'both') {
      this.style.width    = Math.max(120, this._rsW + dx) + 'px';
      vp.style.height     = Math.max(80,  this._rsH + dy) + 'px';
      vp.classList.add('custom-height');
    }
  }

  /* ── Persistenz ── */

  _saveSize() {
    if (!this._storageKey) return;
    const vp = this.shadowRoot.getElementById('vp');
    if (vp.style.height) localStorage.setItem(this._storageKey + '_h', vp.style.height);
    if (this.style.width) localStorage.setItem(this._storageKey + '_w', this.style.width);
  }

  _restoreSize() {
    if (!this._storageKey) return;
    const savedH = localStorage.getItem(this._storageKey + '_h');
    const savedW = localStorage.getItem(this._storageKey + '_w');
    const vp = this.shadowRoot.getElementById('vp');
    if (savedH && vp) { vp.style.height = savedH; vp.classList.add('custom-height'); }
    if (savedW)        { this.style.width = savedW; }
  }

  /* ── Zoom-Logik ── */

  _tdist(t) {
    const dx = t[0].clientX - t[1].clientX;
    const dy = t[0].clientY - t[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  _zoomAt(cx, cy, nz) {
    nz = Math.max(this._minZ, Math.min(this._maxZ, nz));
    const vp = this.shadowRoot.getElementById('vp');
    const vw = vp.offsetWidth, vh = vp.offsetHeight;
    const s  = nz / this._z;
    this._px = cx * vw * (1 - s) + s * this._px;
    this._py = cy * vh * (1 - s) + s * this._py;
    this._z  = nz;
    this._clamp();
    this._applyZoom();
    this._syncServer();
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
    this._updateMmRect();
  }

  _reset() {
    this._z = this._minZ; this._px = 0; this._py = 0;
    this._applyZoom();
    this._syncServer();
  }

  _syncServer() {
    if (!this._hass || !this._config) return;
    const vp  = this.shadowRoot.getElementById('vp');
    const vw  = vp.offsetWidth || 1, vh = vp.offsetHeight || 1;
    const cx  = Math.max(0, Math.min(1, (0.5 * vw - this._px) / (this._z * vw)));
    const cy  = Math.max(0, Math.min(1, (0.5 * vh - this._py) / (this._z * vh)));
    this._hass.callService('wjg_camera', 'set_digital_zoom', {
      entity_id: this._config.entity,
      zoom: this._z, cx, cy,
    }).catch(() => {});
  }
}

/* ── Registrierung ── */
if (!customElements.get('wjg-camera-card')) {
  customElements.define('wjg-camera-card', WjgCameraCard);
  console.info(
    '%c WJG Camera Card v3.0 %c PTZ-Overlay · Minimap · Zoom-Bar · Resize',
    'color:#fff;background:#03a9f4;padding:2px 6px;border-radius:3px;font-weight:bold', ''
  );
}
