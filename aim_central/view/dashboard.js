let containers = [];
let fenceEnabled = true;
let fenceConfig = { lat: 42.3396, lon: -71.0882, radius_m: 200 };
let selectedId = null;
let currentFilter = 'all';
let detailTab = 'overview';
let detailEvents = [];
let detailCalibration = null;
let eventSource = null;
let loadInFlight = null;
let loadQueued = false;
let streamRefreshTimer = null;
let streamRefreshInFlight = false;
let interactionLockUntilMs = 0;

/**
 * Records the current time plus a delay as the interaction lock deadline,
 * preventing stream refreshes from interrupting active user input.
 * @param {number} ms - Duration in milliseconds to lock out stream refreshes.
 */
function markUserInteraction(ms = 350) {
  interactionLockUntilMs = Date.now() + ms;
}

/**
 * Returns true if the user is currently interacting (within the lock window).
 * @returns {boolean}
 */
function isUserInteracting() {
  return Date.now() < interactionLockUntilMs;
}

const API = '';

/**
 * Fetches a JSON resource from the API and returns the parsed response.
 * Returns null on network or HTTP errors.
 * @param {string} url - Path relative to API base.
 * @returns {Promise<any|null>}
 */
async function fetchJSON(url) {
  try {
    const r = await fetch(API + url);
    if (!r.ok) throw new Error(r.statusText);
    return await r.json();
  } catch (e) {
    console.error('Fetch error:', url, e);
    return null;
  }
}

/**
 * Determines the stock level of a bin based on current vs needed stock.
 * @param {number} current - Current stock count.
 * @param {number} needed - Full stock count.
 * @returns {'Red'|'Yellow'|'Green'}
 */
function stockLevel(current, needed) {
  if (current === 0) return 'Red';
  if (current <= needed * 0.5) return 'Yellow';
  return 'Green';
}

const levelClass = { Red: 'level-red', Yellow: 'level-yellow', Green: 'level-green' };
const badgeClass = { Red: 'badge-red', Yellow: 'badge-yellow', Green: 'badge-green' };
const badgeText = { Red: 'CRITICAL', Yellow: 'LOW', Green: 'STOCKED' };
const levelColor = { Red: '#ef4444', Yellow: '#f59e0b', Green: '#10b981' };

/**
 * Generates an SVG gauge arc showing stock percentage for a bin.
 * @param {number} current - Current stock count.
 * @param {number} needed - Full stock count.
 * @param {number} size - Width and height of the SVG in pixels.
 * @returns {string} SVG markup string.
 */
