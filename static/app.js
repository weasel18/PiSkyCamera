'use strict';

// ── Shutter speed table (1/3-stop steps, 1/8000 → 60s) ───────────────────────

const SHUTTER_SPEEDS = [
    125, 156, 200,          // 1/8000  1/6400  1/5000
    250, 313, 400,          // 1/4000  1/3200  1/2500
    500, 625, 800,          // 1/2000  1/1600  1/1250
   1000, 1250, 1563,        // 1/1000  1/800   1/640
   2000, 2500, 3125,        // 1/500   1/400   1/320
   4000, 5000, 6250,        // 1/250   1/200   1/160
   8000, 10000, 12500,      // 1/125   1/100   1/80
  16667, 20000, 25000,      // 1/60    1/50    1/40
  33333, 40000, 50000,      // 1/30    1/25    1/20
  66667, 76923, 100000,     // 1/15    1/13    1/10
  125000, 166667, 200000,   // 1/8     1/6     1/5
  250000, 333333, 400000,   // 1/4     1/3     0.4"
  500000, 600000, 800000,   // 1/2     0.6"    0.8"
  1000000, 1300000, 1600000,// 1"      1.3"    1.6"
  2000000, 2500000, 3000000,// 2"      2.5"    3"
  4000000, 5000000, 6000000,// 4"      5"      6"
  8000000, 10000000, 13000000, // 8"   10"     13"
  15000000, 20000000, 25000000,// 15"  20"     25"
  30000000, 40000000, 50000000,// 30"  40"     50"
  60000000, 80000000, 100000000, // 60"  80"    100"
  120000000, 150000000, 200000000,// 120" 150"   200"
  240000000,                   // 240"
];

const SHUTTER_LABELS = [
  '1/8000','1/6400','1/5000',
  '1/4000','1/3200','1/2500',
  '1/2000','1/1600','1/1250',
  '1/1000','1/800','1/640',
  '1/500','1/400','1/320',
  '1/250','1/200','1/160',
  '1/125','1/100','1/80',
  '1/60','1/50','1/40',
  '1/30','1/25','1/20',
  '1/15','1/13','1/10',
  '1/8','1/6','1/5',
  '1/4','1/3','0.4"',
  '1/2','0.6"','0.8"',
  '1"','1.3"','1.6"',
  '2"','2.5"','3"',
  '4"','5"','6"',
  '8"','10"','13"',
  '15"','20"','25"',
  '30"','40"','50"',
  '60"','80"','100"',
  '120"','150"','200"',
  '240"',
];

const SHUTTER_MAX_IDX = SHUTTER_SPEEDS.length - 1;

function usToIndex(us) {
  // Find the index of the nearest shutter speed
  let best = 0, bestDiff = Infinity;
  for (let i = 0; i < SHUTTER_SPEEDS.length; i++) {
    const diff = Math.abs(SHUTTER_SPEEDS[i] - us);
    if (diff < bestDiff) { bestDiff = diff; best = i; }
  }
  return best;
}

function formatExposure(us) {
  if (!us || us <= 0) return '—';
  return SHUTTER_LABELS[usToIndex(us)] ?? '—';
}

// ── State ─────────────────────────────────────────────────────────────────────

let settings = {};          // current settings, populated from /api/settings
let pendingUpdate = {};     // accumulated changes waiting to send
let sendTimer = null;       // debounce timer
let countdownTimer = null;  // long-exposure countdown interval
let lastFrameTs = 0;

// ── API calls ─────────────────────────────────────────────────────────────────

function scheduleUpdate() {
  if (sendTimer) clearTimeout(sendTimer);
  sendTimer = setTimeout(flushUpdate, 400);
}

async function flushUpdate() {
  if (!Object.keys(pendingUpdate).length) return;
  const payload = { ...pendingUpdate };
  pendingUpdate = {};
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      // Server rejected the payload — refetch canonical settings so the UI
      // doesn't keep showing the user's invalid input as if it had stuck.
      try { await loadSettings(); } catch (_) {}
      return;
    }
    // Server may have clamped values (e.g. fps capped at 10).  Merge the
    // applied dict back into local state and re-render any affected control.
    if (data.applied) {
      Object.assign(settings, data.applied);
      renderControls(settings);
    }
    if (data.restart) {
      setStatus('Restarting camera…', 'warn');
    }
  } catch (e) {
    console.error('Settings update failed:', e);
    try { await loadSettings(); } catch (_) {}
  }
}

