# -*- coding: utf-8 -*-
"""ИИМетр — замер текста по трём осям, полностью локально и детерминированно.

ЧТО ЭТО НЕ ЕСТЬ: детектор авторства. Низкий балл не доказывает, что писал человек,
высокий — что писала машина. Инструмент отвечает на другой вопрос:
«насколько этот текст отошёл от того, как пишет автор, и много ли в нём известных
машинных оборотов».

Три оси:
  1. Маркеры   — совпадения с редакторским словарём оборотов на 1000 слов.
  2. Структура — однородность ритма и формы; эвристическая оценка.
  3. Голос     — стилометрическое расстояние до рукописного корпуса автора
                 (voice-corpus/): служебные слова, длины предложений, пунктуация,
                 лицо, абзацы. Без личного корпуса итоговая оценка не вычисляется.

Вызов:
    py -3.14 iimeter.py текст.md            → JSON с баллами и подсветкой
    py -3.14 iimeter.py --profile           → пересобрать профиль голоса из корпуса
"""
import json
import hashlib
import math
import os
import re
import statistics
import sys
import uuid
from pathlib import Path
if __package__ in {None,''}:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import config as environment

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CORPUS_DIR = environment.path(environment.load()['voice']['corpus_dir'])
PROFILE = environment.path(environment.load()['voice']['profile_file'])
ENGINE_VERSION = "0.2"
PROFILES = {"blog": "Статьи и блог", "talks": "Доклады"}


def corpus_inputs(profile_id="blog"):
    if profile_id not in PROFILES:
        raise ValueError("Неизвестный профиль голоса")
    directory = CORPUS_DIR if profile_id == "blog" else CORPUS_DIR / "talks"
    files = sorted(p for p in directory.glob("*.md") if p.name != "README.md")
    inputs = [(p.name, p.read_text(encoding="utf-8")) for p in files]
    fingerprint = hashlib.sha256(json.dumps([ENGINE_VERSION, profile_id, inputs],
                                ensure_ascii=False).encode("utf-8")).hexdigest()
    return inputs, fingerprint


def profile_path(profile_id):
    return PROFILE if profile_id == "blog" else PROFILE.with_name("voice-profile-" + profile_id + ".json")

# --- редакторские эвристики: фраза → причина проверки ---
MARKERS = {
    r"в современном мире|в эпоху цифровизации|на сегодняшний день": "пустая космическая вводная",
    r"игра\w+ (?:ключевую|важную) роль|знаменует собой|является свидетельством": "раздувание значимости",
    r"важно отметить|стоит подчеркнуть|следует учитывать|ключевым моментом является": "филлер значимости",
    r"представля\w+ собой|является мощным инструментом": "глагол-избегание",
    r"позволяет обеспечить|позволяет не только": "связка-усилитель",
    r"данн\w+ (?:подход|документ|решение)|настоящ\w+ документ|соответствующ\w+ мер": "канцелярит-определитель",
    r"осуществлени\w+|проведени\w+ анализа|обеспечени\w+ функционирования": "отглагольный канцелярит",
    r"от \w+ до \w+, от \w+ до": "ложный диапазон",
    r"не только .{2,40}, но и": "балансовая антитеза",
    r"давайте разберёмся|давайте рассмотрим|в этой статье мы": "мета-анонс",
    r"по своей сути|если смотреть шире|в самой основе лежит": "псевдоглубинная преамбула",
    r"таким образом,|подводя итог|всё вышесказанное": "формульное заключение",
    r"эксперты счита\w+|исследования показыва\w+|по мнению аналитиков": "размытая атрибуция",
    r"отличный вопрос|надеюсь, помогло|конечно, давайте": "чат-артефакт",
    r"адресовать проблему|доставить ценность|сделать разницу|раскрывает потенциал": "калька-сочетаемость",
    r"бесшовн\w+|комплексн\w+ решени|инновационн\w+|уникальн\w+ возможност": "маркетинговый глянец",
    r"ключев\w+ (?:роль|момент|фактор|аспект)": "слово-затычка «ключевой»",
    r"способству\w+ развитию|подчёркива\w+ важность|отража\w+ тенденцию": "деепричастный псевдоанализ",
}
MARKER_RE = {re.compile(p, re.I): why for p, why in MARKERS.items()}

