"""Import explicitly selected author DOCX files into the private calibration corpus."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import config
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract(path):
    with ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
        # Only prose paragraphs in the document body: no tables, headers or slide files.
        paragraphs = ["".join(t.text or "" for t in p.iter(W + "t")).strip()
                      for p in body if p.tag == W + "p"]
        props = {e.tag.split("}")[-1]: e.text
                 for e in ET.fromstring(z.read("docProps/core.xml"))}
    kept = [p for p in paragraphs if p and not re.fullmatch(r"\d+[.)]?", p)]
    return "\n\n".join(kept) + "\n", props


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path,nargs='+',help='Your own handwritten DOCX files; verify authorship before importing')
    parser.add_argument('--profile',choices=['blog','talks'],default='talks')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    dest = args.output or config.path(config.load()['voice']['corpus_dir'])
    if args.profile=='talks':dest=dest/'talks'
    prepared, seen = [], set()
    for source in args.files:
        rel=source.name
        text, props = extract(source)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen:
            raise ValueError("Duplicate reviewed text: " + rel)
        seen.add(digest)
        words = len(re.findall(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z-]*", text))
        if words < 200:
            raise ValueError("Too little prose: " + rel)
        key=digest[:16]
        prepared.append((key + ".md", text, {"file": key + ".md", "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "text_sha256": digest, "words": words, "group": key,
            "genre": args.profile, "document_properties": props,
            "authorship_basis": "Файл явно выбран пользователем. Метаданные сами по себе не доказывают авторство.",
            "extraction": "Основные абзацы Word; без таблиц, колонтитулов и отдельных номеров слайдов. Авторские формулировки сохранены."}))
    dest.mkdir(parents=True, exist_ok=True)
    for filename, text, row in prepared:
        (dest / filename).write_text(text, encoding="utf-8")
    manifest = {"profile": args.profile, "label": "Личный корпус",
                "selection": "Явно выбранные пользователем файлы; ответственность за проверку авторства лежит на пользователе.",
                "sources": [row for _, _, row in prepared]}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"texts": len(prepared), "words": sum(r["words"] for _, _, r in prepared),
                      "sources": [{"file": r["file"], "words": r["words"]} for _, _, r in prepared]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
