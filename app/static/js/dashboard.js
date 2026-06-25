'use strict';

let currentPeriod = '30d';
let lang = localStorage.getItem('lang') || 'en';
let chart = null;
let chartData = {};

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
        label: 'abarrotes', data: [], borderColor: '#FFC2D6',
        backgroundColor: mkGrad(255, 194, 214, 0.14), borderWidth: 1.5,
        borderDash: [5, 4], fill: true, tension: 0.42, pointRadius: 2.5, order: 2,
      },
      {
        label: 'carnes', data: [], borderColor: '#FF6F91',
        backgroundColor: mkGrad(255, 111, 145, 0.11), borderWidth: 1.5,
        borderDash: [5, 4], fill: true, tension: 0.42, pointRadius: 2.5, order: 1,
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
              return ` delivery + servicio  S/.${(v || 0).toFixed(0)}`;
            }
            return ` ${c.dataset.label}  S/.${c.parsed.y.toFixed(0)}`;
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
        ticks: { color: 'rgba(255,157,110,.52)', font: { family: 'IBM Plex Mono', size: 9 }, callback: v => 'S/.' + v },
        border: { display: false },
      },
    },
  },
});

/* ── DATA LOADING ── */

async function loadAll() {
  await Promise.all([loadBudget(), loadKPIs(currentPeriod), loadChart(currentPeriod), loadNeeded(), loadTopItems(), loadOrders(currentPeriod)]);
  scrollChat();
}

function drawRing(svgId, pct, color, label) {
  const svg = document.getElementById(svgId);
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
  if (!d.spent_this_month) return;
  const pct = d.pct_of_budget || 0;
  drawRing('ringBudget', pct, '#FF9D6E', `${pct}%`);

  const dev = d.deviation_pct;
  const devLabel = dev == null ? '—' : `${dev > 0 ? '+' : ''}${dev}%`;
  const devColor = dev > 0 ? '#FF6F91' : '#FFE0A3';
  drawRing('ringDeviation', dev == null ? 0 : Math.abs(dev), devColor, devLabel);

  document.getElementById('ringmeta-spent').innerHTML =
    `${T[lang].spentLbl} <b><span class="curr-ring">S/.</span>${(d.spent_this_month || 0).toFixed(2)}</b>`;
  document.getElementById('ringmeta-days').innerHTML =
    `<b>${d.days_remaining ?? '—'}</b> ${T[lang].daysLbl}`;
}

async function loadKPIs(period = currentPeriod) {
  const d = await fetch(`/api/kpis?period=${period}`).then(r => r.json());
  if (period !== currentPeriod) return; // stale response from a since-changed period tab
  const fmt = v => v == null ? '—' : (v >= 0 ? `↑ ${v}%` : `↓ ${Math.abs(v)}%`);

  const tot = d.month_total || 0;
  document.getElementById('kpi-total').innerHTML = `<span class="curr-kpi">S/.</span>${tot.toFixed(2)}`;
  const td = d.month_total_delta;
  document.getElementById('kpi-total-delta').className = 'kpi-delta ' + (td > 0 ? 'up' : td < 0 ? 'down' : 'flat');
  document.getElementById('kpi-total-delta').textContent = td != null ? `${td > 0 ? '↑' : '↓'} ${Math.abs(td)}%` : '—';

  document.getElementById('kpi-orders').textContent = d.orders || 0;
  const od = d.orders_delta || 0;
  document.getElementById('kpi-orders-delta').className = 'kpi-delta ' + (od < 0 ? 'down' : od > 0 ? 'up' : 'flat');
  document.getElementById('kpi-orders-delta').textContent = od !== 0 ? `${od > 0 ? '+' : ''}${od}` : '—';

  const avg = d.avg_order || 0;
  document.getElementById('kpi-avg').innerHTML = `<span class="curr-kpi">S/.</span>${avg.toFixed(2)}`;
  const ad = d.avg_order_delta;
  document.getElementById('kpi-avg-delta').className = 'kpi-delta ' + (ad > 2 ? 'up' : ad < -2 ? 'down' : 'flat');
  document.getElementById('kpi-avg-delta').textContent = ad != null ? `${ad > 0 ? '↑' : '↓'} ${Math.abs(ad)}%` : '—';

  document.getElementById('kpi-tracked').textContent = d.tracked_items || 0;
  document.getElementById('kpi-tracked-delta').className = 'kpi-delta ' + (d.new_this_month ? 'up' : 'flat');
  document.getElementById('kpi-tracked-delta').textContent = d.new_this_month ? `+${d.new_this_month}` : '—';
}

