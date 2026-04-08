// Theme initialization — runs before React hydration to prevent FOUC.
// Extracted to external file for CSP compliance (avoids unsafe-inline).
(function() {
  var t = localStorage.getItem('sagewai-theme');
  if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
})();
