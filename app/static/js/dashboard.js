'use strict';

let currentPeriod = '30d';
let lang = localStorage.getItem('lang') || 'en';
/* curr() is shared — see static/js/utils.js */
let chart = null;
let chartData = {};
const chatHistory = [];  // [{role:'user'|'assistant', content:'...'}]

/* ── CHART SETUP ── */
const ctx = document.getElementById('chart').getContext('2d');

function mkGrad(r, g, b, a0 = 0.22, a1 = 0.01) {
  const g2 = ctx.createLinearGradient(0, 0, 0, 220);
  g2.addColorStop(0, `rgba(${r},${g},${b},${a0})`);
  g2.addColorStop(1, `rgba(${r},${g},${b},${a1})`);
  return g2;
}

function ptStyle(data, anomIdx) {
  return {
    pointRadius:          data.map((_, i) => i === anomIdx ? 9 : 3),
    pointBorderColor:     data.map((_, i) => i === anomIdx ? '#FF6F91' : '#FF9D6E'),
    pointBackgroundColor: data.map((_, i) => i === anomIdx ? 'transparent' : '#FF9D6E'),
    pointBorderWidth:     data.map((_, i) => i === anomIdx ? 2.5 : 1.5),
  };
}

chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'total', data: [], borderColor: '#FF9D6E',
        backgroundColor: mkGrad(255, 157, 110), borderWidth: 3,
        fill: true, tension: 0.42, order: 3,
        pointRadius: [], pointBorderColor: [], pointBackgroundColor: [], pointBorderWidth: [],
      },
      {
        label: 'groceries', data: [], borderColor: '#FFC2D6',
        backgroundColor: mkGrad(255, 194, 214, 0.18), borderWidth: 2,
        borderDash: [6, 3], fill: true, tension: 0.42, pointRadius: 3, order: 2,
      },
      {
        label: 'meat', data: [], borderColor: '#FF6F91',
        backgroundColor: mkGrad(255, 111, 145, 0.16), borderWidth: 2,
        borderDash: [6, 3], fill: true, tension: 0.42, pointRadius: 3, order: 1,
      },
      {
        label: 'delivery + servicio', data: [], borderColor: '#FFE0A3',
        backgroundColor: 'transparent', borderWidth: 1, fill: false,
        tension: 0.42, pointRadius: 2, pointBackgroundColor: '#FFE0A3', order: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#17161A', borderColor: 'rgba(255,157,110,.35)', borderWidth: 1,
        titleColor: 'rgba(255,157,110,.7)', bodyColor: '#FFFBFC',
        titleFont: { family: 'IBM Plex Mono', size: 10 },
        bodyFont:  { family: 'IBM Plex Mono', size: 11 },
        usePointStyle: true,
        callbacks: {
          label: c => {
            if (c.datasetIndex === 3) {
              const actual = (chartData[currentPeriod] || {}).delivery;
              const v = actual ? actual[c.dataIndex] : c.parsed.y;
              return ` delivery + servicio  ${curr()}${(v || 0).toFixed(0)}`;
            }
            return ` ${c.dataset.label}  ${curr()}${c.parsed.y.toFixed(0)}`;
          },
        },
      },
    },
    scales: {
      x: {
        grid: { color: 'rgba(255,157,110,.12)', drawBorder: false },
        ticks: { color: 'rgba(255,157,110,.52)', font: { family: 'IBM Plex Mono', size: 9 } },
        border: { display: false },
      },
      y: {
        grid: { color: 'rgba(255,157,110,.12)', drawBorder: false },
        ticks: { color: 'rgba(255,157,110,.52)', font: { family: 'IBM Plex Mono', size: 9 }, callback: v => curr() + v },
        border: { display: false },
      },
    },
  },
});

/* ── DATA LOADING ── */

async function loadAll() {
  // Auto-switch to 90d if current calendar month has no purchases
  if (currentPeriod === '30d') {
    const b = await fetch('/api/budget').then(r => r.json()).catch(() => ({}));
    if (b && !b.spent_this_month) {
      currentPeriod = '90d';
      document.querySelectorAll('#period-tabs .tab').forEach(t =>
        t.classList.toggle('active', t.textContent.trim() === '90d'));
      updatePeriodLabel('90d');
    }
  }
  await Promise.all([loadBudget(), loadKPIs(currentPeriod), loadChart(currentPeriod), loadNeeded(), loadTopItems(currentPeriod), loadOrders(currentPeriod)]);
  scrollChat();
}

