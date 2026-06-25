'use strict';

let selectedFiles = [];
let sessionId = null;
let evtSource = null;

const UP_ST = {
  en: { saveToLib: '+ save', saved: '✓ saved', saveErr: 'error saving',
        confirmName: 'item name for the library (edit to simplify, e.g. "platano de seda" → "platano"):' },
  es: { saveToLib: '+ guardar', saved: '✓ guardado', saveErr: 'error al guardar',
        confirmName: 'nombre del item para la biblioteca (edita para simplificar, ej. "platano de seda" → "platano"):' },
};
function ut(key) {
  const lang = localStorage.getItem('lang') || 'en';
  return (UP_ST[lang] || UP_ST.en)[key];
}

/* ── FILE SELECTION ── */
function handleFiles(files) {
  selectedFiles = [...files];
  renderPreviews();
  document.getElementById('uploadBtn').disabled = selectedFiles.length === 0;
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('over');
  handleFiles(e.dataTransfer.files);
}

function renderPreviews() {
  const grid = document.getElementById('previewGrid');
  grid.innerHTML = '';
  selectedFiles.forEach(f => {
    const img = document.createElement('img');
    img.className = 'thumb';
    img.src = URL.createObjectURL(f);
    grid.appendChild(img);
  });
}

/* ── UPLOAD ── */
async function startUpload() {
  if (!selectedFiles.length) return;

  setStatus('subiendo imágenes…');
  const fd = new FormData();
  selectedFiles.forEach(f => fd.append('images', f));

  const res = await fetch('/api/upload/files', { method: 'POST', body: fd });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    setStatus(data.error || 'error al subir', true);
    return;
  }

  const data = await res.json();
  sessionId = data.session_id;
  listenSSE();
}

/* ── SSE STREAM ── */
function listenSSE() {
  setStatus('conectando…');
  evtSource = new EventSource(`/api/upload/parse-status/${sessionId}`);

  evtSource.addEventListener('date', e => {
    const d = JSON.parse(e.data);
    showDateModal(d.value, d.inferred);
  });

  evtSource.addEventListener('progress', e => {
    const d = JSON.parse(e.data);
    setStatus(d.message || 'procesando…');
  });

  evtSource.addEventListener('done', e => {
    const d = JSON.parse(e.data);
    evtSource.close();
    setStatus(`${d.count} items parseados — revisa y confirma`);
    showReview(d.items);
  });

  evtSource.addEventListener('error', e => {
    const d = JSON.parse(e.data || '{}');
    evtSource.close();
    setStatus(d.message || 'error al parsear', true);
  });
}

/* ── DATE MODAL ── */
function showDateModal(inferredStr, wasInferred) {
  const modal = document.getElementById('dateModal');
  const lbl = document.getElementById('inferredDate');
  if (inferredStr) {
    const d = new Date(inferredStr);
    const MES = ['','ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
    lbl.textContent = `${d.getDate()} ${MES[d.getMonth()+1]} ${d.getFullYear()}`;
  } else {
    lbl.textContent = 'fecha no detectada';
  }
  modal.classList.add('open');
}

async function confirmDate() {
  const override = document.getElementById('dateOverride').value.trim();
  const modal = document.getElementById('dateModal');

  let dateStr = null;
  if (override) {
    dateStr = parseUserDate(override);
    if (!dateStr) { alert('No se pudo interpretar la fecha'); return; }
  } else {
    dateStr = inferredDateIso();
  }

  modal.classList.remove('open');
  setStatus('confirmando fecha…');

  await fetch('/api/upload/confirm-date', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, date: dateStr }),
  });
}

