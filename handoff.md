# Handoff — Session 4

## 1 · Goal

Build step 4: the scanner, the scan sprint queue, and the review screen (§12).

Per the spec's own ordering inside that step: the manual kit template form
first, so onboarding never depends on automation; then EAN-keyed contents
resolution; then photo extraction as the fallback.

## 2 · Current State

Steps 1–4 done and merged through step 3; step 4 is on this branch. 164 tests
pass, CI is green on `main`, and the scanner was driven end to end in a real
browser against a **real barcode**, not a mock.

Working:

- **`/scan`** — sprint capture. Camera opens once and stays open, each decode
  posts immediately, scanning resumes. Beep on decode, duplicate debounce,
  manual digit entry alongside.
- **`/scan/review`** — enrichment. Known codes need one tap; unknown ones link
  through to the template form with the code pre-filled.
- **`/templates`** — kit templates, defined by hand, contents picked against
  imported datasheets only.
- **Local `barcodes` table** — define one box, every other copy of it in the
  queue resolves behind it.

**Not built, deliberately:** EAN-keyed contents resolution and photo
extraction. Both need an Anthropic API key and a decision from Clay; the seam
is `scanning.lookup_code()`, which returns `None` and is documented as optional
enrichment. Onboarding works identically without them, which is the spec's own
design rule.

### Verified in a browser, with a real decode

Chromium was given a fake camera fed a generated Y4M containing a genuine
EAN-13 — `5011921204021`, the 2024 Ork Combat Patrol from §12's worked example.
The full chain ran: **camera decoded it** → landed on the queue → typed an ISBN
by hand and it was flagged as a book → defined contents against the real
imported datasheets → the barcode linked → the row went ready → confirm created
the kit. 21 models, no JS errors, no horizontal overflow at 390px.

Debounce confirmed: the decoder fires many times a second and the queue row
still read `quantity=1`.

## 3 · Active Files

| File | Role |
|---|---|
| `scanning.py` | Codes, the queue, kit templates, the lookup seam |
| `static/js/scan.js` | Camera, both decoders, manual entry |
| `static/js/review.js` | Confirm / quantity / discard |
| `static/js/template-form.js` | Contents builder |
| `static/vendor/zxing.min.js` | ZXing-js 0.23.0, Apache-2.0 |
| `templates/scan*.html`, `template*.html` | The four screens |
| `tests/test_scanning.py` | 35 tests on the rules that matter |

No migration: `barcodes`, `scan_queue`, `kit_templates` and
`kit_template_units` all came from migration 001, which was written for this.

## 4 · Changes Made

**ZXing is vendored, not CDN-loaded.** 362 KB, Apache-2.0, licence included.
The scanner is the critical onboarding path and should not depend on a CDN
being reachable from `bastion`.

**Both decoders share one loop shape.** ZXing is primary because WebKit has no
`BarcodeDetector` and fails silently on every iOS browser; `BarcodeDetector` is
feature-detected for desktop Chrome and never depended on.

**Nothing is ever rejected.** Prefix and check-digit problems are notes on a row
that is already saved. A scanner that refuses a real box Clay is holding is
worse than one that shrugs.

**Quantity is the shelf count.** Rescanning bumps it, and confirming
instantiates that many kits. Resolved rows stay as the audit trail for how the
collection was built.

## 5 · Failed Attempts

**`reader.decodeFromVideoElement(video, callback)` never fired.** The camera
ran, the video played (640×480, `readyState` 4, `currentTime` advancing), ZXing
loaded — and no decode arrived in 25 seconds. It was not obvious from the page
whether the decoder, the camera, or the fake feed was at fault, so I decoded a
single grabbed frame directly in the console: it returned `5011921204021`
immediately. That isolated it to the helper, which wants to own the `<video>`
element and attach its own stream — ours was already attached, because we ask
for the rear camera explicitly.

**`decodeFromCanvas` was the obvious replacement and does not exist in 0.23.**
Enumerating the prototype chain showed twenty-odd `decodeFrom*` methods and no
canvas one. The working path is the low-level one: build an
`HTMLCanvasElementLuminanceSource` → `HybridBinarizer` → `BinaryBitmap` and call
`decodeBitmap`. No data-URL round trip per frame, and it made the two decoder
paths the same shape.

**`class="advance"` on the confirm button fired a request for unit
`undefined`.** I reused the class for its green styling; `app.js` binds
`button.advance` globally to "advance a unit one stage". Confirming a scan
worked (`POST /api/scan/1/resolve` → 200) *and* fired
`POST /api/units/undefined/advance` → 404, so the toast read "404" on a
successful action. Fixed twice over: a separate `.go` styling class, and the
handlers now require `data-unit` before acting.

**`const $` in a second classic script is a SyntaxError.** `scan.js`,
`review.js` and `template-form.js` share global scope with `app.js`, which
already declares it. Caught before it shipped; all three are wrapped in IIFEs.

**The datasheet picker's submit guard blocked saving a template.** It refuses a
submit when its hidden input is empty — correct on the add-unit form, wrong on
the template form where the picker adds a line and is *meant* to be empty at
save time. It now only guards when the hidden input is a named form field.

**"Start camera" stayed visible while scanning.** `button { display:
inline-block }` beats the browser's default `[hidden]` rule, so setting
`.hidden = true` did nothing. Only visible in a screenshot — no test would have
caught it.

## 6 · Next Steps

**Step 5 — v1 ends here.** Kit catalogue seed job (§11) plus the Combat Patrol
magazine templates, then the collection view. The seed job is the one with
teeth: the catalogue must be *derived* from sources with EAN and GW's verbatim
contents block, two sources agreeing, emitted to a reviewable file rather than
straight into the database. A catalogue written from memory is the exact failure
§11 exists to prevent.

**Then stop and use it for a few weeks.**

**Open, none blocking:**

1. **EAN lookup and photo extraction** need an Anthropic API key and a decision.
   The seam is one function.
2. **`BACKUP_DEST` is still unset** — backups are local-only. A dead Jetson
   still costs the collection.
3. **Dependencies are unpinned** (`>=`), so CI resolves the latest release each
   run and a breaking upstream release can turn it red with no change here.

**Do not build past step 5.**
