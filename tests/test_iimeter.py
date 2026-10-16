import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import iimeter as meter


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        (self.corpus / "one.md").write_text("Я пишу сам. Мне нравится писать. " * 50, encoding="utf-8")
        (self.corpus / "two.md").write_text("Сегодня я сделал программу. Потом проверил её. " * 50, encoding="utf-8")
        for name, value in [("CORPUS_DIR", self.corpus), ("PROFILE", self.root / "profile.json")]:
            p = patch.object(meter, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_added_text_rebuilds_profile(self):
        a = meter.load_profile()
        (self.corpus / "three.md").write_text("Новый текст. " * 120, encoding="utf-8")
        b = meter.load_profile()
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])
        self.assertGreater(b["words"], a["words"])
        self.assertEqual(len(b["calibration"]["samples"]), 3)

    def test_profiles_stay_separate(self):
        talks = self.corpus / "talks"
        talks.mkdir()
        (talks / "speech.md").write_text("Уважаемые коллеги. Доклад готов. " * 80, encoding="utf-8")
        self.assertEqual(meter.load_profile("talks")["sources"], ["speech.md"])
        self.assertEqual(len(meter.load_profile("blog")["sources"]), 2)

    def test_code_markers_do_not_change_score(self):
        plain = "Я написал программу. Вечером проверил, утром исправил. " * 40
        code = "\n\n```text\nВ современном мире важно отметить: инновационные решения.\n```"
        a, b = meter.measure(plain), meter.measure(plain + code)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["words"], b["words"])
        self.assertEqual(a["highlights"], b["highlights"])

    def test_mask_retains_offsets_with_emoji_and_links(self):
        text = '😀 [ссылка](https://example.com/)\n\n```\nважно отметить\n```\n\nВажно отметить это.'
        masked = meter.strip_markup(text)
        self.assertEqual(len(masked), len(text))
        hits = meter.find_markers(text)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["start"], text.rindex("Важно"))
        self.assertEqual(text[hits[0]["start"]:hits[0]["end"]], hits[0]["quote"])

    def test_empty_text_has_no_score(self):
        for text in ("", "```\nВ современном мире\n```", "---\ntitle: важно отметить\n---\n"):
            with self.subTest(text=text):
                result = meter.measure(text)
                self.assertIsNone(result["score"])
                self.assertEqual(result["status"], "insufficient_text")


if __name__ == "__main__":
    unittest.main()
