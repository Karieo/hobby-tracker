# Stage icons

Seven of the eight stage icons in `templates/_macros.html` are **Lucide**,
vendored inline rather than linked or bundled. The eighth — On sprue — is
hand-drawn and stays that way; see the table.

- Source: https://lucide.dev — https://github.com/lucide-icons/lucide
- Revision pinned: `23f9abc4ed0146cffededd3d7f94c1018bfdf693`
- Licence: ISC (full text below)

## Which icon is which rung

| position | stage          | lucide icon       |
|---------:|----------------|-------------------|
| 0        | Wishlist       | `heart`           |
| 1        | On sprue       | *(hand-drawn)*    |
| 2        | Assembled      | `person-standing` |
| 3        | Base prepared  | `disc`            |
| 4        | Primed         | `spray-can`       |
| 5        | Painted        | `paintbrush`      |
| 6        | Based          | `sprout`          |
| 7        | Battle ready   | `shield-check`    |

## Why On sprue is not from the set

Clay: *"I actually liked your Sprue icon better."*

Lucide's `component` is four loose diamonds — tidier than the hand-drawn frame,
and it means nothing. A sprue is a frame with parts still attached by their
gates, and no icon set has one, because nobody outside this hobby needs it.
That is the case where drawing it badly still beats picking something neat and
wrong.

The other seven are ordinary objects that professionals have already drawn
better than I will.

## Why a pin

Same reason `rules_data.py` pins BSData and the Munitorum manual: an upstream
that moves under the app changes a screen with no commit to point at. Lucide
redraws icons between releases, so tracking `main` would mean the paint icon
quietly becoming a different paint icon.

To re-fetch exactly what is vendored here:

```bash
SHA=23f9abc4ed0146cffededd3d7f94c1018bfdf693
for n in heart person-standing disc spray-can paintbrush sprout shield-check; do
  curl -sS "https://raw.githubusercontent.com/lucide-icons/lucide/$SHA/icons/$n.svg"
done
```

Bumping the pin is a deliberate change, not a refresh: look at the eight at
20px before and after, because that is the size that matters and the only one
anybody sees.

## Licence

ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