async function loadSettings() {
  const res = await fetch('/api/settings');
  if (!res.ok) throw new Error('settings fetch failed');
  settings = await res.json();
  renderControls(settings);
}

function queueSetting(key, value) {
  pendingUpdate[key] = value;
  settings[key] = value;
  scheduleUpdate();
}

// ── Control handlers ──────────────────────────────────────────────────────────

function setExposureMode(mode) {
  settings.exposure_mode = mode;
  queueSetting('exposure_mode', mode);
  updateExpModeUI(mode);
}

function updateExpModeUI(mode) {
  document.getElementById('btn-ae-auto').classList.toggle('active', mode === 'auto');
  document.getElementById('btn-ae-manual').classList.toggle('active', mode === 'manual');
  document.getElementById('manual-exp-section').style.opacity = mode === 'manual' ? '1' : '0.4';
}

function setWbMode(mode) {
  settings.awb_mode = mode;
  queueSetting('awb_mode', mode);
  updateWbModeUI(mode);
}

function updateWbModeUI(mode) {
  document.getElementById('btn-wb-auto').classList.toggle('active', mode === 'auto');
  document.getElementById('btn-wb-manual').classList.toggle('active', mode === 'manual');
  document.getElementById('manual-wb-section').style.opacity = mode === 'manual' ? '1' : '0.4';
}

function onExpSlider(v) {
  const idx = parseInt(v);
  const us = SHUTTER_SPEEDS[idx];
  settings.exposure_time = us;
  document.getElementById('exp-display').textContent = SHUTTER_LABELS[idx];
  queueSetting('exposure_time', us);
}

function stepExposure(delta) {
  const slider = document.getElementById('exp-slider');
  const newIdx = Math.max(0, Math.min(SHUTTER_MAX_IDX, parseInt(slider.value) + delta));
  slider.value = newIdx;
  onExpSlider(newIdx);
}

function onAeConstraint(v) {
  queueSetting('ae_constraint_mode', parseInt(v));
}

function onEvSlider(v) {
  const val = parseFloat(v);
  const sign = val > 0 ? '+' : '';
  document.getElementById('ev-display').textContent = sign + val.toFixed(2);
  queueSetting('exposure_value', val);
}

function onGainSlider(v) {
  const gain = parseFloat(v);
  settings.analogue_gain = gain;
  document.getElementById('gain-display').textContent = gain.toFixed(1) + '×';
  queueSetting('analogue_gain', gain);
}

function onColorSlider(key, v, displayId) {
  const val = parseFloat(v);
  document.getElementById(displayId).textContent = val.toFixed(2);
  queueSetting(key, val);
}

function onImgSlider(key, v, displayId) {
  const val = parseFloat(v);
  document.getElementById(displayId).textContent = val.toFixed(2);
  queueSetting(key, val);
}

function onNrMode(v) {
  queueSetting('noise_reduction_mode', parseInt(v));
}

function onResolutionChange(v) {
  const [w, h] = v.split('x').map(Number);
  queueSetting('resolution', [w, h]);
}

function onFpsSlider(v) {
  const fps = parseInt(v);
  document.getElementById('fps-display').textContent = fps;
  queueSetting('stream_fps', fps);
}

function onFlip() {
  queueSetting('hflip', document.getElementById('hflip').checked);
  queueSetting('vflip', document.getElementById('vflip').checked);
}

// ── Presets ───────────────────────────────────────────────────────────────────

// Loaded from /api/presets on boot. Shape: { name: {label, values:{...}} }
let presets = {};

