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
  const pipe = $('.ramp');
  if (!pipe || !breakdown) return;

  // Matched by stage id, not by index: a ramp renders only the owned stages,
  // so the wishlist rung is missing and positions no longer line up.
  //
  // If that lookup matches nothing, fall back to reloading. This is not
  // theoretical — changing this function to match by id while one screen's
  // markup still had no data-stage made every advance on that screen a silent
  // no-op: the model moved, the page did not, and it only looked right after a
  // manual refresh. A repaint that quietly matches nothing is the worst
  // outcome available, so the failure now costs a page load instead of the
  // user's trust in the number.
  //
  // The `.pipe` selectors this used to fall back through were the same hazard
  // one level up: they had matched nothing since the ramp replaced that markup,
  // so every `x || y` here read as a live alternative and was really dead code
  // shielding the live path from view.
  let painted = 0;
  breakdown.forEach((stage) => {
    const li = $(`[data-stage="${stage.id}"]`, pipe);
    if (!li) return;
    painted += 1;
    // Session mode shows a plain count; unit detail makes it editable. Both
    // are the same number and both have to stay honest.
    const count = $('.count b', li);
    if (count) count.textContent = stage.count;
    const field = $('.count-at', li);
    if (field) field.value = stage.count;
    li.classList.toggle('empty', stage.count === 0);
    const tick = $('.tick', li);
    if (tick) tick.disabled = !stage.can_advance;
    // Nothing at this stage means nothing to step back.
    const untick = $('.untick', li);
    if (untick) untick.disabled = stage.count === 0;
  });

  if (!painted) location.reload();
}

/* Stepping back. The mirror of advance(), and deliberately just as cheap:
 * every tap in a session saves immediately with no confirmation, so undo has
 * to cost one tap too, or the screen becomes one you are careful with. */
async function retreat(unitId, body) {
  try {
    const data = await post(`/api/units/${unitId}/retreat`, body);
    if (!data.moved) { toast('Nothing to step back', 'warn'); return; }
    repaintPipe(data.breakdown);
    toast(`${data.moved} model${data.moved === 1 ? '' : 's'} stepped back`);
    if ($('.units')) setTimeout(() => location.reload(), 700);
  } catch (err) {
    toast(err.message, 'error');
  }
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

  const untick = e.target.closest('button.untick');
  if (untick && untick.dataset.unit) {
    retreat(untick.dataset.unit,
            {count: 1, from_stage_id: Number(untick.dataset.from)});
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
 * primed, you don't know or care which six.
 *
 * Typed straight into the rung it refers to, rather than into a separate form
 * with its own stage picker: the stage is the row you are typing in, so there
 * is nothing to re-select and nothing to get out of step with the pipeline
 * above it. Commits on change (blur or Enter), not on every keystroke — a
 * partially-typed "1" on the way to "12" would otherwise reconcile to one. */
document.addEventListener('change', async (e) => {
  const field = e.target.closest('.count-at');
  if (!field) return;
  const wanted = Math.max(0, Number(field.value) || 0);
  field.value = wanted;
  try {
    const data = await post(`/api/units/${field.dataset.unit}/stage`, {
      stage_id: Number(field.dataset.stage),
      count: wanted,
    });
    // Repaint from the server's answer either way. Asking for six when the
    // unit holds five legitimately moves nothing, and the field must not keep
    // showing the six — a count that disagrees with the data is worse than no
    // count, and this is the number the whole screen is about.
    repaintPipe(data.breakdown);
    if (!data.moved) {
      toast(`Still ${field.value} there`, 'warn');
      return;
    }
    toast(`${data.moved} model${data.moved === 1 ? '' : 's'} updated`);
    // The bars and percentages elsewhere on the page are server-rendered.
    setTimeout(() => location.reload(), 700);
  } catch (err) {
    toast(err.message, 'error');
    setTimeout(() => location.reload(), 600);
  }
});

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

/* ── Lists ───────────────────────────────────────────────
 * The keystone: the only screen that says what to do next, and why. */

const newList = $('#new-list');
if (newList) {
  newList.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    new FormData(newList).forEach((v, k) => { if (v !== '') body[k] = v; });
    try {
      const data = await post('/api/lists', body);
      location.href = `/lists/${data.id}`;
    } catch (err) { toast(err.message, 'error'); }
  });
}

const addEntry = $('#add-entry');
if (addEntry) {
  addEntry.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    new FormData(addEntry).forEach((v, k) => { if (v !== '') body[k] = v; });
    if (!body.datasheet_id) { toast('Pick a unit from the list first'); return; }
    try {
      await post(`/api/lists/${addEntry.dataset.list}/entries`, body);
      location.reload();
    } catch (err) { toast(err.message, 'error'); }
  });
}

