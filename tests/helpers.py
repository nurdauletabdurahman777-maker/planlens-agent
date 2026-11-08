from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "planlens_agent" / "templates" / "release_basic"
ACCEPTANCE = Path(__file__).resolve().parent / "acceptance"


def load_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = tomllib.loads((ACCEPTANCE / "cases.toml").read_text())
    return data["defaults"], data["cases"]


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "planlens_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


@pytest.fixture
def template() -> Path:
    return TEMPLATE


class Page(HTMLParser):
    """What a browser's parser would see: element names, script bodies, visible text."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs_seen: list[tuple[str, str | None]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self.text: list[str] = []
        self._in: dict[str, str | None] | None = None
        self._skip = 0
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attrs_seen += attrs
        if tag == "script":
            self._in = dict(attrs)
            self.scripts.append((self._in, ""))
        if tag in ("style", "title"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in = None
        if tag in ("style", "title"):
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in is not None:
            attrs, body = self.scripts[-1]
            self.scripts[-1] = (attrs, body + data)
        elif not self._skip:
            self.text.append(data)

    @property
    def visible(self) -> str:
        return "".join(self.text)

    def view(self) -> dict[str, Any]:
        [body] = [b for a, b in self.scripts if a.get("id") == "planlens-view"]
        return json.loads(body)
