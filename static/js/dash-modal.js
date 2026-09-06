/* Create/edit popup for the admin console. Every "+ New X" button and row
   Edit icon opens dash/_resource_form_inner.html in this modal instead of
   navigating to its own page — the anchor's real href is still a working
   full-page form, so if this script fails to load the link just navigates
   there instead (progressive enhancement, not the only way in).
   A successful save can't patch every resource's very differently-shaped
   table in place, so it takes the simplest reliable route: stash a
   confirmation message and reload the page that opened the popup. On the
   next load this same file turns that stashed message into a toast plus a
   short best-effort chime. */
(function () {
  var backdrop = document.getElementById('dash-modal');
  var content = document.getElementById('dash-modal-content');
  var toast = document.getElementById('dash-toast');
  var toastMsg = document.getElementById('dash-toast-msg');
  if (!backdrop || !content) return;

  var lastFocused = null;

  function openModal() {
    lastFocused = document.activeElement;
    backdrop.hidden = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(function () {
      backdrop.classList.add('open');
      var box = backdrop.querySelector('.modal-box');
      if (box) box.focus();
    });
  }

  function closeModal() {
    backdrop.classList.remove('open');
    document.body.style.overflow = '';
    window.setTimeout(function () {
      backdrop.hidden = true;
      content.innerHTML = '';
    }, 220);
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function focusFirstField() {
    var el = content.querySelector(
      'input:not([type=hidden]):not([disabled]), select, textarea');
    if (el) el.focus();
  }

  function loadInto(url) {
    content.innerHTML = '<div class="empty">Loading…</div>';
    openModal();
    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('load failed');
        return resp.text();
      })
      .then(function (html) {
        content.innerHTML = html;
        focusFirstField();
      })
      .catch(function () {
        // Permission denied, resource missing, network hiccup — fall back
        // to the real page rather than leaving a broken empty popup open.
        closeModal();
        window.location.href = url;
      });
  }

  function submitForm(form) {
    var submitBtn = form.querySelector('button[type=submit]');
    if (submitBtn) submitBtn.disabled = true;
    var fd = new FormData(form);
    fetch(form.action, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then(function (resp) {
        if (resp.ok) {
          return resp.json().then(function (data) {
            try { sessionStorage.setItem('dashToast', data.message || 'Saved.'); } catch (e) { /* ignore */ }
            window.location.reload();
          });
        }
        if (resp.status === 422) {
          // Validation failed — the server re-rendered the same form with
          // errors baked in; swap it in and let the admin fix it up
          // without losing the popup or re-typing anything that was valid.
          return resp.text().then(function (html) {
            content.innerHTML = html;
            focusFirstField();
            var box = backdrop.querySelector('.modal-box');
            if (box) box.scrollTop = 0;
          });
        }
        throw new Error('save failed');
      })
      .catch(function () {
        if (submitBtn) submitBtn.disabled = false;
        window.alert('Something went wrong saving that — please try again.');
      });
  }

  document.addEventListener('click', function (e) {
    var trigger = e.target.closest('[data-modal-trigger]');
    if (trigger) {
      e.preventDefault();
      loadInto(trigger.getAttribute('href'));
      return;
    }
    var closer = e.target.closest('[data-modal-close]');
    if (closer) {
      if (backdrop.hidden) return; // the same href also works as a plain link (dform-page fallback)
      e.preventDefault();
      closeModal();
      return;
    }
    if (e.target === backdrop) closeModal();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !backdrop.hidden) closeModal();
  });

  document.addEventListener('submit', function (e) {
    if (e.target && e.target.matches && e.target.matches('[data-modal-form]')) {
      e.preventDefault();
      submitForm(e.target);
    }
  });

  // --- Save confirmation: a quiet toast plus a short two-note chime.
  // Sound is best-effort only — browsers can block audio that isn't tied
  // to a fresh user gesture (this fires right after a page reload), so a
  // failure here is silently swallowed and the toast alone still confirms
  // the save.
  function playChime() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      var ctx = new Ctx();
      var now = ctx.currentTime;
      [660, 880].forEach(function (freq, i) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var t = now + i * 0.09;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.linearRampToValueAtTime(0.16, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
        osc.connect(gain).connect(ctx.destination);
        osc.start(t);
        osc.stop(t + 0.24);
      });
      window.setTimeout(function () { ctx.close(); }, 500);
    } catch (e) { /* Web Audio unavailable or blocked — animation alone is enough */ }
  }

  function showToast(message) {
    if (!toast || !toastMsg) return;
    toastMsg.textContent = message;
    toast.hidden = false;
    requestAnimationFrame(function () { toast.classList.add('show'); });
    playChime();
    window.setTimeout(function () {
      toast.classList.remove('show');
      window.setTimeout(function () { toast.hidden = true; }, 260);
    }, 2600);
  }

  try {
    var pending = sessionStorage.getItem('dashToast');
    if (pending) {
      sessionStorage.removeItem('dashToast');
      showToast(pending);
    }
  } catch (e) { /* sessionStorage unavailable (private mode, etc.) — skip the toast */ }
})();