function gaugeArc(current, needed, size) {
  const pct = needed > 0 ? Math.min(100, Math.round((current / needed) * 100)) : 0;
  const sw = 7, r = (size - sw) / 2, cx = size / 2, cy = size / 2;
  const startA = 135, sweep = 270;
  const endA = startA + (sweep * pct) / 100;
  const rad = d => d * Math.PI / 180;
  const arc = (s, e) => {
    const x1 = cx + r * Math.cos(rad(s)), y1 = cy + r * Math.sin(rad(s));
    const x2 = cx + r * Math.cos(rad(e)), y2 = cy + r * Math.sin(rad(e));
    return `M ${x1} ${y1} A ${r} ${r} 0 ${e - s > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  };
  const level = stockLevel(current, needed);
  const c = levelColor[level];
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <path d="${arc(startA, startA + sweep)}" fill="none" stroke="#1a1a24" stroke-width="${sw}" stroke-linecap="round"/>
    ${pct > 0 ? `<path d="${arc(startA, endA)}" fill="none" stroke="${c}" stroke-width="${sw}" stroke-linecap="round" style="filter:drop-shadow(0 0 6px ${c}55);transition:all 0.5s"/>` : ''}
    <text x="${cx}" y="${cy - 1}" text-anchor="middle" dominant-baseline="middle" fill="#e4e4e8" font-size="${size > 100 ? 22 : 16}" font-weight="700" font-family="'Azeret Mono',monospace">${pct}%</text>
    <text x="${cx}" y="${cy + (size > 100 ? 17 : 13)}" text-anchor="middle" fill="#44445a" font-size="${size > 100 ? 10 : 8}" font-family="'Azeret Mono',monospace">STOCK</text>
  </svg>`;
}

/**
 * Generates a small SVG sparkline of stock history for a bin,
 * using only 'accepted' events with a non-null computed_stock.
 * Returns an empty SVG if fewer than 2 data points are available.
 * @param {Array<Object>} events - Array of event objects from the API.
 * @param {number} needed - Full stock count, used to normalize the y-axis.
 * @param {number} w - Width of the SVG in pixels.
 * @param {number} h - Height of the SVG in pixels.
 * @returns {string} SVG markup string.
 */
function sparklineSVG(events, needed, w, h) {
  const pts = events.filter(e => e.decision === 'accepted' && e.computed_stock !== null);
  if (pts.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
  const sorted = [...pts].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const max = Math.max(needed, 1);
  const coords = sorted.map((e, i) => ({
    x: (i / (sorted.length - 1)) * w,
    y: h - 4 - ((e.computed_stock / max) * (h - 8))
  }));
  const line = coords.map(p => `${p.x},${p.y}`).join(' ');
  const fill = `0,${h} ${line} ${w},${h}`;
  const lastLevel = stockLevel(sorted[sorted.length - 1].computed_stock, needed);
  const c = levelColor[lastLevel];
  return `<svg class="sparkline" width="${w}" height="${h}">
    <defs><linearGradient id="sg${c.slice(1)}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${c}" stop-opacity="0.2"/>
      <stop offset="100%" stop-color="${c}" stop-opacity="0"/>
    </linearGradient></defs>
    <polygon points="${fill}" fill="url(#sg${c.slice(1)})"/>
    <polyline points="${line}" fill="none" stroke="${c}" stroke-width="1.5" stroke-linejoin="round"/>
  </svg>`;
}

/**
 * Renders the summary stats bar (total bins, overall %, low count, critical count).
 */
function renderStats() {
  const el = document.getElementById('stats');
  const total = containers.length;
  const totalStock = containers.reduce((s, c) => s + c.current_stock, 0);
  const totalNeeded = containers.reduce((s, c) => s + c.needed_stock, 0);
  const overallPct = totalNeeded > 0 ? Math.round((totalStock / totalNeeded) * 100) : 0;
  const reds = containers.filter(c => stockLevel(c.current_stock, c.needed_stock) === 'Red').length;
  const yellows = containers.filter(c => stockLevel(c.current_stock, c.needed_stock) === 'Yellow').length;
  const pctColor = overallPct > 70 ? 'var(--green)' : overallPct > 40 ? 'var(--yellow)' : 'var(--red)';
  const lowColor = (yellows + reds) > 0 ? 'var(--yellow)' : 'var(--green)';
  const critColor = reds > 0 ? 'var(--red)' : 'var(--green)';
  el.innerHTML = `
    <div class="stat"><span class="stat-val">${total}</span><span class="stat-label">BINS</span></div>
    <div class="stat"><span class="stat-val" style="color:${pctColor}">${overallPct}%</span><span class="stat-label">OVERALL</span></div>
    <div class="stat"><span class="stat-val" style="color:${lowColor}">${yellows + reds}</span><span class="stat-label">LOW</span></div>
    <div class="stat"><span class="stat-val" style="color:${critColor}">${reds}</span><span class="stat-label">CRITICAL</span></div>`;
}

/**
 * Renders the bin card grid, filtered by the current filter selection.
 * Highlights the selected bin if one is active.
 */
function renderGrid() {
  const el = document.getElementById('grid');
  const filtered = containers.filter(c => {
    const level = stockLevel(c.current_stock, c.needed_stock);
    if (currentFilter === 'low') return level === 'Yellow' || level === 'Red';
    if (currentFilter === 'critical') return level === 'Red';
    return true;
  });
  if (filtered.length === 0) { el.innerHTML = '<div class="no-data">No bins match filter</div>'; return; }
  el.innerHTML = filtered.map((c) => {
    const level = stockLevel(c.current_stock, c.needed_stock);
    const sel = selectedId === c.container_id ? 'selected' : '';
    return `
      <div class="card ${levelClass[level]} ${sel}" onclick="selectContainer(${c.container_id})">
        <div class="card-top">
          <span class="card-bin">BIN ${String(c.container_id).padStart(2, '0')}</span>
          <span class="card-badge ${badgeClass[level]}">${badgeText[level]}</span>
        </div>
        <div class="card-name">${c.item_name}</div>
        <div class="card-body">
          <div class="card-gauge">${gaugeArc(c.current_stock, c.needed_stock, 80)}</div>
          <div class="card-info">
            <div class="stock-row">
              <span class="stock-current">${c.current_stock}</span>
              <span class="stock-needed">/ ${c.needed_stock} units</span>
            </div>
            <div class="weight-line">${c.raw_weight_g !== null && c.raw_weight_g !== undefined ? c.raw_weight_g + ' g raw' : 'no readings'}</div>
            <div class="weight-line">${c.item_weight} g per unit</div>
          </div>
        </div>
      </div>`;
  }).join('');
}

/**
 * Renders the detail panel for the currently selected bin.
 * Shows overview, events, or calibration content based on the active tab.
 * Hides the panel if no bin is selected.
 */
function renderDetail() {
  const el = document.getElementById('detail');
  if (selectedId === null) { el.classList.remove('open'); return; }
  const c = containers.find(x => x.container_id === selectedId);
  if (!c) { el.classList.remove('open'); return; }
  el.classList.add('open');
  const level = stockLevel(c.current_stock, c.needed_stock);
  let content = `
    <div class="detail-head">
      <div>
        <div class="detail-bin-id">BIN ${String(c.container_id).padStart(2, '0')}</div>
        <div class="detail-name">${c.item_name}</div>
      </div>
      <button class="close-btn" onclick="closeDetail()">✕</button>
    </div>
    <div class="tab-bar">
      <button class="tab-btn ${detailTab === 'overview' ? 'active' : ''}" onclick="setTab('overview')">OVERVIEW</button>
      <button class="tab-btn ${detailTab === 'events' ? 'active' : ''}" onclick="setTab('events')">EVENTS</button>
      <button class="tab-btn ${detailTab === 'calibration' ? 'active' : ''}" onclick="setTab('calibration')">CALIBRATION</button>
    </div>`;

  if (detailTab === 'overview') {
    content += `
      <div class="detail-gauge">${gaugeArc(c.current_stock, c.needed_stock, 140)}</div>
      <div class="adjust-section">
        <div class="adjust-label">MANUAL STOCK ADJUST</div>
        <div class="adjust-row">
          <button class="adjust-btn adjust-minus" onclick="adjustStock(${c.container_id}, -1)">−</button>
          <div class="adjust-display">${c.current_stock} <span class="adjust-max">/ ${c.needed_stock}</span></div>
          <button class="adjust-btn adjust-plus" onclick="adjustStock(${c.container_id}, 1)">+</button>
        </div>
      </div>
      <button class="tare-btn" id="tare-btn" onclick="sendTare(${c.container_id})">⚖ TARE SENSOR</button>
      <div class="detail-rows">
        <div class="d-row"><span class="d-label">ITEM</span><span class="d-value">${c.item_name}</span></div>
        <div class="d-row"><span class="d-label">RAW WEIGHT</span><span class="d-value" style="color:var(--text1)">${c.raw_weight_g !== null && c.raw_weight_g !== undefined ? c.raw_weight_g + ' g' : '—'}</span></div>
        <div class="d-row"><span class="d-label">STOCK LEVEL</span><span class="d-value" style="color:${levelColor[level]}">${level}</span></div>
        <div class="d-row"><span class="d-label">WEIGHT / UNIT</span><span class="d-value">${c.item_weight} g</span></div>
        <div class="d-row"><span class="d-label">TARE OFFSET</span><span class="d-value">${detailCalibration ? detailCalibration.empty_bin_weight_g.toFixed(1) + ' g' : '0.0 g'}</span></div>
      </div>
      ${detailEvents.length > 0 ? `
        <div class="hist-label">STOCK HISTORY</div>
        <div class="hist-wrap">${sparklineSVG(detailEvents, c.needed_stock, 260, 64)}</div>` : ''}
      <button class="edit-btn" onclick="showEditBinModal(${c.container_id})">✎ EDIT BIN CONFIG</button>
      <button class="delete-btn" onclick="confirmDeleteBin(${c.container_id})">DELETE BIN</button>`;
  } else if (detailTab === 'events') {
    const recent = [...detailEvents].slice(0, 40);
    content += `<div class="events-list">
      ${recent.length === 0 ? '<div class="no-data" style="height:120px">No events recorded</div>' :
        recent.map(e => `<div class="event-row">
          <span class="event-time">${new Date(e.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span>
          <span class="event-decision dec-${e.decision}">${e.decision}</span>
          <span class="event-stock">${e.computed_stock ?? '—'}</span>
        </div>`).join('')}
    </div>`;
  } else if (detailTab === 'calibration') {
    const cal = detailCalibration || { empty_bin_weight_g: 0, scale_factor: 1, min_detectable_weight_g: 0, rounding_mode: 'round' };
    content += `
      <div class="detail-rows">
        <div class="d-row"><span class="d-label">TARE OFFSET</span><span class="d-value">${cal.empty_bin_weight_g.toFixed(1)} g</span></div>
      </div>
      <div style="margin-top:16px">
        <div class="form-group"><label class="form-label">SCALE FACTOR</label><input class="form-input" type="number" step="0.01" id="cal-scale" value="${cal.scale_factor}"></div>
        <div class="form-group"><label class="form-label">MIN DETECTABLE WEIGHT (g)</label><input class="form-input" type="number" step="0.5" id="cal-min" value="${cal.min_detectable_weight_g}"></div>
        <div class="form-group"><label class="form-label">ROUNDING MODE</label>
          <select class="form-input" id="cal-round">
            <option value="round" ${cal.rounding_mode === 'round' ? 'selected' : ''}>Round</option>
            <option value="floor" ${cal.rounding_mode === 'floor' ? 'selected' : ''}>Floor</option>
            <option value="ceil" ${cal.rounding_mode === 'ceil' ? 'selected' : ''}>Ceil</option>
          </select>
        </div>
        <button class="modal-btn modal-btn-primary" style="width:100%" onclick="saveCalibration(${c.container_id})">SAVE CALIBRATION</button>
      </div>`;
  }
  el.innerHTML = content;
}

/**
 * Selects a bin by ID, opening its detail panel and fetching its events and
 * calibration data. Clicking the same bin again closes the detail panel.
 * @param {number} id - The container_id of the bin to select.
 */
async function selectContainer(id) {
  if (selectedId === id) { closeDetail(); return; }
  selectedId = id; detailTab = 'overview'; detailEvents = []; detailCalibration = null;
  renderGrid(); renderDetail();
  const [events, cal] = await Promise.all([fetchJSON(`/api/events/${id}?limit=100`), fetchJSON(`/api/calibration/${id}`)]);
  detailEvents = events || []; detailCalibration = cal;
  renderDetail(); renderGrid();
}

/**
 * Closes the detail panel and clears the selected bin state.
 */
function closeDetail() {
  selectedId = null; detailEvents = []; detailCalibration = null;
  document.getElementById('detail').classList.remove('open'); renderGrid();
}

/**
 * Switches the active tab in the detail panel and re-renders it.
 * @param {'overview'|'events'|'calibration'} t - Tab name to activate.
 */
function setTab(t) { detailTab = t; renderDetail(); }

/**
 * Sets the active grid filter and re-renders the bin grid.
 * @param {'all'|'low'|'critical'} f - Filter to apply.
 */
function setFilter(f) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(btn => { btn.classList.toggle('active', btn.dataset.filter === f); });
  renderGrid();
}

/**
 * Sends a manual stock adjustment to the API and updates the local state.
 * @param {number} containerId - The container_id of the bin to adjust.
 * @param {number} change - Amount to add (positive) or remove (negative).
 */
async function adjustStock(containerId, change) {
  try {
    const r = await fetch(`/api/containers/${containerId}/adjust`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ change }) });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    const c = containers.find(x => x.container_id === containerId);
    if (c) c.current_stock = data.current_stock;
    renderStats(); renderGrid(); renderDetail();
    showToast(`Bin ${containerId} → ${data.current_stock} units`);
  } catch (e) { showToast('Network error'); }
}