// Editor schema. Each entry drives one row per preset card. `type` controls
// the input widget; for 'seconds' the stored value is microseconds but the
// editor reads/writes seconds for usability.
const PRESET_FIELDS = [
  { key: 'exposure_mode', label: 'Exposure', type: 'select',
    options: [['auto','Auto'], ['manual','Manual']] },
  { key: 'exposure_time', label: 'Shutter (s)', type: 'seconds',
    min: 0.0001, max: 240, step: 0.001 },
  { key: 'analogue_gain', label: 'Gain (×)', type: 'number',
    min: 1, max: 16, step: 0.1 },
  { key: 'exposure_value', label: 'EV bias', type: 'number',
    min: -3, max: 3, step: 0.25 },
  { key: 'ae_constraint_mode', label: 'AE bias', type: 'select',
    options: [[0,'Normal'], [1,'Highlights'], [2,'Shadows']] },
  { key: 'awb_mode', label: 'WB mode', type: 'select',
    options: [['auto','Auto'], ['manual','Manual']] },
  { key: 'colour_gain_r', label: 'WB Red', type: 'number',
    min: 0.5, max: 4.0, step: 0.05 },
  { key: 'colour_gain_b', label: 'WB Blue', type: 'number',
    min: 0.5, max: 4.0, step: 0.05 },
  { key: 'noise_reduction_mode', label: 'NR mode', type: 'select',
    options: [[0,'Off'], [3,'Minimal'], [1,'Fast'], [2,'High Q']] },
  { key: 'stream_fps', label: 'Stream FPS', type: 'number',
    min: 1, max: 10, step: 1 },
  { key: 'brightness', label: 'Brightness', type: 'number',
    min: -1, max: 1, step: 0.05 },
  { key: 'contrast', label: 'Contrast', type: 'number',
    min: 0, max: 3, step: 0.05 },
  { key: 'saturation', label: 'Saturation', type: 'number',
    min: 0, max: 2, step: 0.05 },
  { key: 'sharpness', label: 'Sharpness', type: 'number',
    min: 0, max: 2, step: 0.05 },
];

async function loadPresets() {
  const res = await fetch('/api/presets');
  if (!res.ok) throw new Error('presets fetch failed');
  presets = await res.json();
  renderPresetButtons();
  populatePresetSelects(settings);
  if (!document.getElementById('preset-editor').hidden) {
    renderPresetEditor();
  }
}

function renderPresetButtons() {
  const grid = document.getElementById('preset-grid');
  grid.innerHTML = '';
  const names = Object.keys(presets);
  if (!names.length) {
    grid.innerHTML = '<div class="hint" style="padding:0">No presets yet.</div>';
    return;
  }
  for (const name of names) {
    const btn = document.createElement('button');
    btn.className = 'preset-btn';
    btn.textContent = presets[name].label || name;
    btn.onclick = () => applyPreset(name);
    grid.appendChild(btn);
  }
}

async function applyPreset(name) {
  const preset = presets[name];
  if (!preset || !preset.values) return;
  Object.assign(settings, preset.values);
  Object.assign(pendingUpdate, preset.values);
  if (sendTimer) clearTimeout(sendTimer);
  renderControls(settings);
  await flushUpdate();
}

// ── Preset editor ─────────────────────────────────────────────────────────────

function togglePresetEditor() {
  const panel = document.getElementById('preset-editor');
  const btn = document.getElementById('preset-edit-toggle');
  const opening = panel.hidden;
  panel.hidden = !opening;
  btn.textContent = opening ? 'Hide editor ▴' : 'Edit presets ▾';
  if (opening) renderPresetEditor();
}

function renderPresetEditor() {
  const panel = document.getElementById('preset-editor');
  panel.innerHTML = '';
  for (const name of Object.keys(presets)) {
    panel.appendChild(buildPresetCard(name, presets[name]));
  }
  panel.appendChild(buildAddPresetRow());
}

