"""Build a source review ZIP from an explicit allowlist, with a per-file manifest.

    python scripts/make_review_zip.py NAME

Writes dist/NAME.zip. Entries are stored with fixed timestamps, so rebuilding the
same files gives the same archive. Fails if a pattern matches nothing, or if an
excluded path would be included.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOW = [
    "A_REVIEW.md",
    "B1_REVIEW.md",
    "B2_REVIEW.md",
    "B2_R2_REVIEW.md",
    "B2_R3_REVIEW.md",
    ".github/workflows/*.yml",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    "LICENSE",
    "NOTICE",
    "AUTHORS.md",
    "PROVENANCE.md",
    "CHANGELOG.md",
    "docs/*.md",
    "examples/*.py",
    "examples/langgraph_gate/*",
    "scripts/*.py",
    "src/planlens_agent/*.py",
    "src/planlens_agent/py.typed",
    "src/planlens_agent/inspector/*.py",
    "src/planlens_agent/inspector/*.css",
    "src/planlens_agent/inspector/*.js",
    "src/planlens_agent/schemas/*.schema.json",
    "src/planlens_agent/templates/release_basic/*.md",
    "src/planlens_agent/templates/release_basic/*.toml",
    "src/planlens_agent/templates/release_basic/plans/*.json",
    "src/planlens_agent/templates/release_basic/candidates/*/*.json",
    "tests/*.py",
    "tests/acceptance/cases.toml",
    "tests/acceptance/contracts/*.toml",
    "tests/acceptance/plans/*.json",
    "tests/data/sarif/*",
    "review/b1/*.md",
    "review/b1/*.log",
    "review/b1/reports/*",
    "review/b1/screenshots/*.png",
    "review/b2/*",
    "review/b2r2/*",
    "review/b2r3/*",
]
FORBIDDEN = [
    ".git/*",
    ".venv/*",
    "*__pycache__*",
    "*.pyc",
    ".env",
    "*.env",
    ".env.*",
    "*.pem",
    "*.key",
    "dist/*",
    ".pytest_cache/*",
    ".ruff_cache/*",
    ".mypy_cache/*",
    "*.DS_Store",
]


def main(name: str) -> None:
    rels: set[str] = set()
    for pattern in ALLOW:
        hits = [p for p in ROOT.glob(pattern) if p.is_file()]
        if not hits:
            sys.exit(f"allowlist pattern matched nothing: {pattern}")
        rels |= {p.relative_to(ROOT).as_posix() for p in hits}
    for rel in sorted(rels):
        if any(fnmatch.fnmatch(rel, f) for f in FORBIDDEN):
            sys.exit(f"excluded path selected: {rel}")

    ordered = sorted(rels)
    manifest = {
        "package": name,
        "file_count": len(ordered),
        "files": {
            r: {
                "sha256": hashlib.sha256((ROOT / r).read_bytes()).hexdigest(),
                "bytes": (ROOT / r).stat().st_size,
            }
            for r in ordered
        },
    }
    out = ROOT / "dist" / f"{name}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:

        def add(rel: str, data: bytes) -> None:
            info = zipfile.ZipInfo(f"{name}/{rel}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)

        for rel in ordered:
            add(rel, (ROOT / rel).read_bytes())
        add(
            "REVIEW_ZIP_MANIFEST.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
    print(f"{out}\n{len(ordered)} files + REVIEW_ZIP_MANIFEST.json")


if __name__ == "__main__":
    main(sys.argv[1])
