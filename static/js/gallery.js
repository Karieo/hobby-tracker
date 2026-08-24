/* Scrubbing the journey.
 *
 * Every frame is already in the document; this only changes which one carries
 * `.on`. That is the whole reason the markup ships them all — a scrub that
 * fetched per position would be a hundred requests over a tunnel, and the grid
 * below needs the same pictures anyway.
 *
 * `input` rather than `change`: on a range input, `change` fires when the
 * thumb is released, which would make dragging show nothing until you let go —
 * the opposite of scrubbing.
 */
(() => {

const scrub = document.querySelector('#scrub');
if (!scrub) return;                     // one picture, or none

const frames = [...document.querySelectorAll('.frame')];
const at = document.querySelector('.scrubat');
let showing = 0;

function show(index) {
  const next = Math.max(0, Math.min(frames.length - 1, index));
  if (next === showing) return;
  frames[showing].classList.remove('on');
  frames[next].classList.add('on');
  showing = next;
  if (at) at.textContent = `${next + 1} / ${frames.length}`;
  // The frame the thumb is heading for is worth having decoded already.
  const ahead = frames[next + 1];
  if (ahead) {
    const img = ahead.querySelector('img[loading="lazy"]');
    if (img) img.loading = 'eager';
  }
}

scrub.addEventListener('input', () => show(Number(scrub.value)));

/* Arrow keys work on the range itself, but only while it has focus — and the
 * picture is what the eye is on. Left and right anywhere on the page move it,
 * unless something is being typed into. */
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = e.target.closest('input, textarea, select');
  if (typing && typing !== scrub) return;
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  e.preventDefault();
  const next = showing + (e.key === 'ArrowRight' ? 1 : -1);
  scrub.value = String(Math.max(0, Math.min(frames.length - 1, next)));
  show(next);
});

})();
