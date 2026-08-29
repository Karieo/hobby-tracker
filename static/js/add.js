/* Confirming a pasted list before any of it is written.
 *
 * The rule this enforces on the client is the same one bulk_add enforces on
 * the server: a line either resolves to a real datasheet or Clay says to skip
 * it. Nothing is guessed, nothing vanishes. */

/* IIFE — classic scripts share one global scope with app.js. */
(() => {
// The "did you mean" shortcuts. Cheaper than typing into the picker for the
// overwhelmingly common case: a typo, and the right answer is right there.
document.addEventListener('click', (e) => {
  const button = e.target.closest('.pick');
  if (!button) return;
  const row = button.closest('.addrow');
  row.querySelector('.ds').value = button.dataset.id;
  const picker = row.querySelector('.picker');
  if (picker) picker.value = button.dataset.name;

  // Raise the count to the datasheet's minimum unit size, the same thing
  // `list_resolve._clamp_to_minimum` does for a row that resolved on its own —
  // without this the identical line got 10 or 1 depending only on whether the
  // name happened to match. Only ever upward, and never over a number Clay has
  // touched: `data-touched` is set the moment he edits the box.
  const count = row.querySelector('.count');
  const min = Number(button.dataset.min) || 0;
  if (count && min && !count.dataset.touched && Number(count.value) < min) {
    count.value = min;
  }
  row.classList.remove('warn');
  const skip = row.querySelector('.skip');
  if (skip) skip.checked = false;
});

// Skipping is the other way to resolve a line, so it clears the warning too.
document.addEventListener('change', (e) => {
  const box = e.target.closest('.skip');
  if (!box) return;
  box.closest('.addrow').classList.toggle('warn', !box.checked);
});

document.addEventListener('click', async (e) => {
  const button = e.target.closest('#commit');
  if (!button) return;

  const rows = [...document.querySelectorAll('.addrow')].map((row) => ({
    datasheet_id: Number(row.querySelector('.ds').value) || null,
    // What the box says. Editable since 2026-08-29, the same as the list
    // import's, so the number on screen is the number written.
    count: Number((row.querySelector('.count') || {}).value)
      || Number(row.dataset.count) || 1,
    stage_word: row.dataset.stageWord || null,
    skip: !!(row.querySelector('.skip') || {}).checked,
  }));

  const stuck = rows.filter((r) => !r.datasheet_id && !r.skip).length;
  if (stuck) {
    toast(`${stuck} line${stuck === 1 ? ' still needs' : 's still need'} ` +
          'a datasheet — pick one, or tick skip', 'warn');
    return;
  }

  button.disabled = true;
  try {
    const data = await post('/api/add/commit', {
      rows,
      army_id: button.dataset.army || null,
      stage_id: button.dataset.stage || null,
    });
    const n = data.units.length;
    toast(`${n} unit${n === 1 ? '' : 's'} added`);
    setTimeout(() => { location.href = '/collection'; }, 700);
  } catch (err) {
    button.disabled = false;
    toast(err.message, 'error');
  }
});
})();

/* A count Clay has typed is his, and nothing may raise it afterwards. */
document.addEventListener('input', (e) => {
  const count = e.target.closest('.count');
  if (count) count.dataset.touched = '1';
});
