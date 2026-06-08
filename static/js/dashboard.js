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
    pointBorderColor:     data.map((_, i) => i === anomIdx ? '#F92672' : '#FD971F'),
    pointBackgroundColor: data.map((_, i) => i === anomIdx ? 'transparent' : '#FD971F'),
    pointBorderWidth:     data.map((_, i) => i === anomIdx ? 2.5 : 1.5),
  };
}

chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'total', data: [], borderColor: '#FD971F',
        backgroundColor: mkGrad(253, 151, 31), borderWidth: 2.5,
        fill: true, tension: 0.42, order: 3,
        pointRadius: [], pointBorderColor: [], pointBackgroundColor: [], pointBorderWidth: [],
      },
      {
        label: 'abarrotes', data: [], borderColor: '#66D9E8',
        backgroundColor: mkGrad(102, 217, 232, 0.14), borderWidth: 1.5,
        borderDash: [5, 4], fill: true, tension: 0.42, pointRadius: 2.5, order: 2,
      },
      {
        label: 'carnes', data: [], borderColor: '#F92672',
        backgroundColor: mkGrad(249, 38, 114, 0.11), borderWidth: 1.5,
        borderDash: [5, 4], fill: true, tension: 0.42, pointRadius: 2.5, order: 1,
      },
      {
        label: 'delivery + servicio', data: [], borderColor: '#A6E22E',
        backgroundColor: 'transparent', borderWidth: 1, fill: false,
        tension: 0.42, pointRadius: 2, pointBackgroundColor: '#A6E22E', order: 0,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#2E323C', borderColor: '#343842', borderWidth: 1,
        titleColor: '#75715E', bodyColor: '#F8F8F2',
        titleFont: { family: 'IBM Plex Mono', size: 10 },
        bodyFont:  { family: 'IBM Plex Mono', size: 11 },
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
        grid: { color: 'rgba(52,56,66,.5)', drawBorder: false },
        ticks: { color: '#6B7585', font: { family: 'IBM Plex Mono', size: 9 } },
        border: { display: false },
      },
      y: {
        grid: { color: 'rgba(52,56,66,.5)', drawBorder: false },
        ticks: { color: '#6B7585', font: { family: 'IBM Plex Mono', size: 9 }, callback: v => 'S/.' + v },
        border: { display: false },
      },
    },
  },
});

/* ── DATA LOADING ── */

async function loadAll() {
  await Promise.all([loadBudget(), loadKPIs(currentPeriod), loadChart(currentPeriod), loadNeeded(), loadTopItems(), loadOrders(currentPeriod)]);
  scrollChat();
  document.getElementById('basket-demo').innerHTML = basketHTML(lang);
}

async function loadBudget() {
  const d = await fetch('/api/budget').then(r => r.json());
  if (!d.spent_this_month) return;
  const pct = d.pct_of_budget || 0;
  document.getElementById('budget-pct').textContent = `${pct}%`;
  const bar = document.getElementById('budget-bar');
  bar.style.width = `${Math.min(pct, 100)}%`;
  bar.classList.toggle('over', pct > 100);
  document.getElementById('budget-spent').textContent =
    `S/.${(d.spent_this_month || 0).toFixed(2)} / S/.${(d.effective_budget || 0).toFixed(2)}`;
  document.getElementById('budget-avg').textContent = `S/.${Math.round(d.avg_baseline || 0)}`;
  document.getElementById('budget-days').textContent = d.days_remaining ?? '—';
  const now = new Date();
  document.getElementById('budget-period').textContent =
    now.toLocaleString('es-PE', { month: 'long', year: 'numeric' });
}