document.addEventListener('click', async (e) => {
  const remove = e.target.closest('.entry-remove');
  if (remove) {
    try {
      await post(`/api/lists/entries/${remove.dataset.entry}`, null, 'DELETE');
      location.reload();
    } catch (err) { toast(err.message, 'error'); }
    return;
  }

  const raise = e.target.closest('#raise-wishlist');
  if (raise) {
    raise.disabled = true;
    try {
      const data = await post(`/api/lists/${raise.dataset.list}/wishlist`, {});
      toast(data.added
        ? `${data.added} added to the wishlist`
        : 'Already on the wishlist');
      location.reload();
    } catch (err) {
      raise.disabled = false;
      toast(err.message, 'error');
    }
  }
});

/* ── Basing applicability ────────────────────────────────
 * Whether a model has a base is a fact about the plastic, and the rules data
 * cannot tell us — a Rhino and a Dreadnought are both effort-8 vehicles and
 * only one has a base. So the app asks, once, next to the model it is asking
 * about, and stops asking as soon as it has an answer. */
$$('.basing-set').forEach((button) => {
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await post(`/api/datasheets/${button.dataset.datasheet}/basing`,
                 {basing: button.dataset.basing || null});
      location.reload();
    } catch (err) {
      button.disabled = false;
      toast(err.message, 'error');
    }
  });
});

/* ── One kit ─────────────────────────────────────────────
 * The kit page was the missing half of the Kits table: it could show "0 units,
 * 0 models" and offer nowhere to go and find out why, and nothing anywhere
 * could correct a name or remove a mis-scan. */

const kitEdit = $('#kit-edit');
if (kitEdit) {
  kitEdit.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    new FormData(kitEdit).forEach((v, k) => { body[k] = v; });
    const button = $('button[type=submit]', kitEdit);
    button.disabled = true;
    try {
      await post(`/api/kits/${kitEdit.dataset.kit}`, body);
      toast('Saved');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      button.disabled = false;
    }
  });
}

const kitDelete = $('#kit-delete');
if (kitDelete) {
  kitDelete.addEventListener('click', async () => {
    // Spelled out rather than "are you sure?": this is the one control here
    // that destroys history, and the count is what makes it real.
    const units = Number(kitDelete.dataset.units) || 0;
    const models = Number(kitDelete.dataset.models) || 0;
    const carries = units
      ? ` and its ${units} unit${units === 1 ? '' : 's'} (${models} model${models === 1 ? '' : 's'}, with their stage history)`
      : '';
    if (!confirm(`Delete this kit${carries}? This cannot be undone.\n\n`
                 + 'If you owned it and sold it, close this and use the status '
                 + 'control instead — that keeps the models and the spend history.')) {
      return;
    }
    kitDelete.disabled = true;
    try {
      await post(`/api/kits/${kitDelete.dataset.kit}`, null, 'DELETE');
      location.href = '/kits';
    } catch (err) {
      kitDelete.disabled = false;
      toast(err.message, 'error');
    }
  });
}

const kitAdopt = $('#kit-adopt');
if (kitAdopt) {
  kitAdopt.addEventListener('click', async () => {
    const templateId = $('#adopt-template').value;
    if (!templateId) { toast('Pick which box this is first'); return; }
    kitAdopt.disabled = true;
    try {
      await post(`/api/kits/${kitAdopt.dataset.kit}/adopt`,
                 {kit_template_id: templateId});
      location.reload();
    } catch (err) {
      kitAdopt.disabled = false;
      toast(err.message, 'error');
    }
  });
}

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
        // The system is named only when it is not 40,000. Searching
        // "Intercessor" now returns both the 40,000 datasheet and Kill Team's
        // Intercessor Warrior, and picking the wrong one records models that
        // are not on the shelf. The edition matters for the same reason a
        // Combat Patrol's year does: the 2021 and 2024 boxes differ.
        const system = row.game_system && row.game_system !== 'wh40k'
          ? `Kill Team${row.variant ? ` ${row.variant}` : ''}` : null;
        li.querySelector('.muted').textContent =
          [system, row.faction_name,
           row.min_models ? `${row.min_models}–${row.max_models}` : null,
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

/* Blueprint by day, Nuln at the desk.
 *
 * The OS setting is the default; this overrides it and remembers, because a
 * phone in light mode at a dark desk is exactly the case prefers-color-scheme
 * gets wrong — and the paint session is the screen that matters most at night.
 */
document.addEventListener('click', (e) => {
  if (!e.target.closest('#ground')) return;
  const root = document.documentElement;
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const current = root.dataset.ground || (dark ? 'nuln' : 'blueprint');
  const next = current === 'nuln' ? 'blueprint' : 'nuln';
  root.dataset.ground = next;
  try { localStorage.setItem('ground', next); } catch (err) { /* private mode */ }
});