function buildPresetCard(name, preset) {
  const card = document.createElement('div');
  card.className = 'preset-card';
  card.dataset.name = name;

  const header = document.createElement('div');
  header.className = 'preset-card-header';
  const labelIn = document.createElement('input');
  labelIn.type = 'text';
  labelIn.value = preset.label || name;
  labelIn.dataset.role = 'label';
  labelIn.maxLength = 40;
  const nameKey = document.createElement('span');
  nameKey.className = 'name-key';
  nameKey.textContent = name;
  header.appendChild(labelIn);
  header.appendChild(nameKey);
  card.appendChild(header);

  for (const f of PRESET_FIELDS) {
    card.appendChild(buildPresetField(f, preset.values || {}));
  }

  const actions = document.createElement('div');
  actions.className = 'preset-card-actions';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'primary';
  saveBtn.textContent = 'Save';
  saveBtn.onclick = () => savePresetCard(card);
  const delBtn = document.createElement('button');
  delBtn.className = 'danger';
  delBtn.textContent = 'Delete';
  delBtn.onclick = () => deletePresetCard(card);
  actions.appendChild(saveBtn);
  actions.appendChild(delBtn);
  card.appendChild(actions);

  const status = document.createElement('div');
  status.className = 'preset-card-status';
  status.dataset.role = 'status';
  card.appendChild(status);

  return card;
}

function buildPresetField(field, values) {
  const row = document.createElement('div');
  row.className = 'preset-field';
  row.dataset.key = field.key;
  row.dataset.type = field.type;

  const has = Object.prototype.hasOwnProperty.call(values, field.key);

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = has;
  cb.dataset.role = 'enable';
  row.appendChild(cb);

  const lbl = document.createElement('label');
  lbl.textContent = field.label;
  row.appendChild(lbl);

  let input;
  if (field.type === 'select') {
    input = document.createElement('select');
    for (const [v, l] of field.options) {
      const o = document.createElement('option');
      o.value = String(v);
      o.textContent = l;
      input.appendChild(o);
    }
    input.value = has ? String(values[field.key]) : String(field.options[0][0]);
  } else if (field.type === 'seconds') {
    input = document.createElement('input');
    input.type = 'number';
    input.min = field.min;
    input.max = field.max;
    input.step = field.step;
    input.value = has ? (values[field.key] / 1_000_000) : 1;
  } else {
    input = document.createElement('input');
    input.type = 'number';
    input.min = field.min;
    input.max = field.max;
    input.step = field.step;
    input.value = has ? values[field.key] : field.min;
  }
  input.dataset.role = 'value';
  input.disabled = !has;
  cb.onchange = () => { input.disabled = !cb.checked; };
  row.appendChild(input);

  return row;
}

function readPresetCard(card) {
  const name = card.dataset.name;
  const label = card.querySelector('input[data-role=label]').value.trim() || name;
  const values = {};
  for (const row of card.querySelectorAll('.preset-field')) {
    const cb = row.querySelector('input[data-role=enable]');
    if (!cb.checked) continue;
    const valueEl = row.querySelector('[data-role=value]');
    const key = row.dataset.key;
    const type = row.dataset.type;
    if (type === 'select') {
      const v = valueEl.value;
      values[key] = (key === 'exposure_mode' || key === 'awb_mode') ? v : parseInt(v);
    } else if (type === 'seconds') {
      const sec = parseFloat(valueEl.value);
      values[key] = Math.round(sec * 1_000_000);
    } else {
      const step = parseFloat(valueEl.step);
      values[key] = (step >= 1) ? parseInt(valueEl.value) : parseFloat(valueEl.value);
    }
  }
  return { name, label, values };
}

function setCardStatus(card, text, kind) {
  const el = card.querySelector('[data-role=status]');
  el.textContent = text;
  el.className = 'preset-card-status' + (kind ? ' ' + kind : '');
}

async function savePresetCard(card) {
  const { name, label, values } = readPresetCard(card);
  setCardStatus(card, 'Saving…');
  try {
    const res = await fetch('/api/presets/' + encodeURIComponent(name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, values }),
    });
    const data = await res.json();
    if (!res.ok) {
      setCardStatus(card, data.error || 'Save failed', 'error');
      return;
    }
    presets[name] = data.preset;
    setCardStatus(card, 'Saved', 'ok');
    renderPresetButtons();
    populatePresetSelects(settings);
  } catch (e) {
    setCardStatus(card, 'Network error', 'error');
  }
}

