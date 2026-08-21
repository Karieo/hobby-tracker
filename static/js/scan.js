/* Sprint capture.
 *
 * ZXing is the primary decoder, not a fallback. The native BarcodeDetector API
 * is Chrome/Android only — WebKit does not implement it, so it silently fails
 * on *every* browser on iOS, Safari and Chrome alike, because they all use
 * WebKit underneath. Since an iPhone is the target device, depending on it
 * would mean the scanner never fires at all on the only phone that matters.
 * It is feature-detected below and used when present (desktop Chrome), but
 * nothing here relies on it.
 *
 * The camera opens once and stays open. Each decode posts to the server
 * immediately and scanning resumes — no modal, no navigation, no confirmation
 * tap. A reload or a flat battery must never cost a shelf's worth of work. */

/* Wrapped so its top-level names cannot collide with app.js, which is
 * loaded first and shares this global scope. */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const video = $('#video');
const statusEl = $('#status');
const scansEl = $('#scans');
const countsEl = $('#counts');

let reader = null;
let stream = null;
let running = false;

/* Decoders fire the same code many times a second. Without this, one box
 * becomes twenty. The window is long enough to cover a slow pan off the label
 * but short enough that deliberately rescanning the same kit still registers. */
const DEBOUNCE_MS = 2500;
const lastSeen = new Map();

function recentlySeen(code) {
  const now = Date.now();
  for (const [seen, at] of lastSeen) {
    if (now - at > DEBOUNCE_MS * 4) lastSeen.delete(seen);
  }
  const at = lastSeen.get(code);
  lastSeen.set(code, now);
  return at !== undefined && now - at < DEBOUNCE_MS;
}

function setStatus(text, kind = '') {
  statusEl.textContent = text;
  statusEl.className = `scanner-status ${kind}`;
}

/* A short tone, built with WebAudio rather than an audio file: it needs no
 * asset, and it has to fire while the phone is at arm's length pointing at a
 * shelf, where a toast alone is not feedback. */
let audio = null;
function beep(ok = true) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.connect(gain); gain.connect(audio.destination);
    osc.frequency.value = ok ? 880 : 300;
    gain.gain.setValueAtTime(0.0001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.16);
    osc.start(); osc.stop(audio.currentTime + 0.17);
  } catch { /* no audio is survivable; the toast still fires */ }
}

function addRow(result) {
  const li = document.createElement('li');
  li.className = result.known ? 'known' : '';
  const code = document.createElement('span');
  code.className = 'code';
  code.textContent = result.code;
  const name = document.createElement('span');
  name.textContent = result.name || 'Unknown box';
  li.append(code, name);
  if (result.quantity > 1) {
    const pill = document.createElement('span');
    pill.className = 'pill';
    pill.textContent = `×${result.quantity}`;
    li.append(pill);
  }
  scansEl.prepend(li);
  while (scansEl.children.length > 12) scansEl.lastElementChild.remove();
}

async function submit(code) {
  try {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code}),
    });
    const data = await res.json();
    if (!res.ok) { beep(false); toast(data.error || 'Scan failed', 'error'); return; }

    beep(true);
    addRow(data);
    const boxes = data.summary.open_boxes;
    countsEl.textContent = `${boxes} box${boxes === 1 ? '' : 'es'} waiting`;

    // Prefix and check-digit notes warn, never reject — an unexpected code is
    // "check this", not "invalid", and the row is already saved either way.
    if (data.notes && data.notes.length) {
      toast(`${data.code} — ${data.notes[0]}`, 'warn');
    } else if (data.duplicate) {
      toast(`${data.name || data.code} ×${data.quantity}`);
    } else {
      toast(data.known ? `${data.name} added` : 'New code — define it later');
    }
  } catch (err) {
    beep(false);
    toast('Could not reach the server — scan not saved', 'error');
  }
}

function onDecode(code) {
  if (!code || recentlySeen(code)) return;
  submit(code);
}

async function start() {
  if (running) return;
  try {
    setStatus('Starting camera…');
    // The rear camera is the one pointed at the shelf.
    stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode: {ideal: 'environment'}},
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
    running = true;
    $('#start').hidden = true;
    $('#stop').hidden = false;

    if (window.BarcodeDetector) {
      // Desktop Chrome only. Nice when it exists, never depended on.
      setStatus('Scanning (native decoder)', 'live');
      nativeLoop();
    } else {
      setStatus('Scanning', 'live');
      zxingLoop();
    }
  } catch (err) {
    running = false;
    if (!window.isSecureContext) {
      setStatus('The camera needs HTTPS. Use the tunnel address, not a plain '
                + 'http:// LAN or Tailscale IP — and type the code below '
                + 'meanwhile.', 'error');
    } else {
      setStatus(`Camera unavailable: ${err.message}. Type the code below.`, 'error');
    }
  }
}