/**
 * Triggers a tare operation on the bin's scale sensor via the API.
 * Updates calibration data and re-renders the detail panel on success.
 * @param {number} containerId - The container_id of the bin to tare.
 */
async function sendTare(containerId) {
  const btn = document.getElementById('tare-btn');
  if (btn) btn.classList.add('sending');
  showToast('Taring…');
  try {
    const r = await fetch(`/api/containers/${containerId}/tare`, { method: 'POST' });
    const data = await r.json();
    if (data.status === 'tare_ok') {
      showToast(`Tared bin ${containerId} at ${data.empty_bin_weight_g} g`);
      await loadContainers();
      if (selectedId === containerId) { detailCalibration = await fetchJSON(`/api/calibration/${containerId}`); renderDetail(); }
    } else { showToast(`Tare failed: ${data.error || 'unknown'}`); }
  } catch (e) { showToast('Tare failed: network error'); }
  if (btn) btn.classList.remove('sending');
}

/**
 * Displays a brief toast notification at the bottom of the screen.
 * @param {string} msg - Message to display.
 */
function showToast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

/**
 * Fetches the full container list from the API and re-renders the UI.
 * Debounces concurrent calls: if a load is already in flight, queues one
 * additional refresh to run after it completes.
 */
async function loadContainers() {
  if (loadInFlight) { loadQueued = true; return loadInFlight; }
  loadInFlight = (async () => {
    const data = await fetchJSON('/api/containers');
    if (data && Array.isArray(data)) {
      containers = data;
      renderStats();
      renderGrid();
      // Avoid replacing button DOM while a touch/click is in progress.
      if (selectedId !== null && !isUserInteracting()) renderDetail();
    }
  })();
  try { await loadInFlight; } finally {
    loadInFlight = null;
    if (loadQueued) { loadQueued = false; await loadContainers(); }
  }
}

