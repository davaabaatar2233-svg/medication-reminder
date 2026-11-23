"""Translations for Sanakh.

All text lives in languages/<lang>.json as  "key": "Text with {placeholders}".
English (en.json) is the base: a key missing in another language falls back to English.
The same JSON files are used by Python (pages, alerts, algorithm reasons) and by
JavaScript (window.I18N), so every language says exactly the same thing everywhere.
"""
import json
import os

from markupsafe import Markup, escape

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "languages")
DEFAULT = "en"

# code -> (name in its own language, speech-synthesis language tag)
LANGUAGES = {
    "en": ("English", "en-US"),
    "mn": ("Монгол", "mn-MN"),
    "ru": ("Русский", "ru-RU"),
    "zh": ("中文", "zh-CN"),
    "ko": ("한국어", "ko-KR"),
    "ja": ("日本語", "ja-JP"),
    "es": ("Español", "es-ES"),
    "fr": ("Français", "fr-FR"),
    "de": ("Deutsch", "de-DE"),
}

_cache = {}


class _KeepMissing(dict):
    """format_map helper: leaves unknown {placeholders} untouched instead of failing."""
    def __missing__(self, key):
        return "{" + key + "}"


def normalize(lang):
    """'ru-RU' -> 'ru'; unknown or empty -> None."""
    code = (lang or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else None


def strings(lang):
    """All strings for a language, with English filling any gaps."""
    lang = normalize(lang) or DEFAULT
    if lang not in _cache:
        data = {}
        if lang != DEFAULT:
            data.update(strings(DEFAULT))
        with open(os.path.join(DIR, f"{lang}.json"), encoding="utf-8") as f:
            data.update(json.load(f))
        _cache[lang] = data
    return _cache[lang]


def t(lang, key, **params):
    """Plain-text translation, e.g. t('mn', 'fl.saved')."""
    text = strings(lang).get(key, key)
    return text.format_map(_KeepMissing(params)) if params else text


def t_html(lang, key, **params):
    """For templates: the translation may contain simple HTML (it is ours),
    but every parameter (names typed by users) is escaped."""
    text = strings(lang).get(key, key)
    safe = {k: escape(v) for k, v in params.items()}
    return Markup(text.format_map(_KeepMissing(safe)) if params else text)


def translator(lang):
    """A t(key, **params) function bound to one language (used by algorithm/rules.py)."""
    return lambda key, **params: t(lang, key, **params)


def speech_tag(lang):
    return LANGUAGES[normalize(lang) or DEFAULT][1]


def js_strings(lang):
    """JSON for embedding in a <script> tag."""
    return json.dumps(strings(lang), ensure_ascii=False).replace("</", "<\\/")