async function nativeLoop() {
  const detector = new window.BarcodeDetector({
    formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128'],
  });
  while (running) {
    try {
      const found = await detector.detect(video);
      found.forEach((b) => onDecode(b.rawValue));
    } catch { /* a dropped frame is not an error worth stopping for */ }
    await new Promise((r) => setTimeout(r, 200));
  }
}

async function zxingLoop() {
  const {BarcodeFormat, DecodeHintType, BrowserMultiFormatReader} = window.ZXing;
  const hints = new Map();
  // Restricting the formats is a real speed win: the decoder stops trying to
  // read QR, Aztec, PDF417 and the rest on every frame. Retail barcodes are
  // EAN/UPC; CODE_128 is here for the occasional retailer's own label.
  hints.set(DecodeHintType.POSSIBLE_FORMATS, [
    BarcodeFormat.EAN_13, BarcodeFormat.EAN_8,
    BarcodeFormat.UPC_A, BarcodeFormat.UPC_E, BarcodeFormat.CODE_128,
  ]);
  reader = new BrowserMultiFormatReader(hints);

  // Grab frames and decode them one at a time rather than handing the video
  // element to the library. decodeFromVideoElement() wants to own the element
  // and attach its own stream; ours is already attached (we asked for the rear
  // camera explicitly), and its callback then never fires. Driving the loop
  // here also keeps the two decoders the same shape, so the ZXing path and the
  // BarcodeDetector path behave identically.
  const {HTMLCanvasElementLuminanceSource, HybridBinarizer, BinaryBitmap} = window.ZXing;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', {willReadFrequently: true});

  while (running) {
    if (video.readyState >= 2 && video.videoWidth) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);
      try {
        // Build the bitmap directly rather than using a decodeFrom* helper.
        // The helpers in this version either want to own the video element or
        // round-trip the frame through a data URL, and this is the cheap path:
        // the canvas pixels go straight to the binarizer.
        const source = new HTMLCanvasElementLuminanceSource(canvas);
        const result = reader.decodeBitmap(new BinaryBitmap(new HybridBinarizer(source)));
        if (result) onDecode(result.getText());
      } catch {
        // NotFoundException on a frame with no barcode in it — the normal case
        // for most frames, and not worth a log line each time.
      }
    }
    // Fast enough to feel instant while panning across a shelf, slow enough to
    // leave the phone's CPU alone.
    await new Promise((r) => setTimeout(r, 120));
  }
}

function stop() {
  running = false;
  if (reader) { try { reader.reset(); } catch { /* already torn down */ } reader = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  video.srcObject = null;
  $('#start').hidden = false;
  $('#stop').hidden = true;
  setStatus('Camera off');
}

$('#start').addEventListener('click', start);
$('#stop').addEventListener('click', stop);
// Leaving the page with the torch effectively on would flatten the phone.
window.addEventListener('pagehide', stop);

/* Manual entry. Never optional: glare, damaged boxes and dim shop lighting
 * defeat camera scanning often enough that this is the real fallback path. */
const manual = $('#manual');
const manualNote = $('#manual-note');

manual.addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = manual.querySelector('input[name=code]');
  const code = input.value.trim();
  if (!code) return;
  await submit(code);
  input.value = '';
  manualNote.textContent = '';
  input.focus();
});

// Sanity feedback while typing, before anything is committed.
let checkTimer;
manual.querySelector('input[name=code]').addEventListener('input', (e) => {
  clearTimeout(checkTimer);
  const code = e.target.value.replace(/\D/g, '');
  if (code.length < 8) { manualNote.textContent = ''; return; }
  checkTimer = setTimeout(async () => {
    const res = await fetch(`/api/scan/check?code=${encodeURIComponent(code)}`);
    const data = await res.json();
    manualNote.textContent = data.known
      ? `Known: ${data.name}`
      : (data.notes[0] || 'Looks like a Games Workshop code');
    manualNote.className = data.notes.length ? 'hint warnline' : 'hint';
  }, 250);
});

})();
