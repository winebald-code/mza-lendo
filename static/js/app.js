/* ===========================================================================
   Bacaan — interface behaviour
   No framework, no build step. Every handler is delegated where practical so
   markup rendered later still works.
   =========================================================================== */
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ── Data-driven styles ───────────────────────────────────────────────────
     The Content-Security-Policy has no 'unsafe-inline' in style-src, so an
     inline style="" attribute written by the server would be blocked outright.
     Values that genuinely have to be computed per row — bar widths, gauge
     sizes, Gantt column spans — are therefore emitted as data attributes and
     applied here through the CSSOM, which CSP does not govern. The policy
     stays strict and the bars still draw.
     ─────────────────────────────────────────────────────────────────────── */
  const CSS_MAP = {
    'data-css-w': 'width',
    'data-css-h': 'height',
    'data-css-minh': 'minHeight',
    'data-css-fs': 'fontSize',
    'data-css-color': 'color',
    'data-css-bg': 'background',
    'data-css-opacity': 'opacity',
    'data-css-gc': 'gridColumn',
    'data-css-gtc': 'gridTemplateColumns',
  };

  function applyDataStyles(root) {
    Object.keys(CSS_MAP).forEach((attr) => {
      $$('[' + attr + ']', root).forEach((el) => {
        el.style[CSS_MAP[attr]] = el.getAttribute(attr);
      });
    });
  }

  applyDataStyles(document);

  /* Everything below that binds to an element rather than delegating has to run
     again when the page content is replaced. Collected here so there is one
     list to keep in step rather than a hunt through the file. */
  const PAGE_INIT = [];
  function rescan(root) {
    applyDataStyles(root || document);
    PAGE_INIT.forEach((fn) => { try { fn(root || document); } catch (e) { /* keep going */ } });
    if (window.BacaanCharts) window.BacaanCharts();
  }
  window.Bacaan = window.Bacaan || {};
  window.Bacaan.rescan = rescan;
  window.mzApplyStyles = applyDataStyles;

  /* ── Content protection ───────────────────────────────────────────────
     Requested for the whole site. Worth being clear about what it is: a
     deterrent against casual copying, not a security control — view-source,
     developer tools and curl all still reach the same markup. It is here
     because it raises the effort slightly, not because it prevents anything. */
  document.addEventListener('contextmenu', function (event) { event.preventDefault(); });
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey && (e.key === 'u' || e.key === 'U' || e.key === 's' || e.key === 'S')) {
      e.preventDefault();
    }
  });

  /* ── Navigation rail (mobile) ─────────────────────────────────────────── */
  const rail = $('#rail');
  const scrim = $('#rail-scrim');

  function openRail() {
    if (!rail) return;
    rail.classList.remove('-translate-x-full');
    if (scrim) scrim.hidden = false;
    document.body.style.overflow = 'hidden';
  }
  function closeRail() {
    if (!rail) return;
    rail.classList.add('-translate-x-full');
    if (scrim) scrim.hidden = true;
    document.body.style.overflow = '';
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-rail-open]')) { openRail(); }
    if (e.target.closest('[data-rail-close]')) { closeRail(); }
  });

  /* ── Public header menu ───────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-menu-toggle]');
    if (!btn) return;
    const nav = $('#mobile-nav');
    if (!nav) return;
    const open = nav.hidden;
    nav.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
  });

  /* ── Dropdowns ────────────────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-dropdown-trigger]');
    $$('[data-dropdown]').forEach((dd) => {
      const menu = $('[data-dropdown-menu]', dd);
      const own = trigger && dd.contains(trigger);
      if (!menu) return;
      if (own) {
        const show = menu.classList.contains('hidden');
        menu.classList.toggle('hidden', !show);
        $('[data-dropdown-trigger]', dd).setAttribute('aria-expanded', String(show));
      } else if (!dd.contains(e.target)) {
        menu.classList.add('hidden');
        const t = $('[data-dropdown-trigger]', dd);
        if (t) t.setAttribute('aria-expanded', 'false');
      }
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    $$('[data-dropdown-menu]').forEach((m) => m.classList.add('hidden'));
    closeRail();
    const results = $('[data-search-results]');
    if (results) results.classList.add('hidden');
  });

  /* ── Flash messages ───────────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const close = e.target.closest('[data-flash-close]');
    if (close) close.closest('.flash').remove();
  });
  $$('.flash').forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = 'opacity 300ms, transform 300ms';
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
      setTimeout(() => el.remove(), 320);
    }, 7000 + i * 500);
  });

  /* ── Destructive action guard ─────────────────────────────────────────── */
  document.addEventListener('submit', (e) => {
    const form = e.target.closest('[data-confirm]');
    if (!form) return;
    if (!window.confirm(form.getAttribute('data-confirm'))) {
      e.preventDefault();
    }
  });

  /* ── Plant switcher ───────────────────────────────────────────────────── */
  document.addEventListener('change', (e) => {
    const sel = e.target.closest('[data-switch-select]');
    if (sel && sel.value) window.location.href = sel.value;
  });

  /* ── Global search ────────────────────────────────────────────────────── */
  const searchInput = $('[data-search-input]');
  const searchResults = $('[data-search-results]');
  let searchTimer = null;

  if (searchInput && searchResults) {
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const term = searchInput.value.trim();
      if (term.length < 2) { searchResults.classList.add('hidden'); return; }
      searchTimer = setTimeout(() => runSearch(term), 220);
    });
    searchInput.addEventListener('focus', () => {
      if (searchResults.children.length && searchInput.value.trim().length >= 2) {
        searchResults.classList.remove('hidden');
      }
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('[data-search]')) searchResults.classList.add('hidden');
    });
  }

  function runSearch(term) {
    fetch('/api/search?q=' + encodeURIComponent(term), {
      headers: { 'Accept': 'application/json' }, credentials: 'same-origin',
    })
      .then((r) => (r.ok ? r.json() : { results: [] }))
      .then((data) => {
        searchResults.textContent = '';
        if (!data.results || !data.results.length) {
          const p = document.createElement('p');
          p.className = 'px-3 py-4 text-[12.5px] text-slate-400 m-0 text-center';
          p.textContent = 'Nothing matches “' + term + '”.';
          searchResults.appendChild(p);
        } else {
          data.results.forEach((r) => {
            const a = document.createElement('a');
            a.href = r.url;
            a.className = 'flex items-center gap-3 px-3 py-2.5 rounded-[10px] hover:bg-fog no-underline';
            a.setAttribute('role', 'option');

            const type = document.createElement('span');
            type.className = 'tag tag-neutral shrink-0';
            type.textContent = r.type;

            const wrap = document.createElement('span');
            wrap.className = 'min-w-0 flex-1';
            const label = document.createElement('span');
            label.className = 'block text-[13px] font-medium text-ink truncate';
            label.textContent = r.label;
            const meta = document.createElement('span');
            meta.className = 'block font-mono text-[10.5px] text-slate-400 truncate';
            meta.textContent = r.meta || '';
            wrap.appendChild(label);
            wrap.appendChild(meta);

            a.appendChild(type);
            a.appendChild(wrap);
            searchResults.appendChild(a);
          });
        }
        searchResults.classList.remove('hidden');
      })
      .catch(() => searchResults.classList.add('hidden'));
  }

  /* ── Repeatable line items (orders, purchase orders, BOM, QC) ─────────── */
  document.addEventListener('click', (e) => {
    const add = e.target.closest('[data-row-add]');
    if (add) {
      e.preventDefault();
      const wrap = document.querySelector(add.getAttribute('data-row-add'));
      const tpl = wrap.querySelector('[data-row-template]');
      const clone = tpl.cloneNode(true);
      clone.removeAttribute('data-row-template');
      clone.hidden = false;
      clone.classList.remove('hidden');
      clone.querySelectorAll('input, select').forEach((f) => {
        f.disabled = false;
        if (f.type === 'checkbox') f.checked = false;
        else if (f.tagName === 'SELECT') f.selectedIndex = 0;
        else f.value = f.getAttribute('data-default') || '';
      });
      reindexChecks(clone, wrap.querySelectorAll('[data-row]').length);
      wrap.appendChild(clone);
      recalcTotals();
      const first = clone.querySelector('input, select');
      if (first) first.focus();
      return;
    }

    const remove = e.target.closest('[data-row-remove]');
    if (remove) {
      e.preventDefault();
      const row = remove.closest('[data-row]');
      const wrap = row.parentElement;
      if (wrap.querySelectorAll('[data-row]:not([data-row-template])').length > 1) {
        row.remove();
      } else {
        row.querySelectorAll('input').forEach((f) => { f.value = ''; });
        row.querySelectorAll('select').forEach((f) => { f.selectedIndex = 0; });
      }
      recalcTotals();
    }
  });

  function reindexChecks(row, index) {
    row.querySelectorAll('[data-check-index]').forEach((el) => {
      el.name = 'check_pass_' + index;
    });
  }

  /* ── Live line totals ─────────────────────────────────────────────────── */
  function recalcTotals() {
    $$('[data-total-scope]').forEach((scope) => {
      let sum = 0;
      $$('[data-row]', scope).forEach((row) => {
        if (row.hasAttribute('data-row-template')) return;
        const qty = parseFloat((row.querySelector('[data-qty]') || {}).value || 0) || 0;
        const price = parseFloat((row.querySelector('[data-price]') || {}).value || 0) || 0;
        const line = qty * price;
        const out = row.querySelector('[data-line-total]');
        if (out) out.textContent = fmt(line);
        sum += line;
      });
      const target = $('[data-grand-total]', scope);
      if (target) target.textContent = fmt(sum);
    });
  }

  function fmt(n) {
    return (n || 0).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-qty], [data-price]')) recalcTotals();
  });

  /* ── Auto-fill unit price when a product is chosen ────────────────────── */
  document.addEventListener('change', (e) => {
    const sel = e.target.closest('[data-product-select]');
    if (!sel) return;
    const opt = sel.options[sel.selectedIndex];
    const price = opt && opt.getAttribute('data-price');
    const row = sel.closest('[data-row]');
    const priceField = row && row.querySelector('[data-price]');
    if (price && priceField && !priceField.value) {
      priceField.value = price;
      recalcTotals();
    }
  });

  document.addEventListener('change', (e) => {
    const sel = e.target.closest('[data-material-select]');
    if (!sel) return;
    const opt = sel.options[sel.selectedIndex];
    const cost = opt && opt.getAttribute('data-cost');
    const row = sel.closest('[data-row]');
    const costField = row && row.querySelector('[data-price]');
    if (cost && costField && !costField.value) {
      costField.value = cost;
      recalcTotals();
    }
  });

  recalcTotals();

  /* ── Password visibility ──────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-reveal]');
    if (!btn) return;
    const input = document.getElementById(btn.getAttribute('data-reveal'));
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    $$('svg', btn).forEach((s) => { s.classList.toggle('hidden'); });
  });

  /* ── Password strength meter ──────────────────────────────────────────── */
  let pwField = $('[data-strength]');
  PAGE_INIT.push((root) => { pwField = $('[data-strength]', root) || pwField; });
  if (pwField) {
    const meter = $('#pw-meter');
    const rules = $$('[data-rule]');
    pwField.addEventListener('input', () => {
      const v = pwField.value;
      const checks = {
        len: v.length >= 12,
        upper: /[A-Z]/.test(v),
        lower: /[a-z]/.test(v),
        digit: /[0-9]/.test(v),
        symbol: /[^A-Za-z0-9]/.test(v),
      };
      let passed = 0;
      rules.forEach((el) => {
        const key = el.getAttribute('data-rule');
        const good = checks[key];
        if (good) passed += 1;
        el.classList.toggle('text-ok', !!good);
        el.classList.toggle('text-slate-400', !good);
        const dot = $('.dot', el);
        if (dot) {
          dot.classList.toggle('bg-ok', !!good);
          dot.classList.toggle('bg-divider', !good);
        }
      });
      if (meter) {
        $$('span', meter).forEach((seg, i) => {
          const on = i < passed;
          seg.className = 'flex-1 h-[4px] rounded-full transition-colors ' +
            (on ? (passed >= 5 ? 'bg-ok' : passed >= 3 ? 'bg-warn' : 'bg-bad') : 'bg-divider');
        });
      }
    });
  }

  /* ── Copy to clipboard ────────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy]');
    if (!btn) return;
    const text = btn.getAttribute('data-copy');
    const done = () => {
      const original = btn.getAttribute('data-label') || btn.textContent.trim();
      btn.setAttribute('data-label', original);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = original; }, 1600);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done).catch(() => {});
  });

  /* ── Tab panels ───────────────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]');
    if (!tab) return;
    e.preventDefault();
    const group = tab.closest('[data-tabs]');
    const name = tab.getAttribute('data-tab');
    $$('[data-tab]', group).forEach((t) => {
      const on = t === tab;
      // One class. Colours live in .tab / .tab.is-active so there is nothing
      // for competing utilities to win or lose against.
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', String(on));
    });
    $$('[data-panel]', group).forEach((p) => {
      p.hidden = p.getAttribute('data-panel') !== name;
    });
  });

  /* ── Notification bell ────────────────────────────────────────────────
     Polls the unread count and chimes when it rises. The sound is synthesised
     with WebAudio rather than loaded from a file: no asset to ship, nothing
     for the Content-Security-Policy to block, and no request on every page.
     Browsers refuse to start audio before a user gesture, so the context is
     created on the first interaction and the chime simply does not play until
     then — the badge still updates. */
  var bell = document.querySelector('[data-bell]');
  if (bell) {
    var badge = bell.querySelector('[data-bell-badge]');
    var live = bell.querySelector('[data-bell-live]');
    var muteBtn = document.querySelector('[data-bell-mute]');
    var known = parseInt(bell.getAttribute('data-count') || '0', 10);
    var audioCtx = null;
    var muted = false;
    try { muted = localStorage.getItem('bacaan.mute') === '1'; } catch (e) { muted = false; }

    function paintMute() {
      if (!muteBtn) return;
      muteBtn.setAttribute('aria-pressed', String(muted));
      muteBtn.title = muted ? 'Unmute notification sound' : 'Mute notification sound';
      var use = muteBtn.querySelector('use');
      if (use) use.setAttribute('href', muted ? '#i-volume-off' : '#i-volume');
    }
    paintMute();

    if (muteBtn) {
      muteBtn.addEventListener('click', function () {
        muted = !muted;
        try { localStorage.setItem('bacaan.mute', muted ? '1' : '0'); } catch (e) {}
        paintMute();
        if (!muted) chime();
      });
    }

    ['pointerdown', 'keydown'].forEach(function (evt) {
      addEventListener(evt, function start() {
        if (!audioCtx) {
          var Ctx = window.AudioContext || window.webkitAudioContext;
          if (Ctx) audioCtx = new Ctx();
        }
        removeEventListener(evt, start);
      }, { once: true });
    });

    function chime() {
      if (muted || !audioCtx) return;
      if (audioCtx.state === 'suspended') audioCtx.resume();
      // Two short notes, a fifth apart. Short and quiet: this fires on a
      // factory floor dashboard that may be open all day.
      [[880, 0], [1320, 0.11]].forEach(function (pair) {
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = pair[0];
        var t = audioCtx.currentTime + pair[1];
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(0.13, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
        osc.connect(gain); gain.connect(audioCtx.destination);
        osc.start(t); osc.stop(t + 0.22);
      });
    }

    function paint(count) {
      if (!badge) return;
      badge.textContent = count < 100 ? String(count) : '99+';
      badge.classList.toggle('hidden', count === 0);
    }

    function poll() {
      fetch('/api/alerts', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          var n = d.count || 0;
          if (n > known) {
            chime();
            var newest = (d.items && d.items[0] && d.items[0].title) || 'New notification';
            if (live) live.textContent = newest;
            bell.animate(
              [{ transform: 'rotate(0)' }, { transform: 'rotate(-12deg)' },
               { transform: 'rotate(10deg)' }, { transform: 'rotate(0)' }],
              { duration: 420, easing: 'ease-in-out' });
          }
          known = n;
          paint(n);
        })
        .catch(function () { /* offline or logged out — try again next tick */ });
    }

    setInterval(poll, 20000);
    // Coming back to the tab is the moment a missed notification matters most.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
  }

  /* ── USSD handset simulator ───────────────────────────────────────────── */
  PAGE_INIT.push((root) => {
    const sim = $('[data-ussd-sim]', root);
    if (sim && !sim.dataset.simReady) { sim.dataset.simReady = '1'; initSimulator(sim); }
  });

  function initSimulator(root) {
    const screen = $('[data-sim-screen]', root);
    const input = $('[data-sim-input]', root);
    const phoneField = $('[data-sim-phone]', root);
    const dialBtn = $('[data-sim-dial]', root);
    const sendBtn = $('[data-sim-send]', root);
    const endBtn = $('[data-sim-end]', root);
    const hint = $('[data-sim-hint]', root);
    const codeEl = $('[data-sim-code]', root);
    let sessionId = null;
    let history = [];
    let busy = false;

    function paint(text, kind) {
      screen.textContent = text;
      screen.className = 'term ' + (kind === 'end' ? 'text-signal-200' : 'text-signal-300');
      revealScreen();
    }

    // On a phone the on-screen keyboard covers the lower half of the viewport,
    // so the handset screen ends up above the fold exactly when the reply
    // lands. Bring it back into view after every exchange and when the field
    // takes focus.
    function revealScreen() {
      if (window.innerWidth >= 640) return;
      window.requestAnimationFrame(function () {
        screen.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    }
    input.addEventListener('focus', function () { setTimeout(revealScreen, 250); });
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', function () {
        if (document.activeElement === input) revealScreen();
      });
    }

    function setLive(live) {
      input.disabled = !live;
      sendBtn.disabled = !live;
      endBtn.disabled = !live;
      dialBtn.disabled = live;
      phoneField.disabled = live;
      if (hint) {
        hint.textContent = live
          ? 'Session open. Reply with a menu number, then press Send.'
          : 'Enter a registered worker number and press Dial.';
      }
    }

    function post(textValue) {
      if (busy) return;
      busy = true;
      const body = new URLSearchParams({
        sessionId: sessionId,
        serviceCode: codeEl ? codeEl.textContent.trim() : '',
        phoneNumber: phoneField.value.trim(),
        networkCode: '63902',
        text: textValue,
      });
      paint('Connecting…', 'con');
      fetch(root.getAttribute('data-sim-endpoint') || '/ussd/callback', {
        method: 'POST', body: body, credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': root.getAttribute('data-sim-csrf') || '',
        },
      })
        .then((r) => {
          if (r.status === 403) throw new Error('rejected');
          if (r.status === 429) throw new Error('too fast');
          return r.text();
        })
        .then((raw) => {
          const ended = raw.startsWith('END');
          const shown = raw.replace(/^(CON|END)\s*/, '');
          paint(shown, ended ? 'end' : 'con');
          if (ended) { sessionId = null; history = []; setLive(false); }
          input.value = '';
          input.focus();
        })
        .catch((err) => {
          // Say which failure it was. "Network error" for everything sends
          // people hunting a connection problem that is not there.
          var why = err && err.message === 'rejected'
            ? 'Session rejected. Reload the page and dial again.'
            : err && err.message === 'too fast'
            ? 'Too many sessions too quickly. Wait a moment.'
            : 'Could not reach the dashboard. Check your connection.';
          paint(why, 'end');
          sessionId = null; history = [];
          setLive(false);
        })
        .finally(() => { busy = false; });
    }

    dialBtn.addEventListener('click', () => {
      if (!phoneField.value.trim()) { phoneField.focus(); return; }
      sessionId = 'sim-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
      history = [];
      setLive(true);
      post('');
    });

    sendBtn.addEventListener('click', () => {
      const v = input.value.trim();
      if (!v) return;
      history.push(v);
      post(history.join('*'));
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); sendBtn.click(); }
    });

    endBtn.addEventListener('click', () => {
      sessionId = null; history = [];
      paint('Session ended.', 'end');
      setLive(false);
    });

    $$('[data-sim-key]', root).forEach((k) => {
      k.addEventListener('click', () => {
        if (input.disabled) return;
        input.value += k.getAttribute('data-sim-key');
        input.focus();
      });
    });

    setLive(false);
    paint('Ready.\nEnter a worker number and press Dial.', 'end');
  }

  /* ── Filter forms submit on change ────────────────────────────────────── */
  document.addEventListener('change', (e) => {
    const sel = e.target.closest('[data-autosubmit] select, select[data-autosubmit]');
    if (sel && sel.form) sel.form.submit();
  });


  /* ── In-place navigation ──────────────────────────────────────────────────
     Clicking the rail swaps the page content instead of reloading the whole
     document. The rail, the topbar and the fonts stay put, so the sidebar does
     not flash and scroll position in the rail is kept.

     Progressive enhancement throughout: these are ordinary links, and every
     branch that cannot be handled cleanly falls back to a normal navigation.
     Middle-click, modifier-click, downloads and external hosts are left alone,
     and with JavaScript off the application behaves exactly as before. */
  const shell = $('#main');
  const railEl = $('#rail');

  if (shell && railEl && window.history && window.fetch && window.DOMParser) {
    let bar = null;
    let token = 0;

    function progress(on) {
      if (on) {
        if (!bar) {
          bar = document.createElement('div');
          bar.className = 'fixed top-0 left-0 h-[2px] bg-signal-500 z-[60] transition-[width] duration-200';
          bar.style.width = '0';
          document.body.appendChild(bar);
        }
        requestAnimationFrame(() => { bar.style.width = '70%'; });
      } else if (bar) {
        bar.style.width = '100%';
        setTimeout(() => { if (bar) { bar.remove(); bar = null; } }, 220);
      }
    }

    function samePage(url) {
      return url.pathname === location.pathname && url.search === location.search;
    }

    /* Only the application shell is swappable. A link out of it — the public
       site, a file download, a different origin — is a real navigation. */
    function swappable(url) {
      if (url.origin !== location.origin) return false;
      // Exports stream a file. Fetching one only to hand it back to the browser
      // would download it twice, so they are left as plain links.
      if (/\/export(\/|$)|\.csv$/.test(url.pathname)) return false;
      // Sign-out must be a real navigation: the session it ends is the one the
      // swapped page would be rendered against.
      if (/\/(logout|signout)(\/|$)/.test(url.pathname)) return false;
      return /^\/(dashboard|admin|account)(\/|$)/.test(url.pathname);
    }

    async function go(href, push) {
      const mine = ++token;
      progress(true);
      try {
        const res = await fetch(href, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'fetch', Accept: 'text/html' },
        });
        // A sign-in redirect, an export, anything that is not our own HTML:
        // hand it back to the browser rather than guessing.
        const type = res.headers.get('content-type') || '';
        if (!res.ok || type.indexOf('text/html') === -1) { location.assign(href); return; }

        const html = await res.text();
        if (mine !== token) return;                 // a newer click has overtaken this one
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const nextMain = doc.querySelector('#main');
        const nextRail = doc.querySelector('#rail');
        if (!nextMain) { location.assign(href); return; }

        const landed = res.url || href;             // follow any redirect the server made
        if (push) history.pushState({ spa: true }, '', landed);

        shell.innerHTML = nextMain.innerHTML;
        if (nextRail) {
          // Replace the rail's inner markup only, so the element keeps its
          // open/closed state on mobile and its scroll position on desktop.
          // Keep the element itself: its open/closed state on mobile lives in
          // a class on the <aside>, and replacing the node would lose it along
          // with the rail's scroll position.
          const keepScroll = railEl.scrollTop;
          railEl.innerHTML = nextRail.innerHTML;
          railEl.scrollTop = keepScroll;
        }
        if (doc.title) document.title = doc.title;

        // Flash messages live outside main; carry any across.
        const oldFlash = $('#flash-area');
        const newFlash = doc.querySelector('#flash-area');
        if (oldFlash) oldFlash.innerHTML = newFlash ? newFlash.innerHTML : '';

        rescan(document);
        window.scrollTo({ top: 0, behavior: 'auto' });
        // Move focus to the heading so a screen reader announces the new page.
        const h1 = $('h1', shell);
        if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
      } catch (err) {
        location.assign(href);                      // offline, blocked, anything else
      } finally {
        if (mine === token) progress(false);
      }
    }

    document.addEventListener('click', (e) => {
      if (e.defaultPrevented || e.button !== 0) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest('a[href]');
      if (!a || a.hasAttribute('download') || a.hasAttribute('data-no-spa')) return;
      if (a.target && a.target !== '_self') return;

      let url;
      try { url = new URL(a.getAttribute('href'), location.href); } catch (_) { return; }
      if (url.hash && samePage(url)) return;        // an in-page anchor
      if (!swappable(url)) return;

      e.preventDefault();
      if (innerWidth <= 1024) closeRail();          // the drawer covers the content
      if (samePage(url)) return;
      go(url.href, true);
    });

    addEventListener('popstate', () => { go(location.href, false); });
  }

  /* ── Reveal on scroll (public pages only, respects reduced motion) ────── */
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const reveals = $$('[data-reveal-on-scroll]');
  if (reveals.length && !reduce && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.style.transition = 'opacity 600ms ease, transform 600ms cubic-bezier(.2,.7,.3,1)';
          entry.target.style.opacity = '1';
          entry.target.style.transform = 'none';
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px' });
    reveals.forEach((el) => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(14px)';
      io.observe(el);
    });
  } else {
    reveals.forEach((el) => { el.style.opacity = '1'; });
  }
})();
