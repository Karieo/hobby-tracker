/* The gap report's own controls: resolving a row, and re-reading the paste.
 *
 * Adding and removing entries is app.js's — this is only the two things the
 * report added. Wrapped so its top-level names cannot collide with app.js or
 * add.js, which are loaded first and share this global scope. */
(() => {

document.addEventListener('click', async (e) => {
  const resolve = e.target.closest('.entry-resolve');
  if (resolve) {
    // `closest('li')`, not `closest('[data-entry]')`: the button carries its
    // own id and would match itself, leaving the row's hidden input unread and
    // the click silently doing nothing.
    const row = resolve.closest('li');
    const chosen = $('.ds', row);
    if (!chosen || !chosen.value) {
      toast('Pick a datasheet first', 'warn');
      return;
    }
    resolve.disabled = true;
    try {
      await post(`/api/lists/${resolve.dataset.list}/entries/` +
                 `${resolve.dataset.resolve}`,
                 {datasheet_id: Number(chosen.value)}, 'PATCH');
      // Reload rather than patch the row in place. Resolving one line can move
      // every number on the screen — the list may go from "3 units short" to
      // fieldable, because the models it just claimed were being counted
      // against something else a moment ago. Re-rendering the lot is the only
      // way the page stays true, and it is one request.
      toast('Saved — and remembered for next time');
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      resolve.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  const builtAs = e.target.closest('#save-built-as');
  if (builtAs) {
    builtAs.disabled = true;
    try {
      await post(`/api/units/${builtAs.dataset.unit}/built-as`, {
        datasheet_id: Number($('#built-as').value),
        is_flexible: $('#built-flexible').checked,
      });
      toast('Saved');
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      builtAs.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  const del = e.target.closest('#delete-list');
  if (del) {
    // The one control on this screen with no opposite beside it, so it is the
    // one that asks. Naming the list in the prompt rather than saying "this
    // list": the button sits at the bottom of a long page, and by the time you
    // reach it the heading is well off-screen.
    if (!window.confirm(
        `Delete "${del.dataset.name}"? This cannot be undone.\n\n` +
        'Anything it put on your wishlist stays there.')) return;
    del.disabled = true;
    try {
      await post(`/api/lists/${del.dataset.list}`, null, 'DELETE');
      // Straight to the index, not a reload: this page no longer exists.
      location.href = '/lists';
    } catch (err) {
      del.disabled = false;
      toast(err.message, 'error');
    }
    return;
  }

  const reparse = e.target.closest('#reparse');
  if (reparse) {
    reparse.disabled = true;
    try {
      const data = await post(`/api/lists/${reparse.dataset.list}/reparse`, {});
      const bits = [`${data.resolved} unit${data.resolved === 1 ? '' : 's'}`];
      if (data.unresolved) bits.push(`${data.unresolved} still unmatched`);
      toast(`Read again — ${bits.join(', ')}`);
      setTimeout(() => location.reload(), 800);
    } catch (err) {
      reparse.disabled = false;
      toast(err.message, 'error');
    }
  }
});

})();
