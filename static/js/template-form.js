/* Defining what is inside a box.
 *
 * Every line must point at a datasheet the importer loaded — never free text.
 * A template is what the shopping list later inverts to say "buy this", so an
 * invented unit name here becomes wrong purchase advice months from now. */

/* Wrapped so its top-level names cannot collide with app.js, which is
 * loaded first and shares this global scope. */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function readContents() {
  return $$('#contents li').map((li) => ({
    datasheet_id: Number(li.dataset.datasheet),
    model_count: Number(li.dataset.count),
  }));
}

function addLine(datasheetId, name, faction, count) {
  if ($$('#contents li').some((li) => Number(li.dataset.datasheet) === datasheetId)) {
    toast('That unit is already in the list', 'warn');
    return;
  }
  const li = document.createElement('li');
  li.dataset.datasheet = datasheetId;
  li.dataset.count = count;
  const label = document.createElement('span');
  label.innerHTML = '<b></b> ';
  label.querySelector('b').textContent = `${count}×`;
  label.append(name);
  const fac = document.createElement('span');
  fac.className = 'muted';
  fac.textContent = faction || '';
  const drop = document.createElement('button');
  drop.type = 'button';
  drop.className = 'ghost drop';
  drop.textContent = 'Remove';
  li.append(label, fac, drop);
  $('#contents').append(li);
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.drop')) e.target.closest('li').remove();
});

const addButton = $('#add-line');
if (addButton) {
  addButton.addEventListener('click', () => {
    const hidden = $('#pick-datasheet');
    const picker = $('.picker');
    const count = Number($('#pick-count').value) || 1;
    if (!hidden.value) { toast('Pick a datasheet from the list', 'warn'); return; }
    addLine(Number(hidden.value), picker.dataset.name || picker.value,
            picker.dataset.faction || '', count);
    hidden.value = '';
    picker.value = '';
    $('#pick-count').value = 1;
  });
}

function formBody(form) {
  const body = {};
  new FormData(form).forEach((value, key) => { body[key] = value; });
  body.contents = readContents();
  return body;
}

const create = $('#new-template');
if (create) {
  create.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!readContents().length) {
      toast('Add at least one unit — an empty template makes empty kits', 'warn');
      return;
    }
    try {
      const res = await fetch('/api/templates', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(formBody(create)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      // Back where the definition was asked for: the box page Clay scanned
      // from if there is one, otherwise the queue whose codes this resolved.
      const next = create.querySelector('[name=next]');
      location.href = (next && next.value)
        || (create.querySelector('[name=code]').value
            ? '/scan/review' : `/templates/${data.id}`);
    } catch (err) { toast(err.message, 'error'); }
  });
}

const edit = $('#edit-template');
if (edit) {
  edit.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!readContents().length) {
      toast('A template needs at least one unit', 'warn');
      return;
    }
    try {
      const res = await fetch(`/api/templates/${edit.dataset.template}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(formBody(edit)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      toast('Saved');
    } catch (err) { toast(err.message, 'error'); }
  });
}

const link = $('#link-code');
if (link) {
  link.addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = link.querySelector('input[name=code]');
    try {
      const res = await fetch(`/api/templates/${link.dataset.template}/barcodes`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: input.value}),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      if (data.notes && data.notes.length) toast(data.notes[0], 'warn');
      location.reload();
    } catch (err) { toast(err.message, 'error'); }
  });
}

})();
