"""Immutable manual versions and conservative annotation transfer (code points)."""
from copy import deepcopy
from difflib import SequenceMatcher
import time
import uuid


def unchanged_ranges(before, after):
    """Match whole lines first; only compare characters inside small changed blocks."""
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    apos, bpos = [0], [0]
    for line in a:
        apos.append(apos[-1] + len(line))
    for line in b:
        bpos.append(bpos[-1] + len(line))
    ranges = []
    for tag, i, j, k, l in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ranges.append((apos[i], apos[j], bpos[k]))
        elif tag == "replace":
            old, new = before[apos[i]:apos[j]], after[bpos[k]:bpos[l]]
            if len(old) * len(new) <= 2_000_000:
                for match in SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks():
                    if match.size:
                        ranges.append((apos[i] + match.a, apos[i] + match.a + match.size,
                                       bpos[k] + match.b))
    # Adjacent equal blocks are one safe span, including annotations crossing lines.
    merged = []
    for start, end, new_start in ranges:
        if merged and merged[-1][1] == start and merged[-1][2] + start - merged[-1][0] == new_start:
            merged[-1] = (merged[-1][0], end, merged[-1][2])
        else:
            merged.append((start, end, new_start))
    return merged


_INHERIT_COVER = object()


def edit_text(data, base_version, text, *, cover=_INHERIT_COVER):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Текст статьи не может быть пустым")
    if len(text) > 300_000:
        raise ValueError("Текст больше 300 000 символов")
    base = data["versions"][-1]
    if base["id"] != base_version:
        raise ValueError("Появилась новая версия статьи. Перечитайте документ перед правкой.")
    next_cover = base.get('cover') if cover is _INHERIT_COVER else cover
    cover_changed = next_cover != base.get('cover')
    if text == base["text"] and not cover_changed:
        return {"version": base_version, "unchanged": True}
    spans = unchanged_ranges(base["text"], text)
    existing = {v["id"] for v in data["versions"]}
    n = len(existing) + 1
    while "v" + str(n) in existing:
        n += 1
    vid = "v" + str(n)
    copies, retired = [], []
    for ann in data["annotations"]:
        if ann["version"] != base_version:
            continue
        found = next(((s, e, ns) for s, e, ns in spans
                      if s <= ann["start"] < ann["end"] <= e), None)
        if found is None:
            retired.append(ann["id"])
            continue
        s, _, ns = found
        clone = deepcopy(ann)
        clone.update(id=("u" if ann["type"] == "note" else "a") + uuid.uuid4().hex,
                     version=vid, start=ns + ann["start"] - s, end=ns + ann["end"] - s,
                     source_annotation=ann["id"])
        if text[clone["start"]:clone["end"]] != ann["quote"]:
            raise ValueError("Не удалось перенести пометку")
        copies.append(clone)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["versions"].append({"id": vid, "label": "ручная правка " + time.strftime("%d.%m %H:%M"),
        "ts": ts, "text": text, "origin": "author", "parent_version": base_version,
        "transferred_annotations": len(copies), "retired_annotations": retired})
    if next_cover is not None:
        data['versions'][-1]['cover'] = deepcopy(next_cover)
    if cover_changed:
        data['versions'][-1].update(origin='cover', label=('обложка ' if next_cover else 'без обложки ') + time.strftime('%d.%m %H:%M'))
    data["annotations"].extend(copies)
    previous_status = data.get("status", "draft")
    data["status"] = "draft"
    data.setdefault("history", []).append({"ts": ts, "action": "cover-edit" if cover_changed else "text-edit", "version": vid,
        "base_version": base_version, "transferred": len(copies), "retired": retired,
        "previous_status": previous_status})
    return {"version": vid, "transferred": len(copies), "retired": len(retired)}
