'use strict';

/* Shared HTML-escaping helper — always escape server-sourced strings before
   interpolating them into innerHTML. Loaded on every page via base.html. */
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* Shared currency-symbol helper. Currency is a separate preference from
   language (set in Settings, free text) so it doesn't get silently reset
   by toggling ES/EN; it defaults to match the page's own module-level
   `lang` variable until the user explicitly sets one. Loaded on every page
   via base.html. */
function curr() {
  const saved = localStorage.getItem('currency');
  if (saved) return saved;
  return lang === 'en' ? '$' : 'S/.';
}

/* DEMO_MODE is set as a page-global in base.html; the typeof check guards
   pages that load this before base.html's inline script has run. Loaded on
   every page via base.html. */
function isDemoMode() {
  return typeof DEMO_MODE !== 'undefined' && DEMO_MODE;
}

/* Display-only translation for the English demo dataset's item/category/
   subcategory/synonym strings when viewed in Spanish - the data itself
   stays English; DEMO_ES_TR (loaded conditionally in base.html) swaps only
   what's shown. No-ops for real (non-demo) data or English UI, so a real
   user's own data is never touched. */
function _demoTr(map, key) {
  if (!isDemoMode() || lang !== 'es' || typeof DEMO_ES_TR === 'undefined') return null;
  return (DEMO_ES_TR[map] && DEMO_ES_TR[map][key]) || null;
}
function trItem(name) { return _demoTr('items', name) || name; }
function trCategory(cat) { return _demoTr('categories', cat) || cat; }
function trSubcategory(sub) { return _demoTr('subcategories', sub) || sub; }
function trSynonyms(itemName, fallback) { return _demoTr('synonyms', itemName) || fallback; }

/* Swaps any known English item name appearing inside a full backend-composed
   sentence (e.g. the chat proactive notice, "Te estas quedando sin Spinach,
   ...") for its Spanish translation. The item name is the only English
   fragment in an otherwise-Spanish string, so a substring swap is enough -
   no need to know the sentence's structure. Longest names first so e.g.
   "Salad Mix" doesn't get shadowed by a shorter unrelated match. */
function trNoticeText(text) {
  if (!isDemoMode() || lang !== 'es' || typeof DEMO_ES_TR === 'undefined' || !text) return text;
  const items = DEMO_ES_TR.items || {};
  const names = Object.keys(items).sort((a, b) => b.length - a.length);
  for (const name of names) {
    if (text.includes(name)) return text.split(name).join(items[name]);
  }
  return text;
}
