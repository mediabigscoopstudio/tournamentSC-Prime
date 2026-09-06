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

  function bindAll() {
    bindForms();
    bindEditButtons();
  }

  bindAll();
})();
