/* Live-score real-time engine. Works via SSE stream events + silent polling fallback.
   Polls only while a match is LIVE or SCHEDULED, stops on completion.
   Flashes changed score numbers green for ~450ms without dimming or refreshing
   the scoreboard container, keeping clocks, win probability, and player points in sync. */
(function () {
  function initIndividualScoringSwitcher() {
    const panel = document.getElementById('individual-scoring');
    if (!panel) return;
    const switchBtns = panel.querySelectorAll('[data-team-switch]');
    const teamPanels = panel.querySelectorAll('[data-team-panel]');
    if (!switchBtns.length) return;

    function activate(teamId) {
      if (!teamId) return;
      switchBtns.forEach(function (btn) {
        const active = btn.getAttribute('data-team-switch') === teamId;
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        if (active) {
          btn.style.background = '#F26A1B';
          btn.style.color = '#ffffff';
        } else {
          btn.style.background = 'transparent';
          btn.style.color = '#64748b';
        }
      });
      teamPanels.forEach(function (p) {
        const active = p.getAttribute('data-team-panel') === teamId;
        p.classList.toggle('is-active', active);
        p.style.display = active ? 'block' : 'none';
      });
      panel.setAttribute('data-active-team', teamId);
    }

    switchBtns.forEach(function (btn) {
      btn.addEventListener('click', function () { activate(btn.getAttribute('data-team-switch')); });
    });

    const activeTeam = panel.getAttribute('data-active-team') || switchBtns[0].getAttribute('data-team-switch');
    activate(activeTeam);
  }
  initIndividualScoringSwitcher();

  const board = document.querySelector('[data-live-fixture]');
  if (!board) return;
  const fixtureId = board.getAttribute('data-live-fixture');

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const endpoint = '/api/fixtures/' + fixtureId + '/live';
  const POLL_MS = 3000;
  let timer = null;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function setText(el, value) {
    if (!el || value === null || value === undefined) return;
    const text = String(value);
    if (el.textContent.trim() === text) return;
    el.textContent = text;
    if (!prefersReduced) {
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 450);
    }
  }

  function updateProbability(wp) {
    const bar = document.querySelector('[data-prob-bar]');
    const wrap = document.querySelector('[data-prob-wrap]');
    if (!bar || !wrap) return;
    if (wp === null || wp === undefined) { wrap.hidden = true; return; }
    wrap.hidden = false;
    bar.style.width = wp + '%';
    setText(document.querySelector('[data-prob-a]'), wp);
    setText(document.querySelector('[data-prob-b]'), 100 - wp);
  }

  function stop() {
    if (timer) clearInterval(timer);
    const badge = document.querySelector('[data-live-badge]');
    if (badge) badge.outerHTML = '<span class="badge badge-completed">Final</span>';
    board.classList.remove('is-live');
  }

  function updateClock(status, clock) {
    if (!clock) return;

    const clockEl = document.querySelector('[data-quarter-clock]');
    if (clockEl) {
      clockEl.setAttribute('data-clock-status', status);
      clockEl.setAttribute('data-started-at', clock.started_at || '');
      clockEl.setAttribute('data-extra-seconds', clock.extra_seconds);
      clockEl.setAttribute('data-quarter-length-seconds', clock.quarter_length_seconds);
      clockEl.setAttribute('data-paused', clock.paused ? '1' : '0');
      clockEl.setAttribute('data-paused-remaining-seconds',
        clock.paused_remaining_seconds !== null && clock.paused_remaining_seconds !== undefined
          ? clock.paused_remaining_seconds : '');
    }

    const pausedBadge = document.querySelector('[data-clock-paused-badge]');
    if (pausedBadge) pausedBadge.hidden = !clock.paused;

    const periodPill = document.querySelector('[data-period-pill]');
    if (periodPill && clock.period_display) periodPill.textContent = clock.period_display;

    const shotEl = document.querySelector('[data-shot-clock]');
    if (shotEl && clock.shot_duration_seconds !== undefined) {
      shotEl.setAttribute('data-running', clock.shot_running ? '1' : '0');
      shotEl.setAttribute('data-started-at', clock.shot_started_at || '');
      shotEl.setAttribute('data-remaining-seconds', clock.shot_remaining_seconds);
      shotEl.setAttribute('data-duration-seconds', clock.shot_duration_seconds);
    }

    if (window.bballInitClocks) window.bballInitClocks();
  }

  function updateIndividualScoring(html) {
    if (html === undefined || html === null) return;
    const panel = document.getElementById('individual-scoring');
    if (panel && html.trim()) {
      panel.style.display = '';
    }
    const body = document.getElementById('individual-scoring-body');
    if (!body) return;
    if (body.innerHTML.trim() === html.trim()) return;
    body.innerHTML = html;
    initIndividualScoringSwitcher();
  }

  // Globally accessible real-time dispatcher (called by SSE and polling)
  window.applyFixtureLiveData = function (data) {
    if (!data) return;

    if (data.participants && Array.isArray(data.participants)) {
      data.participants.forEach((p) => {
        setText(document.querySelector('[data-score="' + p.id + '"]'),
                p.score !== null ? p.score : (p.time || '—'));
        const rank = document.querySelector('[data-rank="' + p.id + '"]');
        if (rank && p.rank) setText(rank, p.rank);
      });
    }

    if (data.win_probability !== undefined) {
      updateProbability(data.win_probability);
    }
    if (data.status) {
      updateClock(data.status, data.clock);
    }
    if (data.individual_scoring_html !== undefined) {
      updateIndividualScoring(data.individual_scoring_html);
    }

    const feed = document.querySelector('[data-feed]');
    if (feed && data.events) {
      feed.innerHTML = data.events.length
        ? data.events.map((e) =>
            '<li><span class="t">' + escapeHtml(e.at) + '</span><span>' +
            escapeHtml(e.text) + '</span></li>').join('')
        : '<li class="dim">No commentary yet.</li>';
    }

    if (data.status === 'LIVE') {
      board.classList.add('is-live');
      board.setAttribute('data-live-status', 'LIVE');
      let badge = document.querySelector('[data-live-badge]');
      if (!badge) {
        const top = document.querySelector('.scoreboard-top');
        if (top) {
          const oldBadge = top.querySelector('.badge');
          if (oldBadge) oldBadge.remove();
          badge = document.createElement('span');
          badge.className = 'live';
          badge.setAttribute('data-live-badge', '');
          badge.innerHTML = '<span class="live__dot"></span> Live';
          top.prepend(badge);
        }
      }
    } else if (data.status === 'COMPLETED') {
      stop();
    }
  };

  async function poll() {
    try {
      const res = await fetch(endpoint, { headers: { 'X-Requested-With': 'fetch' }, cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      window.applyFixtureLiveData(data);
    } catch (e) {
      /* silent retry */
    }
  }

  // Poll only if match is not completed
  if (board.getAttribute('data-live-status') !== 'COMPLETED') {
    timer = setInterval(poll, POLL_MS);
    poll();
  }
})();
