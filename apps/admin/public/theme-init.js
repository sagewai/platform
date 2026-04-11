// Theme initialization — runs before React hydration to prevent FOUC.
// Extracted to external file for CSP compliance (avoids unsafe-inline).
(function() {
  var t = localStorage.getItem('sagewai-theme');
  if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
  // shadcn/ui primitives use a `.dark` class. Keep it in sync with data-theme so
  // both selectors are valid before React mounts (prevents flash of unstyled UI).
  if (t === 'dark') document.documentElement.classList.add('dark');
  else document.documentElement.classList.remove('dark');
})();
