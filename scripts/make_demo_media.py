# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0", "fonttools==4.68.0", "brotli==1.2.1", "playwright==1.64.1"]
# ///
"""Regenerate docs/media and docs/brand from the real installed package.

    uv run --script scripts/make_demo_media.py [--chrome PATH]

Builds the wheel, installs it in a temporary environment and runs the authored demo with relative
paths. Terminal media shows an excerpt of what the commands printed, wrapped at word boundaries;
the renderer adds colour and captions but changes no character. The complete output is written to
docs/media/demo-transcript.txt. Exit statuses are asserted. Inspector images are real Chrome
screenshots of the report that run generated.

Development tooling only: nothing here is a project dependency. Needs uv, Google Chrome and the
network, to download the pinned open-licensed fonts (see docs/brand/README.md).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tempfile
import textwrap
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MEDIA, BRAND = ROOT / "docs" / "media", ROOT / "docs" / "brand"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDN = "https://cdn.jsdelivr.net/npm/@fontsource"
FONTS = {
    "plex": (
        f"{CDN}/ibm-plex-sans@5.3.0/files/ibm-plex-sans-latin-600-normal.woff2",
        "8960851d691c054ed38e259bdcf1a6190d157b4203ed5bb32c632a863fb8ec2f",
    ),
    "mono": (
        f"{CDN}/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-400-normal.woff2",
        "14425ba9c695763c1547f48a206b7aa60350a33ae23de09f0407877f3fcd89eb",
    ),
    "bold": (
        f"{CDN}/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-700-normal.woff2",
        "d0d4e818808f2a0ba39b2b09d1989366f63494e295f003c7ef436697378507e8",
    ),
}
THEMES = {
    "light": {"ink": "#1F2328", "muted": "#59636E", "accent": "#B45309"},
    "dark": {"ink": "#E6EDF3", "muted": "#8B949E", "accent": "#F59E0B"},
}
BG, FG, DIM, PROMPT = "#0D1117", "#E6EDF3", "#8B949E", "#7EE787"
RED, GREEN, STRIP_BG, ACCENT, OTHER = "#FF7B72", "#56D364", "#161B22", "#F59E0B", "#79C0FF"
WIDTH, SIZE, LINE, PAD, STRIP = 1040, 20, 28, 28, 50
FAIL = "plans/artifact-mismatch.json"
SED = f"sed 's/build_hotfix.artifact/build_rc.artifact/' {FAIL} > my-plan.json"
WRONG, RIGHT = "build_hotfix.artifact", "build_rc.artifact"
OMITTED = "…"


def fetch_fonts(tmp: Path) -> dict[str, Path]:
    paths = {}
    for name, (url, want) in FONTS.items():
        with urllib.request.urlopen(url) as response:
            data = response.read()
        assert hashlib.sha256(data).hexdigest() == want, name
        font = TTFont(io.BytesIO(data))
        font.flavor = None
        paths[name] = tmp / f"{name}.ttf"
        font.save(paths[name])
    return paths


def capture(tmp: Path) -> tuple[Path, dict[str, str]]:
    def sh(*argv: str | Path, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True)

    assert sh("uv", "build", "--wheel", "--out-dir", tmp / "w").returncode == 0
    (wheel,) = (tmp / "w").glob("*.whl")
    assert sh("uv", "venv", "-q", "--python", "3.14", tmp / "env").returncode == 0
    done = sh("uv", "pip", "install", "-q", "--python", tmp / "env/bin/python", "--no-deps", wheel)
    assert done.returncode == 0, done.stderr
    cli = tmp / "env/bin/planlens-agent"
    assert sh(cli, "init", "release-basic", "demo", cwd=tmp).returncode == 0
    demo = tmp / "demo"
    transcript: list[str] = []

    def expect(code: int, *argv: str) -> str:
        done = sh(cli, *argv, cwd=demo)
        assert done.returncode == code, (argv, done.returncode, done.stderr)
        assert str(tmp) not in done.stdout, "output would expose a local path"
        transcript.extend(
            [f"$ planlens-agent {' '.join(argv)}", done.stdout.rstrip("\n"), f"[exit {code}]", ""]
        )
        return done.stdout

    out = {"check": expect(1, "check", FAIL)}
    out["json"] = sh(cli, "check", FAIL, "--format", "json", cwd=demo).stdout
    assert str(tmp) not in out["json"]
    (demo / "my-plan.json").write_text((demo / FAIL).read_text().replace(WRONG, RIGHT))
    diff = sh("diff", FAIL, "my-plan.json", cwd=demo)
    assert diff.returncode == 1
    out["diff"] = diff.stdout
    transcript.extend(
        [
            f"$ {SED}",
            "[exit 0]",
            "",
            f"$ diff {FAIL} my-plan.json",
            diff.stdout.rstrip("\n"),
            "[exit 1]",
            "",
        ]
    )
    out["recheck"] = expect(0, "check", "my-plan.json")
    expect(1, "inspect", FAIL, "--output", "report.html")
    header = (
        "Complete output of the authored demo, captured from the installed wheel. "
        "Nothing was deployed.\n"
    )
    (MEDIA / "demo-transcript.txt").write_text(
        header + "\n" + "\n".join(transcript).rstrip("\n") + "\n"
    )
    return demo / "report.html", out


def wrap(line: str, cols: int) -> list[str]:
    """Wrap at word boundaries, keeping every character of the original line."""
    stripped = line.lstrip(" ")
    indent = " " * (len(line) - len(stripped))
    return textwrap.wrap(
        stripped, cols, initial_indent=indent, subsequent_indent=indent + "  ",
        break_long_words=False, break_on_hyphens=False, replace_whitespace=False,
        drop_whitespace=True,
    ) or [""]  # fmt: skip


def pick(output: str, *prefixes: str) -> list[str]:
    lines = output.splitlines()
    found = []
    for prefix in prefixes:
        (match,) = [line for line in lines if line.startswith(prefix)]
        found.append(match)
    return found


class Screen:
    def __init__(self, fonts: dict[str, Path]) -> None:
        self.regular = ImageFont.truetype(str(fonts["mono"]), SIZE)
        self.bold = ImageFont.truetype(str(fonts["bold"]), SIZE)
        self.char = self.regular.getlength("M")
        self.cols = int((WIDTH - 2 * PAD) / self.char)

    def lines(self, *raw: str) -> list[str]:
        return [part for line in raw for part in wrap(line, self.cols)]

    def draw_line(self, draw: ImageDraw.ImageDraw, y: int, text: str, emphasis: bool) -> None:
        font, colour = self.regular, FG
        if text.startswith("$ "):
            draw.text((PAD, y), "$", font=self.bold, fill=PROMPT)
            draw.text((PAD + 2 * self.char, y), text[2:], font=self.bold, fill=FG)
            return
        if text == OMITTED:
            draw.text((PAD, y), text, font=self.regular, fill=DIM)
            return
        if text.startswith("RESULT: REJECTED"):
            font, colour = self.bold, RED
        elif text.startswith("RESULT: PASS"):
            font, colour = self.bold, GREEN
        x, rest = PAD, text
        while rest:
            hits = [(rest.find(t), t) for t in (WRONG, RIGHT) if emphasis and t in rest]
            if not hits:
                draw.text((x, y), rest, font=font, fill=colour)
                return
            at, token = min(hits)
            draw.text((x, y), rest[:at], font=font, fill=colour)
            x += at * self.char
            draw.text((x, y), token, font=self.bold, fill=ACCENT if token == WRONG else OTHER)
            x += len(token) * self.char
            rest = rest[at + len(token) :]

    def render(
        self, lines: list[str], rows: int, caption: str, emphasis: bool, done: bool = False
    ) -> Image.Image:
        height = PAD * 2 + rows * LINE + STRIP
        image = Image.new("RGB", (WIDTH, height), BG)
        draw = ImageDraw.Draw(image)
        for n, text in enumerate(lines):
            self.draw_line(draw, PAD + n * LINE, text, emphasis)
        top = height - STRIP
        draw.rectangle([0, top, WIDTH, height], fill=STRIP_BG)
        draw.text((PAD, top + 13), caption, font=self.bold, fill=GREEN if done else ACCENT)
        tag = "Authored example · excerpt"
        small = self.regular.font_variant(size=16)
        draw.text((WIDTH - PAD - small.getlength(tag), top + 16), tag, font=small, fill=DIM)
        return image


def build_terminal(screen: Screen, out: dict[str, str]) -> None:
    command = f"$ planlens-agent check {FAIL}"
    finding = pick(out["check"], "  [violation]", "    steps[5]", "    required:", "    next:")
    (rejected,) = pick(out["check"], "RESULT:")
    (passed,) = pick(out["recheck"], "RESULT:")
    first = [command, OMITTED, *screen.lines(rejected), OMITTED, *screen.lines(*finding), OMITTED]
    diff = [line for line in out["diff"].splitlines() if line.startswith(("<", ">"))]
    second = [
        *screen.lines(f"$ {SED}"),
        f"$ diff {FAIL} my-plan.json",
        OMITTED,
        *screen.lines(*diff),
        "",
        "$ planlens-agent check my-plan.json",
        OMITTED,
        *screen.lines(passed),
        OMITTED,
    ]
    upto = second.index("$ planlens-agent check my-plan.json")
    rows = max(len(first), len(second))
    frames = [
        (screen.render(first, rows, "Rejected, with every required action present", False), 3400),
        (
            screen.render(first, rows, "Deploy uses a different build from tests and scan", True),
            5400,
        ),
        (screen.render(second[:upto], rows, "Correct a copy of the plan", True), 3600),
        (screen.render(second, rows, "Recheck: PASS. Nothing was deployed", True, True), 4600),
    ]
    total = sum(ms for _, ms in frames) / 1000
    assert 15 <= total <= 20, total
    sheet = Image.new("RGB", (WIDTH, frames[0][0].height * len(frames)), BG)
    for n, (frame, _) in enumerate(frames):
        sheet.paste(frame, (0, n * frame.height))
    palette = sheet.quantize(colors=64, dither=Image.Dither.NONE)
    images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f, _ in frames]
    images[0].save(
        MEDIA / "demo.gif", save_all=True, append_images=images[1:],
        duration=[ms for _, ms in frames], loop=0, optimize=False, disposal=1,
    )  # fmt: skip
    upper = screen.render(first, len(first), "Rejected: deploy uses a different build", True)
    lower = screen.render(
        second, len(second), "Corrected copy: PASS. Nothing was deployed", True, True
    )
    joined = Image.new("RGB", (WIDTH, upper.height + lower.height), BG)
    joined.paste(upper, (0, 0))
    joined.paste(lower, (0, upper.height))
    joined.quantize(colors=64, dither=Image.Dither.NONE).save(MEDIA / "demo.png", optimize=True)
    print(f"demo.gif: {len(frames)} frames, {total:.1f}s, {WIDTH}x{frames[0][0].height}")


def text_path(font: TTFont, text: str, size: float, x: float, baseline: float) -> tuple[str, float]:
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    for char in text:
        name = cmap[ord(char)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        x += hmtx[name][0] * scale
    return pen.getCommands(), x


def mark(colours: dict[str, str]) -> str:
    """A plan line through a lens: inside the lens, the step is visibly out of line."""
    return (
        f'<rect x="1" y="8" width="17" height="4" rx="2" fill="{colours["muted"]}"/>'
        f'<rect x="1" y="21" width="15" height="4" rx="2" fill="{colours["muted"]}"/>'
        f'<rect x="1" y="34" width="17" height="4" rx="2" fill="{colours["muted"]}"/>'
        f'<rect x="43" y="21" width="5" height="4" rx="2" fill="{colours["muted"]}"/>'
        f'<rect x="21" y="26" width="18" height="4" rx="2" fill="{colours["accent"]}"/>'
        f'<circle cx="30" cy="23" r="14" fill="none" stroke="{colours["ink"]}" stroke-width="3"/>'
    )


def build_brand(fonts: dict[str, Path]) -> None:
    BRAND.mkdir(exist_ok=True)
    plex = TTFont(fonts["plex"])
    outline, end = text_path(plex, "PlanLens", 34, 62, 35)
    for theme, colours in THEMES.items():
        (BRAND / f"planlens-mark-{theme}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 49 46" role="img" '
            f'aria-label="PlanLens">{mark(colours)}</svg>\n'
        )
        (BRAND / f"planlens-wordmark-{theme}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {end + 2:.0f} 46" role="img" '
            f'aria-label="PlanLens">{mark(colours)}'
            f'<path d="{outline}" fill="{colours["ink"]}"/></svg>\n'
        )


def build_social(fonts: dict[str, Path], out: dict[str, str], browser: object, tmp: Path) -> None:
    report = json.loads(out["json"])
    finding = report["diagnostics"][0]
    rows = "".join(
        f'<tr><td>{o["step_id"]}</td><td class="{"bad" if o["refers_to"] == WRONG else "ok"}">'
        f"{o['refers_to']}</td></tr>"
        for o in finding["observed"]
    )
    wordmark = (BRAND / "planlens-wordmark-dark.svg").read_text()
    style = f"""
