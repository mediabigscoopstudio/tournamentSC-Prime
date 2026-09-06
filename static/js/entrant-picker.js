/* Searchable entrant picker (organizer/_entrant_picker.html) — a text input
   + filtered dropdown list standing in for a plain <select>, so organizers
   can find an entrant by name and see rating/phone underneath. The actual
   submitted value stays a hidden input with the original field `name`, so
   no other JS/view code needs to know this isn't a <select> anymore. */
(function () {
  function initPicker(root) {
    if (root.dataset.epBound) return;
    root.dataset.epBound = '1';

    var hidden = root.querySelector('[data-entrant-picker-value]');
    var search = root.querySelector('[data-entrant-picker-search]');
    var list = root.querySelector('[data-entrant-picker-list]');
    var opts = Array.prototype.slice.call(root.querySelectorAll('.entrant-picker__opt'));
    if (!hidden || !search || !list) return;

    function filter() {
      var q = search.value.trim().toLowerCase();
      var any = false;
      opts.forEach(function (opt) {
        var match = !q || (opt.getAttribute('data-label') || '').toLowerCase().indexOf(q) !== -1;
        opt.hidden = !match;
        if (match) any = true;
      });
      list.hidden = !any;
    }

    search.addEventListener('focus', filter);
    search.addEventListener('input', function () {
      if (hidden.value) { // typing again invalidates the old pick
        hidden.value = '';
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
      }
      filter();
    });
    search.addEventListener('blur', function () {
      // Delay so a click on an option (which also blurs the input) still registers.
      setTimeout(function () { list.hidden = true; }, 150);
    });

    opts.forEach(function (opt) {
      opt.addEventListener('mousedown', function (e) {
        e.preventDefault();
        hidden.value = opt.getAttribute('data-value');
        search.value = opt.getAttribute('data-label');
        list.hidden = true;
        // Some pages (e.g. the knockout seed dialog's live pairing preview)
        // listen for a native 'change' on this field's name — dispatch one
        // since setting .value programmatically never fires it on its own.
        hidden.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
  }

  window.initEntrantPickers = function () {
    document.querySelectorAll('[data-entrant-picker]').forEach(initPicker);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window.initEntrantPickers);
  } else {
    window.initEntrantPickers();
  }
})();
