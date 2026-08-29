/* Committing a pasted army list.
 *
 * The row markup and the datasheet picker are shared with the collection
 * paste — add.js is loaded alongside this and wires both. Only the
 * destination differs: a list of what Clay wants to field, rather than models
 * he owns. That distinction is the whole point, because the gap between the
 * two is what the app is for. */

/* Wrapped so its top-level names cannot collide with app.js or add.js, which
 * are loaded first and share this global scope. */
(() => {

document.addEventListener('click', async (e) => {
  const button = e.target.closest('#commit-list');
  if (!button) return;

  const rows = [...document.querySelectorAll('.addrow')].map((row) => ({
    datasheet_id: Number(row.querySelector('.ds').value) || null,
    model_count: Number(row.dataset.count) || 1,
    // The line it came from travels with it, so a datasheet Clay picked here
    // teaches the alias table and the same spelling is never asked about
    // twice. A row that arrived already resolved has nothing to teach.
    raw_name: row.dataset.raw || null,
    points: Number(row.dataset.points) || null,
    resolved_by: row.querySelector('.ds').value && !row.dataset.resolved
      ? 'manual' : (row.dataset.resolved || null),
    skip: !!(row.querySelector('.skip') || {}).checked,
  }));

  // A line left unresolved is a unit Clay would turn up to a game without, so
  // this refuses rather than quietly dropping it. Skipping is allowed — it is
  // his decision, made on purpose.
  const stuck = rows.filter((r) => !r.datasheet_id && !r.skip).length;
  if (stuck) {
    toast(`${stuck} line${stuck === 1 ? ' still needs' : 's still need'} ` +
          'a datasheet — pick one, or tick skip', 'warn');
    return;
  }

  // Read from the fields rather than from the button. They are pre-filled from
  // the paste and editable right up to this click, so a data attribute stamped
  // at render time would send whatever they said before he corrected them.
  const details = document.querySelector('#list-details');
  const field = (n) => (details && details.elements[n]
    ? details.elements[n].value.trim() : '');

  // Checked here rather than left to the server, so the button never goes
  // disabled on the one mistake he can fix without leaving the screen.
  if (!field('name')) {
    toast('The list needs a name', 'warn');
    if (details) details.elements.name.focus();
    return;
  }

  button.disabled = true;
  try {
    const raw = document.querySelector('#raw-text');
    const data = await post('/api/lists/import', {
      rows,
      name: field('name'),
      faction_id: field('faction_id') || null,
      points_limit: field('points_limit') || null,
      // Kept so the parser getting better does not mean pasting it again.
      raw_text: raw ? raw.value : null,
      source_format: button.dataset.format || null,
      points_total: button.dataset.declared || null,
    });
    const n = data.entries.length;
    toast(`List created — ${n} unit${n === 1 ? '' : 's'}`);
    // Straight to the gap report, because "what stands between this and the
    // table" is the question the list was imported to answer.
    setTimeout(() => { location.href = `/lists/${data.list_id}`; }, 700);
  } catch (err) {
    button.disabled = false;
    toast(err.message, 'error');
  }
});

})();