async function deletePresetCard(card) {
  const name = card.dataset.name;
  const label = presets[name]?.label || name;
  if (!confirm(`Delete preset "${label}"?`)) return;
  setCardStatus(card, 'Deleting…');
  try {
    const res = await fetch('/api/presets/' + encodeURIComponent(name), { method: 'DELETE' });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setCardStatus(card, data.error || 'Delete failed', 'error');
      return;
    }
    delete presets[name];
    renderPresetEditor();
    renderPresetButtons();
    populatePresetSelects(settings);
  } catch (e) {
    setCardStatus(card, 'Network error', 'error');
  }
}

function buildAddPresetRow() {
  const wrap = document.createElement('div');
  wrap.id = 'preset-add-row';
  const labelIn = document.createElement('input');
  labelIn.type = 'text';
  labelIn.placeholder = 'New preset label';
  labelIn.maxLength = 40;
  const btn = document.createElement('button');
  btn.textContent = '+ Add';
  btn.onclick = () => addPreset(labelIn);
  wrap.appendChild(labelIn);
  wrap.appendChild(btn);
  return wrap;
}

function slugify(label) {
  return label.toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 32) || 'preset';
}

async function addPreset(labelInput) {
  const label = labelInput.value.trim();
  if (!label) { labelInput.focus(); return; }
  let name = slugify(label);
  // Ensure uniqueness by appending a numeric suffix if needed.
  if (presets[name]) {
    let n = 2;
    while (presets[`${name}_${n}`]) n++;
    name = `${name}_${n}`;
  }
  try {
    const res = await fetch('/api/presets/' + encodeURIComponent(name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label, values: {} }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Create failed');
      return;
    }
    presets[name] = data.preset;
    labelInput.value = '';
    renderPresetEditor();
    renderPresetButtons();
    populatePresetSelects(settings);
  } catch (e) {
    alert('Network error');
  }
}

// ── Populate controls from settings object ────────────────────────────────────

function renderControls(s) {
  // Exposure mode
  updateExpModeUI(s.exposure_mode || 'auto');

  // Exposure time slider
  const expUs = s.exposure_time || 4000;  // default 1/250
  const expIdx = usToIndex(expUs);
  const slider = document.getElementById('exp-slider');
  slider.value = expIdx;
  document.getElementById('exp-display').textContent = SHUTTER_LABELS[expIdx];

  // AE constraint mode
  const aec = document.getElementById('ae-constraint');
  if (aec) aec.value = String(s.ae_constraint_mode ?? 0);

  // EV offset
  const ev = s.exposure_value ?? 0.0;
  document.getElementById('ev-slider').value = ev;
  const evSign = ev > 0 ? '+' : '';
  document.getElementById('ev-display').textContent = evSign + parseFloat(ev).toFixed(2);

  // Gain
  const gain = s.analogue_gain || 1.0;
  document.getElementById('gain-slider').value = gain;
  document.getElementById('gain-display').textContent = parseFloat(gain).toFixed(1) + '×';

  // AWB mode
  updateWbModeUI(s.awb_mode || 'auto');

  // Colour gains
  const rg = s.colour_gain_r || 2.0;
  const bg = s.colour_gain_b || 1.5;
  document.getElementById('r-gain').value = rg;
  document.getElementById('r-display').textContent = parseFloat(rg).toFixed(2);
  document.getElementById('b-gain').value = bg;
  document.getElementById('b-display').textContent = parseFloat(bg).toFixed(2);

  // Noise reduction
  const nrSel = document.getElementById('nr-mode');
  if (nrSel) nrSel.value = String(s.noise_reduction_mode ?? 0);

  // Image adjustments
  setSliderVal('brightness', s.brightness ?? 0.0,  'br-display');
  setSliderVal('contrast',   s.contrast   ?? 1.0,  'ct-display');
  setSliderVal('saturation', s.saturation ?? 1.0,  'sat-display');
  setSliderVal('sharpness',  s.sharpness  ?? 1.0,  'sh-display');

  // Resolution
  const res = s.resolution || [1920, 1080];
  const resStr = `${res[0]}x${res[1]}`;
  const sel = document.getElementById('resolution-select');
  for (let opt of sel.options) {
    if (opt.value === resStr) { sel.value = resStr; break; }
  }

  // FPS
  const fps = s.stream_fps || 30;
  document.getElementById('fps-slider').value = fps;
  document.getElementById('fps-display').textContent = fps;

  // Flips
  document.getElementById('hflip').checked = !!s.hflip;
  document.getElementById('vflip').checked = !!s.vflip;

  // Fisheye
  const fisheyeOn = !!s.fisheye_lens;
  document.getElementById('btn-fisheye-off').classList.toggle('active', !fisheyeOn);
  document.getElementById('btn-fisheye-on').classList.toggle('active',   fisheyeOn);
  document.getElementById('fisheye-opts').style.opacity = fisheyeOn ? '1' : '0.4';
  if (s.fisheye_fov != null) document.getElementById('fisheye-fov').value = s.fisheye_fov;

  // Schedule
  const schedOn = !!s.schedule_enabled;
  document.getElementById('btn-sched-off').classList.toggle('active', !schedOn);
  document.getElementById('btn-sched-on').classList.toggle('active',   schedOn);
  if (s.latitude  != null) document.getElementById('sched-lat').value = s.latitude;
  if (s.longitude != null) document.getElementById('sched-lon').value = s.longitude;
  if (s.schedule_sunset_offset  != null) document.getElementById('sched-sunset-off').value  = s.schedule_sunset_offset;
  if (s.schedule_sunrise_offset != null) document.getElementById('sched-sunrise-off').value = s.schedule_sunrise_offset;
  populatePresetSelects(s);
}

