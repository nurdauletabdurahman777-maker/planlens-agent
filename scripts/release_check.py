"""Check one built wheel and sdist as a release pair, without rebuilding either.

    python scripts/release_check.py --wheel FILE --sdist FILE --sums FILE
                                    [--tag vX.Y.Z] [--python PYTHON]

Checks that the versions agree, that metadata, notices and dependency boundaries are as declared,
that the sdist holds only the anchored selection, that the wheel's code is the sdist's code, that
the PyPI description has no GitHub-relative content, and that the wheel installs outside the
checkout and runs the documented example. Writes SHA256SUMS for exactly this pair. Exits 1 on any
failure. pip is confined to the given wheel, so this says nothing about PyPI.

Standard library only. Development tooling: not part of the package.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.message import Message
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "nurdauletabdurahman777-maker/planlens-agent"
GITHUB = f"https://github.com/{SLUG}"
RAW = f"https://raw.githubusercontent.com/{SLUG}"
ALPHA = "Development Status :: 3 - Alpha"
BIN = "Scripts" if os.name == "nt" else "bin"
EXE = ".exe" if os.name == "nt" else ""
FENCE = re.compile(r"^```.*?^```", re.M | re.S)
TARGET = re.compile(r"\]\(([^)\s]+)|(?:href|src|srcset)=\"([^\"]+)\"")
MEDIA = (".gif", ".png", ".svg")
SAME_FIELDS = ("Name", "Version", "Summary", "Requires-Python", "License-Expression")


def description_problems(text: str, version: str, members: set[str]) -> list[str]:
    """What would break or mislead on PyPI, given the paths the sdist holds."""
    found = []
    prose = FENCE.sub("", text)
    found += [
        f"GitHub-only markup: <{t}" for t in ("picture", "source", "details") if f"<{t}" in prose
    ]
    for match in TARGET.finditer(prose):
        target = match.group(1) or match.group(2)
        if target.startswith("#"):
            continue
        if not target.startswith("https://"):
            found.append(f"not an absolute https link: {target}")
            continue
        for base in (RAW + "/", GITHUB + "/blob/"):
            if target.startswith(base):
                ref, _, path = target[len(base) :].partition("/")
                if ref != f"v{version}":
                    found.append(f"not bound to tag v{version}: {target}")
                elif path.partition("#")[0] not in members:
                    found.append(f"no such file in the sdist: {target}")
    return found


def tag_problem(tag: str | None, version: str) -> str | None:
    if tag is not None and tag != f"v{version}":
        return f"tag {tag} does not match version {version}"
    return None


def sdist_problems(names: set[str], allowed: set[str], prefix: str) -> list[str]:
    """Top-level entries outside the anchored selection, or files outside the root folder."""
    found = []
    for name in sorted(names):
        if not name.startswith(prefix + "/"):
            found.append(f"outside {prefix}/: {name}")
        elif name[len(prefix) + 1 :].split("/")[0] not in allowed:
            found.append(f"not selected: {name}")
    return found


def source_versions() -> dict[str, str]:
    code = (ROOT / "src" / "planlens_agent" / "_version.py").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = next(p["version"] for p in lock["package"] if p["name"] == project["name"])
    found = re.search(r'__version__ = "([^"]+)"', code)
    assert found, "_version.py"
    return {"_version.py": found.group(1), "pyproject.toml": project["version"], "uv.lock": locked}


def metadata_problems(meta: Message, project: dict, version: str) -> list[str]:
    """Where the built metadata differs from what the project declares."""
    wanted = {
        "Name": project["name"],
        "Version": version,
        "Requires-Python": project["requires-python"],
        "License-Expression": project["license"],
        "Description-Content-Type": "text/markdown",
        "Author": ", ".join(a["name"] for a in project["authors"]),
        "Maintainer": ", ".join(a["name"] for a in project["maintainers"]),
    }
    found = [f"{k}: {meta[k]!r}, wanted {v!r}" for k, v in wanted.items() if meta[k] != v]
    classifiers = meta.get_all("Classifier") or []
    if ALPHA not in classifiers or any("Pre-Alpha" in c for c in classifiers):
        found.append(f"maturity classifier: {classifiers}")
    if sorted(meta.get_all("License-File") or []) != sorted(project["license-files"]):
        found.append(f"License-File: {meta.get_all('License-File')}")
    required = [r for r in meta.get_all("Requires-Dist") or [] if "extra ==" not in r]
    if required:
        found.append(f"runtime dependencies: {required}")
    urls = meta.get_all("Project-URL") or []
    for label, url in project["urls"].items():
        if f"{label}, {url}" not in urls:
            found.append(f"Project-URL missing: {label}")
    return found


def run_installed(wheel: Path, version: str, python: str) -> list[str]:
    """Install only this wheel in a new environment outside the checkout, and run the example."""
    with tempfile.TemporaryDirectory(prefix="planlens-release-") as directory:
        tmp = Path(directory).resolve()
        links = tmp / "links"
        links.mkdir()
        shutil.copy(wheel, links)
        subprocess.run([python, "-m", "venv", str(tmp / "env")], check=True, cwd=tmp)
        py, cli = tmp / "env" / BIN / f"python{EXE}", tmp / "env" / BIN / f"planlens-agent{EXE}"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PIP_"))}
        env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1")
        spec = f"planlens-agent=={version}"
        install = [str(py), "-m", "pip", "install", "--no-index", "--find-links", str(links), spec]
        done = subprocess.run(install, cwd=tmp, env=env, capture_output=True, text=True)
        if done.returncode:
            return [f"pip install failed: {done.stdout[-400:]}{done.stderr[-400:]}"]

        def tool(*argv: str, cwd: Path = tmp) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(cli), *argv], cwd=cwd, env=env, capture_output=True, text=True
            )

        found = []
        where = subprocess.run(
            [str(py), "-c", "import planlens_agent; print(planlens_agent.__file__)"],
            cwd=tmp, env=env, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip
        if not where or not Path(where).is_relative_to(tmp / "env"):
            found.append(f"imported from {where!r}, not the new environment")
        shown = tool("--version").stdout.strip()
        if shown != f"planlens-agent {version}":
            found.append(f"--version printed {shown!r}")
        if tool("init", "release-basic", "demo").returncode:
            found.append("init failed")
        check = tool("check", "plans/artifact-mismatch.json", cwd=tmp / "demo")
        if check.returncode != 1 or "plan/artifact-identity" not in check.stdout:
            found.append(f"example: exit {check.returncode}: {check.stdout[-300:]}")
        return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--sdist", required=True, type=Path)
    parser.add_argument("--sums", required=True, type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--python", default=sys.executable)
    a = parser.parse_args()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = source_versions()
    version = declared["pyproject.toml"]
    root = f"planlens_agent-{version}"
    problems = [
        f"{k} says {v}, pyproject.toml {version}" for k, v in declared.items() if v != version
    ]
    problem = tag_problem(a.tag, version)
    problems += [problem] if problem else []
    if a.wheel.name != f"{root}-py3-none-any.whl":
        problems.append(f"wheel filename: {a.wheel.name}")
    if a.sdist.name != f"{root}.tar.gz":
        problems.append(f"sdist filename: {a.sdist.name}")
    if problems:
        print("\n".join(f"FAIL: {p}" for p in problems))
        return 1

    with zipfile.ZipFile(a.wheel) as z:
        wheel = {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
    with tarfile.open(a.sdist) as t:
        sdist = {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}  # type: ignore[union-attr]

    info = f"{root}.dist-info"
    meta = Parser().parsestr(wheel[f"{info}/METADATA"].decode())
    pkg = Parser().parsestr(sdist[f"{root}/PKG-INFO"].decode())
    problems += metadata_problems(meta, project, version)
    problems += metadata_problems(pkg, project, version)
    problems += [f"wheel and sdist differ: {k}" for k in SAME_FIELDS if meta[k] != pkg[k]]

    description = meta.get_payload()
    readme = (ROOT / project["readme"]).read_text(encoding="utf-8")
    if not isinstance(description, str) or description.strip() != readme.strip():
        problems.append(f"the wheel's description is not {project['readme']}")
    if pkg.get_payload() != description:
        problems.append("wheel and sdist descriptions differ")
    members = {n[len(root) + 1 :] for n in sdist}
    problems += description_problems(readme, version, members)

    for name in project["license-files"]:
        expected = (ROOT / name).read_bytes()
        if wheel.get(f"{info}/licenses/{name}") != expected:
            problems.append(f"wheel {name} differs from the source")
        if sdist.get(f"{root}/{name}") != expected:
            problems.append(f"sdist {name} differs from the source")
    build = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]
    selection = build["build"]["targets"]["sdist"]["include"]
    if not all(p.startswith("/") for p in selection):
        problems.append("the sdist selection is not anchored")
    allowed = {p.strip("/") for p in selection} | {"PKG-INFO", ".gitignore"}
    problems += sdist_problems(set(sdist), allowed, root)
    for wanted in (f"{root}/README.pypi.md", f"{root}/README.md", f"{root}/pyproject.toml"):
        if wanted not in sdist:
            problems.append(f"sdist is missing {wanted}")

    code = {n for n in wheel if n.startswith("planlens_agent/")}
    if not code:
        problems.append("the wheel has no package code")
    for name in sorted(code):
        if sdist.get(f"{root}/src/{name}") != wheel[name]:
            problems.append(f"wheel and sdist code differ: {name}")
    for name in wheel:
        unexpected = not name.startswith(("planlens_agent/", f"{info}/"))
        if unexpected or name.endswith(MEDIA) or "langgraph" in name or "tests/" in name:
            problems.append(f"unexpected in wheel: {name}")
    problems += run_installed(a.wheel, version, a.python)

    if problems:
        print("\n".join(f"FAIL: {p}" for p in problems))
        return 1
    pair = sorted((a.wheel, a.sdist), key=lambda p: p.name)
    a.sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in pair), encoding="utf-8")
    print(f"release pair {version}: all checks passed")
    print(a.sums.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
