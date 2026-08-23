/* Adding one set, three ways in.
 *
 * By name is the door and the other two are options, which is the whole point
 * of the screen: a barcode is only quicker when the box is already in your
 * hand, and the camera is only quicker when there are twenty of them. */

/* Wrapped so its top-level names cannot collide with app.js, which is loaded
 * first and shares this global scope. */
(() => {
const results = document.querySelector('#set-results');
const noMatch = document.querySelector('#no-match');
const field = document.querySelector('#set-q');
const codeField = document.querySelector('#set-code');
const codeNote = document.querySelector('#code-note');

async function send(url, method = 'POST', body = null) {
  const res = await fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status}`);
  return data;
}

/* ── By name ──────────────────────────────────────────── */

function render(rows, query) {
  results.innerHTML = '';
  noMatch.classList.toggle('hidden', rows.length > 0 || query.length < 2);
  if (!rows.length) {
    document.querySelector('#typed').textContent = query;
    // Carry the typed name across so the contents form opens with it filled in
    // rather than making Clay type it a second time.
    const define = document.querySelector('#define-it');
    if (define) define.href = `/templates?name=${encodeURIComponent(query)}`;
    return;
  }
  rows.forEach((t) => {
    const li = document.createElement('li');
    li.className = `cat-row${t.owned_count ? ' owned' : ''}`;
    li.dataset.template = t.id;
    const bits = [`${t.model_count} model${t.model_count === 1 ? '' : 's'}`];
    if (t.contents.length) bits.push(t.contents.join(', '));
    li.innerHTML = `
      <div class="cat-head">
        <div><b></b> <span class="muted"></span></div>
        <div class="cat-state"></div>
      </div>
      <p class="muted"></p>
      <div class="actions"><button class="go set-own">I have this →</button></div>`;
    // textContent rather than interpolation: a set name is data, and the only
    // reason this markup is built here is that the list changes as you type.
    li.querySelector('b').textContent = t.name;
    li.querySelector('.cat-head .muted').textContent =
      [t.year, t.faction_name].filter(Boolean).join(' · ');
    li.querySelector('p.muted').textContent = bits.join(' · ');
    if (t.owned_count) {
      const badge = document.createElement('span');
      badge.className = 'ok-badge';
      badge.textContent = t.owned_count > 1 ? `Own ×${t.owned_count}` : 'Own';
      li.querySelector('.cat-state').appendChild(badge);
    }
    results.appendChild(li);
  });
}

let timer;
if (field) {
  field.addEventListener('input', () => {
    clearTimeout(timer);
    const query = field.value.trim();
    if (query.length < 2) { render([], query); return; }
    timer = setTimeout(async () => {
      try {
        const data = await send(`/api/templates/search?q=${encodeURIComponent(query)}`,
                                'GET');
        render(data.results, query);
      } catch (err) { toast(err.message, 'error'); }
    }, 200);
  });
}

/* ── Owning one ───────────────────────────────────────── */

document.addEventListener('click', async (e) => {
  const own = e.target.closest('.set-own');
  if (own) {
    const row = own.closest('.cat-row');
    own.disabled = true;                    // one box, not three
    try {
      const data = await send(`/api/templates/${row.dataset.template}/own`,
                              'POST', {});
      const n = data.units.length;
      toast(`Added — ${n} unit${n === 1 ? '' : 's'} at On sprue`);
      setTimeout(() => { location.href = `/kits/${data.kit}`; }, 600);
    } catch (err) {
      own.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  // No catalogue entry, and Clay would rather not stop and define one. The
  // box is recorded as owned holding nothing, which is the same honest bargain
  // the scanner makes — ownership now, contents whenever.
  const record = e.target.closest('#record-it');
  if (record) {
    const name = field.value.trim();
    if (!name) return;
    record.disabled = true;
    try {
      const data = await send('/api/kits', 'POST',
                              {name, box_state: 'sealed'});
      toast('Box recorded — say what is in it whenever you like');
      setTimeout(() => { location.href = `/kits/${data.id}`; }, 600);
    } catch (err) {
      record.disabled = false;
      toast(err.message, 'error');
    }
  }
});

/* ── By code ──────────────────────────────────────────── */

async function lookUpCode() {
  const code = (codeField.value || '').replace(/\D/g, '');
  if (code.length < 8) { toast('That is too short for a barcode', 'warn'); return; }
  try {
    const check = await send(`/api/scan/check?code=${encodeURIComponent(code)}`, 'GET');
    if (check.known) {
      // The app already knows this box. Nothing to ask.
      const found = await send(`/api/templates/search?q=${encodeURIComponent(check.name)}`,
                               'GET');
      const match = found.results.find((r) => r.name === check.name);
      if (match) {
        const data = await send(`/api/templates/${match.id}/own`, 'POST', {});
        toast(`${check.name} added`);
        setTimeout(() => { location.href = `/kits/${data.kit}`; }, 600);
        return;
      }
    }
    // Unknown code: record it against the code so the next copy of the same
    // box resolves behind it once the contents exist.
    const data = await send('/api/kits', 'POST',
                            {name: `Unidentified box ${code}`,
                             source_ref: code, box_state: 'sealed'});
    toast('Box recorded against that code');
    setTimeout(() => { location.href = `/kits/${data.id}`; }, 600);
  } catch (err) {
    toast(err.message, 'error');
  }
}

const codeGo = document.querySelector('#code-go');
if (codeGo) codeGo.addEventListener('click', lookUpCode);
if (codeField) {
  codeField.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); lookUpCode(); }
  });
  // Same live sanity note the scanner gives, so a mistyped digit shows up
  // before it becomes a kit.
  let codeTimer;
  codeField.addEventListener('input', () => {
    clearTimeout(codeTimer);
    const code = codeField.value.replace(/\D/g, '');
    if (code.length < 8) return;
    codeTimer = setTimeout(async () => {
      try {
        const data = await send(`/api/scan/check?code=${encodeURIComponent(code)}`,
                                'GET');
        codeNote.textContent = data.known
          ? `Known: ${data.name}`
          : (data.notes[0] || 'Looks like a Games Workshop code');
        codeNote.className = data.notes.length ? 'hint warnline' : 'hint';
      } catch { /* the note is a nicety, not the action */ }
    }, 250);
  });
}

})();
