/* Lesson Log setup builder: turns the list editors into a ready-to-run Apps Script. */
(function () {
  var DEFAULTS = {
    name: 'Lesson Log',
    instructors: [],
    disciplines: ['Ski', 'Snowboard', 'Telemark', 'Adaptive'],
    levels: ['Level 1', 'Level 2', 'Level 3', 'Level 4', 'Level 5', 'Level 6', 'Level 7', 'Level 8', 'Level 9'],
    lessonTypes: ['Private', 'Semi-private', 'Group', 'Kids program', 'Clinic'],
    technicalFocus: ['Stance & balance', 'Edging', 'Rotary / steering', 'Pressure control', 'Timing & coordination', 'Turn shape', 'Pole use'],
    behaviorFocus: ['Confidence', 'Fear management', 'Motivation', 'Attention & focus', 'Following directions', 'Energy / fatigue'],
    terrain: ['Learning area', 'Green', 'Blue', 'Black', 'Bumps', 'Trees', 'Park', 'Powder']
  };
  var LISTS = ['instructors', 'disciplines', 'levels', 'lessonTypes', 'technicalFocus', 'behaviorFocus', 'terrain'];
  var CONFIG_KEY = 'ig-lessonlog-config';
  var STEPS_KEY = 'ig-lessonlog-steps';
  var template = null;

  function load(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; } }
  function save(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {} }
  function $(id) { return document.getElementById(id); }

  function lines(text) {
    return text.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function readConfig() {
    var cfg = { name: $('ll-name').value.trim() || DEFAULTS.name };
    LISTS.forEach(function (k) { cfg[k] = lines($('ll-' + k).value); });
    return cfg;
  }

  function fillForm(cfg) {
    $('ll-name').value = cfg.name || DEFAULTS.name;
    LISTS.forEach(function (k) { $('ll-' + k).value = (cfg[k] || []).join('\n'); updateCount(k); });
  }

  function updateCount(k) {
    var n = lines($('ll-' + k).value).length;
    $('ll-' + k + '-count').textContent = n ? n + (n === 1 ? ' option' : ' options') : 'short answer';
  }

  function script() {
    if (!template) return '';
    var json = JSON.stringify(readConfig(), null, 2);
    return template.replace('__CONFIG__', function () { return json; });
  }

  function refresh() {
    save(CONFIG_KEY, readConfig());
    $('ll-preview').value = script();
  }

  function flash(msg) {
    var el = $('ll-copy-status');
    el.textContent = msg;
    clearTimeout(flash.t);
    flash.t = setTimeout(function () { el.textContent = ''; }, 4000);
  }

  function copy() {
    var text = script();
    if (!text) { flash('Still loading — try again in a second.'); return; }
    function fallback() {
      var ta = $('ll-preview');
      ta.value = text; ta.focus(); ta.select();
      try { document.execCommand('copy'); flash('Copied. Now open Apps Script.'); }
      catch (e) { flash('Select the script in the preview below and copy it.'); }
    }
    if (!navigator.clipboard) { fallback(); return; }
    navigator.clipboard.writeText(text).then(function () { flash('Copied. Now open Apps Script.'); }, fallback);
    tick(2);
  }

  function download() {
    var text = script();
    if (!text) return;
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    a.download = 'lesson-log-setup.gs';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    tick(2);
  }

  // ── Checklist ──
  function steps() { return Array.prototype.slice.call(document.querySelectorAll('.ls-step')); }

  function renderProgress() {
    var all = steps(), done = all.filter(function (s) { return s.querySelector('.ls-done input').checked; });
    all.forEach(function (s) { s.classList.toggle('is-done', s.querySelector('.ls-done input').checked); });
    $('ls-progress-text').textContent = done.length + ' of ' + all.length + ' steps done';
    $('ls-progress-fill').style.width = (done.length / all.length * 100) + '%';
    $('ls-progress').setAttribute('aria-valuenow', done.length);
    save(STEPS_KEY, done.map(function (s) { return s.dataset.step; }));
  }

  function tick(n) {
    var box = document.querySelector('.ls-step[data-step="' + n + '"] .ls-done input');
    if (box && !box.checked) { box.checked = true; renderProgress(); }
  }

  function init() {
    fillForm(load(CONFIG_KEY) || DEFAULTS);
    LISTS.forEach(function (k) {
      $('ll-' + k).addEventListener('input', function () { updateCount(k); refresh(); });
    });
    $('ll-name').addEventListener('input', refresh);
    $('ll-reset-lists').addEventListener('click', function () { fillForm(DEFAULTS); refresh(); });
    $('ll-copy').addEventListener('click', copy);
    $('ll-download').addEventListener('click', download);
    $('ll-open-script').addEventListener('click', function () { tick(3); });

    var saved = load(STEPS_KEY) || [];
    steps().forEach(function (s) {
      var box = s.querySelector('.ls-done input');
      box.checked = saved.indexOf(s.dataset.step) !== -1;
      box.addEventListener('change', renderProgress);
    });
    $('ls-reset').addEventListener('click', function () {
      steps().forEach(function (s) { s.querySelector('.ls-done input').checked = false; });
      renderProgress();
    });
    renderProgress();

    fetch('/lesson-log/lesson-log-setup.txt?v=1')
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (t) { template = t; refresh(); })
      .catch(function () { flash('Could not load the setup script. Refresh the page to try again.'); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