function populatePresetSelects(s) {
  for (const [selId, key] of [
    ['day-preset-sel', 'schedule_day_preset'],
    ['night-preset-sel', 'schedule_night_preset'],
  ]) {
    const sel = document.getElementById(selId);
    if (!sel) continue;
    const prev = s?.[key] ?? sel.value;
    sel.innerHTML = '';
    for (const name of Object.keys(presets)) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = presets[name].label || name;
      sel.appendChild(opt);
    }
    if (prev && presets[prev]) sel.value = prev;
  }
}

function setSliderVal(id, val, displayId) {
  document.getElementById(id).value = val;
  document.getElementById(displayId).textContent = parseFloat(val).toFixed(2);
}

// ── Live preview + SSE ────────────────────────────────────────────────────────

function refreshPreview() {
  const img = document.getElementById('preview');
  img.src = '/snapshot.jpg?t=' + Date.now();
}

function setStatus(text, state) {
  const dot = document.getElementById('cam-dot');
  const lbl = document.getElementById('status-label');
  dot.className = 'status-dot' + (state ? ' ' + state : '');
  lbl.textContent = text;
}

let _lastColourGains = null;

function updateMetaOverlay(data) {
  document.getElementById('meta-exp').textContent  = 'Exp: ' + (data.exposure_fmt || '—');
  document.getElementById('meta-gain').textContent = 'Gain: ' + (data.gain ? data.gain + '×' : '—');
  document.getElementById('meta-lux').textContent  = data.lux > 0 ? 'Lux: ' + data.lux : '';
  document.getElementById('frame-time').textContent = data.time || '—';

  if (data.colour_gains && data.colour_gains.length === 2) {
    _lastColourGains = data.colour_gains;
    const r = data.colour_gains[0].toFixed(2);
    const b = data.colour_gains[1].toFixed(2);
    document.getElementById('wb-live-r').textContent = 'R ' + r;
    document.getElementById('wb-live-b').textContent = 'B ' + b;
  }
}

function applyAutoWb() {
  if (!_lastColourGains) return;
  const r = parseFloat(_lastColourGains[0].toFixed(2));
  const b = parseFloat(_lastColourGains[1].toFixed(2));
  // Clamp to slider range
  const rc = Math.max(0.5, Math.min(4.0, r));
  const bc = Math.max(0.5, Math.min(4.0, b));
  document.getElementById('r-gain').value = rc;
  document.getElementById('b-gain').value = bc;
  document.getElementById('r-display').textContent = rc.toFixed(2);
  document.getElementById('b-display').textContent = bc.toFixed(2);
  setWbMode('manual');
  queueSetting('colour_gain_r', rc);
  queueSetting('colour_gain_b', bc);
}

