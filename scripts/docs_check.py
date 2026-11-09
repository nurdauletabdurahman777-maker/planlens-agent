"""Run the copy-and-run blocks of Markdown documents, exactly as written.

    python scripts/docs_check.py DOC [DOC...] --python PYTHON --work DIR
                                 [--wheel FILE --checkout DIR] [--shell bash]
                                 [--deny-network] [--log FILE]

Every ```sh block is copy-and-run and must be annotated on the line before it:

    <!-- run exit=1 contains="RESULT: REJECTED" -->

`prereq` marks steps that need network access and repository credentials. With --wheel they
are skipped, and the wheel and examples they would have produced are put in place. `online`
marks later steps that need the network. With --deny-network (macOS) everything else runs with
network access denied. All blocks except `prereq` run with an empty environment: only HOME, PATH
holding the chosen Python and the system directories, and TERM.

Standard library only. Development tooling: not part of the package.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BLOCK = re.compile(
    r"(?:<!--\s*run\b(?P<attrs>[^\n]*?)-->[ \t]*\n)?```sh\n(?P<body>.*?)^```", re.M | re.S
)
CONTAINS = re.compile(r'contains="([^"]*)"')
SANDBOX = "(version 1)(allow default)(deny network*)"


@dataclass
class Block:
    body: str
    annotated: bool
    exit: int = 0
    contains: list[str] = field(default_factory=list)
    kind: str = "normal"


def parse(text: str) -> list[Block]:
    blocks = []
    for match in BLOCK.finditer(text):
        attrs = match.group("attrs")
        block = Block(match.group("body"), attrs is not None)
        if attrs:
            found = re.search(r"\bexit=(\d+)", attrs)
            block.exit = int(found.group(1)) if found else 0
            block.contains = CONTAINS.findall(attrs)
            flags = CONTAINS.sub("", attrs)
            block.kind = (
                "prereq" if "prereq" in flags else "online" if "online" in flags else "normal"
            )
        blocks.append(block)
    return blocks


def problems(blocks: list[Block]) -> list[str]:
    """Rules that keep a block pasteable: annotated, and no comments zsh would run."""
    found = []
    for block in blocks:
        first = block.body.splitlines()[0]
        if not block.annotated:
            found.append(f"not annotated: {first}")
        in_heredoc = False
        for line in block.body.splitlines():
            if in_heredoc:
                in_heredoc = line != "EOF"
                continue
            in_heredoc = "<<'EOF'" in line
            if line.startswith("#") or " #" in line or line.startswith("$ "):
                found.append(f"not pasteable: {line}")
    return found


def run_doc(doc: Path, a: argparse.Namespace, say: list[str]) -> int:
    blocks = parse(doc.read_text("utf-8"))
    bad = problems(blocks)
    if bad:
        say.extend(f"INVALID {doc.name}: {item}" for item in bad)
        return len(bad)
    work = a.work / doc.stem
    work.mkdir(parents=True)
    home = work / ".home"
    home.mkdir()
    tools = work / ".python"
    tools.mkdir()
    for name in ("python3", "python"):
        (tools / name).symlink_to(Path(a.python).resolve())
    if a.wheel:
        (work / "wheelhouse").mkdir()
        shutil.copy(a.wheel, work / "wheelhouse")
        shutil.copytree(a.checkout / "examples", work / "planlens-agent" / "examples")
        blocks = [b for b in blocks if b.kind != "prereq"]
    minimal = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "TERM": "dumb"}

    # Consecutive blocks of one kind share a shell; the working directory and environment
    # carry over, as they would for a person working in one terminal.
    state, cwd, unmet, position = work / ".state", work / ".cwd", 0, 0
    cwd.write_text(str(work))
    while position < len(blocks):
        kind = blocks[position].kind
        group = []
        while position < len(blocks) and blocks[position].kind == kind:
            group.append(blocks[position])
            position += 1
        lines = [f'cd "$(cat "{cwd}")"', f'[ -f "{state}" ] && . "{state}"']
        base = position - len(group)
        for offset, block in enumerate(group):
            n = base + offset
            lines.append(f'{{\n{block.body}}} > "{work}/.out{n}" 2>&1; echo $? > "{work}/.code{n}"')
        lines += [f'export -p > "{state}"', f'pwd > "{cwd}"']
        script = work / f".phase{base}.sh"
        script.write_text("\n".join(lines) + "\n")
        env = dict(os.environ) if kind == "prereq" else minimal
        argv = [a.shell, str(script)]
        if a.deny_network and kind == "normal":
            argv = ["sandbox-exec", "-p", SANDBOX, *argv]
        subprocess.run(argv, env=env, cwd=work, check=False)
        for offset, block in enumerate(group):
            n = base + offset
            code_file = work / f".code{n}"
            code = int(code_file.read_text()) if code_file.exists() else -1
            output = (work / f".out{n}").read_text("utf-8", "replace")
            ok = code == block.exit
            missing = [c for c in block.contains if c not in output]
            unmet += (not ok) + len(missing)
            say.append(
                f"[{kind}] exit {code}" + ("" if ok else f" UNEXPECTED, wanted {block.exit}")
            )
            say.extend(f"  $ {line}" for line in block.body.splitlines()[:40])
            say.extend(f"  found: {c}" for c in block.contains if c in output)
            say.extend(f"  MISSING: {c}" for c in missing)
            if missing or not ok:
                say.append("  output: " + output[-1200:])
    return unmet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docs", nargs="+", type=Path)
    parser.add_argument("--python", required=True)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--shell", default="bash")
    parser.add_argument("--deny-network", action="store_true")
    parser.add_argument("--log", type=Path)
    a = parser.parse_args()
    a.work = a.work.resolve()
    unmet = 0
    say: list[str] = []
    for doc in a.docs:
        say.append(f"=== {doc}")
        unmet += run_doc(doc, a, say)
    say.append(f"unmet expectations: {unmet}")
    text = "\n".join(say).replace(str(a.work), "<work>").replace(str(Path.home()), "<home>")
    print(text)
    if a.log:
        a.log.write_text(text + "\n", encoding="utf-8")
    return 1 if unmet else 0


if __name__ == "__main__":
    sys.exit(main())