/**
 * Manually refreshes all container data and shows a confirmation toast.
 */
async function refresh() { await loadContainers(); showToast('Refreshed'); }

// ── Geofence toggle ──────────────────────────────────────────────────────────

/**
 * Updates the fence button appearance to reflect the current enabled state.
 */
function renderFenceBtn() {
  const btn = document.getElementById('fence-btn');
  if (!btn) return;
  btn.classList.toggle('fence-on', fenceEnabled);
  btn.classList.toggle('fence-off', !fenceEnabled);
  btn.textContent = fenceEnabled ? '⊙ FENCE ON' : '⊘ FENCE OFF';
}

/**
 * Fetches the current geofence state from the server and updates the button.
 */
async function loadFenceState() {
  const data = await fetchJSON('/api/geofence');
  if (data !== null) { fenceEnabled = data.enabled; renderFenceBtn(); }
}

/**
 * Fetches the current geofence config (lat/lon/radius) from the server.
 */
async function loadFenceConfig() {
  const data = await fetchJSON('/api/settings/geofence');
  if (data !== null) { fenceConfig = data; }
}

/**
 * Opens the geofence configuration modal pre-populated with current values.
 */
function showFenceConfigModal() {
  openModal(`<div class="modal-title">⚙ GEOFENCE CONFIG</div>
    <div class="form-group"><label class="form-label">LATITUDE</label><input class="form-input" type="number" step="0.000001" id="fence-lat" value="${fenceConfig.lat}"><div class="form-hint">Decimal degrees (e.g. 42.339600)</div></div>
    <div class="form-group"><label class="form-label">LONGITUDE</label><input class="form-input" type="number" step="0.000001" id="fence-lon" value="${fenceConfig.lon}"><div class="form-hint">Decimal degrees (e.g. -71.088200)</div></div>
    <div class="form-group"><label class="form-label">RADIUS (m)</label><input class="form-input" type="number" step="1" min="1" id="fence-radius" value="${fenceConfig.radius_m}"><div class="form-hint">Distance from center in metres</div></div>
    <div class="modal-actions"><button class="modal-btn modal-btn-cancel" onclick="forceCloseModal()">CANCEL</button><button class="modal-btn modal-btn-primary" onclick="saveFenceConfig()">SAVE</button></div>`);
}