function drawRing(svgId, pct, color, label) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const size = 112, r = 46, cx = size / 2, cy = size / 2, stroke = 9;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, pct));
  const offset = circ * (1 - clamped / 100);
  svg.innerHTML = `
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,157,110,.14)" stroke-width="${stroke}"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${circ}" stroke-dashoffset="${offset}" transform="rotate(-90 ${cx} ${cy})"
      style="filter:drop-shadow(0 0 5px ${color})"/>
    <text x="${cx}" y="${cy + 7}" text-anchor="middle" font-size="21" fill="#FFFBFC" font-weight="700">${label}</text>`;
}

async function loadBudget() {
  const d = await fetch('/api/budget').then(r => r.json());
  // Only skip if the response is completely empty (no DB data at all)
  if (!d || Object.keys(d).length === 0) return;

  const pct = d.pct_of_budget || 0;
  drawRing('ringBudget', pct, '#FF9D6E', `${pct}%`);

  const dev = d.deviation_pct;
  const devLabel = dev == null ? '—' : `${dev > 0 ? '+' : ''}${dev}%`;
  const devColor = dev > 0 ? '#FF6F91' : '#FFE0A3';
  drawRing('ringDeviation', dev == null ? 0 : Math.abs(dev), devColor, devLabel);

  const spentEl = document.getElementById('ringmeta-spent');
  const daysEl  = document.getElementById('ringmeta-days');
  if (spentEl) spentEl.innerHTML =
    `${T[lang].spentLbl} <b><span class="curr-ring">${curr()}</span>${(d.spent_this_month || 0).toFixed(2)}</b>`;
  if (daysEl) daysEl.innerHTML =
    `<b>${d.days_remaining ?? '—'}</b> ${T[lang].daysLbl}`;

  // Reflect ring cap labels in current lang
  const rbEl = document.getElementById('lbl-ring-budget');
  const rdEl = document.getElementById('lbl-ring-dev');
  if (rbEl) rbEl.textContent = T[lang].ringBudgetCap;
  if (rdEl) rdEl.textContent = T[lang].ringDevCap;
}

async function loadKPIs(period = currentPeriod) {
  const d = await fetch(`/api/kpis?period=${period}`).then(r => r.json());
  if (period !== currentPeriod) return;

  const kpiLbl = document.getElementById('kpi-total-lbl');
  if (kpiLbl) {
    kpiLbl.textContent = T[lang].kpiPeriodLabels[period] || T[lang].kpiPeriodLabels['30d'];
  }

  const tot = d.month_total || 0;
  document.getElementById('kpi-total').innerHTML = `<span class="curr-kpi">${curr()}</span>${tot.toFixed(2)}`;
  const td = d.month_total_delta;
  document.getElementById('kpi-total-delta').className = 'kpi-delta ' + (td > 0 ? 'up' : td < 0 ? 'down' : 'flat');
  document.getElementById('kpi-total-delta').textContent = td != null ? `${td > 0 ? '↑' : '↓'} ${Math.abs(td)}%` : '—';

  document.getElementById('kpi-orders').textContent = d.orders || 0;
  const od = d.orders_delta || 0;
  document.getElementById('kpi-orders-delta').className = 'kpi-delta ' + (od < 0 ? 'down' : od > 0 ? 'up' : 'flat');
  document.getElementById('kpi-orders-delta').textContent = od !== 0 ? `${od > 0 ? '+' : ''}${od}` : '—';

  const avg = d.avg_order || 0;
  document.getElementById('kpi-avg').innerHTML = `<span class="curr-kpi">${curr()}</span>${avg.toFixed(2)}`;
  const ad = d.avg_order_delta;
  document.getElementById('kpi-avg-delta').className = 'kpi-delta ' + (ad > 2 ? 'up' : ad < -2 ? 'down' : 'flat');
  document.getElementById('kpi-avg-delta').textContent = ad != null ? `${ad > 0 ? '↑' : '↓'} ${Math.abs(ad)}%` : '—';

  document.getElementById('kpi-tracked').textContent = d.tracked_items || 0;
  document.getElementById('kpi-tracked-delta').className = 'kpi-delta flat';
  document.getElementById('kpi-tracked-delta').textContent = d.category_count ? `${d.category_count} cat.` : '—';
}