FUNCTION_WORDS = """и в не на я что с а как это по но у же к так вот бы за
то из для все или если когда только там ещё уже они мы он она их его её
можно надо просто даже потом сам себя чтобы тут ну вообще короче причём""".split()

SENT_SPLIT = re.compile(r"[.!?…]+[\s»\")]*")
# тире считаем во ВСЕХ начертаниях: pandoc из docx пишет " -- ", браузерный
# текст даёт "—". Без этого корпус автора выглядел бы «без тире» — ложный сигнал.
DASH_RE = re.compile(r"—|–|(?<=\s)--(?=\s)|(?<=\s)-(?=\s)")


def count_dashes(t: str) -> int:
    return len(DASH_RE.findall(t))
WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]*")


def strip_markup(text: str) -> str:
    """Mask non-prose in place: every axis sees the same text and offsets stay valid."""
    def spaces(value):
        return re.sub(r"[^\r\n]", " ", value)
    for pattern in (
        r"\A\ufeff?---\r?\n.*?\r?\n---(?:\r?\n|$)",
        r"(?m)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\1[ \t]*\r?$|\Z)",
        r"`[^`\n]+`", r"<!--.*?-->",
        r"\[(?:ЛИЧНОЕ|ВСТАВЬ ЛИЧНОЕ|СКРИНШОТ)[^\]]*\]",
        r"!\[[^\]]*\]\([^)]*\)",
    ):
        text = re.sub(pattern, lambda m: spaces(m.group()), text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)",
                  lambda m: " " + m.group(1) + spaces(m.group()[len(m.group(1)) + 1:]), text)
    text = re.sub(r"https?://\S+", lambda m: spaces(m.group()), text)
    return text


def paragraphs(text: str):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def sentences(text: str):
    return [s.strip() for s in SENT_SPLIT.split(text) if len(s.strip()) > 1]


def style_vector(text: str) -> dict:
    """Стилометрический профиль: то, что устойчиво отличает одного автора от другого."""
    clean = strip_markup(text)
    words = [w.lower() for w in WORD_RE.findall(clean)]
    sents = sentences(clean)
    paras = paragraphs(clean)
    n = max(1, len(words))
    lens = [len(WORD_RE.findall(s)) for s in sents] or [0]
    plens = [len(WORD_RE.findall(p)) for p in paras] or [0]
    fw = {w: words.count(w) / n * 100 for w in FUNCTION_WORDS}
    return {
        "words": len(words),
        "sent_mean": statistics.fmean(lens),
        "sent_sd": statistics.pstdev(lens) if len(lens) > 1 else 0.0,
        "para_mean": statistics.fmean(plens),
        "para_sd": statistics.pstdev(plens) if len(plens) > 1 else 0.0,
        "dash_1k": count_dashes(clean) / n * 1000,
        "paren_1k": clean.count("(") / n * 1000,
        "quest_1k": clean.count("?") / n * 1000,
        "excl_1k": clean.count("!") / n * 1000,
        "ellips_1k": clean.count("...") / n * 1000 + clean.count("…") / n * 1000,
        "first_person": sum(words.count(w) for w in ("я", "мне", "меня", "мой", "моя", "мои")) / n * 100,
        "fw": fw,
    }


