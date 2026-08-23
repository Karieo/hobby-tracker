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
    count: Number(row.dataset.count) || 1,
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

  button.disabled = true;
  try {
    const data = await post('/api/lists/import', {
      rows,
      name: button.dataset.name,
      faction_id: button.dataset.faction || null,
      points_limit: button.dataset.points || null,
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