// Local calendar date as YYYY-MM-DD. toISOString() converts to UTC, which
// rolls over to the wrong day near midnight in negative-UTC-offset zones.
function isoLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function inferredDateIso() {
  const lbl = document.getElementById('inferredDate').textContent;
  if (lbl === 'fecha no detectada') return isoLocal(new Date());
  const MES = {ene:1,feb:2,mar:3,abr:4,may:5,jun:6,jul:7,ago:8,sep:9,oct:10,nov:11,dic:12};
  const parts = lbl.split(' ');
  if (parts.length >= 3) {
    const d = parseInt(parts[0]), m = MES[parts[1]], y = parseInt(parts[2]);
    if (d && m && y) return `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
  }
  return isoLocal(new Date());
}

const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

function parseUserDate(raw) {
  const today = new Date();
  raw = raw.trim().toLowerCase();
  if (raw === 'hoy' || raw === 'today') return isoLocal(today);
  if (raw === 'ayer' || raw === 'yesterday') {
    const y = new Date(today); y.setDate(y.getDate() - 1);
    return isoLocal(y);
  }
  const wd = raw.match(/^last\s+(\w+)$/);
  if (wd && WEEKDAYS.includes(wd[1])) {
    const target = WEEKDAYS.indexOf(wd[1]);
    const delta = ((today.getDay() + 6) % 7 - target + 7) % 7 || 7;
    const y = new Date(today); y.setDate(y.getDate() - delta);
    return isoLocal(y);
  }
  const m = raw.match(/^(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?$/);
  if (m) {
    let yr = parseInt(m[3] || today.getFullYear());
    if (yr < 100) yr += 2000;
    const dt = new Date(yr, parseInt(m[2]) - 1, parseInt(m[1]));
    return isoLocal(dt);
  }
  return null;
}

/* ── REVIEW TABLE ── */
function showReview(items) {
  const section = document.getElementById('reviewSection');
  const tbody = document.getElementById('reviewBody');
  tbody.innerHTML = '';
  section.classList.add('open');

  items.forEach((item, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input value="${esc(item.raw_name || '')}" onchange="updateItem(${i},'raw_name',this.value)"></td>
      <td><input value="${esc(item.matched_category || '')}" onchange="updateItem(${i},'matched_category',this.value)"></td>
      <td><input value="${esc(item.quantity ?? '')}" style="width:55px" onchange="updateItem(${i},'quantity',this.value)"></td>
      <td><input value="${esc(item.unit_price ?? '')}" style="width:60px" onchange="updateItem(${i},'unit_price',this.value)"></td>
      <td><input value="${esc(item.total_price ?? '')}" style="width:60px" onchange="updateItem(${i},'total_price',this.value)"></td>
      <td><input value="${esc(item.source || '')}" onchange="updateItem(${i},'source',this.value)"></td>
      <td><input value="${esc(item.matched_id || '')}" style="color:var(--cyan)" onchange="updateItem(${i},'matched_id',this.value)"></td>
      <td><input value="${esc(item.gpt_notes || '')}" style="color:var(--yellow)" onchange="updateItem(${i},'gpt_notes',this.value)"></td>
      <td>${item.matched_id
        ? '<span style="color:var(--green)">✓</span>'
        : `<button class="lib-btn" onclick="saveToLibrary(${i})">${ut('saveToLib')}</button>`}</td>`;
    tbody.appendChild(tr);
  });

  window._reviewItems = items;
}

function updateItem(idx, field, value) {
  if (window._reviewItems) window._reviewItems[idx][field] = value;
}

async function saveToLibrary(idx) {
  const item = window._reviewItems[idx];
  const name = (prompt(ut('confirmName'), item.raw_name || '') || '').trim();
  if (!name) return;
  const payload = {
    item: name,
    unit: item.unit || '',
    category: item.matched_category || '',
    subcategory: item.matched_subcategory || '',
    tags: item.tags || '',
  };

  const res = await fetch('/api/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) { alert(ut('saveErr')); return; }

  const data = await res.json();
  item.matched_id = data.id;
  item.matched_category = data.category;
  showReview(window._reviewItems);
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

async function confirmImport() {
  if (!sessionId) return;
  const res = await fetch('/api/upload/confirm-parse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, items: window._reviewItems }),
  });
  const data = await res.json();
  if (res.ok) {
    setStatus(`importado: ${data.message}`);
    document.getElementById('reviewSection').classList.remove('open');
    selectedFiles = [];
    document.getElementById('previewGrid').innerHTML = '';
    document.getElementById('uploadBtn').disabled = true;
  } else {
    setStatus(data.error || 'error al importar', true);
  }
}

/* ── STATUS ── */
function setStatus(msg, isErr = false) {
  const el = document.getElementById('statusBox');
  el.className = 'status-box' + (isErr ? ' status-err' : '');
  el.textContent = msg;
}
