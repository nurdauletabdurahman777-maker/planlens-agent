"""Run the copy-and-run blocks of Markdown documents, exactly as written.

    python scripts/docs_check.py DOC [DOC...] --python PYTHON --work DIR
                                 [--wheel FILE --checkout DIR] [--shell bash]
                                 [--deny-network] [--log FILE]

Every ```sh block is copy-and-run and must be annotated on the line before it:

    <!-- run exit=1 contains="RESULT: REJECTED" -->

Each command in a block has its own status, and the block stops at the first failure. Every
command except the last must exit 0; the last must exit with `exit=` (default 0). A command that
is meant to fail therefore goes last in its block. Constructs that could hide a failure inside one
line (`;`, pipes, `||`, `&`, command substitution, including inside double quotes) are refused.

`prereq` marks steps that need network access. With --wheel they are skipped, and the wheel and a
copy of the checkout are put in place. With --wheel, ordinary blocks also run with pip confined to
that wheel, so a documented `pip install "planlens-agent==X"` resolves locally; that verifies the
command against the wheel and says nothing about PyPI. `online` marks later steps that need the
network, and never see that confinement. `dev` marks commands run from the root of the clone in a
clean shell: real HOME for tool caches, PATH with only uv and the system directories, and no
virtual environment. With --deny-network (macOS) ordinary blocks run with network access denied.
Ordinary blocks run with an empty environment: only HOME, PATH holding the chosen Python and the
system directories, and TERM.

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
HEREDOC = re.compile(r"<<'(\w+)'")
SANDBOX = "(version 1)(allow default)(deny network*)"
COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "dist", "build", "review", "__pycache__", ".*_cache"
)


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
            flags = CONTAINS.sub("", attrs).split()
            block.kind = next((k for k in ("prereq", "online", "dev") if k in flags), "normal")
        blocks.append(block)
    return blocks


def commands(body: str) -> list[str]:
    """Split a block into commands, keeping continuations and heredocs whole."""
    lines, found, i = body.splitlines(), [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        parts = [lines[i]]
        while parts[-1].endswith("\\") and i + 1 < len(lines):
            i += 1
            parts.append(lines[i])
        heredoc = HEREDOC.search(parts[-1])
        if heredoc:
            while i + 1 < len(lines):
                i += 1
                parts.append(lines[i])
                if lines[i] == heredoc.group(1):
                    break
        found.append("\n".join(parts))
        i += 1
    return found


def _code(command: str) -> str:
    """The shell text of a command, without any heredoc body."""
    lines = command.split("\n")
    for n, line in enumerate(lines):
        if HEREDOC.search(line):
            return "\n".join(lines[: n + 1])
    return command


def _hiding_construct(code: str) -> str | None:
    """A construct that lets one command's failure go unseen.

    Single quotes make everything literal. Double quotes do not: command substitution stays
    active inside them unless its opening character is escaped.
    """
    quote, i = "", 0
    while i < len(code):
        c = code[i]
        substitution = "$(" if code.startswith("$(", i) else "`" if c == "`" else None
        if quote == "'":
            quote = "" if c == "'" else quote
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = ""
            elif substitution:
                return substitution
        elif c in "'\"":
            quote = c
        elif c == "\\":
            i += 1
        elif substitution:
            return substitution
        elif c in ";|":
            return c
        elif c == "&":
            if code.startswith("&&", i):
                i += 1
            elif i == 0 or code[i - 1] != ">":
                return "&"
        i += 1
    return None


def problems(blocks: list[Block]) -> list[str]:
    """Rules that keep a block pasteable and every command's failure observable."""
    found = []
    for block in blocks:
        if not block.annotated:
            found.append(f"not annotated: {block.body.splitlines()[0]}")
        for command in commands(block.body):
            code, first = _code(command), command.split("\n")[0]
            heredoc = HEREDOC.search(code)
            if heredoc and not command.endswith("\n" + heredoc.group(1)):
                found.append(f"unterminated heredoc: {first}")
            if "<<" in code and not heredoc:
                found.append(f"heredoc delimiter must be quoted: {first}")
            if code.startswith("#") or " #" in code or code.startswith("$ "):
                found.append(f"not pasteable: {first}")
            hiding = _hiding_construct(code)
            if hiding:
                found.append(f"{hiding!r} can hide a failure; use separate lines: {first}")
    return found