async function loadChart(period) {
  const d = await fetch(`/api/chart?period=${period}`).then(r => r.json());
  chartData[period] = d;
  if (period !== currentPeriod) return; // stale response from a since-changed period tab

  const anomIdx = d.anomalyIdx;
  const ps = ptStyle(d.total || [], anomIdx);
  chart.data.labels = d.labels || [];
  Object.assign(chart.data.datasets[0], { data: d.total || [], ...ps });
  chart.data.datasets[1].data = d.abarrotes || [];
  chart.data.datasets[2].data = d.carnes || [];
  chart.data.datasets[3].data = d.deliveryAbove || [];
  chart.update('active');

  const lbl = d.anomalyLabel || {};
  document.getElementById('lbl-anomaly').textContent = lbl[lang] || '';
}

async function loadNeeded() {
  const data = await fetch('/api/needed-soon').then(r => r.json());
  const items = data.items || [];
  const grid = document.getElementById('neededGrid');
  grid.innerHTML = '';

  if (!items.length) {
    grid.innerHTML = data.reliable_count > 0
      ? `<div class="needed-tag needed-ok">${T[lang].neededAllGood}</div>`
      : `<div class="needed-tag needed-nodata">${T[lang].neededNoData}</div>`;
    document.getElementById('needExpBtn').style.display = 'none';
    return;
  }

  const visible = items.slice(0, 6);
  const extra = items.slice(6);

  visible.forEach((item, i) => grid.appendChild(makeCircle(item, i, false)));
  extra.forEach((item, i) => grid.appendChild(makeCircle(item, i, true)));

  const btn = document.getElementById('needExpBtn');
  if (extra.length) {
    btn.style.display = 'flex';
    document.getElementById('needExpLabel').textContent = `ver más (${extra.length})`;
  } else {
    btn.style.display = 'none';
  }

  setTimeout(() => animateArcs([...document.querySelectorAll('.ni:not(.ni-extra) .ni-arc')]), 50);
}

