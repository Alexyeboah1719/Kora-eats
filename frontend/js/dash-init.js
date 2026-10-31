/* Starts a dashboard. Configured through data-attributes on this script tag, so the pages need no inline
   JavaScript (which lets the Content-Security-Policy forbid inline scripts). */
(() => {
  const el = document.currentScript;
  Dash.init(el.dataset.area, { title: el.dataset.title, roles: JSON.parse(el.dataset.roles) });
})();