def render(block: Block, n: int, work: Path) -> str:
    """Shell text that records each command's status and stops the block at the first failure."""
    out, codes = work / f".out{n}", work / f".code{n}"
    lines = [f': > "{out}"', f': > "{codes}"', "__stop="]
    for command in commands(block.body):
        lines.append(
            f'if [ -z "$__stop" ]; then\n{{\n{command}\n}} >> "{out}" 2>&1; __c=$?\n'
            f'echo "$__c" >> "{codes}"\n[ "$__c" -eq 0 ] || __stop=1\nfi'
        )
    return "\n".join(lines)


def judge(block: Block, recorded: list[int]) -> str | None:
    """Why this block's command statuses are wrong, or None."""
    cmds = commands(block.body)
    for i, command in enumerate(cmds):
        wanted = block.exit if i == len(cmds) - 1 else 0
        if i >= len(recorded):
            return f"command {i + 1} of {len(cmds)} did not finish: {command.split(chr(10))[0]}"
        if recorded[i] != wanted:
            return (
                f"command {i + 1} of {len(cmds)} exited {recorded[i]}, wanted {wanted}: "
                + command.split("\n")[0]
            )
    return None


def offline_pip(wheelhouse: Path) -> dict[str, str]:
    """pip settings that resolve only from the local wheel. Not a download from any index."""
    return {
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(wheelhouse),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }


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
    python = Path(a.python).resolve()
    for name in ("python3", "python"):
        (tools / name).symlink_to(python)
    clone = work / "planlens-agent"
    if a.wheel:
        (work / "wheelhouse").mkdir()
        shutil.copy(a.wheel, work / "wheelhouse")
        shutil.copytree(a.checkout, clone, ignore=COPY_IGNORE)
        blocks = [b for b in blocks if b.kind != "prereq"]
        say.append("ordinary blocks: pip confined to the local wheel; this does not exercise PyPI")
    minimal = {"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "TERM": "dumb"}
    uv = shutil.which("uv")
    clean = {
        "HOME": str(Path.home()),
        "PATH": f"{Path(uv).parent if uv else ''}:/usr/bin:/bin",
        "TERM": "dumb",
        "UV_PYTHON": str(python),
    }

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
        base = position - len(group)
        if kind == "dev":
            lines = [f'cd "{clone}"']
            lines += [render(b, base + i, work) for i, b in enumerate(group)]
        else:
            lines = [f'cd "$(cat "{cwd}")"', f'[ -f "{state}" ] && . "{state}"']
            if kind != "normal":
                lines.append("unset " + " ".join(offline_pip(work)))
            lines += [render(b, base + i, work) for i, b in enumerate(group)]
            lines += [f'export -p > "{state}"', f'pwd > "{cwd}"']
        script = work / f".phase{base}.sh"
        script.write_text("\n".join(lines) + "\n")
        # Prerequisites keep the real HOME for repository credentials, but still use this Python.
        real = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
        env = real if kind == "prereq" else clean if kind == "dev" else minimal
        if kind == "normal" and a.wheel:
            env = {**env, **offline_pip(work / "wheelhouse")}
        argv = [a.shell, str(script)]
        if a.deny_network and kind == "normal":
            argv = ["sandbox-exec", "-p", SANDBOX, *argv]
        subprocess.run(argv, env=env, cwd=work, check=False)
        for offset, block in enumerate(group):
            n = base + offset
            code_file = work / f".code{n}"
            recorded = [int(x) for x in code_file.read_text().split()] if code_file.exists() else []
            out_file = work / f".out{n}"
            output = out_file.read_text("utf-8", "replace") if out_file.exists() else ""
            wrong = judge(block, recorded)
            missing = [c for c in block.contains if c not in output]
            unmet += (wrong is not None) + len(missing)
            final = recorded[-1] if recorded else -1
            say.append(f"[{kind}] exit {final}" + (f" UNEXPECTED: {wrong}" if wrong else ""))
            say.extend(f"  $ {line}" for line in block.body.splitlines()[:40])
            say.extend(f"  found: {c}" for c in block.contains if c in output)
            say.extend(f"  MISSING: {c}" for c in missing)
            if missing or wrong:
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
