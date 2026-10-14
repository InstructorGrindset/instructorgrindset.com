/* Loads the shared nav drawer into #sidebar-placeholder and wires up its behavior. */
(function () {
  var t = document.getElementById('sidebar-placeholder');
  if (!t) return;

  fetch('/partials/sidebar.html?v=14')
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
    .then(function (html) {
      // data-defer-images on the placeholder holds drawer images until the drawer first opens.
      var defer = t.hasAttribute('data-defer-images');
      var d = document.createElement('div');
      d.innerHTML = defer ? html.trim().replace(/<img ([^>]*?)src="/g, '<img $1data-src="') : html.trim();
      var root = d.firstElementChild;
      t.replaceWith(root);
      wire(root);
    })
    .catch(function () { t.removeAttribute('aria-busy'); });

  function norm(p) { return p.replace(/index\.html$/, '').replace(/\/+$/, '') || '/'; }

  function wire(root) {
    var burger = root.querySelector('.hamburger');
    var closeBtn = root.querySelector('.nav-close');
    var backdrop = root.querySelector('.nav-backdrop');

    function open() {
      root.querySelectorAll('img[data-src]').forEach(function (i) {
        i.src = i.getAttribute('data-src');
        i.removeAttribute('data-src');
      });
      root.classList.add('is-open');
      burger.setAttribute('aria-expanded', 'true');
      closeBtn.focus();
    }
    function close() {
      if (!root.classList.contains('is-open')) return;
      root.classList.remove('is-open');
      burger.setAttribute('aria-expanded', 'false');
      burger.focus();
    }

    burger.addEventListener('click', open);
    closeBtn.addEventListener('click', close);
    backdrop.addEventListener('click', close);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });

    var here = norm(location.pathname);
    root.querySelectorAll('.nav a[href]').forEach(function (a) {
      if (norm(a.getAttribute('href')) === here) a.setAttribute('aria-current', 'page');
    });
  }
})();
