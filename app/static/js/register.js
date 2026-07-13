'use strict';

let lang = localStorage.getItem('lang') || 'en';
let currentStart = null;
let currentEnd = null;
let searchTimer = null;

let allEntries = [];

const RT = {
  en: { noResults: 'no entries in this range', loading: 'loading…', total: 'total',
        entries: 'entries', searchPlaceholder: 'search item, category…',
        edit: 'edit', editTitle: 'edit entry', deleteConfirm: 'Delete this entry?', saveError: 'Error saving entry' },
  es: { noResults: 'sin entradas en este rango', loading: 'cargando…', total: 'total',
        entries: 'entradas', searchPlaceholder: 'buscar item, categoría…',
        edit: 'editar', editTitle: 'editar entrada', deleteConfirm: '¿Eliminar esta entrada?', saveError: 'Error al guardar' },
};
function t(key) { return (RT[lang] || RT.en)[key] || key; }

// Override base setLang to also handle register-page specifics
window.setLang = function(l) {
  lang = l;
  localStorage.setItem('lang', l);
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('active', b.dataset.lang === l));
  document.querySelectorAll('.i18n').forEach(el => { if (el.dataset[l]) el.textContent = el.dataset[l]; });
  const si = document.getElementById('searchInput');
  if (si) si.placeholder = t('searchPlaceholder');
};

function isoLocal(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/* ── RANGE ── */
function setRange(days) {
  if (days === 'all') {
    currentStart = null;
    currentEnd = null;
    document.getElementById('startDate').value = '';
    document.getElementById('endDate').value = '';
  } else {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - days);
    currentStart = isoLocal(start);
    currentEnd = isoLocal(end);
    document.getElementById('startDate').value = currentStart;
    document.getElementById('endDate').value = currentEnd;
  }
  document.querySelectorAll('.reg-range-btn').forEach(b => b.classList.toggle('active', b.dataset.range === String(days)));
  loadEntries();
}

function setCustomRange() {
  currentStart = document.getElementById('startDate').value || null;
  currentEnd = document.getElementById('endDate').value || null;
  document.querySelectorAll('.reg-range-btn').forEach(b => b.classList.remove('active'));
  loadEntries();
}

/* ── LOAD ── */
async function loadEntries(q = '') {
  const params = new URLSearchParams();
  if (currentStart) params.set('start', currentStart);
  if (currentEnd) params.set('end', currentEnd);
  if (q) params.set('q', q);
  const rows = await fetch(`/api/register/entries?${params}`).then(r => r.json());
  allEntries = rows;
  renderEntries(rows);
}

function renderEntries(rows) {
  const tbody = document.getElementById('regBody');
  const summary = document.getElementById('regSummary');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state">${t('noResults')}</div></td></tr>`;
    summary.textContent = '';
    return;
  }
  const sum = rows.reduce((s, r) => s + (r.total_price || 0), 0);
  summary.innerHTML = `${rows.length} ${t('entries')} · ${t('total')} <span class="hi">${curr()}${sum.toFixed(2)}</span>`;
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.datetime || '')}</td>
      <td><span class="item-name">${esc(r.matched_id || r.raw_name || '')}</span></td>
      <td>${r.matched_category ? `<span class="cat-badge">${esc(r.matched_category)}</span>` : '—'}</td>
      <td>${r.quantity ?? '—'}</td>
      <td>${r.unit_price != null ? curr() + Number(r.unit_price).toFixed(2) : '—'}</td>
      <td class="reg-total">${curr()}${Number(r.total_price || 0).toFixed(2)}</td>
      <td>${esc(r.source || '—')}</td>
      <td>
        <button class="act-btn act-edit" onclick="openModal(${r.id})">${t('edit')}</button>
        <button class="act-btn act-del" onclick="deleteEntry(${r.id})">×</button>
      </td>
    </tr>`).join('');
}

/* ── EDIT MODAL ── */
function openModal(id) {
  const entry = allEntries.find(e => e.id === id);
  if (!entry) return;
  document.getElementById('editId').value = entry.id;
  document.getElementById('fRawName').value = entry.raw_name || '';
  document.getElementById('fMatchedId').value = entry.matched_id || '';
  document.getElementById('fCategory').value = entry.matched_category || '';
  document.getElementById('fDate').value = entry.datetime || '';
  document.getElementById('fQuantity').value = entry.quantity ?? '';
  document.getElementById('fTotalPrice').value = entry.total_price ?? '';
  document.getElementById('fSource').value = entry.source || '';
  document.getElementById('entryModal').classList.add('open');
  document.getElementById('fRawName').focus();
}

function closeModal() {
  document.getElementById('entryModal').classList.remove('open');
}

async function saveEntry() {
  const id = document.getElementById('editId').value;
  const payload = {
    raw_name:         document.getElementById('fRawName').value.trim(),
    matched_id:       document.getElementById('fMatchedId').value.trim() || null,
    matched_category: document.getElementById('fCategory').value.trim() || null,
    datetime:         document.getElementById('fDate').value,
    quantity:         document.getElementById('fQuantity').value,
    total_price:      document.getElementById('fTotalPrice').value,
    source:           document.getElementById('fSource').value.trim() || null,
  };
  if (!payload.raw_name) { document.getElementById('fRawName').focus(); return; }

  const res = await fetch(`/api/register/entries/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    closeModal();
    loadEntries(document.getElementById('searchInput').value);
  } else {
    alert(t('saveError'));
  }
}

async function deleteEntry(id) {
  if (!confirm(t('deleteConfirm'))) return;
  await fetch(`/api/register/entries/${id}`, { method: 'DELETE' });
  loadEntries(document.getElementById('searchInput').value);
}

document.getElementById('entryModal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

/* ── SEARCH ── */
function searchEntries(q) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadEntries(q), 220);
}

/* esc() is shared — see static/js/utils.js */

/* ── INIT ── */
document.addEventListener('DOMContentLoaded', () => {
  const si = document.getElementById('searchInput');
  if (si) si.placeholder = t('searchPlaceholder');
  setLang(lang);
  setRange(7);
});
