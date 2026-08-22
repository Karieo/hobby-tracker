/* Browsing what exists, and acting on it.
 *
 * The catalogue's whole job is to turn "this box exists" into either "I have
 * it" or "I want it" without leaving the row. */

/* Wrapped so its top-level names cannot collide with app.js, which is loaded
 * first and shares this global scope. */
(() => {

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

document.addEventListener('click', async (e) => {
  const row = e.target.closest('.cat-row');
  if (!row) return;
  const id = row.dataset.template;

  const want = e.target.closest('.cat-want');
  if (want) {
    want.disabled = true;
    try {
      const data = await send(`/api/templates/${id}/want`);
      toast(data.added
        ? `${data.added} model${data.added === 1 ? '' : 's'} on the wishlist`
        : 'Already on the wishlist');
      swap(want, 'cat-unwant', 'On the wishlist ✓');
      mark(row, 'pill', 'Wanted');
    } catch (err) {
      want.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  const unwant = e.target.closest('.cat-unwant');
  if (unwant) {
    unwant.disabled = true;
    try {
      await send(`/api/templates/${id}/want`, 'DELETE');
      toast('Off the wishlist');
      swap(unwant, 'cat-want', 'Want it');
      mark(row, null);
    } catch (err) {
      unwant.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  const own = e.target.closest('.cat-own');
  if (own) {
    // One box, not three: a second tap while the first is in flight would
    // record a kit Clay does not have.
    own.disabled = true;
    try {
      const data = await send(`/api/templates/${id}/own`, 'POST', {});
      const n = data.units.length;
      toast(`Added — ${n} unit${n === 1 ? '' : 's'} at On sprue`);
      // Reloaded rather than patched: owning a box changes the row's badge,
      // its wanted state, and whether it survives an "own already" filter.
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      own.disabled = false;
      toast(err.message, 'error');
    }
  }
});

function swap(button, className, label) {
  button.classList.remove('cat-want', 'cat-unwant');
  button.classList.add(className);
  button.textContent = label;
  button.disabled = false;
}

function mark(row, kind, label) {
  const state = row.querySelector('.cat-state');
  if (!state) return;
  if (row.querySelector('.ok-badge')) return;   // owning outranks wanting
  state.innerHTML = kind ? `<span class="${kind}">${label}</span>` : '';
}

})();