@font-face{{font-family:Mono;src:url(mono.ttf)}}
@font-face{{font-family:MonoB;src:url(bold.ttf)}}
@font-face{{font-family:Plex;src:url(plex.ttf)}}
*{{margin:0;box-sizing:border-box}}
body{{width:1280px;height:640px;background:{BG};color:{FG};padding:72px 80px;font-family:Plex;
  display:flex;flex-direction:column;justify-content:space-between}}
.w svg{{height:68px;width:auto;display:block}}
h1{{font-size:62px;line-height:1.12;margin-top:36px;font-weight:400;letter-spacing:-.5px}}
table{{font:27px/1.55 Mono;border-collapse:collapse}}
td{{padding-right:56px}}
.ok{{color:{OTHER}}}
.bad{{color:{ACCENT};font-family:MonoB}}
.r{{font:27px/1.5 MonoB;color:{RED};margin-top:14px}}
.r span{{color:{FG};font-family:Mono}}
.t{{position:absolute;right:80px;bottom:76px;font:20px Mono;color:{DIM}}}
"""
    (tmp / "social.html").write_text(
        f"<!doctype html><meta charset=utf-8><style>{style}</style>"
        f"<div><div class=w>{wordmark}</div><h1>Lint AI-agent plans before they run.</h1></div>"
        f"<div><table>{rows}</table><div class=r>REJECTED <span>{finding['rule_id']}</span></div>"
        "</div><div class=t>Authored example</div>"
    )
    page = browser.new_page(viewport={"width": 1280, "height": 640})  # type: ignore[attr-defined]
    page.goto((tmp / "social.html").as_uri())
    page.wait_for_timeout(300)
    page.screenshot(path=str(MEDIA / "social-preview.png"))
    page.close()
    Image.open(MEDIA / "social-preview.png").convert("RGB").quantize(
        colors=128, dither=Image.Dither.NONE
    ).save(MEDIA / "social-preview.png", optimize=True)


def build_inspector(report: Path, browser: object) -> None:
    for theme, name in (("dark", "inspector.png"), ("light", "inspector-light.png")):
        context = browser.new_context(  # type: ignore[attr-defined]
            viewport={"width": 1280, "height": 900}, device_scale_factor=1.5, color_scheme=theme
        )
        page = context.new_page()
        page.goto(report.as_uri())
        page.wait_for_timeout(300)
        # Stop at the end of the proposed sequence, so no section is cut part-way.
        bottom = page.evaluate(
            """() => {
              const heading = [...document.querySelectorAll('h2,h3')]
                .find(h => h.textContent.trim().startsWith('Proposed sequence'));
              return Math.ceil(heading.closest('section').getBoundingClientRect().bottom) + 24;
            }"""
        )
        clip = {"x": 0, "y": 0, "width": 1280, "height": bottom}
        page.screenshot(path=str(MEDIA / name), full_page=True, clip=clip)
        context.close()
        Image.open(MEDIA / name).convert("RGB").quantize(colors=200, dither=Image.Dither.NONE).save(
            MEDIA / name, optimize=True
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", default=CHROME)
    args = parser.parse_args()
    MEDIA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="planlens-media-") as directory:
        tmp = Path(directory).resolve()
        fonts = fetch_fonts(tmp)
        report, out = capture(tmp)
        build_terminal(Screen(fonts), out)
        build_brand(fonts)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chrome)
            build_inspector(report, browser)
            build_social(fonts, out, browser, tmp)
            browser.close()
    for path in sorted([*MEDIA.iterdir(), *BRAND.glob("*.svg")]):
        print(path.name, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
