/* Enrichment: the keyboard half, done later and away from the shelf.
 *
 * Known codes need one tap. Unknown ones need contents defined once, after
 * which every other copy of that box resolves behind them. */

/* Wrapped so its top-level names cannot collide with app.js, which is
 * loaded first and shares this global scope. */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function defaults() {
  const form = $('#defaults');
  const body = {};
  new FormData(form).forEach((v, k) => { if (v !== '') body[k] = v; });
  return body;
}

async function post(url, body, method = 'POST') {
  const res = await fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status}`);
  return data;
}

document.addEventListener('click', async (e) => {
  const row = e.target.closest('.qrow');
  if (!row) return;
  const id = row.dataset.queue;

  if (e.target.closest('.q-confirm')) {
    const button = e.target.closest('.q-confirm');
    button.disabled = true;                     // one box, not three
    try {
      const data = await post(`/api/scan/${id}/resolve`, defaults());
      const n = data.kits.length;
      toast(`${n} kit${n === 1 ? '' : 's'} added`);
      row.remove();
      refreshCounts(data.summary);
    } catch (err) {
      button.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  if (e.target.closest('.q-discard')) {
    if (!confirm('Discard this scan? The box stays unrecorded.')) return;
    try {
      await post(`/api/scan/${id}`, null, 'DELETE');
      row.remove();
      toast('Discarded');
    } catch (err) { toast(err.message, 'error'); }
  }
});

// Quantity is how many of that box are on the shelf, so it decides how many
// kits get created. Saved as it changes rather than behind another button.
document.addEventListener('change', async (e) => {
  const input = e.target.closest('.q-qty');
  if (!input) return;
  const row = input.closest('.qrow');
  const quantity = Math.max(1, Number(input.value) || 1);
  input.value = quantity;
  try {
    await post(`/api/scan/${row.dataset.queue}/quantity`, {quantity});
    const label = row.querySelector('.q-confirm .n');
    if (label) label.textContent = quantity;
  } catch (err) { toast(err.message, 'error'); }
});

function refreshCounts(summary) {
  if (!summary) return;
  const stats = $$('.stat b');
  if (stats.length >= 4) {
    stats[0].textContent = summary.open_boxes;
    stats[1].textContent = summary.known;
    stats[2].textContent = summary.unknown;
    stats[3].textContent = summary.open_rows;
  }
  if (!$('.qrow')) setTimeout(() => location.reload(), 500);
}

})();