async function loadChart(period) {
  const d = await fetch(`/api/chart?period=${period}`).then(r => r.json());
  chartData[period] = d;
  if (period !== currentPeriod) return;

  const anomIdx = d.anomalyIdx;
  const ps = ptStyle(d.total || [], anomIdx);
  chart.data.labels = d.labels || [];
  Object.assign(chart.data.datasets[0], { data: d.total || [], ...ps });
  chart.data.datasets[1].data = d.groceries || d.abarrotes || [];
  chart.data.datasets[2].data = d.meat || d.carnes || [];
  chart.data.datasets[3].data = d.deliveryAbove || [];
  chart.update('active');

  const lbl = d.anomalyLabel || {};
  document.getElementById('lbl-anomaly').textContent = lbl[lang] || '';
}

async function loadNeeded() {
  const data = await fetch('/api/needed-soon').then(r => r.json());
  const items = data.items || [];
  const grid   = document.getElementById('neededGrid');
  const noteEl = document.getElementById('neededNote');
  grid.innerHTML = '';
  if (noteEl) noteEl.innerHTML = '';

  // Show all items (up to 12), pad to 6 minimum so grid always has one full row
  const display = items.slice(0, 12);
  while (display.length < 6) display.push(null);
  display.forEach((item, i) => grid.appendChild(makeCircle(item, i)));

  if (!items.length && noteEl) {
    const cls = data.reliable_count > 0 ? 'needed-tag needed-ok' : 'needed-tag needed-nodata';
    const msg = data.reliable_count > 0 ? T[lang].neededAllGood : T[lang].neededNoData;
    noteEl.innerHTML = `<div class="${cls}">${msg}</div>`;
  }

  setTimeout(() => animateArcs([...grid.querySelectorAll('.ni-arc')]), 50);
}

function makeCircle(item, i) {
  if (!item) {
    const div = document.createElement('div');
    div.className = 'ni ni-placeholder';
    div.innerHTML = `
      <div class="ni-circ">
        <svg viewBox="0 0 36 36">
          <circle cx="18" cy="18" r="15.915" fill="none"
            stroke="rgba(255,157,110,.1)" stroke-width="2.5" stroke-dasharray="2.5 4"/>
        </svg>
        <div class="ni-val"><span class="ni-val-pct" style="color:rgba(255,255,255,.18)">—</span></div>
      </div>
      <div class="ni-name" style="color:rgba(255,255,255,.13)">—</div>`;
    return div;
  }

  const pct   = item.urgency_pct || 0;
  const color = item.urgency_color || 'rgba(255,157,110,.18)';

  const div = document.createElement('div');
  div.className = 'ni';
  div.style.cssText = `--nd:${0.35 + i * 0.07}s`;
  div.innerHTML = `
    <div class="ni-circ" style="box-shadow:0 0 14px ${color}4d">
      <svg viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.915" fill="none" stroke="rgba(58,77,97,.9)" stroke-width="2.5"/>
        <circle cx="18" cy="18" r="15.915" fill="none" stroke="${color}" stroke-width="2.5"
          stroke-linecap="round" data-pct="${pct}"
          transform="rotate(-90 18 18)" class="ni-arc"/>
      </svg>
      <div class="ni-val">
        <span class="ni-val-pct" style="color:${color}">${pct}%</span>
      </div>
    </div>
    <div class="ni-name">${esc(item.matched_id || '—')}</div>`;
  return div;
}

async function loadTopItems(period = currentPeriod) {
  const items = await fetch(`/api/top-items?period=${period}`).then(r => r.json());
  if (period !== currentPeriod) return;
  const list = document.getElementById('topItemsList');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--dim2);padding:8px 0">—</div>';
    return;
  }
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'ti-row';
    // Use actual pct for bar width so bars reflect real share, not just relative rank
    row.innerHTML = `
      <div class="ti-name">${esc(item.matched_id)}</div>
      <div class="ti-bar"><div class="ti-fill" style="width:${item.pct}%"></div></div>
      <div class="ti-pct">${item.pct}%</div>`;
    list.appendChild(row);
  });
}

