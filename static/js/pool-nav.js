/* Pool navigation: a floating rail, shared by the fixtures-by-pool and
   standings-by-pool groupings (template/public/_pool_nav.html). Auto-inits
   over every [data-poolnav] instance on the page — there can be more than
   one (organizer/fixtures.html has both a fixtures nav and a standings nav
   on the same continuously-scrolling page), so every bit of state
   (scroll-spy, rail visibility, collapsed/expanded) is scoped per instance,
   never global. */
(function () {
  if (!('IntersectionObserver' in window)) return;

  function stackTop() {
    var h = 0;
    var nav = document.querySelector('.nav');
    if (nav) h += nav.getBoundingClientRect().height;
    var ticker = document.querySelector('.ticker');
    if (ticker) h += ticker.getBoundingClientRect().height;
    return h;
  }

  document.querySelectorAll('[data-poolnav]').forEach(function (root) {
    var rail = root.querySelector('.poolnav__rail');
    var tabs = root.querySelectorAll('[data-poolnav-target]');
    if (!tabs.length) return;

    // ---- resolve + de-dupe targets, wire click-to-scroll ----
    var targets = [];
    var seen = {};
    tabs.forEach(function (t) {
      var id = t.getAttribute('data-poolnav-target');
      var el = document.getElementById(id);
      if (el && !seen[id]) { seen[id] = true; targets.push(el); }
      t.addEventListener('click', function () {
        if (!el) return;
        var y = el.getBoundingClientRect().top + window.pageYOffset - stackTop() - 12;
        window.scrollTo({ top: y, behavior: 'smooth' });
      });
    });
    if (!targets.length) return;

    // ---- scroll-spy: same technique as .tcrail (tournament_form.html) ----
    function setActive(id) {
      tabs.forEach(function (t) {
        t.classList.toggle('is-on', t.getAttribute('data-poolnav-target') === id);
      });
    }
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) setActive(en.target.id); });
    }, { rootMargin: '-45% 0px -50% 0px' });
    targets.forEach(function (el) { spy.observe(el); });

    // ---- per-instance rail visibility + collapse toggle ----
    if (!rail) return;
    var scopeName = root.getAttribute('data-poolnav-scope');
    var scopeEl = scopeName && document.querySelector('[data-poolnav-scope-for="' + scopeName + '"]');
    if (scopeEl) {
      // A single continuous element spanning this instance's whole section
      // range (e.g. .fxsecs or .poolgrid). Using a narrow centred band (same
      // shape as the spy above) rather than "any part visible" means: (a) no
      // flicker between pools inside one instance (the wrapper spans all of
      // them), and (b) on a page where two instances' wrappers could both be
      // partly on-screen at once (organizer/fixtures.html), only the one
      // whose wrapper currently crosses the viewport's centre wins — so the
      // two rails never show at the same time.
      var vis = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { rail.classList.toggle('is-near', en.isIntersecting); });
      }, { rootMargin: '-40% 0px -40% 0px' });
      vis.observe(scopeEl);
    }

    var toggle = rail.querySelector('[data-poolnav-toggle]');
    if (toggle) {
      toggle.addEventListener('click', function () {
        var collapsed = rail.classList.toggle('is-collapsed');
        toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      });
    }
  });
})();