/**
 * Reads the fence config form and POSTs updated values to the API.
 */
async function saveFenceConfig() {
  const lat = parseFloat(document.getElementById('fence-lat').value);
  const lon = parseFloat(document.getElementById('fence-lon').value);
  const radius_m = parseFloat(document.getElementById('fence-radius').value);
  if (isNaN(lat) || isNaN(lon) || isNaN(radius_m)) { showToast('Fill in all fields'); return; }
  try {
    const r = await fetch('/api/settings/geofence', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat, lon, radius_m }),
    });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    fenceConfig = data;
    forceCloseModal();
    showToast('Geofence updated');
  } catch (e) { showToast('Network error'); }
}

/**
 * Toggles the geofence on or off and updates the button.
 */
async function toggleFence() {
  try {
    const r = await fetch('/api/geofence', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !fenceEnabled }),
    });
    const data = await r.json();
    fenceEnabled = data.enabled;
    renderFenceBtn();
    showToast(fenceEnabled ? 'Geofence enabled' : 'Geofence disabled');
  } catch (e) { showToast('Network error'); }
}

/**
 * Performs a full data refresh triggered by a stream event.
 * Also refreshes detail-panel event or calibration data if that tab is active.
 * Guards against concurrent runs with a flag.
 */
async function runStreamRefresh() {
  if (streamRefreshInFlight) return;
  streamRefreshInFlight = true;
  try {
    await loadContainers();
    let detailChanged = false;
    if (selectedId !== null) {
      if (detailTab === 'events') {
        detailEvents = (await fetchJSON(`/api/events/${selectedId}?limit=100`)) || [];
        detailChanged = true;
      }
      if (detailTab === 'calibration') {
        detailCalibration = await fetchJSON(`/api/calibration/${selectedId}`);
        detailChanged = true;
      }
      if (detailChanged) renderDetail();
    }
  } finally {
    streamRefreshInFlight = false;
  }
}