const ENTRY_LABEL = {
  image:  { en: 'image',  es: 'imagen' },
  import: { en: 'import', es: 'import' },
  text:   { en: 'text',   es: 'texto' },
  other:  { en: 'other',  es: 'otro' },
};

async function loadOrders(period = currentPeriod) {
  const orders = await fetch(`/api/recent-orders?period=${period}`).then(r => r.json());
  if (period !== currentPeriod) return;
  const tbody = document.getElementById('ordersBody');
  tbody.innerHTML = '';
  const BADGE = {
    image:  'background:rgba(255,194,214,.1);border:1px solid rgba(255,194,214,.25);color:var(--bright2)',
    import: 'background:rgba(255,157,110,.1);border:1px solid rgba(255,157,110,.25);color:var(--orange)',
    text:   'background:rgba(255,224,163,.1);border:1px solid rgba(255,224,163,.3);color:var(--green)',
    other:  'background:rgba(255,157,110,.05);border:1px solid var(--border);color:var(--dim2)',
  };
  orders.forEach(o => {
    const tr = document.createElement('tr');
    const type  = ENTRY_LABEL[o.entry_type] ? o.entry_type : 'other';
    const badge = BADGE[type];
    const label = ENTRY_LABEL[type][lang] || ENTRY_LABEL[type].en;
    tr.innerHTML = `
      <td>${esc(o.date_label)}</td>
      <td><span class="td-store">${esc(o.source)}</span></td>
      <td class="td-price">${curr()}${(o.order_total || 0).toFixed(2)}</td>
      <td><span class="badge i18n" data-en="${ENTRY_LABEL[type].en}" data-es="${ENTRY_LABEL[type].es}" style="${badge}">${label}</span></td>`;
    tbody.appendChild(tr);
  });
}

/* ── PERIOD SWITCH ── */
function updatePeriodLabel(period) {
  const pl = T[lang].periodLabels[period] || period;
  const el = document.getElementById('lbl-bycat');
  if (el) el.textContent = `${T[lang].bycat} · ${pl}`;
}

function setPeriod(el, period) {
  currentPeriod = period;
  el.closest('.tab-group').querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  loadChart(period);
  loadTopItems(period);
  loadKPIs(period);
  loadOrders(period);
  updatePeriodLabel(period);
}

/* ── ARC ANIMATION ── */
function animateArcs(arcs) {
  arcs.forEach((el, i) => {
    setTimeout(() => {
      el.style.strokeDashoffset = 100 - parseFloat(el.dataset.pct);
    }, i * 75);
  });
}

/* ── NEEDED SOON EXPAND (pushes content below down, no covering) ── */
function toggleNeeded() {
  const block     = document.getElementById('blockNeeded');
  const btn       = document.getElementById('needExpBtn');
  const lbl       = document.getElementById('needExpLabel');
  const extraGrid = document.getElementById('neededGridExtra');
  const open      = block.classList.toggle('needs-expanded');
  btn.classList.toggle('open', open);

  const extraCount = extraGrid.children.length;
  if (open) {
    lbl.textContent = lang === 'es' ? 'ver menos' : 'see less';
    animateArcs([...extraGrid.querySelectorAll('.ni-arc')]);
    // Scroll bottom of expanded block into view after transition
    setTimeout(() => {
      const rect = block.getBoundingClientRect();
      if (rect.bottom > window.innerHeight - 12) {
        block.scrollIntoView({ behavior: 'smooth', block: 'end' });
      }
    }, 420);
  } else {
    lbl.textContent = lang === 'es' ? `ver más (${extraCount})` : `see more (${extraCount})`;
  }
}

/* ── CHAT (last section — expands in place, nothing collapses) ── */
function onChatFocus() {
  document.getElementById('chatMsgs').classList.add('expanded');
  if (window.innerWidth < 900) {
    setTimeout(() => {
      document.getElementById('chatPanel').scrollIntoView({ behavior: 'smooth', block: 'end' });
    }, 300);
  }
}
function onChatBlur() {
  // Keep expanded while there are messages; collapse only if empty
  if (!document.getElementById('chatMsgs').children.length) {
    document.getElementById('chatMsgs').classList.remove('expanded');
  }
}

