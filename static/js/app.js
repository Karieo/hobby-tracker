/* Minimal vanilla JS — no framework, no build step.
 *
 * The rule this file exists to serve: advancing a unit must cost one tap and
 * must not reload the page. A full navigation after every squad you prime is
 * exactly the friction that kills a tracker, and it loses your scroll position
 * halfway down an army. So the stage controls patch the DOM in place. */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function post(url, body, method = 'POST') {
  const res = await fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try { data = await res.json(); } catch { /* 204 or an HTML error page */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status}`);
  return data;
}

function toast(message, kind = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  document.body.appendChild(el);
  // Long enough to read at arm's length, short enough not to sit in the way.
  setTimeout(() => el.classList.add('out'), 1600);
  setTimeout(() => el.remove(), 2100);
}

/* ── Relative timestamps ──────────────────────────────────
 * Staleness is shown, never hidden: an entry last touched eight months ago is
 * probably wrong, and seeing that is the difference between correcting it and
 * trusting it. */
function since(stamp) {
  const days = Math.floor((Date.now() - new Date(stamp)) / 86400000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

function paintTimestamps(root = document) {
  $$('.stale[data-since]', root).forEach((el) => {
    const text = since(el.dataset.since);
    el.textContent = text ? `· ${text}` : '';
    // Six months untouched is the point where "still primed" stops being
    // believable, so it starts looking like a question rather than a fact.
    const days = Math.floor((Date.now() - new Date(el.dataset.since)) / 86400000);
    el.classList.toggle('old', days > 180);
  });
}

/* ── Stage movement ─────────────────────────────────────── */

function repaintPipe(breakdown) {
  const pipe = $('.pipe');
  if (!pipe || !breakdown) return;
  breakdown.forEach((stage, i) => {
    const li = pipe.children[i];
    if (!li) return;
    $('.pipe-count b', li).textContent = stage.count;
    const pct = $('.pipe-count .muted', li);
    if (pct) pct.textContent = `${stage.percent}%`;
    li.classList.toggle('zero', stage.count === 0);
    const tick = $('.tick', li);
    if (tick) tick.disabled = !stage.can_advance;
  });
}

async function advance(unitId, body, label) {
  try {
    const data = await post(`/api/units/${unitId}/advance`, body);
    if (!data.moved) { toast('Nothing left to advance', 'warn'); return; }
    repaintPipe(data.breakdown);
    toast(`${label || `${data.moved} model${data.moved === 1 ? '' : 's'}`} advanced`);
    // An army page shows many units at once; the bars are server-rendered, so
    // refresh once the toast has been read rather than re-deriving them here.
    if ($('.units')) setTimeout(() => location.reload(), 700);
  } catch (err) {
    toast(err.message, 'error');
  }
}

document.addEventListener('click', (e) => {
  const toggle = e.target.closest('[data-toggle]');
  if (toggle) {
    const panel = $(toggle.dataset.toggle);
    if (panel) {
      panel.classList.toggle('hidden');
      const field = $('input, select', panel);
      if (field && !panel.classList.contains('hidden')) field.focus();
    }
    return;
  }

  // Every one of these needs a unit to act on. Without the guard, any button
  // that merely borrows the styling class fires a request for unit "undefined".
  const all = e.target.closest('button.advance');
  if (all && all.dataset.unit) { advance(all.dataset.unit, {}, 'Whole unit'); return; }

  const some = e.target.closest('button.advance-n');
  if (some && some.dataset.unit) {
    const input = $('input', some.closest('.stepper'));
    advance(some.dataset.unit, {count: Number(input.value) || 1});
    return;
  }

  const tick = e.target.closest('button.tick');
  if (tick && tick.dataset.unit) {
    advance(tick.dataset.unit, {count: 1, from_stage_id: Number(tick.dataset.from)});
    return;
  }
});

/* ── Bulk model selection ────────────────────────────────
 * The escape hatch, not the default path. It still has to be good: select-all,
 * "first N" for the "six of these ten" case, and one stage picker for the whole
 * selection. */

function bulkCount() {
  const form = $('#bulk');
  if (!form) return;
  const n = $$('input[name="model_ids"]:checked', form).length;
  $('#n').textContent = n;
  $('#apply').disabled = n === 0;
}

const bulkForm = $('#bulk');
if (bulkForm) {
  bulkForm.addEventListener('change', bulkCount);

  $('#all').addEventListener('change', (e) => {
    $$('input[name="model_ids"]', bulkForm).forEach((box) => {
      box.checked = e.target.checked;
    });
    bulkCount();
  });

  $('#first-n').addEventListener('click', () => {
    const boxes = $$('input[name="model_ids"]', bulkForm);
    const answer = prompt(`How many of the ${boxes.length}?`, '');
    if (answer === null) return;
    const n = Number(answer);
    if (!Number.isInteger(n) || n < 0) { toast('Whole numbers only', 'warn'); return; }
    boxes.forEach((box, i) => { box.checked = i < n; });
    bulkCount();
  });

  bulkForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ids = $$('input[name="model_ids"]:checked', bulkForm).map((b) => Number(b.value));
    try {
      const data = await post(`/api/units/${bulkForm.dataset.unit}/stage`, {
        model_ids: ids,
        stage_id: Number($('select[name="stage_id"]', bulkForm).value),
      });
      if (!data.moved) { toast('Already at that stage', 'warn'); return; }
      toast(`${data.moved} model${data.moved === 1 ? '' : 's'} updated`);
      setTimeout(() => location.reload(), 600);
    } catch (err) { toast(err.message, 'error'); }
  });
}

/* ── "N of them are at X" ────────────────────────────────
 * For most real updates this replaces selection entirely — you know six are
 * primed, you don't know or care which six. */
const countForm = $('#count-form');
if (countForm) {
  countForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const data = await post(`/api/units/${countForm.dataset.unit}/stage`, {
        stage_id: Number($('select[name="stage_id"]', countForm).value),
        count: Number($('input[name="count"]', countForm).value) || 0,
      });
      if (!data.moved) { toast('Nothing to move', 'warn'); return; }
      toast(`${data.moved} model${data.moved === 1 ? '' : 's'} updated`);
      setTimeout(() => location.reload(), 600);
    } catch (err) { toast(err.message, 'error'); }
  });
}

const moveForm = $('#move-form');
if (moveForm) {
  moveForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const value = $('select[name="army_id"]', moveForm).value;
    try {
      await post(`/api/units/${moveForm.dataset.unit}/move`,
                 {army_id: value === '' ? null : Number(value)});
      location.reload();
    } catch (err) { toast(err.message, 'error'); }
  });
}

/* ── Generic form posting ───────────────────────────────── */

function formBody(form) {
  const body = {};
  new FormData(form).forEach((value, key) => {
    if (key === 'datasheet_q') return;
    body[key] = value;
  });
  return body;
}

$$('form[data-post]').forEach((form) => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await post(form.dataset.post, formBody(form));
      location.reload();
    } catch (err) { toast(err.message, 'error'); }
  });
});

$$('form[data-patch]').forEach((form) => {
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await post(form.dataset.patch, formBody(form), 'PATCH');
      toast('Saved');
    } catch (err) { toast(err.message, 'error'); }
  });
});

$$('select.kit-status').forEach((select) => {
  let previous = select.value;
  select.addEventListener('change', async () => {
    const status = select.value;
    const body = {status};
    // Disposals record money and a note; asking once beats a form nobody fills.
    if (['sold', 'traded', 'gifted'].includes(status)) {
      const price = prompt(`What did it actually go for? (blank if nothing)`, '');
      if (price === null) { select.value = previous; return; }
      if (price.trim()) body.price = price.trim();
      const note = prompt('Note — who to, what you got in trade?', '');
      if (note && note.trim()) body.note = note.trim();
    }
    try {
      await post(`/api/kits/${select.dataset.kit}/status`, body);
      previous = status;
      location.reload();
    } catch (err) { toast(err.message, 'error'); select.value = previous; }
  });
});

/* ── Datasheet picker ────────────────────────────────────
 * Never a free-text field. A unit must point at a real imported datasheet, or
 * points, gaps and purchase advice all quietly go wrong later. */
$$('input.picker').forEach((input) => {
  const hidden = $(input.dataset.target);
  const list = $('.results', input.closest('form'));
  let timer;

  const clear = () => { list.hidden = true; list.innerHTML = ''; };

  input.addEventListener('input', () => {
    hidden.value = '';                       // typing invalidates the last pick
    clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) { clear(); return; }
    timer = setTimeout(async () => {
      const res = await fetch(`/api/datasheets?q=${encodeURIComponent(query)}`);
      const {results} = await res.json();
      list.innerHTML = '';
      if (!results.length) {
        list.innerHTML = '<li class="none">No datasheet matches. '
                       + 'Check the spelling — units can only be added against '
                       + 'imported datasheets.</li>';
        list.hidden = false;
        return;
      }
      results.forEach((row) => {
        const li = document.createElement('li');
        li.innerHTML = `<b></b> <span class="muted"></span>`;
        li.querySelector('b').textContent = row.name;
        li.querySelector('.muted').textContent =
          [row.faction_name, row.min_models ? `${row.min_models}–${row.max_models}` : null,
           `effort ${row.effort}`].filter(Boolean).join(' · ');
        li.addEventListener('click', () => {
          input.value = row.name;
          hidden.value = row.id;
          // Kept on the input so a caller that builds a list from repeated
          // picks (the kit template form) has the label without refetching.
          input.dataset.name = row.name;
          input.dataset.faction = row.faction_name || '';
          input.dataset.minModels = row.min_models || '';
          const count = $('input[name="model_count"]', input.closest('form'));
          if (count && row.min_models) count.value = row.min_models;
          const pickCount = $('#pick-count', input.closest('form'));
          if (pickCount && row.min_models) pickCount.value = row.min_models;
          clear();
        });
        list.appendChild(li);
      });
      list.hidden = false;
    }, 180);
  });

  // Only guard forms where the datasheet *is* the payload (the add-unit form).
  // On the kit template form the picker adds a line to a list and is empty by
  // the time the form is submitted, so guarding there would block every save.
  input.closest('form').addEventListener('submit', (e) => {
    if (!hidden.name) return;
    if (!hidden.value) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast('Pick a datasheet from the list', 'warn');
    }
  }, true);
});

paintTimestamps();