/**
 * Schedules a stream refresh with a short debounce delay (120ms) to coalesce
 * bursts of sensor events. Defers further if the user is currently interacting.
 */
function queueStreamRefresh() {
  if (streamRefreshTimer !== null) return;
  // Coalesce bursts of sensor updates to keep the UI responsive.
  streamRefreshTimer = setTimeout(async () => {
    streamRefreshTimer = null;
    if (isUserInteracting()) {
      queueStreamRefresh();
      return;
    }
    await runStreamRefresh();
  }, 120);
}

/**
 * Opens a Server-Sent Events connection to /api/stream and listens for
 * 'inventory' events to trigger UI refreshes.
 */
function connectStream() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/api/stream');
  eventSource.addEventListener('inventory', queueStreamRefresh);
  eventSource.onerror = () => {};
}

/**
 * Updates the clock element in the header with the current local time.
 */
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
}

// ── Modal helpers ────────────────────────────────────────────────────────────

/**
 * Opens the modal overlay with the provided HTML content.
 * @param {string} html - Inner HTML to render inside the modal.
 */
function openModal(html) { document.getElementById('modal').innerHTML = html; document.getElementById('modal-overlay').classList.add('open'); }

/**
 * Closes the modal overlay when clicking the backdrop (not the modal itself).
 * @param {MouseEvent} e - The click event from the overlay.
 */
function closeModal(e) { if (e && e.target !== document.getElementById('modal-overlay')) return; document.getElementById('modal-overlay').classList.remove('open'); }

/**
 * Force-closes the modal overlay regardless of the event target.
 */
function forceCloseModal() { document.getElementById('modal-overlay').classList.remove('open'); }

/**
 * Opens the Add Bin modal with a form for creating a new bin.
 */