/* ── I18N ── */
const T = {
  es: {
    budget: 'presupuesto', chart: 'historial de gastos',
    legTotal: 'total', legDelivery: 'delivery + servicio',
    needed: 'por agotar',
    bycat: 'mayor gasto',
    periodLabels: { '30d': 'este mes', '90d': 'últ. 90d', 'all': 'todo' },
    kpiPeriodLabels: { '30d': 'este mes', '90d': 'últ. 90d', 'all': 'este año' },
    recent: 'recientes', chat: 'gestor',
    placeholder: 'consulta sobre precios, listas, urgencias, tendencias…',
    noAnswer: 'Sin respuesta.', chatError: 'error al consultar',
    draftTitle: 'confirmar entrada de compra:', draftConfirm: 'confirmar', draftCancel: 'cancelar',
    draftSaved: 'compra registrada.', draftSaveErr: 'error al guardar la compra',
    neededAllGood: 'todo bien — nada urgente por reabastecer',
    neededNoData:  'aún no hay suficiente historial de compras para este cálculo',
    ringBudgetCap: 'presupuesto usado', ringDevCap: 'vs prom. 30d',
    spentLbl: 'gastado', daysLbl: 'días restantes',
  },
  en: {
    budget: 'budget', chart: 'spending history',
    legTotal: 'total', legDelivery: 'delivery + service',
    needed: 'running low',
    bycat: 'top spend',
    periodLabels: { '30d': 'this month', '90d': 'last 90d', 'all': 'all time' },
    kpiPeriodLabels: { '30d': 'this month', '90d': 'last 90d', 'all': 'this year' },
    recent: 'recent', chat: 'warehouse manager',
    placeholder: 'query prices, lists, urgency, trends…',
    noAnswer: 'No answer.', chatError: 'query error',
    draftTitle: 'confirm purchase entry:', draftConfirm: 'confirm', draftCancel: 'cancel',
    draftSaved: 'purchase logged.', draftSaveErr: 'error saving purchase',
    neededAllGood: 'all good — nothing urgent to restock',
    neededNoData:  'not enough purchase history yet for this insight',
    ringBudgetCap: 'budget used', ringDevCap: 'vs 30d avg',
    spentLbl: 'spent', daysLbl: 'days left',
  },
};

function setLang(l) {
  lang = l;
  localStorage.setItem('lang', l);
  const t = T[l];

  // Static label map
  const ids = {
    'lbl-budget':      t.budget,
    'lbl-chart':       t.chart,
    'leg-total':       t.legTotal,
    'leg-delivery':    t.legDelivery,
    'lbl-needed':      t.needed,
    'lbl-recent':      t.recent,
    'lbl-chat':        t.chat,
    'lbl-ring-budget': t.ringBudgetCap,
    'lbl-ring-dev':    t.ringDevCap,
  };
  for (const [id, val] of Object.entries(ids)) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // Dynamic: top spend title includes period
  updatePeriodLabel(currentPeriod);

  document.querySelectorAll('.i18n').forEach(el => { if (el.dataset[l]) el.textContent = el.dataset[l]; });
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('active', b.dataset.lang === l));

  const chatIn = document.getElementById('chatIn');
  if (chatIn) chatIn.placeholder = t.placeholder;

  const anom = chartData[currentPeriod];
  if (anom && anom.anomalyLabel) {
    const anomEl = document.getElementById('lbl-anomaly');
    if (anomEl) anomEl.textContent = anom.anomalyLabel[l] || '';
  }

  // Re-render budget meta text in new language
  const spentEl = document.getElementById('ringmeta-spent');
  if (spentEl && spentEl.innerHTML) loadBudget();

  scrollChat();
}

/* ── CHAT MESSAGING ── */
function scrollChat() {
  const box = document.getElementById('chatMsgs');
  if (box) box.scrollTop = box.scrollHeight;
}

