/* Loads the shared nav drawer into #sidebar-placeholder and wires up its behavior. */
(function () {
  var t = document.getElementById('sidebar-placeholder');
  if (!t) return;

  fetch('/partials/sidebar.html?v=14')
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
    .then(function (html) {
      var d = document.createElement('div');
      d.innerHTML = html.trim();
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