def build_profile(profile_id="blog", inputs=None, fingerprint=None) -> dict:
    if inputs is None:
        inputs, fingerprint = corpus_inputs(profile_id)
    texts = [text for _, text in inputs]
    if not texts:
        raise ValueError("Добавь рукописные тексты в настроенный каталог voice.corpus_dir")
    v = style_vector("\n\n".join(texts))
    v.update(sources=[name for name, _ in inputs], fingerprint=fingerprint,
             engine_version=ENGINE_VERSION, profile_id=profile_id, label=PROFILES[profile_id])
    calibration = []
    if len(texts) > 1:
        for i, (name, text) in enumerate(inputs):
            held_out = style_vector("\n\n".join(t for j, t in enumerate(texts) if i != j))
            result = measure(text, profile_id, profile=held_out)
            calibration.append({"source": name, "words": result["words"], "score": result["score"]})
    v["calibration"] = {"method": "leave-one-document-out", "samples": calibration,
                        "authorship_accuracy": None}
    dest = profile_path(profile_id)
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp = dest.with_name("." + dest.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return v


def load_profile(profile_id="blog") -> dict:
    inputs, fingerprint = corpus_inputs(profile_id)
    if not inputs:return None
    dest = profile_path(profile_id)
    if dest.exists():
        profile = json.loads(dest.read_text(encoding="utf-8"))
        if profile.get("fingerprint") == fingerprint:
            return profile
    return build_profile(profile_id, inputs, fingerprint)


def voice_distance(v: dict, prof: dict) -> tuple:
    """Расстояние до авторского профиля по осям. Возвращает (0..1, разбор)."""
    parts = {}

    def rel(a, b, scale):
        return min(1.0, abs(a - b) / max(scale, 1e-9))

    parts["длина предложений"] = rel(v["sent_mean"], prof["sent_mean"], max(prof["sent_mean"], 8))
    parts["разброс длин"] = rel(v["sent_sd"], prof["sent_sd"], max(prof["sent_sd"], 4))
    parts["длина абзацев"] = rel(v["para_mean"], prof["para_mean"], max(prof["para_mean"], 30))
    parts["тире"] = rel(v["dash_1k"], prof["dash_1k"], max(prof["dash_1k"], 6))
    parts["скобки-ремарки"] = rel(v["paren_1k"], prof["paren_1k"], max(prof["paren_1k"], 4))
    parts["вопросы"] = rel(v["quest_1k"], prof["quest_1k"], max(prof["quest_1k"], 3))
    parts["первое лицо"] = rel(v["first_person"], prof["first_person"], max(prof["first_person"], 1.5))
    # косинус по служебным словам — ядро авторской атрибуции
    a = [v["fw"].get(w, 0) for w in FUNCTION_WORDS]
    b = [prof["fw"].get(w, 0) for w in FUNCTION_WORDS]
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    cos = dot / (na * nb)
    parts["служебные слова"] = 1.0 - cos
    dist = statistics.fmean(parts.values())
    return dist, {k: round(x, 3) for k, x in parts.items()}


def find_markers(text: str):
    """Найти маркеры с позициями в CODE POINTS (контракт Гранки)."""
    clean_for_offsets = strip_markup(text)
    hits = []
    for rx, why in MARKER_RE.items():
        for m in rx.finditer(clean_for_offsets):
            frag = text[m.start():m.end()]
            if len(frag) < 3:
                continue
            hits.append({"start": len(clean_for_offsets[:m.start()]),
                         "end": len(clean_for_offsets[:m.end()]),
                         "quote": frag, "why": why})
    hits.sort(key=lambda h: h["start"])
    # убрать пересечения (оставляем первый)
    out, last = [], -1
    for h in hits:
        if h["start"] >= last:
            out.append(h)
            last = h["end"]
    return out


def structure_report(text: str, prof: dict | None = None):
    """Однородность формы — главный дискриминатор по корпусным данным."""
    clean = strip_markup(text)
    sents = sentences(clean)
    paras = paragraphs(clean)
    words = WORD_RE.findall(clean)
    n = max(1, len(words))
    lens = [len(WORD_RE.findall(s)) for s in sents] or [0]
    sd = statistics.pstdev(lens) if len(lens) > 1 else 0.0
    plens = [len(WORD_RE.findall(p)) for p in paras] or [0]
    psd = statistics.pstdev(plens) if len(plens) > 1 else 0.0
    opens = [(WORD_RE.findall(p) or [""])[0].lower() for p in paras]
    dup_open = len(opens) - len(set(opens))
    list_lines = len([l for l in clean.splitlines() if re.match(r"\s*[-*•]|\s*\d+\.", l)])
    total_lines = max(1, len([l for l in clean.splitlines() if l.strip()]))
    flags = []
    score = 0.0
    if sd < 4:
        flags.append(f"ровный ритм: разброс длин предложений {sd:.1f} слова (у живого текста обычно больше)")
        score += 0.30
    if psd < 12 and len(paras) > 3:
        flags.append(f"абзацы одного размера: разброс {psd:.0f} слов")
        score += 0.20
    if dup_open >= 3:
        flags.append(f"повторяющиеся зачины абзацев: {dup_open}")
        score += 0.20
    if list_lines / total_lines > 0.4:
        flags.append(f"списки съели {list_lines/total_lines:.0%} строк")
        score += 0.15
    dash = count_dashes(clean) / n * 1000
    own = (prof or {}).get("dash_1k") or 0
    limit = max(6.0, own * 2)     # своя норма важнее чужого абсолютного порога
    if dash > limit:
        base = f" (твоя норма {own:.0f})" if own else " (порог для русского — 6)"
        flags.append(f"плотность тире {dash:.1f} на 1000 слов{base}")
        score += 0.15
    return min(1.0, score), flags, {"sent_sd": round(sd, 2), "para_sd": round(psd, 1),
                                    "dash_1k": round(dash, 1), "dup_openings": dup_open}


def measure(text: str, profile_id="blog", profile=None) -> dict:
    prof = profile if profile is not None else load_profile(profile_id)
    v = style_vector(text)
    words = v["words"]
    hits = find_markers(text)
    marker_rate = len(hits) / max(1, words) * 1000
    marker_score = min(1.0, marker_rate / 6.0)          # 6 попаданий на 1000 слов = потолок
    struct_score, struct_flags, struct_raw = structure_report(text, prof)
    dist, dist_parts = voice_distance(v, prof) if prof else (0,{})
    # чем меньше — тем «свой»; веса: структура важнее лексики (корпусные данные)
    total = 0.25 * marker_score + 0.40 * struct_score + 0.35 * min(1.0, dist)
    return {
        "words": words,
        "score": round(total * 100) if words and prof else None,
        "status": "insufficient_text" if not words else "ok" if prof else "uncalibrated",
        "sample_warning": "Короткий текст: оценка может сильно колебаться." if 0 < words < 200 else None,
        "engine_version": ENGINE_VERSION,
        "profile_id": profile_id,
        "profile_label": PROFILES[profile_id],
        "profile_fingerprint": (prof or {}).get("fingerprint"),
        "calibration": (prof or {}).get("calibration"),
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "axes": {
            "маркеры": {"score": round(marker_score * 100), "hits": len(hits),
                        "per_1000": round(marker_rate, 2)},
            "структура": {"score": round(struct_score * 100), "flags": struct_flags, "raw": struct_raw},
            "голос": {"score": round(min(1.0, dist) * 100), "parts": dist_parts,
                      "corpus": (prof or {}).get("sources", []), "corpus_words": (prof or {}).get("words",0)},
        },
        "highlights": hits[:80],
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--profile", action="store_true", help="Пересобрать профиль")
    parser.add_argument("--voice", default="blog", choices=PROFILES)
    args = parser.parse_args()
    if args.profile:
        p = build_profile(args.voice)
        print(json.dumps({"ok": True, "corpus": p["sources"], "words": p["words"]}, ensure_ascii=False))
        return
    if not args.file:
        raise SystemExit("укажи файл или --profile")
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(measure(text, args.voice), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
