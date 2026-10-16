import { $, S, el, events, version, query } from "./core.js";
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
  const at = S.document.versions.findIndex((v) => v.id === S.version),
    left = $("#diffL"),
    right = $("#diffR");
  left.replaceChildren();
  right.replaceChildren();
  if (at < 1) {
    left.textContent = "Предыдущей версии пока нет.";
    return;
  }
  const before = S.document.versions[at - 1],
    after = version();
  left.appendChild(el("h3", null, "Было · " + before.id));
  right.appendChild(el("h3", null, "Стало · " + after.id));
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
  events.addEventListener("view", renderDiff);
  events.addEventListener("document", renderDiff);
  events.addEventListener("version", renderDiff);
}
