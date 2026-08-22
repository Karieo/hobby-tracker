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
    count: Number(row.dataset.count) || 1,
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