function makeCircle(item, i, isExtra) {
  const pct = item.urgency_pct || 0;
  const color = item.urgency_color || '#4D6175';
  const MES = ['','ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  let dateLbl = '';
  try {
    const d = new Date(item.last_purchase_date);
    dateLbl = `${d.getDate()} ${MES[d.getMonth()+1]}`;
  } catch(_) {}
  const cycleLbl = item.avg_interval_days ? `~${Math.round(item.avg_interval_days)}d` : '';

  const div = document.createElement('div');
  div.className = 'ni' + (isExtra ? ' ni-extra' : '');
  div.style.cssText = `--nd:${0.35 + i * 0.07}s`;
  div.innerHTML = `
    <div class="ni-circ">
      <svg viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.915" fill="none" stroke="rgba(58,77,97,.9)" stroke-width="2.5"/>
        <circle cx="18" cy="18" r="15.915" fill="none" stroke="${color}" stroke-width="2.5"
          stroke-linecap="round" data-pct="${pct}"
          transform="rotate(-90 18 18)" class="ni-arc"/>
      </svg>
      <div class="ni-val" style="color:${color}">${pct}%</div>
    </div>
    <div class="ni-name">${item.matched_id || '—'}</div>
    <div class="ni-meta">${dateLbl}${cycleLbl ? ' · ' + cycleLbl : ''}</div>`;
  return div;
}

async function loadTopItems(period = currentPeriod) {
  const items = await fetch(`/api/top-items?period=${period}`).then(r => r.json());
  if (period !== currentPeriod) return; // stale response from a since-changed period tab
  const list = document.getElementById('topItemsList');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--dim2);padding:8px 0">sin datos</div>';
    return;
  }
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'ti-row';
    row.innerHTML = `
      <div class="ti-name">${item.matched_id}</div>
      <div class="ti-bar"><div class="ti-fill" style="width:${item.bar_width}%"></div></div>
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
  if (period !== currentPeriod) return; // stale response from a since-changed period tab
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
    const type = ENTRY_LABEL[o.entry_type] ? o.entry_type : 'other';
    const badge = BADGE[type];
    const label = ENTRY_LABEL[type][lang] || ENTRY_LABEL[type].en;
    tr.innerHTML = `
      <td>${o.date_label}</td>
      <td><span class="td-store">${o.source}</span></td>
      <td class="td-price">S/.${(o.order_total || 0).toFixed(2)}</td>
      <td><span class="badge i18n" data-en="${ENTRY_LABEL[type].en}" data-es="${ENTRY_LABEL[type].es}" style="${badge}">${label}</span></td>`;
    tbody.appendChild(tr);
  });
}

/* ── PERIOD SWITCH ── */
function setPeriod(el, period) {
  currentPeriod = period;
  el.closest('.tab-group').querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  loadChart(period);
  loadTopItems(period);
  loadKPIs(period);
  loadOrders(period);
}

/* ── ARC ANIMATION ── */
function animateArcs(arcs) {
  arcs.forEach((el, i) => {
    setTimeout(() => {
      el.style.strokeDashoffset = 100 - parseFloat(el.dataset.pct);
    }, i * 75);
  });
}

function toggleNeeded() {
  const extras = document.querySelectorAll('.ni.ni-extra');
  const btn = document.getElementById('needExpBtn');
  const lbl = document.getElementById('needExpLabel');
  const open = btn.classList.toggle('open');
  extras.forEach(el => el.classList.toggle('shown', open));
  lbl.textContent = open ? (lang === 'es' ? 'ver menos' : 'see less') : `${lang === 'es' ? 'ver más' : 'see more'} (${extras.length})`;
  if (open) {
    animateArcs([...document.querySelectorAll('.ni-extra.shown .ni-arc')]);
    setExpandedMode('needed');
  } else if (expandedMode === 'needed') {
    setExpandedMode(null);
  }
}

/* ── CHAT <-> NEEDED-SOON MUTUAL-EXCLUSIVE EXPAND ──
   Both hide top_spend + recent_purchases and show the shared restore bar.
   Only one can be expanded at a time; opening one closes the other. */
let expandedMode = null;
function setExpandedMode(mode) {
  expandedMode = mode;
  document.getElementById('dashGrid').classList.toggle('expanded', !!mode);
  if (mode !== 'chat') document.getElementById('chatMsgs').classList.remove('expanded');
  if (mode !== 'needed' && document.getElementById('needExpBtn').classList.contains('open')) {
    toggleNeeded();
  }
}

function onChatFocus() {
  document.getElementById('chatMsgs').classList.add('expanded');
  setExpandedMode('chat');
}
function onChatBlur() {
  if (!document.getElementById('chatMsgs').children.length && expandedMode === 'chat') {
    setExpandedMode(null);
  }
}

// mousedown, not click: focus-triggered layout shift (chat/needed-soon expanding)
// can move what's under the pointer before 'click' fires, retargeting it to the
// wrong element. mousedown resolves before that shift happens.
document.addEventListener('mousedown', e => {
  if (!expandedMode) return;
  const activeEl = expandedMode === 'chat'
    ? document.getElementById('chatPanel')
    : document.querySelector('.block-chart');
  if (activeEl && !activeEl.contains(e.target)) setExpandedMode(null);
});
document.querySelectorAll('[data-restore]').forEach(el => el.addEventListener('click', () => setExpandedMode(null)));

/* ── I18N ── */
const T = {
  es: { budget:'presupuesto', avg18:'promedio 18m', days:'días restantes', chart:'gasto mensual',
        legTotal:'total', legDelivery:'delivery + servicio', needed:'por agotar',
        bycat:'mayor gasto · este mes', recent:'últimas compras', chat:'manager',
        placeholder:'consulta sobre precios, listas, urgencias, tendencias…',
        noAnswer:'Sin respuesta.', chatError:'error al consultar',
        draftTitle:'confirmar entrada de compra:', draftConfirm:'confirmar', draftCancel:'cancelar',
        draftSaved:'compra registrada.', draftSaveErr:'error al guardar la compra',
        neededAllGood:'todo bien — nada urgente por reabastecer', neededNoData:'aún no hay suficiente historial de compras para este cálculo',
        ringBudgetCap:'presupuesto usado', ringDevCap:'vs prom. 30d', spentLbl:'gastado', daysLbl:'días restantes',
        topSpendTitle:'top_spend', recentTitle:'recent_purchases' },
  en: { budget:'budget', avg18:'18-mo avg', days:'days left', chart:'monthly spend',
        legTotal:'total', legDelivery:'delivery + service', needed:'running low',
        bycat:'top spend · this month', recent:'recent purchases', chat:'warehouse manager',
        placeholder:'query prices, lists, urgency, trends…',
        noAnswer:'No answer.', chatError:'query error',
        draftTitle:'confirm purchase entry:', draftConfirm:'confirm', draftCancel:'cancel',
        draftSaved:'purchase logged.', draftSaveErr:'error saving purchase',
        neededAllGood:'all good — nothing urgent to restock', neededNoData:'not enough purchase history yet for this insight',
        ringBudgetCap:'budget used', ringDevCap:'vs 30d avg', spentLbl:'spent', daysLbl:'days left',
        topSpendTitle:'top_spend', recentTitle:'recent_purchases' },
};

function setLang(l) {
  lang = l;
  localStorage.setItem('lang', l);
  const t = T[l];
  const ids = { 'lbl-budget':t.budget, 'lbl-avg18':t.avg18, 'lbl-days':t.days,
                'lbl-chart':t.chart, 'leg-total':t.legTotal, 'leg-delivery':t.legDelivery,
                'lbl-needed':t.needed, 'lbl-bycat':t.bycat, 'lbl-recent':t.recent,
                'lbl-chat':t.chat, 'lbl-ring-budget':t.ringBudgetCap, 'lbl-ring-dev':t.ringDevCap };
  for (const [id, val] of Object.entries(ids)) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  document.querySelectorAll('.i18n').forEach(el => { if (el.dataset[l]) el.textContent = el.dataset[l]; });
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('active', b.dataset.lang === l));
  document.getElementById('chatIn').placeholder = t.placeholder;
  const anom = chartData[currentPeriod];
  if (anom && anom.anomalyLabel) document.getElementById('lbl-anomaly').textContent = anom.anomalyLabel[l] || '';
  if (document.getElementById('ringmeta-spent').innerHTML) loadBudget();
  scrollChat();
}

/* ── CHAT ── */
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
  box.scrollTop = box.scrollHeight;

  // Call real API
  fetch('/api/chat/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: val, lang }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.draft_purchase) {
        renderDraftPurchase(box, data.draft_purchase);
        return;
      }
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.textContent = data.answer || T[lang].noAnswer;
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    })
    .catch(() => {
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.innerHTML = `<span style="color:var(--pink)">${T[lang].chatError}</span>`;
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    });
}

/* ── CHAT-LOGGED PURCHASE (extract -> confirm -> insert) ── */
function renderDraftPurchase(box, items) {
  const card = document.createElement('div');
  card.className = 'msg msg-b draft-card';
  const rowsHtml = items.map((it, i) => `
    <tr>
      <td><input value="${escAttr(it.raw_name || '')}" onchange="updateDraftItem(${i},'raw_name',this.value)"></td>
      <td><input value="${escAttr(it.quantity ?? '')}" style="width:48px" onchange="updateDraftItem(${i},'quantity',this.value)"></td>
      <td><input value="${escAttr(it.total_price ?? '')}" style="width:60px" onchange="updateDraftItem(${i},'total_price',this.value)"></td>
      <td><input type="date" value="${escAttr(it.datetime || '')}" onchange="updateDraftItem(${i},'datetime',this.value)"></td>
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

function escAttr(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}


/* ── INIT ── */
setLang(lang);
loadAll();
