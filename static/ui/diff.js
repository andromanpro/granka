import { $, S, el, events, query, allowNavigation } from "./core.js";
import { setView } from "./editor.js";
const key = () => S.root + '|' + S.file;
function comparison() {
  const versions = S.document.versions;
  const at = versions.findIndex(v => v.id === S.version);
  const chosen = S.comparison;
  if (!chosen || chosen.key !== key() || chosen.anchor !== S.version ||
      !versions.some(v => v.id === chosen.from) || !versions.some(v => v.id === chosen.to)) {
    S.comparison = {key: key(), anchor: S.version,
      from: versions[Math.max(0, at - 1)].id,
      to: at > 0 ? S.version : versions.at(-1).id};
  }
  return S.comparison;
}
export async function compareVersions(from, to = null) {
  const documentKey = key();
  if (S.busy || S.recording || !(await allowNavigation()) || documentKey !== key()) return;
  to = to || S.version;
  if (![from, to].every(id => S.document.versions.some(v => v.id === id))) return;
  S.comparison = {key: key(), anchor: S.version, from, to};
  setView('diff');
  if (getComputedStyle($('#inspector-close')).display !== 'none') $('#inspector').classList.remove('mobile-open');
}
function pairs(a, b, cap) {
  if (a.length * b.length > cap) return null;
  const rows = Array.from(
    { length: a.length + 1 },
    () => new Uint32Array(b.length + 1),
  );
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      rows[i][j] =
        a[i] === b[j]
          ? rows[i + 1][j + 1] + 1
          : Math.max(rows[i + 1][j], rows[i][j + 1]);
  let i = 0,
    j = 0,
    out = [];
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push(["=", i++, j++]);
    } else if (rows[i + 1][j] >= rows[i][j + 1]) out.push(["-", i++, -1]);
    else out.push(["+", -1, j++]);
  }
  while (i < a.length) out.push(["-", i++, -1]);
  while (j < b.length) out.push(["+", -1, j++]);
  return out;
}
function line(parent, number, type = "") {
  const node = el("div", "diff-line " + type);
  node.dataset.line = number ?? "";
  parent.appendChild(node);
  return node;
}
function words(left, right, a, b) {
  const A = a.split(/(\s+)/),
    B = b.split(/(\s+)/),
    ops = pairs(A, B, 2e6);
  if (!ops) {
    left.appendChild(el("del", null, a));
    right.appendChild(el("ins", null, b));
    return;
  }
  for (const [op, i, j] of ops) {
    if (op === "=") {
      left.append(A[i]);
      right.append(B[j]);
    } else if (op === "-") left.appendChild(el("del", null, A[i]));
    else right.appendChild(el("ins", null, B[j]));
  }
}
export function renderDiff() {
  if (S.view !== "diff" || !S.document) return;
  const pair = comparison(),
    left = $("#diffL"),
    right = $("#diffR");
  $('#diff-controls').hidden = false;
  for (const [id, value] of [['diff-from', pair.from], ['diff-to', pair.to]]) {
    const select = $('#' + id); select.replaceChildren();
    for (const v of S.document.versions) select.append(new Option(v.id + ' · ' + (v.label || v.ts || ''), v.id));
    select.value = value;
  }
  left.replaceChildren();
  right.replaceChildren();
  const before = S.document.versions.find(v => v.id === pair.from),
    after = S.document.versions.find(v => v.id === pair.to);
  $('#diff-status').textContent = pair.from === pair.to ? 'Выбрана одна и та же версия' :
    before.text === after.text && JSON.stringify(before.cover) === JSON.stringify(after.cover) ? 'Текст и обложка совпадают' : '';
  left.appendChild(el("h3", null, "Слева · " + before.id));
  right.appendChild(el("h3", null, "Справа · " + after.id));
  if (JSON.stringify(before.cover) !== JSON.stringify(after.cover)) {
    for (const [parent, cover] of [
      [left, before.cover],
      [right, after.cover],
    ]) {
      parent.appendChild(
        el("p", "diff-cover-label", cover ? "Обложка статьи" : "Без обложки"),
      );
      if (cover) {
        const image = el("img", "diff-cover");
        image.src =
          "/api/article-image?" +
          query() +
          "&src=" +
          encodeURIComponent(cover.src);
        image.alt = cover.alt;
        parent.appendChild(image);
      }
    }
  }
  const A = before.text.split("\n"),
    B = after.text.split("\n"),
    ops = pairs(A, B, 12e6);
  if (!ops) {
    left.appendChild(
      el("p", null, "Текст слишком большой для построчного сравнения."),
    );
    return;
  }
  let deleted = [],
    added = [];
  function flush() {
    const n = Math.max(deleted.length, added.length);
    for (let k = 0; k < n; k++) {
      const d = deleted[k],
        a = added[k],
        l = line(left, d == null ? null : d + 1, d == null ? "gap" : "changed"),
        r = line(
          right,
          a == null ? null : a + 1,
          a == null ? "gap" : "changed",
        );
      if (d != null && a != null) words(l, r, A[d], B[a]);
      else if (d != null) l.appendChild(el("del", null, A[d]));
      else r.appendChild(el("ins", null, B[a]));
    }
    deleted = [];
    added = [];
  }
  for (const [op, i, j] of ops) {
    if (op === "=") {
      flush();
      line(left, i + 1).append(A[i]);
      line(right, j + 1).append(B[j]);
    } else if (op === "-") deleted.push(i);
    else added.push(j);
  }
  flush();
  let syncing = false;
  for (const [a, b] of [
    [left, right],
    [right, left],
  ])
    a.onscroll = () => {
      if (syncing) return;
      syncing = true;
      b.scrollTop =
        (a.scrollTop / Math.max(1, a.scrollHeight - a.clientHeight)) *
        (b.scrollHeight - b.clientHeight);
      requestAnimationFrame(() => (syncing = false));
    };
}
export function initDiff() {
  for (const [id, field] of [['diff-from', 'from'], ['diff-to', 'to']]) {
    $('#' + id).addEventListener('change', event => { comparison()[field] = event.target.value; renderDiff(); });
  }
  $('#diff-swap').addEventListener('click', () => {
    const pair = comparison(); [pair.from, pair.to] = [pair.to, pair.from]; renderDiff();
  });
  events.addEventListener("view", renderDiff);
  events.addEventListener("document", renderDiff);
  events.addEventListener("version", renderDiff);
}
