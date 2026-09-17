/* Basketball score-page interactions only (loaded only when is_basketball).

   Every control inside #bball-game-view (scoring, fouls, clocks, quarter
   nav/length, extra time) posts through fetch() instead of a normal form
   submit, and the response HTML is used to patch the page's own DOM in
   place. This is entirely about fullscreen: browsers unconditionally exit
   fullscreen the instant a real page navigation happens, and every one of
   these actions used to be a full-page form POST. Submitting via fetch and
   patching the existing #bball-game-view element's *contents* (never
   replacing the element itself, since that element is what's actually
   in fullscreen) means the document never navigates, so an active
   fullscreen session survives every score/foul/clock update.

   The Django view is untouched — it still handles a normal POST-then-redirect
   exactly as before; fetch simply follows that redirect itself and we read
   the resulting HTML instead of letting the browser load it. */
(function () {
  const PATCH_TARGET_IDS = ['bball-game-view', 'individual-scoring', 'enter-result-panel'];

  function bballApplyPatch(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    if (!doc.getElementById('bball-game-view')) {
      // Unexpected response (session expired, error page, etc.) — fall back
      // to a real navigation rather than silently doing nothing.
      window.location.reload();
      return;
    }
    PATCH_TARGET_IDS.forEach(function (id) {
      const target = document.getElementById(id);
      const fresh = doc.getElementById(id);
      if (!target || !fresh) return;
      target.innerHTML = fresh.innerHTML;
      // innerHTML only replaces children — data-individual-scoring lives on
      // #bball-game-view *itself*, so without this copy it stays stuck at
      // whatever the very first full page load rendered, forever, no matter
      // how many times the scoring-mode dialog changes it.
      if (id === 'bball-game-view') {
        target.setAttribute('data-individual-scoring', fresh.getAttribute('data-individual-scoring'));
      }
    });
    if (window.bballInitClocks) window.bballInitClocks();
    initDashboard();
  }

  function bballSubmit(form, submitter) {
    const fd = new FormData(form);
    if (submitter && submitter.name) fd.append(submitter.name, submitter.value);
    fetch(window.location.href, { method: 'POST', body: fd, credentials: 'same-origin' })
      .then(function (resp) { return resp.text(); })
      .then(bballApplyPatch)
      .catch(function () { form.submit(); });
  }

  function initDashboard() {
    // Set once per match on the pre-match setup screen (score.html) — read
    // straight off the DOM rather than duplicated into JS state, so there is
    // nothing here to fall out of sync with the server's value.
    const bballView = document.getElementById('bball-game-view');
    const individualScoringEnabled = !bballView || bballView.getAttribute('data-individual-scoring') !== '0';

    document.querySelectorAll('#bball-game-view form').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        const confirmMsg = form.getAttribute('data-bball-confirm');
        if (confirmMsg && !confirm(confirmMsg)) return;
        bballSubmit(form, e.submitter);
      });
    });

    document.querySelectorAll('[data-bball-open-dialog]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const dialog = document.getElementById(btn.getAttribute('data-bball-open-dialog'));
        if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
      });
    });

    document.querySelectorAll('[data-bball-open]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const participantId = btn.getAttribute('data-bball-open');
        const points = btn.getAttribute('data-bball-points');
        // Individual scoring off: apply the points straight to the team,
        // same score-form as the roster-pick path below just posts with no
        // membership_id — skip the "who scored?" dialog entirely.
        if (!individualScoringEnabled) {
          const pointsInput = document.getElementById('points-input-' + participantId);
          const form = document.getElementById('score-form-' + participantId);
          if (!pointsInput || !form) return;
          pointsInput.value = points;
          bballSubmit(form);
          return;
        }
        const dialog = document.getElementById('player-dialog-' + participantId);
        if (!dialog) return;
        dialog.setAttribute('data-bball-active-points', points);
        const label = dialog.querySelector('[data-bball-points-label]');
        if (label) label.textContent = '(+' + points + ')';
        if (typeof dialog.showModal === 'function') dialog.showModal();
      });
    });

    document.querySelectorAll('.bball-player-dialog').forEach(function (dialog) {
      const participantId = dialog.getAttribute('data-bball-participant');

      dialog.querySelectorAll('[data-bball-membership]').forEach(function (rosterBtn) {
        rosterBtn.addEventListener('click', function () {
          const membershipId = rosterBtn.getAttribute('data-bball-membership');
          const points = dialog.getAttribute('data-bball-active-points');
          const membershipInput = document.getElementById('membership-input-' + participantId);
          const pointsInput = document.getElementById('points-input-' + participantId);
          const form = document.getElementById('score-form-' + participantId);
          if (!membershipInput || !pointsInput || !form) return;
          membershipInput.value = membershipId;
          pointsInput.value = points;
          bballSubmit(form);
        });
      });

      const cancelBtn = dialog.querySelector('.bball-dialog-cancel');
      if (cancelBtn) cancelBtn.addEventListener('click', function () { dialog.close(); });
    });

    document.querySelectorAll('[data-bball-open-foul]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const participantId = btn.getAttribute('data-bball-open-foul');
        // Individual scoring off: same "team foul (no player)" outcome the
        // dialog's own team-foul option already produces — post it directly.
        if (!individualScoringEnabled) {
          const form = document.getElementById('foul-form-' + participantId);
          if (!form) return;
          bballSubmit(form);
          return;
        }
        const dialog = document.getElementById('foul-dialog-' + participantId);
        if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
      });
    });

    document.querySelectorAll('[data-bball-foul-participant]').forEach(function (dialog) {
      const participantId = dialog.getAttribute('data-bball-foul-participant');

      dialog.querySelectorAll('[data-bball-foul-membership]').forEach(function (rosterBtn) {
        rosterBtn.addEventListener('click', function () {
          const membershipId = rosterBtn.getAttribute('data-bball-foul-membership');
          const membershipInput = document.getElementById('foul-membership-input-' + participantId);
          const form = document.getElementById('foul-form-' + participantId);
          if (!membershipInput || !form) return;
          membershipInput.value = membershipId || '';
          bballSubmit(form);
        });
      });
    });

    const summaryBtn = document.querySelector('[data-bball-view-summary]');
    if (summaryBtn) {
      summaryBtn.addEventListener('click', function () {
        const target = document.getElementById('individual-scoring');
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.classList.add('bball-highlight');
        setTimeout(function () { target.classList.remove('bball-highlight'); }, 1500);
      });
    }

    initIndividualScoringSwitcher();

    const endMatchBtn = document.querySelector('[data-bball-end-match]');
    if (endMatchBtn) {
      endMatchBtn.addEventListener('click', function () {
        const finalizeBtn = document.getElementById('finalize-btn');
        if (finalizeBtn) finalizeBtn.click();
      });
    }

    // Fullscreen toggle button gets replaced by every DOM patch (it lives
    // inside #bball-game-view), so its click handler is rebound here each
    // time. The fullscreen STATE itself (native fullscreenElement, or the
    // .bball-pseudo-fullscreen class) lives on #bball-game-view, which is
    // never replaced — only its innerHTML is — so that state survives
    // patches automatically.
    const fsBtn = document.getElementById('bball-fullscreen-toggle');
    const gameView = document.getElementById('bball-game-view');
    if (fsBtn && gameView) {
      fsBtn.addEventListener('click', function () {
        const active = document.fullscreenElement === gameView || gameView.classList.contains('bball-pseudo-fullscreen');
        if (active) {
          if (document.fullscreenElement === gameView) document.exitFullscreen();
          gameView.classList.remove('bball-pseudo-fullscreen');
          updateFullscreenBtn();
          return;
        }
        if (gameView.requestFullscreen) {
          gameView.requestFullscreen().then(lockLandscapeBestEffort).catch(function () {
            gameView.classList.add('bball-pseudo-fullscreen');
            updateFullscreenBtn();
          });
        } else {
          gameView.classList.add('bball-pseudo-fullscreen');
          updateFullscreenBtn();
        }
      });
      // The freshly-patched button always starts out reading "Enter
      // fullscreen" (that's what the server rendered) — sync it to reality
      // in case we're actually already in fullscreen.
      updateFullscreenBtn();
    }
  }

  // Individual-scoring panel: switches between Team A's and Team B's player
  // cards. #individual-scoring is one of PATCH_TARGET_IDS above, so its
  // *contents* (including whichever team was showing) get overwritten by
  // every score/foul AJAX patch and always come back defaulting to Team A —
  // this is what re-runs after every patch (see initDashboard's caller) to
  // rebind the now-fresh buttons/panels, and it restores the previously
  // selected team from data-active-team on the #individual-scoring element
  // itself, which — like #bball-game-view's fullscreen state — is never
  // replaced, only its innerHTML is, so that one attribute survives patches.
  function initIndividualScoringSwitcher() {
    const panel = document.getElementById('individual-scoring');
    if (!panel) return;
    const switchBtns = panel.querySelectorAll('[data-team-switch]');
    const teamPanels = panel.querySelectorAll('[data-team-panel]');
    if (!switchBtns.length) return;

    function activate(teamId) {
      switchBtns.forEach(function (btn) {
        const active = btn.getAttribute('data-team-switch') === teamId;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      teamPanels.forEach(function (p) {
        p.classList.toggle('is-active', p.getAttribute('data-team-panel') === teamId);
      });
      panel.setAttribute('data-active-team', teamId);
    }

    switchBtns.forEach(function (btn) {
      btn.addEventListener('click', function () { activate(btn.getAttribute('data-team-switch')); });
    });

    activate(panel.getAttribute('data-active-team'));
  }

  function updateFullscreenBtn() {
    const fsBtn = document.getElementById('bball-fullscreen-toggle');
    const gameView = document.getElementById('bball-game-view');
    if (!fsBtn || !gameView) return;
    const active = document.fullscreenElement === gameView || gameView.classList.contains('bball-pseudo-fullscreen');
    fsBtn.textContent = active ? '⤢' : '⛶';
    fsBtn.setAttribute('aria-label', active ? 'Exit fullscreen' : 'Enter fullscreen');
  }

  function lockLandscapeBestEffort() {
    if (screen.orientation && screen.orientation.lock) {
      screen.orientation.lock('landscape').catch(function () {});
    }
  }

  // Registered once, at the document level, rather than inside
  // initDashboard(): the fullscreen button node gets replaced on every
  // patch, but `document` itself never does, so this one listener (re-
  // querying the current button/container each time it fires) is all that's
  // needed for the lifetime of the page — no risk of piling up duplicate
  // listeners across repeated patches.
  document.addEventListener('fullscreenchange', function () {
    const gameView = document.getElementById('bball-game-view');
    if (gameView && document.fullscreenElement === gameView) lockLandscapeBestEffort();
    updateFullscreenBtn();
  });

  initDashboard();

  // One-time: right after "Start Match" redirects here with ?setup=1,
  // auto-open the scoring-mode dialog so it's acknowledged before any point
  // is recorded. Deliberately outside initDashboard() (which re-runs on
  // every AJAX patch) — this must fire only once, off the real page load,
  // and the URL is stripped immediately so a later manual refresh never
  // reopens it.
  const setupParams = new URLSearchParams(location.search);
  if (setupParams.get('setup')) {
    const scoringDialog = document.getElementById('scoring-mode-dialog');
    if (scoringDialog && typeof scoringDialog.showModal === 'function') scoringDialog.showModal();
    setupParams.delete('setup');
    const qs = setupParams.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
  }
})();
