// RSVP reader: plays a doc.json word by word. Port of the original prototype's engine
// (ORP focal letter, punctuation/length/number pacing, warm-up after pauses, sentence and
// paragraph navigation, click-to-jump context), plus figures, recaps and synced progress.
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

  const docId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop());
  const api = path => `/api/docs/${encodeURIComponent(docId)}${path}`;
  const PAUSE_KINDS = new Set(['chart', 'table', 'exhibit', 'diagram']);
  const SENT_END = /[.!?…]["'”’)\]]*$/;

  // ---------- state ----------
  let doc = null, tokens = [], sections = [], paraStarts = [], wordsUpTo = [], totalWords = 0;
  let settings = {wpm: 300, autopause: true, recaps_auto: false};
  let i = 0, playing = false, timer = null, wpm = 300, warm = 0;
  let badgeFig = null, badgeTimer = null, lastRecap = null, overlay = null; // overlay: {el, fromStream}
  const seen = new Set();

  // ---------- building the token stream ----------
  // tokens: {t:'title'|'section'|'word'|'figure'|'recap', ...}
  function build() {
    tokens = []; sections = []; paraStarts = [];
    tokens.push({t: 'title', s: doc.title, sec: ''});
    const secById = Object.fromEntries(doc.sections.map(s => [s.id, s]));
    const figsAt = {}, refsAt = {};
    for (const f of doc.figures) {
      if (f.anchor) (figsAt[f.anchor.block] ||= []).push(f);
      for (const r of f.refs || []) (refsAt[r.block] ||= []).push({char: r.char, fig: f});
    }
    for (const k in figsAt) figsAt[k].sort((a, b) => a.anchor.char - b.anchor.char);
    const recapBySec = Object.fromEntries((doc.recaps || []).map(r => [r.section, r]));
    const recapStop = sid => {
      const r = recapBySec[sid];
      if (r) tokens.push({t: 'recap', recap: {...r, title: secById[sid]?.title || doc.title}, sec: secById[sid]?.title || ''});
    };

    let cur = 's0', curTitle = '';
    doc.body.forEach((p, pi) => {
      if (p.section !== cur) {
        recapStop(cur);
        cur = p.section; curTitle = secById[cur]?.title || '';
        sections.push({name: curTitle, idx: tokens.length});
        tokens.push({t: 'section', s: curTitle, sec: curTitle});
      }
      paraStarts.push(tokens.length);
      const figs = figsAt[p.id] || [], refs = refsAt[p.id] || [];
      let fi = 0, wi = 0, m;
      const re = /\S+/g;
      while ((m = re.exec(p.text))) {
        let off = m.index;
        // split long em-dash compounds so each half is readable (as in the prototype)
        for (const part of m[0].split(/(?<=—)/).filter(Boolean)) {
          const end = off + part.length;
          const tok = {t: 'word', s: part, para: pi, pid: p.id, wi: wi++, sec: curTitle};
          const r = refs.find(r => r.char >= off && r.char < end);
          if (r) tok.ref = r.fig;
          tokens.push(tok);
          off = end;
        }
        const wend = m.index + m[0].length;
        while (fi < figs.length && figs[fi].anchor.char <= wend) tokens.push({t: 'figure', fig: figs[fi++], para: pi, sec: curTitle});
      }
      while (fi < figs.length) tokens.push({t: 'figure', fig: figs[fi++], para: pi, sec: curTitle});
    });
    recapStop(cur);
    const endFigs = doc.figures.filter(f => !f.anchor);
    if (endFigs.length) {
      sections.push({name: 'Remaining exhibits', idx: tokens.length});
      tokens.push({t: 'section', s: 'Remaining exhibits', sec: 'Remaining exhibits'});
      for (const f of endFigs) tokens.push({t: 'figure', fig: f, sec: 'Remaining exhibits'});
    }
    wordsUpTo = new Array(tokens.length + 1); let n = 0;
    tokens.forEach((t, k) => { wordsUpTo[k] = n; if (t.t === 'word') n++; });
    wordsUpTo[tokens.length] = n; totalWords = n;
  }

  // ---------- timing (from the prototype) ----------
  function delayFor(tok) {
    const base = 60000 / wpm;
    if (tok.t === 'figure' || tok.t === 'recap') return 0;
    if (tok.t !== 'word') return base * 6;
    const w = tok.s;
    let m = 1;
    const len = w.replace(/[^\p{L}\p{N}]/gu, '').length;
    if (len > 8) m += (len - 8) * 0.06;
    if (SENT_END.test(w)) m += 1.3;
    else if (/[,;:]["'”’)]*$/.test(w) || /—$/.test(w)) m += 0.6;
    if (/\d/.test(w)) m += 0.4;
    return base * m;
  }
  function paraPauseAfter(k) {
    const a = tokens[k], b = tokens[k + 1];
    return a && b && a.t === 'word' && b.t === 'word' && a.para !== b.para;
  }
  const pausesFor = f => settings.autopause && PAUSE_KINDS.has(f.kind);

  // ---------- ORP ----------
  function orpIndex(word) {
    const core = word.replace(/^[^\p{L}\p{N}]+/u, '');
    const lead = word.length - core.length;
    const n = core.replace(/[^\p{L}\p{N}]+$/u, '').length;
    const k = n <= 1 ? 0 : n <= 5 ? 1 : n <= 9 ? 2 : n <= 13 ? 3 : 4;
    return Math.min(lead + k, word.length - 1);
  }

  // ---------- rendering ----------
  const wordEl = $('word'), card = $('card');
  function figName(f) { return f.label || ({chart: 'Chart', table: 'Table', exhibit: 'Exhibit', diagram: 'Diagram', photo: 'Photo'}[f.kind] || 'Figure'); }

  function render() {
    const tok = tokens[i];
    if (!tok) return;
    if (tok.t === 'word') {
      card.style.display = 'none'; wordEl.style.display = '';
      const k = orpIndex(tok.s);
      wordEl.innerHTML = `<span class="pre">${esc(tok.s.slice(0, k))}</span><span class="orp">${esc(tok.s[k])}</span><span class="post">${esc(tok.s.slice(k + 1))}</span>`;
      const orp = wordEl.querySelector('.orp'), half = orp.offsetWidth / 2;
      wordEl.querySelector('.post').style.left = (orp.offsetLeft + half) + 'px';
      wordEl.querySelector('.pre').style.right = (wordEl.offsetWidth - (orp.offsetLeft - half)) + 'px';
    } else {
      wordEl.style.display = 'none'; card.style.display = 'flex';
      card.style.fontSize = tok.t === 'title' ? '28px' : tok.t === 'section' ? '32px' : '22px';
      card.textContent = tok.t === 'figure' ? `▣ ${figName(tok.fig)}` : tok.t === 'recap' ? 'Recap' : tok.s;
    }
    $('section').textContent = tok.sec || '';
    const done = wordsUpTo[i] + (tok.t === 'word' ? 1 : 0);
    const pct = totalWords ? done / totalWords : 0;
    $('fill').style.width = (pct * 100).toFixed(2) + '%';
    const mins = (totalWords - done) / wpm;
    $('pos').textContent = `${Math.round(pct * 100)}% · word ${done} of ${totalWords}`;
    $('left').textContent = mins < 1 ? `${Math.max(0, Math.round(mins * 60))}s left` : `~${Math.ceil(mins)} min left at ${wpm} wpm`;
    let si = -1; sections.forEach((s, n) => { if (s.idx <= i) si = n; });
    const jsel = $('jump');
    if (jsel.value !== String(si + 1)) jsel.value = String(si + 1);
  }

  function setBadge(f) {
    badgeFig = f;
    $('badgebtn').textContent = `▣ ${figName(f)} ↗`;
    $('badgebtn').title = `Open ${figName(f)} (E)`;
    $('badge').style.display = 'block';
    clearTimeout(badgeTimer);
    badgeTimer = setTimeout(() => { $('badge').style.display = 'none'; }, 7000);
  }

  // ---------- playback ----------
  // Tokens that don't stop the reader are handled in passing; returns true if we must pause here.
  function arrive() {
    const tok = tokens[i];
    if (tok.t === 'word' && tok.ref) setBadge(tok.ref);
    if (tok.t === 'figure') {
      if (pausesFor(tok.fig)) return true;
      setBadge(tok.fig);
    }
    if (tok.t === 'recap') {
      lastRecap = tok.recap;
      if (settings.recaps_auto) return true;
      $('hint').innerHTML = 'Recap available · press <kbd>C</kbd>';
    }
    return false;
  }

  function step() {
    if (!playing) return;
    if (i >= tokens.length - 1) { pause(); $('hint').innerHTML = 'Done. Press <kbd>R</kbd> to restart.'; return; }
    let d = delayFor(tokens[i]);
    if (paraPauseAfter(i)) d += 60000 / wpm * 1.5;
    if (warm > 0) { d *= 1 + warm * 0.12; warm--; } // ease back in after a pause
    timer = setTimeout(() => {
      i++;
      // Pass over figure/recap tokens that don't stop the reader without flashing them.
      let stop = false;
      for (;;) {
        if (!transparent(tokens[i])) { arrive(); break; }
        if (arrive()) { stop = true; break; }
        if (i >= tokens.length - 1) break;
        i++;
      }
      render(); save();
      if (stop) {
        pause();
        if (tokens[i].t === 'figure') openFigure(tokens[i].fig, true);
        else openRecap(tokens[i].recap, true);
        return;
      }
      step();
    }, d);
  }
  function play() {
    if (playing) return;
    closeOverlay();
    if (i >= tokens.length - 1) i = 0;
    playing = true; warm = 5;
    $('play').textContent = 'Pause'; $('hint').textContent = '';
    $('context').style.display = 'none';
    render(); step();
  }
  function pause() {
    playing = false; clearTimeout(timer);
    $('play').textContent = 'Play';
    $('hint').innerHTML = 'Paused · <kbd>space</kbd> to resume';
    showContext(); saveNow();
  }
  const toggle = () => playing ? pause() : play();

  // ---------- navigation ----------
  const transparent = t => t.t === 'figure' || t.t === 'recap';
  function skipFwd(j) { while (j < tokens.length - 1 && transparent(tokens[j])) j++; return j; }
  function sentenceStart(from) {
    let j = from;
    while (j > 0) {
      const prev = tokens[j - 1];
      if (transparent(prev)) { j--; continue; }
      if (prev.t !== 'word' || SENT_END.test(prev.s) || prev.para !== tokens[j].para) break;
      j--;
    }
    return skipFwd(j);
  }
  function back() {
    let j = sentenceStart(i);
    if (j === i || wordsUpTo[i] - wordsUpTo[j] < 3) j = sentenceStart(Math.max(0, j - 1));
    seek(j);
  }
  function fwd() {
    let j = i;
    while (j < tokens.length - 1) {
      const t = tokens[j]; j++;
      if (t.t === 'word' ? SENT_END.test(t.s) : !transparent(t)) break;
    }
    seek(skipFwd(j));
  }
  function currentPara() {
    for (let j = i; j >= 0; j--) if (tokens[j].para != null) return tokens[j].para;
    return -1;
  }
  function paraBack() {
    const p = currentPara();
    if (p < 0) return seek(0);
    const start = paraStarts[p];
    seek(wordsUpTo[i] - wordsUpTo[start] < 3 && p > 0 ? paraStarts[p - 1] : start);
  }
  function paraFwd() {
    const p = currentPara();
    seek(p + 1 < paraStarts.length ? paraStarts[p + 1] : tokens.length - 1);
  }
  function seek(j) {
    i = Math.max(0, Math.min(tokens.length - 1, j));
    warm = 5; render(); save();
    if (!playing) showContext();
  }

  function showContext() {
    const box = $('context');
    let p = null;
    for (let j = i; j < tokens.length; j++) if (tokens[j].para != null) { p = tokens[j].para; break; }
    if (p == null) { box.style.display = 'none'; return; }
    const para = doc.body[p];
    const sec = doc.sections.find(s => s.id === para.section);
    let html = `<div class="lbl">${esc(sec?.title || 'Context')} · paragraph ${p + 1} of ${doc.body.length}</div>`;
    for (let j = paraStarts[p]; j < tokens.length && tokens[j].para === p; j++) {
      const t = tokens[j];
      if (t.t === 'figure') { html += `<button class="fig" data-fig="${esc(t.fig.id)}">▣ ${esc(figName(t.fig))}</button> `; continue; }
      if (t.t !== 'word') break;
      html += `<span class="w${j === i ? ' cur' : ''}" data-j="${j}">${esc(t.s)}</span>${/—$/.test(t.s) ? '' : ' '}`;
    }
    box.className = para.type === 'quote' ? 'quote' : '';
    box.innerHTML = html; box.style.display = 'block';
  }
  $('context').addEventListener('click', e => {
    const j = e.target.dataset?.j, fid = e.target.dataset?.fig;
    if (j != null) seek(+j);
    if (fid) openFigure(doc.figures.find(f => f.id === fid), false);
  });

  // ---------- overlays ----------
  function openFigure(f, fromStream) {
    if (!f) return;
    if (playing) pause();
    seen.add(f.id); markRail();
    $('fkind').textContent = f.kind + (f.placement === 'end' ? ' · not cited in the text' : '');
    $('ftitle').textContent = [f.label, f.title].filter(Boolean).join(': ') || figName(f);
    $('fimgs').className = 'imgs' + (f.images.length > 1 ? ' many' : '');
    $('fimgs').innerHTML = f.images.map(src => `<img src="${api('/files/' + src)}" alt="${esc(f.alt_text || f.caption || '')}">`).join('');
    $('fcap').textContent = f.caption || (f.alt_text && !f.caption ? f.alt_text : '');
    $('fcredit').textContent = f.credit || '';
    $('ffoot').innerHTML = fromStream ? '<kbd>space</kbd> to continue reading' : '<kbd>Esc</kbd> to close · <kbd>space</kbd> to resume reading';
    showOverlay($('figure'), fromStream);
  }
  function openRecap(r, fromStream) {
    if (!r) return;
    if (playing) pause();
    $('rtitle').textContent = r.title || '';
    $('rsummary').textContent = r.summary || '';
    $('rq').textContent = r.check?.q ? `Check: ${r.check.q}` : '';
    $('ra').textContent = r.check?.a || ''; $('ra').hidden = true;
    $('rreveal').style.display = r.check?.a ? '' : 'none';
    showOverlay($('recap'), fromStream);
  }
  $('rreveal').onclick = () => { $('ra').hidden = false; };
  function showOverlay(el, fromStream) {
    closeOverlay();
    el.classList.add('open');
    overlay = {el, fromStream};
  }
  function closeOverlay() {
    if (!overlay) return;
    overlay.el.classList.remove('open');
    overlay = null;
  }
  document.querySelectorAll('.overlay').forEach(el => el.addEventListener('click', e => {
    if (e.target === el || e.target.hasAttribute('data-close')) closeOverlay();
  }));
  $('badgebtn').onclick = e => { e.stopPropagation(); openFigure(badgeFig, false); };

  // ---------- rail ----------
  function buildRail() {
    $('rail').innerHTML = doc.figures.map(f => `<button data-fig="${esc(f.id)}" title="${esc([f.label, f.title, f.caption].filter(Boolean).join(' · '))}">
        <img src="${api('/files/' + f.images[0])}" alt="" loading="lazy"><span>${esc(figName(f))}</span></button>`).join('');
    $('rail').onclick = e => {
      const b = e.target.closest('button[data-fig]');
      if (b) openFigure(doc.figures.find(f => f.id === b.dataset.fig), false);
    };
    // ticks on the progress bar where the reader will pause
    $('ticks').innerHTML = tokens.map((t, k) => t.t === 'figure' && PAUSE_KINDS.has(t.fig.kind)
      ? `<span style="left:${(wordsUpTo[k] / Math.max(1, totalWords) * 100).toFixed(2)}%" title="${esc(figName(t.fig))}"></span>` : '').join('');
  }
  function markRail() {
    document.querySelectorAll('#rail button').forEach(b => b.classList.toggle('seen', seen.has(b.dataset.fig)));
  }

  // ---------- progress & settings (saved to the library folder via the server) ----------
  let saveTimer = null, lastSaved = '';
  function progressNow() {
    let j = i;
    while (j < tokens.length && tokens[j].t !== 'word') j++;
    if (j >= tokens.length) { j = i; while (j > 0 && tokens[j].t !== 'word') j--; }
    const t = tokens[j];
    return t && t.t === 'word' ? {block: t.pid, word: t.wi, updated_at: new Date().toISOString()} : null;
  }
  function save() { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); }
  function saveNow() {
    clearTimeout(saveTimer);
    const p = progressNow();
    if (!p) return;
    const key = p.block + ':' + p.word;
    if (key === lastSaved) return;
    lastSaved = key;
    lsSet('rsvp:progress:' + docId, JSON.stringify(p));
    fetch(api('/progress'), {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(p), keepalive: true}).catch(() => {});
  }
  addEventListener('pagehide', saveNow);
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveNow(); });

  function indexFor(p) {
    if (!p || !p.block) return 0;
    let fallback = -1;
    for (let k = 0; k < tokens.length; k++) {
      const t = tokens[k];
      if (t.pid === p.block) { if (fallback < 0) fallback = k; if (t.wi === p.word) return k; }
    }
    return Math.max(0, fallback);
  }

  let wpmTimer = null;
  function setWpm(v, persist = true) {
    wpm = Math.max(150, Math.min(900, v));
    $('wpm').value = wpm; $('wpmval').textContent = wpm + ' wpm';
    if (tokens.length) render();
    if (persist) {
      clearTimeout(wpmTimer);
      wpmTimer = setTimeout(() => putSettings({wpm}), 800);
    }
  }
  function putSettings(patch) {
    Object.assign(settings, patch);
    lsSet('rsvp:settings', JSON.stringify(settings));
    fetch('/api/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(patch)}).catch(() => {});
  }

  // ---------- wiring ----------
  $('play').onclick = toggle;
  $('stage').onclick = toggle;
  $('back').onclick = back; $('fwd').onclick = fwd;
  $('pback').onclick = paraBack; $('pfwd').onclick = paraFwd;
  $('wpm').oninput = e => setWpm(+e.target.value);
  $('jump').onchange = e => { const n = +e.target.value; seek(n ? skipFwd(sections[n - 1].idx) : 0); };
  $('barline').onclick = e => {
    const r = e.currentTarget.getBoundingClientRect();
    const target = Math.round((e.clientX - r.left) / r.width * totalWords);
    let k = 0; while (k < tokens.length - 1 && wordsUpTo[k] < target) k++;
    seek(k);
  };
  const applyTheme = t => { if (t) document.documentElement.dataset.theme = t; };
  applyTheme(lsGet('rsvp:theme'));
  $('theme').onclick = () => {
    const dark = document.documentElement.dataset.theme === 'dark' ||
      (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches);
    const t = dark ? 'light' : 'dark'; applyTheme(t); lsSet('rsvp:theme', t); render();
  };
  $('gear').onclick = () => {
    if (playing) pause();
    $('s-autopause').checked = settings.autopause;
    $('s-recaps').checked = settings.recaps_auto;
    showOverlay($('settings'), false);
  };
  $('s-autopause').onchange = e => { putSettings({autopause: e.target.checked}); buildRail(); };
  $('s-recaps').onchange = e => putSettings({recaps_auto: e.target.checked});

  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT' && e.target.type !== 'range') return;
    if (overlay) {
      if (e.key === 'Escape') { e.preventDefault(); closeOverlay(); }
      else if (e.code === 'Space' && overlay.el.id !== 'settings') { e.preventDefault(); closeOverlay(); play(); }
      return;
    }
    if (e.code === 'Space') { e.preventDefault(); toggle(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); e.shiftKey ? paraBack() : back(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); e.shiftKey ? paraFwd() : fwd(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setWpm(wpm + 25); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setWpm(wpm - 25); }
    else if (e.key === 'r' || e.key === 'R') { seek(0); }
    else if (e.key === 'e' || e.key === 'E') { if (badgeFig) openFigure(badgeFig, false); }
    else if (e.key === 'c' || e.key === 'C') { if (lastRecap) openRecap(lastRecap, false); }
  });
  addEventListener('resize', () => tokens.length && render());

  // ---------- load ----------
  async function load() {
    try {
      const cached = JSON.parse(lsGet('rsvp:settings') || 'null');
      if (cached) Object.assign(settings, cached);
      Object.assign(settings, await (await fetch('/api/settings')).json());
    } catch (e) {}
    setWpm(settings.wpm || 300, false);
    const r = await fetch(api(''));
    if (!r.ok) { $('doctitle').textContent = 'Reading not found'; return; }
    doc = await r.json();
    build();
    $('doctitle').textContent = doc.title;
    document.title = doc.title + ' · RSVP';
    $('jump').innerHTML = '<option value="0">Start</option>' + sections.map((s, n) => `<option value="${n + 1}">${esc(s.name)}</option>`).join('');
    $('jump').style.display = sections.length ? '' : 'none';
    buildRail();

    let prog = null;
    try { prog = await (await fetch(api('/progress'))).json(); } catch (e) {}
    const local = JSON.parse(lsGet('rsvp:progress:' + docId) || 'null');
    if (local && (!prog?.updated_at || local.updated_at > prog.updated_at)) prog = local;
    i = indexFor(prog);
    lastSaved = prog?.block ? prog.block + ':' + prog.word : '';
    render();
    if (i > 0) { $('hint').innerHTML = 'Picked up where you left off · <kbd>space</kbd> to resume'; showContext(); }
  }
  load();
})();
