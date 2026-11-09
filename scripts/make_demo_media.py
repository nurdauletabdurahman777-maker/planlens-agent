# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0"]
# ///
"""Regenerate docs/media from real installed-wheel output.

    uv run --script scripts/make_demo_media.py [--chrome PATH] [--font FILE]

Builds the wheel, installs it in a temporary environment, runs the authored demo with relative
paths, and renders exactly what the commands printed. Exit statuses are asserted. The renderer
adds colour, a highlight and a caption strip; it does not change any character of the output.
The inspector image is a real headless Chrome screenshot of the report that run generated.

Development tooling: Pillow is used only by this script and is not a project dependency.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
COLS, SIZE, LINE, PAD, STRIP = 96, 16, 21, 22, 44
BG, FG, DIM, PROMPT = "#0d1117", "#e6edf3", "#8b949e", "#7ee787"
RED, GREEN, BAND, STRIP_BG, ACCENT = "#ff7b72", "#56d364", "#3a2a12", "#161b22", "#ffa657"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FAIL = "plans/artifact-mismatch.json"
SED = f"sed 's/build_hotfix.artifact/build_rc.artifact/' {FAIL} > my-plan.json"


def capture(tmp: Path) -> tuple[Path, dict[str, str]]:
    def sh(*argv: str | Path, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True)

    assert sh("uv", "build", "--wheel", "--out-dir", tmp / "w").returncode == 0
    (wheel,) = (tmp / "w").glob("*.whl")
    assert sh("uv", "venv", "-q", "--python", "3.14", tmp / "env").returncode == 0
    python = tmp / "env/bin/python"
    done = sh("uv", "pip", "install", "-q", "--python", python, "--no-deps", wheel)
    assert done.returncode == 0, done.stderr
    cli = tmp / "env/bin/planlens-agent"
    assert sh(cli, "init", "release-basic", "demo", cwd=tmp).returncode == 0
    demo = tmp / "demo"

    def expect(code: int, *argv: str | Path) -> str:
        done = sh(cli, *argv, cwd=demo)
        assert done.returncode == code, (argv, done.returncode, done.stderr)
        assert str(tmp) not in done.stdout, "output would expose a local path"
        return done.stdout

    out = {"check": expect(1, "check", FAIL)}
    (demo / "my-plan.json").write_text(
        (demo / FAIL).read_text().replace("build_hotfix.artifact", "build_rc.artifact")
    )
    diff = sh("diff", FAIL, "my-plan.json", cwd=demo)
    assert diff.returncode == 1
    out["diff"] = diff.stdout
    out["recheck"] = expect(0, "check", "my-plan.json")
    expect(1, "inspect", FAIL, "--output", "report.html")
    return demo / "report.html", out


class Screen:
    def __init__(self, font_path: str) -> None:
        self.regular = ImageFont.truetype(font_path, SIZE, index=0)
        self.bold = ImageFont.truetype(font_path, SIZE, index=1)
        self.char = self.regular.getlength("M")
        self.width = int(COLS * self.char) + 2 * PAD

    @staticmethod
    def wrap(text: str) -> list[str]:
        out: list[str] = []
        for line in text.splitlines() or [""]:
            out += [line[i : i + COLS] for i in range(0, len(line), COLS)] or [""]
        return out

    def style(self, line: str, highlight: bool) -> tuple[str, bool]:
        if line.startswith("$ "):
            return FG, True
        if line.startswith("RESULT: REJECTED"):
            return RED, True
        if line.startswith("RESULT: PASS"):
            return GREEN, True
        if highlight:
            return "#ffe2b8", False
        return (DIM if line.startswith(("Not checked", "  - ", "Explain ")) else FG), False

    def render(
        self, lines: list[str], rows: int, marked: set[int], caption: str, done: bool = False
    ) -> Image.Image:
        height = PAD * 2 + rows * LINE + STRIP
        image = Image.new("RGB", (self.width, height), BG)
        draw = ImageDraw.Draw(image)
        for n, text in enumerate(lines):
            y = PAD + n * LINE
            if n in marked:
                draw.rectangle([PAD - 8, y - 1, self.width - PAD + 8, y + LINE - 2], fill=BAND)
            colour, bold = self.style(text, n in marked)
            if text.startswith("$ "):
                draw.text((PAD, y), "$", font=self.bold, fill=PROMPT)
                draw.text((PAD + 2 * self.char, y), text[2:], font=self.bold, fill=colour)
            else:
                draw.text((PAD, y), text, font=self.bold if bold else self.regular, fill=colour)
        top = height - STRIP
        draw.rectangle([0, top, self.width, height], fill=STRIP_BG)
        draw.text((PAD, top + 12), caption, font=self.bold, fill=GREEN if done else ACCENT)
        tag = "Authored example"
        x = self.width - PAD - self.regular.getlength(tag)
        draw.text((x, top + 12), tag, font=self.regular, fill=DIM)
        return image


def marks(lines: list[str]) -> set[int]:
    """The wrapped lines of the finding's message, requirement and next step."""
    found: set[int] = set()
    active = False
    for n, line in enumerate(lines):
        if line.startswith("  [violation]") or line.startswith("    steps[5] (deploy_prod) input"):
            active = True
        elif line.startswith(("    observed:", "    at:", "    see:", "    why:")):
            active = False
        elif line.startswith("    required:") or line.startswith("    next:"):
            active = True
        elif line == "":
            active = False
        if active:
            found.add(n)
    return found