function showAddBinModal() {
  openModal(`<div class="modal-title">+ ADD NEW BIN</div>
    <div class="form-group"><label class="form-label">BIN ID</label><input class="form-input" type="number" id="add-bin-id" placeholder="e.g. 2" min="0" max="255"><div class="form-hint">Must match the bin_id byte your STM32 sends</div></div>
    <div class="form-group"><label class="form-label">ITEM NAME</label><input class="form-input" type="text" id="add-item-name" placeholder="e.g. Bandages"></div>
    <div class="form-group"><label class="form-label">WEIGHT PER UNIT (g)</label><input class="form-input" type="number" step="0.1" id="add-item-weight" placeholder="e.g. 18.0"></div>
    <div class="form-group"><label class="form-label">NEEDED STOCK (full count)</label><input class="form-input" type="number" id="add-needed" placeholder="e.g. 20" min="1"></div>
    <div class="modal-actions"><button class="modal-btn modal-btn-cancel" onclick="forceCloseModal()">CANCEL</button><button class="modal-btn modal-btn-primary" onclick="submitAddBin()">ADD BIN</button></div>`);
}

/**
 * Reads the Add Bin form and POSTs the new bin to the API.
 * Closes the modal and refreshes the grid on success.
 */
async function submitAddBin() {
  const cid = document.getElementById('add-bin-id').value, name = document.getElementById('add-item-name').value;
  const weight = document.getElementById('add-item-weight').value, needed = document.getElementById('add-needed').value;
  if (!cid || !name || !weight || !needed) { showToast('Fill in all fields'); return; }
  try {
    const r = await fetch('/api/containers/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ container_id: parseInt(cid), item_name: name, item_weight: parseFloat(weight), needed_stock: parseInt(needed) }) });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    forceCloseModal(); showToast(`Bin ${cid} added`); await loadContainers();
  } catch (e) { showToast('Network error'); }
}

/**
 * Opens the Edit Bin modal pre-populated with the existing bin configuration.
 * @param {number} cid - The container_id of the bin to edit.
 */
function showEditBinModal(cid) {
  const c = containers.find(x => x.container_id === cid); if (!c) return;
  openModal(`<div class="modal-title">EDIT BIN ${String(cid).padStart(2, '0')}</div>
    <div class="form-group"><label class="form-label">ITEM NAME</label><input class="form-input" type="text" id="edit-item-name" value="${c.item_name}"></div>
    <div class="form-group"><label class="form-label">WEIGHT PER UNIT (g)</label><input class="form-input" type="number" step="0.1" id="edit-item-weight" value="${c.item_weight}"></div>
    <div class="form-group"><label class="form-label">NEEDED STOCK (full count)</label><input class="form-input" type="number" id="edit-needed" value="${c.needed_stock}" min="1"></div>
    <div class="modal-actions"><button class="modal-btn modal-btn-cancel" onclick="forceCloseModal()">CANCEL</button><button class="modal-btn modal-btn-primary" onclick="submitEditBin(${cid})">SAVE</button></div>`);
}

/**
 * Reads the Edit Bin form and POSTs the updated config to the API.
 * Closes the modal and refreshes the grid on success.
 * @param {number} cid - The container_id of the bin being edited.
 */
async function submitEditBin(cid) {
  const name = document.getElementById('edit-item-name').value, weight = document.getElementById('edit-item-weight').value, needed = document.getElementById('edit-needed').value;
  if (!name || !weight || !needed) { showToast('Fill in all fields'); return; }
  try {
    const r = await fetch(`/api/containers/${cid}/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_name: name, item_weight: parseFloat(weight), needed_stock: parseInt(needed) }) });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    forceCloseModal(); showToast(`Bin ${cid} updated`); await loadContainers();
  } catch (e) { showToast('Network error'); }
}

/**
 * Opens a confirmation modal before deleting a bin.
 * @param {number} cid - The container_id of the bin to delete.
 */
function confirmDeleteBin(cid) {
  openModal(`<div class="modal-title">DELETE BIN ${String(cid).padStart(2, '0')}?</div>
    <p style="color:var(--text2);font-size:13px;line-height:1.6;margin-bottom:20px">This removes the bin, all its sensor history, and calibration data. This cannot be undone.</p>
    <div class="modal-actions"><button class="modal-btn modal-btn-cancel" onclick="forceCloseModal()">CANCEL</button><button class="modal-btn modal-btn-danger" onclick="deleteBin(${cid})">DELETE</button></div>`);
}

/**
 * Sends a DELETE request to remove the bin and all its associated data.
 * Closes the modal and detail panel, then refreshes the grid on success.
 * @param {number} cid - The container_id of the bin to delete.
 */
async function deleteBin(cid) {
  try {
    const r = await fetch(`/api/containers/${cid}`, { method: 'DELETE' });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    forceCloseModal(); closeDetail(); showToast(`Bin ${cid} deleted`); await loadContainers();
  } catch (e) { showToast('Network error'); }
}

/**
 * Reads the calibration form and POSTs updated calibration settings to the API.
 * Refreshes the detail panel on success.
 * @param {number} cid - The container_id of the bin being calibrated.
 */
async function saveCalibration(cid) {
  const sf = document.getElementById('cal-scale').value, md = document.getElementById('cal-min').value, rm = document.getElementById('cal-round').value;
  try {
    const r = await fetch(`/api/calibration/${cid}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scale_factor: parseFloat(sf), min_detectable_weight_g: parseFloat(md), rounding_mode: rm }) });
    const data = await r.json();
    if (data.error) { showToast('Error: ' + data.error); return; }
    showToast('Calibration saved'); detailCalibration = await fetchJSON(`/api/calibration/${cid}`); renderDetail();
  } catch (e) { showToast('Network error'); }
}

