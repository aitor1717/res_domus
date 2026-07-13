'use strict';

/* Shared HTML-escaping helper — always escape server-sourced strings before
   interpolating them into innerHTML. Loaded on every page via base.html. */
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* Shared currency-symbol helper — relies on the page's own module-level
   `lang` variable (each page script declares one). Loaded on every page via
   base.html. */
function curr() {
  return lang === 'en' ? '$' : 'S/.';
}
