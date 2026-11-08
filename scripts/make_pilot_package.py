"""Build the private pilot ZIP from the committed tree, with a per-file manifest.

    python scripts/make_pilot_package.py [--output FILE] [--replace]

Needs uv. Fails if tracked files have uncommitted changes, so the package always corresponds to
one commit. Nothing is uploaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "planlens-agent-pilot"
FILES = {
    "pilot/README.md": "README.md",
    "pilot/TESTER_GUIDE.md": "TESTER_GUIDE.md",
    "pilot/FEEDBACK_FORM.md": "FEEDBACK_FORM.md",
    "LICENSE": "LICENSE",
    "NOTICE": "NOTICE",
    "AUTHORS.md": "AUTHORS.md",
    "examples/sdk_check.py": "examples/sdk_check.py",
    "examples/guarded_runner.py": "examples/guarded_runner.py",
    "examples/langgraph_gate/README.md": "examples/langgraph_gate/README.md",
    "examples/langgraph_gate/gate.py": "examples/langgraph_gate/gate.py",
    "examples/langgraph_gate/requirements.in": "examples/langgraph_gate/requirements.in",
    "examples/langgraph_gate/requirements.lock": "examples/langgraph_gate/requirements.lock",
    "pilot/planner-trial/README.md": "planner-trial/README.md",
    "pilot/planner-trial/capture_trial.py": "planner-trial/capture_trial.py",
    "pilot/planner-trial/planner_info.template.json": "planner-trial/planner_info.template.json",
    **{
        f"pilot/planner-trial/first-trial/{name}": f"planner-trial/first-trial/{name}"
        for name in (
            "README.md",
            "request.txt",
            "contract.toml",
            "catalog.toml",
            "planner_info.json",
            "FROZEN.sha256",
        )
    },
}


def run(*argv: str | Path, cwd: Path = ROOT) -> bytes:
    return subprocess.run([str(a) for a in argv], cwd=cwd, check=True, capture_output=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "planlens-agent-private-pilot.zip",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("tracked files have uncommitted changes; commit them first")
    if args.output.exists() and not args.replace:
        raise SystemExit(f"{args.output} exists; pass --replace to rebuild it")
    commit = run("git", "rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="planlens-pilot-") as directory:
        tmp = Path(directory)
        export = tmp / "export"
        export.mkdir()
        tar = tmp / "tree.tar"
        run("git", "archive", "--format=tar", "-o", tar, commit)
        run("tar", "-x", "-f", tar, "-C", export)
        run("uv", "build", "--wheel", "--out-dir", tmp / "wheel", cwd=export)
        (wheel,) = (tmp / "wheel").glob("*.whl")
        content = {to: (export / source).read_bytes() for source, to in FILES.items()}
        content[f"wheel/{wheel.name}"] = wheel.read_bytes()
    content["BUILD_INFO.json"] = (
        json.dumps(
            {
                "commit": commit,
                "wheel": wheel.name,
                "wheel_sha256": hashlib.sha256(content[f"wheel/{wheel.name}"]).hexdigest(),
                "contents": "authored examples only; no model output",
            },
            indent=2,
        )
        + "\n"
    ).encode()
    manifest = "".join(
        f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(content.items())
    )
    content["MANIFEST.sha256"] = manifest.encode()
    args.output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    print(args.output)
    print(hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