// ── Touch-drag scrolling ─────────────────────────────────────────────────────

/**
 * Attaches touch and mouse drag-to-scroll behavior to a scrollable element.
 * Suppresses click events that follow a drag to prevent accidental selections.
 * @param {HTMLElement} el - The scrollable container element.
 */
function enableTouchDragScroll(el) {
  if (!el) return;
  let isDown = false, startY, scrollTop, moved;

  el.addEventListener('touchstart', function(e) {
    markUserInteraction();
    isDown = true; moved = false; startY = e.touches[0].pageY; scrollTop = el.scrollTop;
  }, { passive: true });

  el.addEventListener('touchmove', function(e) {
    if (!isDown) return; moved = true;
    el.scrollTop = scrollTop + (startY - e.touches[0].pageY);
  }, { passive: true });

  el.addEventListener('touchend', function() { isDown = false; }, { passive: true });

  // Mouse drag (for Pi Connect / VNC)
  el.addEventListener('mousedown', function(e) {
    markUserInteraction();
    if (e.target.closest('button, input, select, a')) return;
    isDown = true; moved = false; startY = e.pageY; scrollTop = el.scrollTop;
    el.classList.add('dragging');
  });

  el.addEventListener('mousemove', function(e) {
    if (!isDown) return;
    const delta = startY - e.pageY;
    if (Math.abs(delta) > 8) moved = true;
    if (moved) { e.preventDefault(); el.scrollTop = scrollTop + delta; }
  });

  el.addEventListener('mouseup', function(e) {
    if (moved && isDown) {
      el.addEventListener('click', function suppress(ev) {
        if (ev.target.closest('button, input, select, a')) return;
        ev.stopPropagation();
        ev.preventDefault();
      }, { capture: true, once: true });
    }
    isDown = false; el.classList.remove('dragging');
  });

  el.addEventListener('mouseleave', function() { isDown = false; el.classList.remove('dragging'); });
}

// ── Init ─────────────────────────────────────────────────────────────────────

/**
 * Initializes the dashboard: sets up interaction tracking, starts the clock,
 * loads container data, connects the SSE stream, and enables drag-scroll.
 */
async function init() {
  // Track interaction globally so stream refreshes don't interrupt taps.
  document.addEventListener('pointerdown', () => markUserInteraction(450), { passive: true });
  document.addEventListener('pointerup', () => markUserInteraction(180), { passive: true });

  updateClock();
  setInterval(updateClock, 1000);
  await Promise.all([loadContainers(), loadFenceState(), loadFenceConfig()]);
  connectStream();

  enableTouchDragScroll(document.querySelector('.grid-wrap'));
  enableTouchDragScroll(document.getElementById('detail'));
}

init();
