'use strict';

let allItems = [];
let searchTimer = null;
let lang = localStorage.getItem('lang') || 'en';

const IT = {
  en: {
    noResults: 'no results',
    loading: 'loading…',
    editItem: 'edit item',
    newItem: 'new item',
    edit: 'edit',
    deleteConfirm: 'Delete this item from the library?',
    saveError: 'Error saving item',
    searchPlaceholder: 'search item, category, synonym…',
    demoBlocked: 'Editing isn\'t available in demo mode.',
  },
  es: {
    noResults: 'sin resultados',
    loading: 'cargando…',
    editItem: 'editar item',
    newItem: 'nuevo item',
    edit: 'editar',
    deleteConfirm: '¿Eliminar este item de la biblioteca?',
    saveError: 'Error al guardar',
    searchPlaceholder: 'buscar item, categoría, sinónimo…',
    demoBlocked: 'La edición no está disponible en modo demo.',
  },
};

function t(key) { return (IT[lang] || IT.en)[key] || key; }

// Override base setLang to also handle items-page specifics
window.setLang = function(l) {
  lang = l;
  localStorage.setItem('lang', l);
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('active', b.dataset.lang === l));
  document.querySelectorAll('.i18n').forEach(el => { if (el.dataset[l]) el.textContent = el.dataset[l]; });
  const si = document.getElementById('searchInput');
  if (si) si.placeholder = t('searchPlaceholder');
  // Re-render already-loaded rows so dynamic text (edit button, empty state)
  // picks up the new language immediately instead of staying stale until
  // the next fetch.
  renderItems(allItems);
};

/* ── LOAD ── */
async function loadItems(q = '') {
  const url = q ? `/api/items?q=${encodeURIComponent(q)}` : '/api/items';
  allItems = await fetch(url).then(r => r.json());
  renderItems(allItems);
}

function renderItems(items) {
  const tbody = document.getElementById('itemsBody');
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">${t('noResults')}</div></td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(item => `
    <tr>
      <td style="color:var(--dim2)">${item.id}</td>
      <td>
        <span class="item-name">${esc(item.item || '')}</span>
        ${item.synonyms ? `<span class="item-syn">${esc(item.synonyms)}</span>` : ''}
      </td>
      <td style="color:var(--cyan);font-family:var(--mono)">${esc(item.unit || '—')}</td>
      <td><span class="cat-badge">${esc(item.category || '—')}</span></td>
      <td style="color:var(--dim2)">${esc(item.subcategory || '—')}</td>
      <td style="color:var(--dim2);font-size:9px">${esc((item.synonyms || '').slice(0, 40))}${(item.synonyms || '').length > 40 ? '…' : ''}</td>
      <td>
        <button class="act-btn act-edit" onclick="openModal(${JSON.stringify(item).replace(/"/g,'&quot;')})">${t('edit')}</button>
        <button class="act-btn act-del" onclick="deleteItem('${item.id}')">×</button>
      </td>
    </tr>`).join('');
}

/* ── SEARCH ── */
function searchItems(q) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadItems(q), 220);
}

/* ── MODAL ── */
function openModal(item = null) {
  document.getElementById('modalTitle').textContent = item ? t('editItem') : t('newItem');
  document.getElementById('editId').value = item ? item.id : '';
  document.getElementById('fItem').value = item ? (item.item || '') : '';
  document.getElementById('fUnit').value = item ? (item.unit || '') : '';
  document.getElementById('fCat').value = item ? (item.category || '') : '';
  document.getElementById('fSubcat').value = item ? (item.subcategory || '') : '';
  document.getElementById('fSyn').value = item ? (item.synonyms || '') : '';
  document.getElementById('fNotes').value = item ? (item.notes || '') : '';
  document.getElementById('fTags').value = item ? (item.tags || '') : '';
  document.getElementById('itemModal').classList.add('open');
  document.getElementById('fItem').focus();
}

function closeModal() {
  document.getElementById('itemModal').classList.remove('open');
}

async function saveItem() {
  const id = document.getElementById('editId').value;
  const payload = {
    item:        document.getElementById('fItem').value.trim(),
    unit:        document.getElementById('fUnit').value.trim(),
    category:    document.getElementById('fCat').value.trim(),
    subcategory: document.getElementById('fSubcat').value.trim(),
    synonyms:    document.getElementById('fSyn').value.trim(),
    notes:       document.getElementById('fNotes').value.trim(),
    tags:        document.getElementById('fTags').value.trim(),
  };
  if (!payload.item) { document.getElementById('fItem').focus(); return; }
  if (typeof DEMO_MODE !== 'undefined' && DEMO_MODE) { alert(t('demoBlocked')); return; }

  const url = id ? `/api/items/${id}` : '/api/items';
  const method = id ? 'PATCH' : 'POST';
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    closeModal();
    loadItems(document.getElementById('searchInput').value);
  } else {
    alert(t('saveError'));
  }
}

async function deleteItem(id) {
  if (typeof DEMO_MODE !== 'undefined' && DEMO_MODE) { alert(t('demoBlocked')); return; }
  if (!confirm(t('deleteConfirm'))) return;
  await fetch(`/api/items/${id}`, { method: 'DELETE' });
  loadItems(document.getElementById('searchInput').value);
}

/* ── CLOSE ON OVERLAY CLICK ── */
document.getElementById('itemModal').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

/* esc() is shared — see static/js/utils.js */

/* ── SUGGESTED CANON ── */
async function loadSuggestions() {
  const suggestions = await fetch('/api/items/suggestions').then(r => r.json()).catch(() => []);
  const box   = document.getElementById('canonSuggest');
  const chips = document.getElementById('canonChips');
  if (!suggestions.length || !box || !chips) return;
  chips.innerHTML = suggestions.map(s =>
    `<div class="canon-chip" onclick="openSuggestion(${JSON.stringify(s.raw_name).replace(/"/g,'&quot;')})">
       ${esc(s.raw_name)}<span class="chip-cnt">×${s.count}</span>
     </div>`
  ).join('');
  box.style.display = '';
}

function openSuggestion(rawName) {
  openModal(null);
  document.getElementById('fItem').value = rawName;
  document.getElementById('fSyn').value  = rawName.toLowerCase();
}

/* ── INIT ── */
document.addEventListener('DOMContentLoaded', () => {
  const si = document.getElementById('searchInput');
  if (si) si.placeholder = t('searchPlaceholder');
  setLang(lang);
});
loadItems();
loadSuggestions();
