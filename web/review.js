// Review screen: see how each block on each page was labeled, fix roles and figures, and
// watch the assembled text update. Every change is saved to labels.user.json (the highest
// label layer), and the server re-assembles doc.json straight away.
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const lsGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  if (lsGet('rsvp:theme')) document.documentElement.dataset.theme = lsGet('rsvp:theme');

  const docId = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop());
  const api = p => `/api/docs/${encodeURIComponent(docId)}${p}`;
  $('readlink').href = `/read/${encodeURIComponent(docId)}`;

  const COLOR = {body: 'body', list_item: 'body', heading: 'heading', title: 'meta', subtitle: 'meta', byline: 'meta',
    dateline: 'meta', caption: 'caption', credit: 'caption', quote: 'quote', boilerplate: 'boiler',
    figure_text: 'figtext', endnote: 'endnote'};
  const color = role => `var(--r-${COLOR[role] || 'boiler'})`;
  const KEYS = {b: 'body', h: 'heading', c: 'caption', q: 'quote', x: 'boilerplate'};
  const MORE = ['title', 'subtitle', 'byline', 'dateline', 'list_item', 'credit', 'endnote', 'figure_text'];
  const KINDS = ['photo', 'chart', 'table', 'exhibit', 'diagram', 'decorative', 'ad'];
  const PAUSE = new Set(['chart', 'table', 'exhibit', 'diagram']);

  let S = null;            // review state from the server
  let page = 1, sel = null; // sel: {type:'block'|'figure', id}
  let anchoring = null;    // figure id waiting for a paragraph click
  const undo = [];

  $('legend').innerHTML = [['body', 'body'], ['heading', 'heading'], ['title/byline', 'title'], ['caption', 'caption'],
    ['quote', 'quote'], ['figure text', 'figure_text'], ['endnote', 'endnote'], ['dropped', 'boilerplate']]
    .map(([n, r]) => `<span><i style="background:${color(r)}"></i>${n}</span>`).join('') +
    `<span><i style="border:2px dashed var(--r-fig)"></i>figure</span>`;

  const blockById = id => S.blocks.find(b => b.id === id);
  const figById = id => S.figures.find(f => f.id === id);
  const roleOf = id => S.roles[id]?.role || 'body';
  const figKind = id => S.figure_labels[id]?.kind || ({vector: 'chart', region: 'table'}[figById(id)?.origin] || 'photo');

  // ---------- page view ----------
  function renderPage() {
    const pg = S.pages[page - 1];
    $('pageimg').src = api(`/files/${pg.image}`);
    const n = S.blocks.filter(b => b.page === page).length;
    $('pageinfo').innerHTML = `Page ${page} of ${S.pages.length} · ${n} blocks${pg.ocr ? ' · <span class="flag">OCR</span>' : ''}`;
    const pct = (v, total) => (v / total * 100).toFixed(3) + '%';
    const box = (bb, extra) => `left:${pct(bb[0], pg.width)};top:${pct(bb[1], pg.height)};width:${pct(bb[2] - bb[0], pg.width)};height:${pct(bb[3] - bb[1], pg.height)};${extra || ''}`;
    let html = '';
    for (const f of S.figures.filter(f => f.page === page)) {
      const k = figKind(f.id), hidden = k === 'decorative' || k === 'ad';
      html += `<div class="fbox${hidden ? ' hidden' : ''}${sel?.type === 'figure' && sel.id === f.id ? ' sel' : ''}" data-fig="${f.id}" style="${box(f.bbox)}"><span class="tag">${esc(S.figure_labels[f.id]?.label || k)}</span></div>`;
    }
    for (const b of S.blocks.filter(b => b.page === page)) {
      const r = roleOf(b.id), c = color(r), by = S.roles[b.id]?.by;
      html += `<div class="box ${r === 'boilerplate' ? 'boilerplate' : ''}${by === 'user' ? ' user' : ''}${sel?.type === 'block' && sel.id === b.id ? ' sel' : ''}" data-block="${b.id}" style="${box(b.bbox, `border-color:${c}`)}" title="${esc(b.id + ' · ' + r)}"><span class="tag" style="background:${c}">${esc(r)}${by ? ' · ' + by : ''}</span></div>`;
    }
    $('page').querySelectorAll('.box,.fbox').forEach(e => e.remove());
    $('page').insertAdjacentHTML('beforeend', html);
  }
  $('page').addEventListener('click', e => {
    const b = e.target.closest('[data-block]'), f = e.target.closest('[data-fig]');
    if (anchoring && b) return setAnchor(anchoring, {block: b.dataset.block});
    if (b) select({type: 'block', id: b.dataset.block});
    else if (f) select({type: 'figure', id: f.dataset.fig});
  });

  function gotoPage(n) {
    page = Math.max(1, Math.min(S.pages.length, n));
    renderPage();
  }
  $('prev').onclick = () => gotoPage(page - 1);
  $('next').onclick = () => gotoPage(page + 1);

  // ---------- inspector ----------
  function select(s, scroll = false) {
    sel = s;
    if (s?.type === 'block') { const b = blockById(s.id); if (b.page !== page) gotoPage(b.page); }
    if (s?.type === 'figure') { const f = figById(s.id); if (f.page !== page) gotoPage(f.page); }
    renderPage(); renderInspector(); highlightPreview(scroll);
    if (scroll || s) {
      const el = $('page').querySelector(s?.type === 'block' ? `[data-block="${s.id}"]` : `[data-fig="${s?.id}"]`);
      if (el && scroll) el.scrollIntoView({block: 'center', behavior: 'smooth'});
    }
  }

  function renderInspector() {
    const box = $('inspector');
    if (!sel) {
      box.innerHTML = `<div class="help"><b>Click a block</b> on the page to see or change its role.
        Keys: <kbd>B</kbd> body · <kbd>H</kbd> heading (<kbd>Shift</kbd>+<kbd>H</kbd> sub-heading) · <kbd>C</kbd> caption · <kbd>Q</kbd> quote · <kbd>X</kbd> drop · <kbd>U</kbd> undo my override ·
        <kbd>↑</kbd><kbd>↓</kbd> previous/next block · <kbd>PgUp</kbd><kbd>PgDn</kbd> pages.<br>
        <b>Click a figure</b> (dashed box) to set its kind or move where the reader pauses for it. Click a paragraph on the right to find it on the page.</div>`;
      return;
    }
    if (sel.type === 'block') {
      const b = blockById(sel.id), r = S.roles[b.id] || {};
      const role = r.role || 'body';
      const figNote = r.figure ? ` → ${esc(S.figure_labels[r.figure]?.label || r.figure)}` : '';
      box.innerHTML = `<h4>${esc(b.id)} <span class="chip" style="border-color:${color(role)};color:${color(role)}">${esc(role)}${figNote}</span>
          <span class="muted">set by ${esc(r.by || 'default')}</span>
          <span class="muted">· ${b.font.size}pt${b.font.bold ? ' bold' : ''}${b.font.italic ? ' italic' : ''}${b.source === 'ocr' ? ' · OCR' : ''}${b.flags.length ? ' · ' + esc(b.flags.join(', ')) : ''}</span></h4>
        <div class="txt">${esc(b.text)}</div>
        <div class="roles">${Object.entries(KEYS).map(([k, rr]) => `<button data-role="${rr}" class="${role === rr ? 'on' : ''}">${rr === 'boilerplate' ? 'drop' : rr} <kbd>${k.toUpperCase()}</kbd></button>`).join('')}
          <select id="more"><option value="">more…</option>${MORE.map(m => `<option ${role === m ? 'selected' : ''}>${m}</option>`).join('')}</select>
          ${r.by === 'user' ? '<button data-reset>use automatic <kbd>U</kbd></button>' : ''}</div>`;
      box.querySelectorAll('[data-role]').forEach(el => el.onclick = () => setRole(b.id, el.dataset.role));
      $('more').onchange = e => e.target.value && setRole(b.id, e.target.value);
      box.querySelector('[data-reset]')?.addEventListener('click', () => resetBlock(b.id));
    } else {
      const f = figById(sel.id), L = S.figure_labels[f.id] || {}, k = figKind(f.id);
      const df = S.doc?.figures.find(x => x.id === f.id || (x.images || []).includes(f.image));
      const where = !df ? 'hidden from the reader' : df.anchor ? `after ${esc(df.anchor.block)} (${esc(df.anchor.method)})` : 'in “Remaining exhibits” at the end';
      box.innerHTML = `<h4>${esc(f.id)} <span class="chip">${esc(k)}</span><span class="muted">set by ${esc(L.by || 'default')}</span></h4>
        <img src="${api('/files/' + f.image)}" alt="">
        <div class="row">Kind <select id="kind">${KINDS.map(x => `<option ${x === k ? 'selected' : ''}>${x}</option>`).join('')}</select>
          Label <input type="text" id="flabel" value="${esc(L.label || df?.label || '')}" placeholder="e.g. Exhibit 4"></div>
        <div class="row muted">${PAUSE.has(k) ? 'Pauses the reader' : k === 'photo' ? 'Rail only (no pause)' : 'Not shown'} · ${where}</div>
        <div class="row"><button id="anch">Set where it appears <kbd>A</kbd></button><button id="toend">Move to end</button>
          ${L.anchor ? '<button id="autoanch">Automatic placement</button>' : ''}</div>`;
      $('kind').onchange = e => setFigure(f.id, {kind: e.target.value});
      $('flabel').onchange = e => setFigure(f.id, {label: e.target.value.trim() || undefined});
      $('anch').onclick = () => startAnchoring(f.id);
      $('toend').onclick = () => setAnchor(f.id, {end: true});
      $('autoanch')?.addEventListener('click', () => setAnchor(f.id, undefined));
    }
  }

  // ---------- edits (all go into the user layer) ----------
  function nearestFigure(b) {
    const cands = S.figures.filter(f => f.page === b.page && !['decorative', 'ad'].includes(figKind(f.id)));
    let best = null, bestD = Infinity;
    for (const f of cands) {
      const d = Math.min(Math.abs(b.bbox[1] - f.bbox[3]), Math.abs(f.bbox[1] - b.bbox[3]));
      if (d < bestD) { bestD = d; best = f; }
    }
    return best;
  }
  function setRole(id, role, level) {
    const u = structuredClone(S.user); u.blocks ||= {};
    const entry = {role};
    if (role === 'heading') entry.level = level || 2;
    if (role === 'caption' || role === 'credit') { const f = nearestFigure(blockById(id)); if (f) entry.figure = f.id; }
    if (role === 'figure_text' && blockById(id).in_figure) entry.figure = blockById(id).in_figure;
    u.blocks[id] = entry;
    save(u, `${id} → ${role}`);
  }
  function resetBlock(id) {
    const u = structuredClone(S.user);
    if (u.blocks) delete u.blocks[id];
    save(u, `${id} back to automatic`);
  }
  function setFigure(fid, patch) {
    const u = structuredClone(S.user); u.figures ||= {};
    const e = u.figures[fid] ||= {};
    for (const [k, v] of Object.entries(patch)) { if (v === undefined) delete e[k]; else e[k] = v; }
    if (!Object.keys(e).length) delete u.figures[fid];
    save(u, `${fid} updated`);
  }
  function startAnchoring(fid) {
    anchoring = fid; document.body.classList.add('anchoring');
    status('Click the paragraph this figure should follow (Esc to cancel)');
  }
  function stopAnchoring() { anchoring = null; document.body.classList.remove('anchoring'); }
  function setAnchor(fid, anchor) {
    stopAnchoring();
    setFigure(fid, {anchor});
  }

  async function save(user, what) {
    undo.push(structuredClone(S.user)); $('undo').disabled = false;
    await put(user, what);
  }
  async function put(user, what) {
    status('Saving…');
    const r = await fetch(api('/labels/user'), {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(user)});
    const body = await r.json();
    if (!r.ok) { status('Not saved: ' + (body.detail?.errors || [body.detail]).join('; ')); undo.pop(); $('undo').disabled = !undo.length; return; }
    S = body;
    status(body.errors?.length ? 'Saved, but: ' + body.errors.join('; ') : `Saved · ${what}`);
    renderAll();
  }
  $('undo').onclick = () => { if (undo.length) { put(undo.pop(), 'undone'); $('undo').disabled = !undo.length; } };
  function status(t) { $('status').textContent = t; }

  // ---------- assembled preview ----------
  function renderPreview() {
    const doc = S.doc;
    if (!doc) { $('preview').innerHTML = '<p class="muted">No assembled document.</p>'; return; }
    const secTitle = Object.fromEntries(doc.sections.map(s => [s.id, s.title]));
    const figsAt = {};
    for (const f of doc.figures) if (f.anchor) (figsAt[f.anchor.block] ||= []).push(f);
    const chip = f => `<span class="chip ${PAUSE.has(f.kind) ? 'pause' : ''}" data-dfig="${esc(f.id)}">▣ ${esc(f.label || f.kind)}${f.caption ? ' · ' + esc(f.caption.slice(0, 60)) : ''}</span>`;
    let html = `<div class="meta"><b>${esc(doc.title)}</b>${doc.subtitle ? '<br>' + esc(doc.subtitle) : ''}${doc.byline ? '<br>' + esc(doc.byline) : ''}${doc.dateline ? ' · ' + esc(doc.dateline) : ''}
      <br>${doc.stats.words.toLocaleString()} words · ${doc.stats.paragraphs} paragraphs · ${doc.figures.length} figures · ${esc(doc.ingest.method)}</div>`;
    let cur = 's0';
    for (const p of doc.body) {
      if (p.section !== cur) { cur = p.section; html += `<h2>${esc(secTitle[cur])}</h2>`; }
      html += `<p data-para="${esc(p.id)}" data-ext="${esc(p.src.map(s => s.ext).join(' '))}" class="${p.type === 'quote' ? 'quote' : ''}">${esc(p.text)}</p>`;
      html += (figsAt[p.id] || []).map(chip).join('');
    }
    const end = doc.figures.filter(f => !f.anchor);
    if (end.length) html += `<h2>Remaining exhibits</h2>` + end.map(chip).join('<br>');
    if (doc.endnotes?.length) html += `<p class="muted" style="cursor:default">(${doc.endnotes.length} endnote blocks not read)</p>`;
    $('preview').innerHTML = html;
  }
  $('preview').addEventListener('click', e => {
    const p = e.target.closest('[data-para]'), c = e.target.closest('[data-dfig]');
    if (anchoring && p) return setAnchor(anchoring, {block: p.dataset.para});
    if (p) select({type: 'block', id: p.dataset.ext.split(' ')[0]}, true);
    if (c) select({type: 'figure', id: c.dataset.dfig}, true);
  });
  function highlightPreview(scroll) {
    $('preview').querySelectorAll('p.hit').forEach(p => p.classList.remove('hit'));
    if (sel?.type !== 'block') return;
    const p = [...$('preview').querySelectorAll('[data-ext]')].find(p => p.dataset.ext.split(' ').includes(sel.id));
    if (p) { p.classList.add('hit'); if (!scroll) p.scrollIntoView({block: 'nearest'}); }
  }

  function renderWarnings() {
    const w = S.doc?.ingest?.warnings || [];
    $('warnings').innerHTML = w.length ? `<b>${w.length} warning${w.length > 1 ? 's' : ''}:</b> ` + w.map(esc).join(' · ') : '';
  }
  function renderAll() {
    $('doctitle').textContent = 'Review · ' + (S.doc?.title || docId);
    document.title = 'Review · ' + (S.doc?.title || docId);
    renderWarnings(); renderPage(); renderInspector(); renderPreview(); highlightPreview(false);
  }

  // ---------- keyboard ----------
  document.addEventListener('keydown', e => {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); $('undo').click(); return; }
    if (e.key === 'Escape') { stopAnchoring(); select(null); status(''); return; }
    if (e.key === 'PageDown' || (e.key === 'ArrowRight' && !sel)) { e.preventDefault(); gotoPage(page + 1); return; }
    if (e.key === 'PageUp' || (e.key === 'ArrowLeft' && !sel)) { e.preventDefault(); gotoPage(page - 1); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const list = S.blocks.filter(b => b.page === page);
      if (!list.length) return;
      const idx = sel?.type === 'block' ? list.findIndex(b => b.id === sel.id) : -1;
      const n = e.key === 'ArrowDown' ? idx + 1 : (idx < 0 ? list.length - 1 : idx - 1);
      if (n >= 0 && n < list.length) select({type: 'block', id: list[n].id}, true);
      else if (e.key === 'ArrowDown' && page < S.pages.length) { gotoPage(page + 1); const f = S.blocks.find(b => b.page === page); if (f) select({type: 'block', id: f.id}, true); }
      return;
    }
    if (sel?.type === 'block') {
      if (KEYS[k]) { e.preventDefault(); setRole(sel.id, KEYS[k], k === 'h' && e.shiftKey ? 3 : 2); }
      else if (k === 'u' && S.roles[sel.id]?.by === 'user') resetBlock(sel.id);
    } else if (sel?.type === 'figure' && k === 'a') startAnchoring(sel.id);
  });

  // ---------- load ----------
  (async () => {
    const r = await fetch(api('/review'));
    if (!r.ok) { $('doctitle').textContent = 'Reading not found'; return; }
    S = await r.json();
    renderAll();
  })();
})();