function startCountdown(exposureUs) {
  if (countdownTimer) clearInterval(countdownTimer);
  const expSec = exposureUs / 1e6;
  if (expSec < 1) {
    document.getElementById('capture-label').textContent = 'Live';
    document.getElementById('capture-progress').style.width = '0%';
    return;
  }
  // Seed elapsed from lastFrameTs so the bar resumes correctly after a page
  // refresh rather than always restarting from zero.
  const alreadyElapsed = lastFrameTs > 0
    ? Math.max(0, Date.now() / 1000 - lastFrameTs)
    : 0;
  const start = Date.now() - alreadyElapsed * 1000;
  const bar = document.getElementById('capture-progress');
  const lbl = document.getElementById('capture-label');
  countdownTimer = setInterval(() => {
    const elapsed = (Date.now() - start) / 1000;
    const pct = Math.min(100, (elapsed / expSec) * 100);
    bar.style.width = pct + '%';
    const rem = expSec - elapsed;
    lbl.textContent = rem > 0
      ? `Capturing… ${rem.toFixed(rem > 10 ? 0 : 1)}s remaining`
      : 'Processing…';
    if (rem <= 0) clearInterval(countdownTimer);
  }, 100);
}

function connectSSE() {
  const evtSource = new EventSource('/api/events');

  evtSource.onmessage = (e) => {
    if (e.data.startsWith(':')) return;  // ping
    try {
      const data = JSON.parse(e.data);
      lastFrameTs = data.ts;
      refreshPreview();
      updateMetaOverlay(data);
      setStatus('Live', 'alive');
      // Restart countdown for next frame (manual mode only)
      startCountdown(settings.exposure_mode === 'manual' ? (settings.exposure_time || 0) : 0);
    } catch (_) {}
  };

  evtSource.onerror = () => {
    setStatus('Reconnecting…', 'warn');
    evtSource.close();
    setTimeout(connectSSE, 3000);
  };
}

// ── Fisheye ───────────────────────────────────────────────────────────────────

function setFisheye(enabled) {
  document.getElementById('btn-fisheye-off').classList.toggle('active', !enabled);
  document.getElementById('btn-fisheye-on').classList.toggle('active',  enabled);
  document.getElementById('fisheye-opts').style.opacity = enabled ? '1' : '0.4';
  queueSetting('fisheye_lens', enabled);
}

// ── Schedule ──────────────────────────────────────────────────────────────────

function setSchedEnabled(enabled) {
  document.getElementById('btn-sched-off').classList.toggle('active', !enabled);
  document.getElementById('btn-sched-on').classList.toggle('active',  enabled);
  queueSetting('schedule_enabled', enabled);
}

function onLocation() {
  const lat = parseFloat(document.getElementById('sched-lat').value);
  const lon = parseFloat(document.getElementById('sched-lon').value);
  if (!isNaN(lat)) queueSetting('latitude', lat);
  if (!isNaN(lon)) queueSetting('longitude', lon);
}

function onSchedPreset(period, value) {
  queueSetting(period === 'day' ? 'schedule_day_preset' : 'schedule_night_preset', value);
}

function onSchedOffset(which, value) {
  const key = which === 'sunset' ? 'schedule_sunset_offset' : 'schedule_sunrise_offset';
  queueSetting(key, parseInt(value));
}

async function setOverride(hours) {
  try {
    await fetch('/api/schedule/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'set', hours }),
    });
    loadScheduleStatus();
  } catch (e) { console.error('Override failed:', e); }
}

async function setOverrideUntilSunrise() {
  try {
    await fetch('/api/schedule/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'until_sunrise' }),
    });
    loadScheduleStatus();
  } catch (e) { console.error('Override failed:', e); }
}