def build(screen: Screen, out: dict[str, str]) -> None:
    command = f"$ planlens-agent check {FAIL}"
    first = screen.wrap(out["check"].rstrip("\n"))
    page2 = [
        f"$ {SED}",
        "$ diff plans/artifact-mismatch.json my-plan.json",
        *screen.wrap(out["diff"].rstrip("\n")),
        "",
        "$ planlens-agent check my-plan.json",
        *screen.wrap(out["recheck"].rstrip("\n")),
    ]
    rows = max(len(first) + 1, len(page2))
    full = [command, *first]
    frames: list[tuple[Image.Image, int]] = []

    def add(
        lines: list[str], ms: int, caption: str, marked: set[int] | None = None, ok: bool = False
    ) -> None:
        frames.append((screen.render(lines, rows, marked or set(), caption, ok), ms))

    step1 = "1  Check the bundled plan"
    for end in range(2, len(command) + 1, 6):
        add([command[:end]], 70, step1)
    add([command], 350, step1)
    add(full, 2800, "1  Rejected: all required actions are present, the plan is still wrong")
    add(
        full,
        5200,
        "2  Deploy references build_hotfix; tests and the scan used build_rc",
        marks(full),
    )
    step3 = "3  Correct a copy: deploy the build that was tested and scanned"
    add([page2[0]], 1400, step3)
    upto = page2.index("$ planlens-agent check my-plan.json")
    add(page2[:upto], 2400, step3)
    add(
        page2, 4200, "4  Recheck: PASS, exit 0. This checks the plan; nothing was deployed", ok=True
    )

    total = sum(ms for _, ms in frames) / 1000
    assert 15 <= total <= 20, total
    # One palette built from every frame, so no colour is lost on any frame.
    sheet = Image.new("RGB", (screen.width, frames[0][0].height * len(frames)), BG)
    for n, (frame, _) in enumerate(frames):
        sheet.paste(frame, (0, n * frame.height))
    palette = sheet.quantize(colors=64, dither=Image.Dither.NONE)
    images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f, _ in frames]
    images[0].save(
        MEDIA / "demo.gif",
        save_all=True,
        append_images=images[1:],
        duration=[ms for _, ms in frames],
        loop=0,
        optimize=False,
        disposal=1,
    )

    caption = "1, 2  Rejected: deploy references a different build"
    upper = screen.render(full, len(full), marks(full), caption)
    lower = screen.render(page2, len(page2), set(), "3, 4  Corrected copy: PASS, exit 0", True)
    joined = Image.new("RGB", (screen.width, upper.height + lower.height), BG)
    joined.paste(upper, (0, 0))
    joined.paste(lower, (0, upper.height))
    joined.quantize(colors=64, dither=Image.Dither.NONE).save(MEDIA / "demo.png", optimize=True)
    print(f"demo.gif: {len(frames)} frames, {total:.1f}s; rows {rows}, width {screen.width}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", default=CHROME)
    parser.add_argument("--font", default="/System/Library/Fonts/Menlo.ttc")
    parser.add_argument("--window", default="1280,1110", help="browser viewport for the inspector")
    args = parser.parse_args()
    MEDIA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="planlens-media-") as directory:
        tmp = Path(directory).resolve()
        report, out = capture(tmp)
        build(Screen(args.font), out)
        subprocess.run(
            [args.chrome, "--headless=new", "--disable-gpu", "--no-first-run",
             "--force-color-profile=srgb", f"--user-data-dir={tmp / 'chrome'}",
             f"--window-size={args.window}", "--hide-scrollbars",
             f"--screenshot={MEDIA / 'inspector.png'}", report.as_uri()],
            check=True, capture_output=True,
        )  # fmt: skip
    for name in ("demo.gif", "demo.png", "inspector.png"):
        print(name, (MEDIA / name).stat().st_size, "bytes")
    assert shutil.which("uv")


if __name__ == "__main__":
    main()
