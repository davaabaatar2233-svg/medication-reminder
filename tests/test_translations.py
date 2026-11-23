"""Every language must have every key, with the same {placeholders} and HTML tags as English."""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from services import translate  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")
TAG = re.compile(r"</?\w+>")


def load(lang):
    with open(os.path.join(ROOT, "languages", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


class Translations(unittest.TestCase):
    def test_every_language_complete_and_consistent(self):
        en = load("en")
        for lang in translate.LANGUAGES:
            # "date.*" keys are optional: only for languages the browser has no date names for.
            data = {k: v for k, v in load(lang).items() if not k.startswith("date.")}
            with self.subTest(lang=lang):
                self.assertEqual(sorted(set(en) - set(data)), [], f"{lang}: missing keys")
                self.assertEqual(sorted(set(data) - set(en)), [], f"{lang}: unknown keys")
                for key, text in en.items():
                    self.assertEqual(sorted(PLACEHOLDER.findall(data[key])), sorted(PLACEHOLDER.findall(text)),
                                     f"{lang}:{key} placeholders")
                    self.assertEqual(sorted(TAG.findall(data[key])), sorted(TAG.findall(text)), f"{lang}:{key} tags")
                    self.assertTrue(data[key].strip(), f"{lang}:{key} empty")

    def test_fallback_and_normalize(self):
        self.assertEqual(translate.normalize("ru-RU"), "ru")
        self.assertIsNone(translate.normalize("xx"))
        self.assertEqual(translate.t("xx", "fl.saved"), translate.t("en", "fl.saved"))
        self.assertIn("<script>", str(translate.t("en", "cg.remove_confirm", name="<script>")))
        self.assertNotIn("<script>", str(translate.t_html("en", "cg.remove_confirm", name="<script>")))


if __name__ == "__main__":
    unittest.main()