function sendMsg(e) {
  if (e.key !== 'Enter') return;
  const inp = document.getElementById('chatIn');
  const val = inp.value.trim();
  if (!val) return;
  const box = document.getElementById('chatMsgs');
  box.classList.add('expanded');

  const mu = document.createElement('div');
  mu.className = 'msg msg-u';
  mu.textContent = val;
  box.appendChild(mu);
  inp.value = '';

  // Typing indicator
  const typing = document.createElement('div');
  typing.className = 'msg msg-b';
  typing.id = 'chatTyping';
  typing.innerHTML = '<span style="opacity:.4;letter-spacing:.15em">···</span>';
  box.appendChild(typing);
  box.scrollTop = box.scrollHeight;

  // Snapshot history before this turn; current question is sent separately
  const historySnapshot = chatHistory.slice(-6);

  fetch('/api/chat/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: val, lang, history: historySnapshot }),
  })
    .then(r => r.json())
    .then(data => {
      document.getElementById('chatTyping')?.remove();
      if (data.draft_purchase) {
        renderDraftPurchase(box, data.draft_purchase);
        return;
      }
      const answer = data.answer || T[lang].noAnswer;
      chatHistory.push({ role: 'user', content: val });
      const histEntry = data.sql ? answer + '\n-- query: ' + data.sql : answer;
      chatHistory.push({ role: 'assistant', content: histEntry });
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.textContent = answer;
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    })
    .catch(() => {
      document.getElementById('chatTyping')?.remove();
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.innerHTML = `<span style="color:var(--pink)">${T[lang].chatError}</span>`;
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    });
}

/* ── CHAT-LOGGED PURCHASE ── */
function renderDraftPurchase(box, items) {
  const card = document.createElement('div');
  card.className = 'msg msg-b draft-card';
  const rowsHtml = items.map((it, i) => `
    <tr>
      <td><input value="${esc(it.raw_name || '')}" onchange="updateDraftItem(${i},'raw_name',this.value)"></td>
      <td><input value="${esc(it.quantity ?? '')}" style="width:48px" onchange="updateDraftItem(${i},'quantity',this.value)"></td>
      <td><input value="${esc(it.total_price ?? '')}" style="width:60px" onchange="updateDraftItem(${i},'total_price',this.value)"></td>
      <td><input type="date" value="${esc(it.datetime || '')}" onchange="updateDraftItem(${i},'datetime',this.value)"></td>
    </tr>`).join('');
  card.innerHTML = `
    <div class="draft-title">${T[lang].draftTitle}</div>
    <table class="draft-table">${rowsHtml}</table>
    <div class="draft-actions">
      <button class="draft-btn draft-confirm" onclick="confirmDraftPurchase(this)">${T[lang].draftConfirm}</button>
      <button class="draft-btn draft-cancel" onclick="cancelDraftPurchase(this)">${T[lang].draftCancel}</button>
    </div>`;
  card._items = items;
  box.appendChild(card);
  box.scrollTop = box.scrollHeight;
}

function updateDraftItem(idx, field, value) {
  const card = document.querySelector('.draft-card:last-of-type');
  if (card && card._items) card._items[idx][field] = value;
}

function cancelDraftPurchase(btn) {
  const card = btn.closest('.draft-card');
  card.querySelectorAll('input, button').forEach(el => el.disabled = true);
  card.style.opacity = '.5';
}

async function confirmDraftPurchase(btn) {
  const card = btn.closest('.draft-card');
  const box = document.getElementById('chatMsgs');
  card.querySelectorAll('input, button').forEach(el => el.disabled = true);
  const res = await fetch('/api/chat/commit-purchase', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items: card._items }),
  });
  const mb = document.createElement('div');
  mb.className = 'msg msg-b';
  if (res.ok) {
    mb.textContent = T[lang].draftSaved;
    loadAll();
  } else {
    mb.innerHTML = `<span style="color:var(--pink)">${T[lang].draftSaveErr}</span>`;
  }
  box.appendChild(mb);
  box.scrollTop = box.scrollHeight;
}

/* esc() is shared — see static/js/utils.js */

async function loadChatNotice() {
  try {
    const d = await fetch(`/api/chat/notice?lang=${lang}`).then(r => r.json());
    if (!d.notice) return;
    const box = document.getElementById('chatMsgs');
    const mb = document.createElement('div');
    mb.className = 'msg msg-b';
    mb.textContent = d.notice;
    box.appendChild(mb);
  } catch {}
}

/* ── INIT ── */
setLang(lang);
loadAll();
loadChatNotice();