async function loadKPIs(period = currentPeriod) {
  const d = await fetch(`/api/kpis?period=${period}`).then(r => r.json());
  const fmt = v => v == null ? '—' : (v >= 0 ? `↑ ${v}%` : `↓ ${Math.abs(v)}%`);

  const tot = d.month_total || 0;
  const intPart = Math.floor(tot);
  const decPart = (tot - intPart).toFixed(2).slice(1);
  document.getElementById('kpi-total').innerHTML = `<span class="u">S/.</span>${intPart}<span class="u">${decPart}</span>`;
  const td = d.month_total_delta;
  document.getElementById('kpi-total-delta').className = 'kpi-delta ' + (td > 0 ? 'up' : td < 0 ? 'down' : 'flat');
  document.getElementById('kpi-total-delta').textContent = td != null ? `${td > 0 ? '↑' : '↓'} ${Math.abs(td)}% vs promedio` : '—';

  document.getElementById('kpi-orders').innerHTML = `${d.orders || 0} <span class="u"><span class="i18n" data-en="ord." data-es="órd.">órd.</span></span>`;
  const od = d.orders_delta || 0;
  document.getElementById('kpi-orders-delta').className = 'kpi-delta ' + (od < 0 ? 'down' : od > 0 ? 'up' : 'flat');
  document.getElementById('kpi-orders-delta').textContent = od !== 0 ? `${od > 0 ? '+' : ''}${od} vs mes pasado` : '— estable';

  const avg = d.avg_order || 0;
  const avgInt = Math.floor(avg), avgDec = (avg - avgInt).toFixed(2).slice(1);
  document.getElementById('kpi-avg').innerHTML = `<span class="u">S/.</span>${avgInt}<span class="u">${avgDec}</span>`;
  const ad = d.avg_order_delta;
  document.getElementById('kpi-avg-delta').className = 'kpi-delta ' + (ad > 2 ? 'up' : ad < -2 ? 'down' : 'flat');
  document.getElementById('kpi-avg-delta').textContent = ad != null ? `${ad > 0 ? '↑' : '↓'} ${Math.abs(ad)}%` : '— estable';

  document.getElementById('kpi-tracked').innerHTML = `${d.tracked_items || 0} <span class="u">items</span>`;
  document.getElementById('kpi-tracked-delta').className = 'kpi-delta down';
  document.getElementById('kpi-tracked-delta').textContent = d.new_this_month ? `+${d.new_this_month} este mes` : '— sin cambios';
}

