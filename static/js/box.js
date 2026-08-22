/* One barcode's page, reached by scanning the box itself.
 *
 * The couch half of onboarding: pick the box up, scan it, say what's in it —
 * rather than reading thirteen digits off a screen to find which shelved box
 * is which. */

/* IIFE for the same reason as every file after app.js: classic scripts share
 * one global scope, and a second top-level `const $` is a SyntaxError that
 * kills the page. */
(() => {
document.addEventListener('click', async (e) => {
  const button = e.target.closest('#adopt-all');
  if (!button) return;

  const form = document.querySelector('#defaults');
  const body = {kit_template_id: button.dataset.template};
  if (form) new FormData(form).forEach((v, k) => { if (v !== '') body[k] = v; });

  button.disabled = true;
  try {
    const data = await post(`/api/box/${button.dataset.code}/adopt-all`, body);
    const n = data.kits.length;
    toast(`${n} box${n === 1 ? '' : 'es'} filled in`);
    setTimeout(() => location.reload(), 700);
  } catch (err) {
    button.disabled = false;
    toast(err.message, 'error');
  }
});
})();
