"""Dashboard translations: every text passed to t() / tr() / msg() exists in every language."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
LOCALES = ("fr", "es", "de")
CALL_RE = re.compile(r'\b(?:t|tr|msg)\(\s*"((?:[^"\\]|\\.)*)"')
VAR_RE = re.compile(r"\{(\w+)\}")


def source_keys() -> list[str]:
    keys: list[str] = []
    files = [*WEB.glob("app/**/*.tsx"), *WEB.glob("components/*.tsx"), *WEB.glob("lib/*.ts"), *WEB.glob("lib/*.tsx")]
    for path in sorted(files):
        for m in CALL_RE.finditer(path.read_text(encoding="utf-8")):
            key = json.loads(f'"{m.group(1)}"')
            if key not in keys:
                keys.append(key)
    return keys


def load(lang: str) -> dict[str, str]:
    return json.loads((WEB / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


def test_keys_are_found():
    keys = source_keys()
    assert "Deploy Supabase" in keys and "{n} editor(s)" in keys and len(keys) > 300


@pytest.mark.parametrize("lang", LOCALES)
def test_every_text_is_translated(lang):
    table = load(lang)
    missing = [k for k in source_keys() if not table.get(k)]
    assert not missing, f"{lang}: {len(missing)} missing, e.g. {missing[:10]}"


@pytest.mark.parametrize("lang", LOCALES)
def test_variables_are_kept(lang):
    table = load(lang)
    wrong = [k for k, v in table.items() if set(VAR_RE.findall(k)) != set(VAR_RE.findall(v))]
    assert not wrong, f"{lang}: placeholders differ in {wrong[:10]}"


if __name__ == "__main__":  # list the texts that still need a translation
    lang = sys.argv[1] if len(sys.argv) > 1 else "fr"
    have = load(lang) if (WEB / "locales" / f"{lang}.json").exists() else {}
    print(json.dumps([k for k in source_keys() if k not in have], ensure_ascii=False, indent=1))
