# -*- coding: utf-8 -*-
"""Transcribe an audio note with the configured faster-whisper model cache."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import config as environment

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SETTINGS=environment.load()['speech']
MODEL_ROOT = environment.path(SETTINGS['model_dir'])
os.environ.setdefault("HF_HOME", str(MODEL_ROOT))
os.environ.setdefault("HF_HUB_OFFLINE", "1" if SETTINGS['offline'] else "0")

PROMPT = "Русская речь. Заметка редактора о тексте статьи."


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: transcribe.py file.wav")
    wav = sys.argv[1]
    from faster_whisper import WhisperModel
    last_err = None
    for name in SETTINGS['models']:
        try:
            model = WhisperModel(name, device="cpu", compute_type="int8",
                                 download_root=str(MODEL_ROOT))
            segments, _info = model.transcribe(
                wav, language=SETTINGS['language'], initial_prompt=PROMPT, vad_filter=True)
            segs = [{"start": round(s.start, 2), "end": round(s.end, 2),
                     "text": s.text.strip()} for s in segments if s.text.strip()]
            print(json.dumps({
                "text": " ".join(s["text"] for s in segs).strip(),
                "segments": segs,
                "model": name,
            }, ensure_ascii=False))
            return
        except Exception as e:  # модель не в кэше и т.п. — пробуем следующую
            last_err = e
    sys.exit(f"transcribe failed: {last_err}")


if __name__ == "__main__":
    main()