async function loadChart(period) {
  const d = await fetch(`/api/chart?period=${period}`).then(r => r.json());
  chartData[period] = d;

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
  const items = await fetch('/api/needed-soon').then(r => r.json());
  const grid = document.getElementById('neededGrid');
  grid.innerHTML = '';

  if (!items.length) {
    grid.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:var(--dim2);padding:8px 0">no hay items que reabastecer pronto</div>';
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
  const color = item.urgency_color || '#454158';
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
        <circle cx="18" cy="18" r="15.915" fill="none" stroke="rgba(52,56,66,.9)" stroke-width="2.5"/>
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

async function loadTopItems() {
  const items = await fetch(`/api/top-items?period=${currentPeriod}`).then(r => r.json());
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
      <div class="bar-bg"><div class="bar-fill" style="width:${item.bar_width}%;background:${item.color}"></div></div>
      <div class="ti-pct">${item.pct}%</div>`;
    list.appendChild(row);
  });
}

const ENTRY_EN = { app: 'app', foto: 'photo', texto: 'text' };

async function loadOrders(period = currentPeriod) {
  const orders = await fetch(`/api/recent-orders?period=${period}`).then(r => r.json());
  const tbody = document.getElementById('ordersBody');
  tbody.innerHTML = '';
  const BADGE = {
    app:   'background:rgba(174,129,255,.1);border:1px solid rgba(174,129,255,.22);color:var(--purple)',
    foto:  'background:rgba(102,217,232,.1);border:1px solid rgba(102,217,232,.22);color:var(--cyan)',
    texto: 'background:rgba(166,226,46,.1);border:1px solid rgba(166,226,46,.22);color:var(--green)',
  };
  orders.forEach(o => {
    const tr = document.createElement('tr');
    const badge = BADGE[o.entry_type] || BADGE.foto;
    const enLabel = ENTRY_EN[o.entry_type] || o.entry_type;
    const label = lang === 'en' ? enLabel : o.entry_type;
    tr.innerHTML = `
      <td>${o.date_label}</td>
      <td><span class="td-store">${o.source}</span></td>
      <td class="td-price">S/.${(o.order_total || 0).toFixed(2)}</td>
      <td><span class="badge i18n" data-en="${enLabel}" data-es="${o.entry_type}" style="${badge}">${label}</span></td>`;
    tbody.appendChild(tr);
  });
}

/* ── PERIOD SWITCH ── */
function setPeriod(el, period) {
  currentPeriod = period;
  el.closest('.tab-group').querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  loadChart(period);
  loadTopItems();
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
  lbl.textContent = open ? 'ver menos' : `ver más (${extras.length})`;
  if (open) animateArcs([...document.querySelectorAll('.ni-extra.shown .ni-arc')]);
}

/* ── I18N ── */
const T = {
  es: { budget:'presupuesto', avg18:'promedio 18m', days:'días restantes', chart:'gasto mensual',
        legTotal:'total', legDelivery:'delivery + servicio', needed:'por agotar',
        bycat:'mayor gasto · este mes', recent:'últimas compras', chat:'manager',
        placeholder:'consulta sobre precios, listas, urgencias, tendencias…' },
  en: { budget:'budget', avg18:'18-mo avg', days:'days left', chart:'monthly spend',
        legTotal:'total', legDelivery:'delivery + service', needed:'running low',
        bycat:'top spend · this month', recent:'recent purchases', chat:'warehouse manager',
        placeholder:'query prices, lists, urgency, trends…' },
};

function setLang(l) {
  lang = l;
  localStorage.setItem('lang', l);
  const t = T[l];
  const ids = { 'lbl-budget':t.budget, 'lbl-avg18':t.avg18, 'lbl-days':t.days,
                'lbl-chart':t.chart, 'leg-total':t.legTotal, 'leg-delivery':t.legDelivery,
                'lbl-needed':t.needed, 'lbl-bycat':t.bycat, 'lbl-recent':t.recent,
                'lbl-chat':t.chat };
  for (const [id, val] of Object.entries(ids)) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  document.querySelectorAll('.i18n').forEach(el => { if (el.dataset[l]) el.textContent = el.dataset[l]; });
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('active', b.dataset.lang === l));
  document.getElementById('chatIn').placeholder = t.placeholder;
  const anom = chartData[currentPeriod];
  if (anom && anom.anomalyLabel) document.getElementById('lbl-anomaly').textContent = anom.anomalyLabel[l] || '';
  document.getElementById('basket-demo').innerHTML = basketHTML(l);
  scrollChat();
}

/* ── CHAT ── */
function basketHTML(l) {
  const note = l === 'es'
    ? '<span class="basket-note">solo abarrotes y despensa · farmacia excluida</span>'
    : '<span class="basket-note">groceries & pantry only · pharmacy excluded</span>';
  return l === 'es'
    ? `lista para esta semana:<br><br><span class="src">pedidosya market</span><span class="src-items">aceite primor 1L &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.7.50</span><br>leche gloria 6× &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.23.90</span><br>arroz costenho 1kg &nbsp;~<span class="hi">S/.4.20</span></span><span class="src-sub">subtotal ~S/.35.60</span><br><span class="src">tottus (mejor precio)</span><span class="src-items">huevos pardos 30u &nbsp;&nbsp;~<span class="hi">S/.18.90</span><br>papa yungay 2kg &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.5.80</span></span><span class="src-sub">subtotal ~S/.24.70</span><span class="basket-total">total estimado &nbsp;&nbsp;<span class="hi">~S/.60.30</span></span>${note}`
    : `shopping list for this week:<br><br><span class="src">pedidosya market</span><span class="src-items">aceite primor 1L &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.7.50</span><br>leche gloria 6× &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.23.90</span><br>arroz costenho 1kg &nbsp;~<span class="hi">S/.4.20</span></span><span class="src-sub">subtotal ~S/.35.60</span><br><span class="src">tottus (best price)</span><span class="src-items">huevos pardos 30u &nbsp;&nbsp;~<span class="hi">S/.18.90</span><br>papa yungay 2kg &nbsp;&nbsp;&nbsp;&nbsp;~<span class="hi">S/.5.80</span></span><span class="src-sub">subtotal ~S/.24.70</span><span class="basket-total">estimated total &nbsp;&nbsp;<span class="hi">~S/.60.30</span></span>${note}`;
}

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
    body: JSON.stringify({ question: val }),
  })
    .then(r => r.json())
    .then(data => {
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.textContent = data.answer || 'Sin respuesta.';
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    })
    .catch(() => {
      const mb = document.createElement('div');
      mb.className = 'msg msg-b';
      mb.innerHTML = '<span style="color:var(--pink)">error al consultar</span>';
      box.appendChild(mb);
      box.scrollTop = box.scrollHeight;
    });
}


/* ── INIT ── */
document.getElementById('basket-demo').innerHTML = basketHTML(lang);
loadAll();