async function clearOverride() {
  try {
    await fetch('/api/schedule/override', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'clear' }),
    });
    loadScheduleStatus();
  } catch (e) { console.error('Override failed:', e); }
}

async function loadScheduleStatus() {
  try {
    const res = await fetch('/api/schedule');
    const d = await res.json();
    const el = document.getElementById('sched-status');
    if (!d.enabled) { el.textContent = 'Schedule disabled'; return; }
    let txt = `Period: ${d.current_period}`;
    if (d.next_transition_epoch) {
      const next = new Date(d.next_transition_epoch * 1000);
      txt += ` → ${d.next_period} at ${next.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    }
    if (d.override_active && d.override_until_epoch) {
      const until = new Date(d.override_until_epoch * 1000);
      txt += ` [override until ${until.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}]`;
    }
    el.textContent = txt;
  } catch (_) {
    document.getElementById('sched-status').textContent = '—';
  }
}

// ── System stats ──────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    const d = await res.json();
    document.getElementById('stat-temp').textContent =
      d.cpu_temp_c != null ? d.cpu_temp_c + '°C' : '—';
    document.getElementById('stat-load').textContent =
      d.load_1m != null ? d.load_1m.toFixed(2) + ' / ' + d.load_5m.toFixed(2) : '—';
    document.getElementById('stat-mem').textContent =
      d.mem_avail_mb != null ? d.mem_avail_mb + ' MB free (' + d.mem_used_pct + '% used)' : '—';
    document.getElementById('stat-disk').textContent =
      d.disk_free_gb != null ? d.disk_free_gb.toFixed(1) + ' GB free (' + d.disk_used_pct + '% used)' : '—';
    document.getElementById('stat-uptime').textContent = d.uptime || '—';
    const throttleRow = document.getElementById('stat-throttle-row');
    const hasCurrent = d.throttled;
    const hasPast    = d.throttle_past_flags && d.throttle_past_flags.length;
    if (hasCurrent || hasPast) {
      throttleRow.style.display = '';
      const keyEl = throttleRow.querySelector('.info-key');
      const valEl = document.getElementById('stat-throttle');
      if (hasCurrent) {
        keyEl.className = 'info-key warn';
        keyEl.textContent = 'Throttle';
        valEl.className = 'warn';
        valEl.textContent = d.throttle_flags.join(', ') || d.throttled_hex || 'yes';
      } else {
        keyEl.className = 'info-key';
        keyEl.textContent = 'Past throttle';
        valEl.className = '';
        valEl.textContent = d.throttle_past_flags.join(', ');
      }
    } else {
      throttleRow.style.display = 'none';
    }
  } catch (_) {}
}

// ── Info URLs ─────────────────────────────────────────────────────────────────

function urlLink(url) {
  if (!url) return '—';
  return `<a href="${url}" target="_blank" rel="noopener">${url}</a>`;
}

async function loadInfo() {
  try {
    const res  = await fetch('/api/info');
    const data = await res.json();
    document.getElementById('rtsp-url').innerHTML     = urlLink(data.rtsp_url);
    document.getElementById('rtsp-sub-url').innerHTML = urlLink(data.rtsp_sub_url);
    document.getElementById('onvif-url').innerHTML    = urlLink(data.onvif_url);
    document.getElementById('snap-url').innerHTML     = urlLink(data.snapshot_url);
    // Seed lastFrameTs so startCountdown resumes at the correct position on load
    if (data.last_frame_ts) lastFrameTs = data.last_frame_ts;
  } catch (_) {}
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function init() {
  try {
    await loadPresets();
  } catch (_) {}
  try {
    await loadSettings();
  } catch (e) {
    setStatus('Camera offline', 'warn');
  }

  await loadInfo();
  await loadScheduleStatus();
  loadStats();
  connectSSE();
  refreshPreview();
  if (settings.exposure_mode === 'manual') {
    startCountdown(settings.exposure_time || 0);
  }
  setInterval(loadScheduleStatus, 60_000);
  setInterval(loadStats, 10_000);
}

document.addEventListener('DOMContentLoaded', init);
