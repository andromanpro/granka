from copy import deepcopy
import unittest
from tools.text_edit import edit_text


def document(text="Начало.\n\nМой голос.\n\nКонец."):
    start = text.index("Мой голос")
    return {"status": "ready", "versions": [{"id": "v1", "text": text}], "annotations": [
        {"id": "a1", "version": "v1", "type": "candidate", "start": start,
         "end": start + len("Мой голос"), "quote": "Мой голос",
         "decision": {"action": "reject", "reason": "voice"}}]}


class TextEditTests(unittest.TestCase):
    def test_insertion_preserves_history_and_unicode_offsets(self):
        data = document()
        old = deepcopy(data)
        text = "😀 Вступление.\n\n" + data["versions"][0]["text"]
        out = edit_text(data, "v1", text)
        self.assertEqual(data["versions"][0], old["versions"][0])
        self.assertEqual(data["annotations"][0], old["annotations"][0])
        ann = data["annotations"][1]
        self.assertEqual(text[ann["start"]:ann["end"]], "Мой голос")
        self.assertEqual(ann["decision"], old["annotations"][0]["decision"])
        self.assertEqual(ann["source_annotation"], "a1")
        self.assertNotEqual(ann["id"], "a1")
        self.assertEqual(out["version"], "v2")
        self.assertEqual(data["status"], "draft")

    def test_changed_annotation_stays_in_previous_version(self):
        data = document()
        result = edit_text(data, "v1", data["versions"][0]["text"].replace("Мой голос", "Мой текст"))
        self.assertEqual(result["retired"], 1)
        self.assertEqual(len(data["annotations"]), 1)
        self.assertEqual(data["versions"][-1]["retired_annotations"], ["a1"])

    def test_change_earlier_in_same_line_transfers_annotation(self):
        data = document("Начало. Мой голос. Конец.")
        result = edit_text(data, "v1", "Другое начало. Мой голос. Конец.")
        self.assertEqual(result["transferred"], 1)
        a = data["annotations"][-1]
        self.assertEqual(data["versions"][-1]["text"][a["start"]:a["end"]], "Мой голос")

    def test_newlines_and_markdown_are_exact(self):
        data = document()
        text = "# Заголовок\n\n- пункт 1\n- пункт 2\n\n```bsl\nСообщить(1);\n```\n"
        edit_text(data, "v1", text)
        self.assertEqual(data["versions"][-1]["text"], text)

    def test_no_change_does_not_create_version(self):
        data = document()
        original = deepcopy(data)
        self.assertTrue(edit_text(data, "v1", data["versions"][0]["text"])["unchanged"])
        self.assertEqual(data, original)

    def test_stale_base_and_invalid_text_leave_data_unchanged(self):
        for base, text in [("v0", "Правка"), ("v1", "  "), ("v1", None), ("v1", "x" * 300_001)]:
            with self.subTest(base=base, size=len(text) if isinstance(text, str) else None):
                data = document()
                original = deepcopy(data)
                with self.assertRaises(ValueError):
                    edit_text(data, base, text)
                self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
