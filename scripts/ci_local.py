"""Run a CI stage locally. .github/workflows/ci.yml calls this same script.

    python scripts/ci_local.py STAGE [--python VERSION] [--log FILE]

Stages: lint, test, package, journey, recipe, all. Needs uv on PATH. Virtual environments
and build output go to a temporary directory outside the checkout. Any failing command
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
RECIPE = ROOT / "examples" / "langgraph_gate"
BIN = "Scripts" if os.name == "nt" else "bin"
EXE = ".exe" if os.name == "nt" else ""


def sh(*argv: str | Path, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    command = [str(a) for a in argv]
    print("+", " ".join(command), flush=True)
    done = subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, check=False)
    if done.returncode != 0:
        sys.exit(done.returncode)


def lint(python: str, tmp: Path, log: Path | None) -> None:
    sh("uvx", RUFF, "format", "--check", ".")
    sh("uvx", RUFF, "check", ".")
    sh("uvx", MYPY, "--strict", "src/planlens_agent")
    sh("uvx", "--from", ACTIONLINT, "actionlint")


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
    wheel = build_wheel(tmp)
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        metadata = next(z.read(n).decode() for n in names if n.endswith(".dist-info/METADATA"))
    for wanted in ("schemas/run.v1.schema.json", "release_basic/suite.toml"):
        if not any(n.endswith(wanted) for n in names):
            sys.exit(f"wheel is missing {wanted}")
    if any("tests/data" in n or "langgraph" in n for n in names):
        sys.exit("wheel contains test data or the framework recipe")
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


STAGES = {"lint": lint, "test": test, "package": package, "journey": journey, "recipe": recipe}


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
