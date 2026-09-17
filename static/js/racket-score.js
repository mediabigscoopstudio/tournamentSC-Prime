/* Badminton/pickleball score-page interactions only (loaded only when
   is_racket). Every control inside #racket-board (point +1/-1, Finish Set,
   Add Set, edit/delete a set) posts through fetch() instead of a normal
   form submit, and the response HTML patches #racket-board's own contents
   in place — same no-reload technique as static/js/basketball-score.js,
   scoped to this one panel since there's no fullscreen/clock state here to
   preserve, just a snappier tap-to-score experience than a full page
   reload per point. */
(function () {
  function applyPatch(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const fresh = doc.getElementById('racket-board');
    const target = document.getElementById('racket-board');
    if (!fresh || !target) { window.location.reload(); return; }
    target.innerHTML = fresh.innerHTML;
    // innerHTML only replaces children — data-individual-scoring lives on
    // #racket-board itself, so it needs its own copy or it stays stuck at
    // whatever the first full page load rendered (same bug class as
    // basketball-score.js's #bball-game-view, fixed there the same way).
    target.setAttribute('data-individual-scoring', fresh.getAttribute('data-individual-scoring'));
    bindAll();
  }

  function submitForm(form, submitter) {
    const fd = new FormData(form);
    if (submitter && submitter.name) fd.append(submitter.name, submitter.value);
    fetch(window.location.href, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (resp) { return resp.text(); })
      .then(applyPatch)
      .catch(function () { form.submit(); });
  }

  function bindForms() {
    const board = document.getElementById('racket-board');
    if (!board) return;
    board.querySelectorAll('form').forEach(function (form) {
      if (form.dataset.racketBound) return;
      form.dataset.racketBound = '1';
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        submitForm(form, e.submitter);
      });
    });
  }

  /* "Edit this set" pencil buttons: populate the one shared dialog with
     that row's own set_index/scores, then open it. */
  function bindEditButtons() {
    const dialog = document.getElementById('edit-set-dialog');
    if (!dialog) return;
    const indexInput = document.getElementById('edit-set-index');
    const aInput = document.getElementById('edit-set-a');
    const bInput = document.getElementById('edit-set-b');
    const title = document.getElementById('edit-set-title');

    document.querySelectorAll('[data-edit-set]').forEach(function (btn) {
      if (btn.dataset.racketEditBound) return;
      btn.dataset.racketEditBound = '1';
      btn.addEventListener('click', function () {
        const idx = btn.getAttribute('data-edit-set');
        indexInput.value = idx;
        aInput.value = btn.getAttribute('data-edit-a') || 0;
        bInput.value = btn.getAttribute('data-edit-b') || 0;
        if (title) title.textContent = 'Set ' + (parseInt(idx, 10) + 1);
        dialog.showModal();
      });
    });

    dialog.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
      if (btn.dataset.racketCloseBound) return;
      btn.dataset.racketCloseBound = '1';
      btn.addEventListener('click', function () { dialog.close(); });
    });
  }

  /* +1 buttons: team mode submits straight through (via bindForms' fetch
     interception, using requestSubmit so that submit-event interception
     actually fires — plain .submit() does not dispatch a submit event).
     Individual mode (Doubles/Mixed Doubles only) opens a 2-player roster
     dialog first; picking one fills the hidden membership_id then submits
     the same way. -1 corrections are untouched, plain submit buttons —
     never asks who to attribute a correction to. */
  function bindPointButtons() {
    const board = document.getElementById('racket-board');
    if (!board) return;
    const individualScoringEnabled = board.getAttribute('data-individual-scoring') === '1';

    document.querySelectorAll('[data-racket-open]').forEach(function (btn) {
      if (btn.dataset.racketPointBound) return;
      btn.dataset.racketPointBound = '1';
      btn.addEventListener('click', function () {
        const side = btn.getAttribute('data-racket-open');
        const form = document.getElementById('racket-point-form-' + side);
        if (!form) return;
        if (!individualScoringEnabled) { form.requestSubmit(); return; }
        const dialog = document.getElementById('racket-player-dialog-' + side);
        if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
      });
    });

    document.querySelectorAll('.racket-player-dialog').forEach(function (dialog) {
      if (dialog.dataset.racketDialogBound) return;
      dialog.dataset.racketDialogBound = '1';
      const side = dialog.getAttribute('data-racket-side');
      dialog.querySelectorAll('[data-racket-membership]').forEach(function (rosterBtn) {
        rosterBtn.addEventListener('click', function () {
          const form = document.getElementById('racket-point-form-' + side);
          const input = form && form.querySelector('[name="membership_id"]');
          if (!form || !input) return;
          input.value = rosterBtn.getAttribute('data-racket-membership');
          dialog.close();
          form.requestSubmit();
        });
      });
      dialog.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
        btn.addEventListener('click', function () { dialog.close(); });
      });
    });
  }

  /* "Scoring mode" popup: opened from the ⚙️ button (Doubles/Mixed Doubles
     only — the button doesn't render at all for Singles), submitted through
     the same fetch-patch flow as everything else in #racket-board via
     bindForms(). */
  function bindScoringModeDialog() {
    const openBtn = document.querySelector('[data-open-dialog="scoring-mode-dialog"]');
    const dialog = document.getElementById('scoring-mode-dialog');
    if (openBtn && !openBtn.dataset.racketScoringBound) {
      openBtn.dataset.racketScoringBound = '1';
      openBtn.addEventListener('click', function () {
        if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
      });
    }
    if (dialog && !dialog.dataset.racketScoringCloseBound) {
      dialog.dataset.racketScoringCloseBound = '1';
      dialog.querySelectorAll('[data-close-dialog]').forEach(function (btn) {
        btn.addEventListener('click', function () { dialog.close(); });
      });
    }
  }

  function bindAll() {
    bindForms();
    bindEditButtons();
    bindPointButtons();
    bindScoringModeDialog();
  }

  bindAll();

  // One-time: right after "Start Match" redirects here with ?setup=1,
  // auto-open the scoring-mode dialog (Doubles/Mixed Doubles only — it
  // simply doesn't exist in the DOM for Singles) so it's acknowledged
  // before any point is recorded. Outside bindAll()/applyPatch() on
  // purpose — this must fire only once, off the real page load, and the
  // URL is stripped immediately so a later manual refresh never reopens it.
  const setupParams = new URLSearchParams(location.search);
  if (setupParams.get('setup')) {
    const scoringDialog = document.getElementById('scoring-mode-dialog');
    if (scoringDialog && typeof scoringDialog.showModal === 'function') scoringDialog.showModal();
    setupParams.delete('setup');
    const qs = setupParams.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
  }
})();
