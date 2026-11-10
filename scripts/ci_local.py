"""Run a CI stage locally. .github/workflows/ci.yml calls this same script.

    python scripts/ci_local.py STAGE [--python VERSION] [--log FILE]

Stages: lint, test, package, journey, recipe, pilot, docs, all. Needs uv on PATH. Virtual
environments and build output go to a temporary directory outside the checkout. Any failing command
stops the stage with its exit status.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUFF = "ruff@0.17.4"
MYPY = "mypy@2.4.1"
ACTIONLINT = "actionlint-py==1.7.12.25"
# actionlint runs ShellCheck on workflow scripts only when it can find it, and silently skips it
# otherwise. The lint stage provisions this exact version and proves it reaches the result.
SHELLCHECK = "shellcheck-py==0.11.0.1"
SHELLCHECK_VERSION = "0.11.0"
SHELLCHECK_CANARY = """on: push
jobs:
  canary:
    runs-on: ubuntu-latest
    steps:
      - run: |
          if true; then echo unterminated
"""
TWINE = "twine==7.0.0"
README_RENDERER = "readme-renderer[md]==46.0"
RECIPE = ROOT / "examples" / "langgraph_gate"
PILOT_TRIAL = ROOT / "pilot" / "planner-trial"
BIN = "Scripts" if os.name == "nt" else "bin"
EXE = ".exe" if os.name == "nt" else ""


def sh(*argv: str | Path, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    command = [str(a) for a in argv]
    print("+", " ".join(command), flush=True)
    done = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, check=False)
    if done.returncode != 0:
        sys.exit(done.returncode)


def pinned_shellcheck() -> str:
    """The provisioned ShellCheck executable, of exactly the pinned version, or the stage stops."""
    found = subprocess.run(
        ["uvx", "--from", SHELLCHECK, "python", "-c",
         "import pathlib, sys; print(pathlib.Path(sys.executable).with_name('shellcheck'))"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    path = Path(found.stdout.strip() + EXE)
    if found.returncode or not path.is_file():
        sys.exit(f"lint: could not provision ShellCheck from {SHELLCHECK}")
    shown = subprocess.run([path, "--version"], capture_output=True, text=True, check=False).stdout
    if f"version: {SHELLCHECK_VERSION}" not in shown.splitlines():
        sys.exit(f"lint: {path} is not ShellCheck {SHELLCHECK_VERSION}")
    return str(path)


def lint(python: str, tmp: Path, log: Path | None) -> None:
    sh("uvx", RUFF, "format", "--check", ".")
    sh("uvx", RUFF, "check", ".")
    sh("uvx", MYPY, "--strict", "src/planlens_agent")
    shellcheck = pinned_shellcheck()
    # A known shell error must come back from actionlint; otherwise shell checks are not running.
    canary = tmp / "shellcheck-canary.yml"
    canary.write_text(SHELLCHECK_CANARY, encoding="utf-8")
    probe = subprocess.run(
        ["uvx", "--from", ACTIONLINT, "actionlint", f"-shellcheck={shellcheck}", str(canary)],
        cwd=tmp, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if probe.returncode == 0 or "shellcheck reported issue" not in probe.stdout:
        print(probe.stdout + probe.stderr)
        sys.exit(
            "lint: actionlint did not report the canary's ShellCheck error, "
            "so shell checks are not running"
        )
    print(f"+ ShellCheck {SHELLCHECK_VERSION} reaches actionlint (canary reported)", flush=True)
    sh("uvx", "--from", ACTIONLINT, "actionlint", f"-shellcheck={shellcheck}")


def test(python: str, tmp: Path, log: Path | None) -> None:
    sh(
        "uv", "run", "--locked", "--extra", "dev", "--python", python, "pytest", "tests", "-q",
        env={"UV_PROJECT_ENVIRONMENT": str(tmp / "project-env")},
    )  # fmt: skip


def build_wheel(tmp: Path) -> Path:
    out = tmp / "dist"
    sh("uv", "build", "--wheel", "--out-dir", out)
    (wheel,) = out.glob("*.whl")
    return wheel


def wheel_env(python: str, tmp: Path, wheel: Path, name: str) -> Path:
    env = tmp / name
    sh("uv", "venv", "-q", "--python", python, env)
    sh("uv", "pip", "install", "-q", "--python", env / BIN / f"python{EXE}", "--no-deps", wheel)
    return env


def package(python: str, tmp: Path, log: Path | None) -> None:
    out = tmp / "dist"
    sh("uv", "build", "--out-dir", out)
    (wheel,), (sdist,) = out.glob("*.whl"), out.glob("*.tar.gz")
    found = subprocess.run(
        ["uv", "python", "find", "--no-project", python],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    sh(
        sys.executable, ROOT / "scripts" / "release_check.py", "--wheel", wheel, "--sdist", sdist,
        "--sums", tmp / "SHA256SUMS", "--python", found.stdout.strip(), cwd=tmp,
    )  # fmt: skip
    sh(
        "uvx",
        "--from",
        TWINE,
        "--with",
        README_RENDERER,
        "twine",
        "check",
        "--strict",
        wheel,
        sdist,
    )
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        metadata = next(z.read(n).decode() for n in names if n.endswith(".dist-info/METADATA"))
    for wanted in ("schemas/run.v1.schema.json", "release_basic/suite.toml"):
        if not any(n.endswith(wanted) for n in names):
            sys.exit(f"wheel is missing {wanted}")
    if any("tests/data" in n or "langgraph" in n for n in names):
        sys.exit("wheel contains test data or the framework recipe")
    if any(n.endswith((".gif", ".png", ".svg")) or "docs/" in n or "scripts/" in n for n in names):
        sys.exit("wheel contains media or development tooling")
    required = [x for x in metadata.splitlines() if x.startswith("Requires-Dist:")]
    if [x for x in required if "extra ==" not in x]:
        sys.exit(f"wheel has runtime dependencies: {required}")

    env = wheel_env(python, tmp, wheel, "wheel-env")
    py = env / BIN / f"python{EXE}"
    locked = tmp / "dev-locked.txt"
    sh("uv", "export", "-q", "--locked", "--extra", "dev", "--no-emit-project", "-o", locked)
    sh("uv", "pip", "install", "-q", "--python", py, "--require-hashes", "-r", locked)
    where = subprocess.run(
        [py, "-c", "import planlens_agent; print(planlens_agent.__file__)"],
        cwd=tmp, capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    if not Path(where).is_relative_to(env):
        sys.exit(f"tests would import {where}, not the installed wheel")
    sh(py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
       env={"PYTHONDONTWRITEBYTECODE": "1"})  # fmt: skip


def journey(python: str, tmp: Path, log: Path | None) -> None:
    env = wheel_env(python, tmp, build_wheel(tmp), "journey-env")
    args: list[str | Path] = [
        env / BIN / f"python{EXE}", ROOT / "scripts" / "journey.py",
        "--cli", env / BIN / f"planlens-agent{EXE}", "--work", tmp / "journey",
    ]  # fmt: skip
    if log:
        args += ["--log", log]
    sh(*args, cwd=tmp)


def recipe(python: str, tmp: Path, log: Path | None) -> None:
    env = wheel_env(python, tmp, build_wheel(tmp), "recipe-env")
    py = env / BIN / f"python{EXE}"
    sh("uv", "pip", "install", "-q", "--python", py, "--require-hashes",
       "-r", RECIPE / "requirements.lock")  # fmt: skip
    sh(py, "-m", "pytest", RECIPE / "test_gate.py", "-q", "-p", "no:cacheprovider",
       cwd=tmp, env={"PYTHONDONTWRITEBYTECODE": "1"})  # fmt: skip
    args: list[str | Path] = [
        py, ROOT / "scripts" / "journey.py",
        "--cli", env / BIN / f"planlens-agent{EXE}", "--work", tmp / "recipe-journey",
        "--gate-python", py, "--gate-script", RECIPE / "gate.py",
    ]  # fmt: skip
    if log:
        args += ["--log", log]
    sh(*args, cwd=tmp)


def pilot(python: str, tmp: Path, log: Path | None) -> None:
    env = wheel_env(python, tmp, build_wheel(tmp), "pilot-env")
    py = env / BIN / f"python{EXE}"
    sh("uv", "pip", "install", "-q", "--python", py, "--require-hashes",
       "-r", RECIPE / "requirements.lock")  # fmt: skip
    sh(py, "-m", "pytest", PILOT_TRIAL / "test_capture_trial.py", "-v", "-p", "no:cacheprovider",
       cwd=tmp, env={"PYTHONDONTWRITEBYTECODE": "1"})  # fmt: skip


def docs(python: str, tmp: Path, log: Path | None) -> None:
    wheel = build_wheel(tmp)
    found = subprocess.run(
        ["uv", "python", "find", "--no-project", python],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    quickstart = ROOT / "docs" / "quickstart.md"

    def check(*documents: Path, work: str) -> list[str | Path]:
        return [
            sys.executable, ROOT / "scripts" / "docs_check.py", *documents,
            "--python", found.stdout.strip(), "--work", tmp / work,
            "--wheel", wheel, "--checkout", ROOT,
        ]  # fmt: skip

    # The release notes must stay verbatim, so a copy carries the annotations the runner needs.
    (notes,) = (ROOT / "release").glob("NOTES_*.md")
    note_text = notes.read_text(encoding="utf-8")
    if note_text.count("```sh\n") != 1:
        sys.exit("the release notes no longer have exactly one command block")
    runnable = tmp / "release-notes.md"
    runnable.write_text(
        "<!-- run -->\n```sh\npython3 -m venv planlens-env\n. planlens-env/bin/activate\n```\n\n"
        + note_text.replace(
            "```sh\n",
            '<!-- run exit=1 contains="REJECTED" contains="plan/artifact-identity" -->\n```sh\n',
        ),
        encoding="utf-8",
    )
    readme = ROOT / "README.md"
    pypi = ROOT / "README.pypi.md"
    args = check(readme, pypi, quickstart, ROOT / "CONTRIBUTING.md", runnable, work="docs")
    if log:
        args += ["--log", log]
    sh(*args, cwd=tmp)

    # Negative control: a missing script in the middle of a block must fail the real quickstart.
    text = quickstart.read_text(encoding="utf-8")
    if "examples/sdk_check.py" not in text:
        sys.exit("negative control: the quickstart no longer runs examples/sdk_check.py")
    broken = tmp / "quickstart-missing-sdk.md"
    broken.write_text(text.replace("examples/sdk_check.py", "examples/definitely-missing-sdk.py"))
    command = [str(a) for a in check(broken, work="docs-negative")]
    print("+ negative control:", " ".join(command), flush=True)
    done = subprocess.run(command, cwd=tmp, capture_output=True, text=True, check=False)
    if done.returncode != 1 or "definitely-missing-sdk.py" not in done.stdout:
        print(done.stdout)
        sys.exit(
            "negative control failed: a missing SDK script did not fail the documentation check"
        )
    print("negative control: a missing SDK script fails the documentation check", flush=True)


STAGES = {
    "lint": lint,
    "test": test,
    "package": package,
    "journey": journey,
    "recipe": recipe,
    "pilot": pilot,
    "docs": docs,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=[*STAGES, "all"])
    parser.add_argument("--python", default=sysconfig.get_python_version())
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    log = args.log.resolve() if args.log else None
    names = list(STAGES) if args.stage == "all" else [args.stage]
    for name in names:
        print(f"=== {name} (Python {args.python})", flush=True)
        with tempfile.TemporaryDirectory(prefix="planlens-ci-") as directory:
            STAGES[name](args.python, Path(directory).resolve(), log)
    print(f"stages passed: {', '.join(names)}")


if __name__ == "__main__":
    main()
