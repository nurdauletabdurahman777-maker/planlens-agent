# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0", "fonttools==4.68.0", "brotli==1.2.1", "playwright==1.64.1"]
# ///
"""Regenerate docs/media and docs/brand from the real installed package.

    uv run --script scripts/make_demo_media.py [--chrome PATH]

Builds the wheel, installs it in a temporary environment and runs the authored demo with relative
paths. The walkthrough shows excerpts of what the commands printed, wrapped at word boundaries, and
a real Chrome capture of the report that run generated. Colour and the captions are editorial: they
change no character of the output. The complete output is written to docs/media/demo-transcript.txt.
Exit statuses are asserted.

Development tooling only: nothing here is a project dependency. Needs uv, Google Chrome and the
network, to download the pinned open-licensed fonts (see docs/brand/README.md).
"""

from __future__ import annotations

import argparse
import hashlib
import html
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
from PIL import Image
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
# Scene geometry. The GIFs are shown at about 830 and 358 CSS pixels wide, so type stays readable.
DESKTOP = {"width": 1200, "height": 660, "mono": 24, "note": 29, "tag": 20, "pad": 36, "cols": 72}
NARROW = {"width": 720, "height": 1180, "mono": 29, "note": 35, "tag": 24, "pad": 30, "cols": 37}
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
    out["plan"] = (demo / FAIL).read_text()
    (demo / "my-plan.json").write_text(out["plan"].replace(WRONG, RIGHT))
    out["fixed"] = (demo / "my-plan.json").read_text()
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


def paint(text: str) -> str:
    """Escape one line of output and colour it. The characters are unchanged."""
    safe = html.escape(text, quote=False)
    if text.startswith("RESULT: REJECTED"):
        return f'<b class="bad">{safe}</b>'
    if text.startswith("RESULT: PASS"):
        return f'<b class="good">{safe}</b>'
    if text.startswith("$ "):
        return f'<b class="prompt">$</b> <b>{safe[2:]}</b>'
    return safe.replace(WRONG, f'<b class="b">{WRONG}</b>').replace(
        RIGHT, f'<b class="a">{RIGHT}</b>'
    )


def plan_rows(plan: str, shape: dict[str, int]) -> str:
    """The plan's own steps, set out so that each step's artifact reference can be compared."""
    rows = []
    for step in json.loads(plan)["steps"]:
        reference = step.get("inputs", {}).get("artifact")
        line = paint(reference) if reference else '<span class="dim">no artifact input</span>'
        name = f'<b>{step["id"]}</b> <span class="dim">{step["action"]}</span>'
        if shape is NARROW:
            rows.append(f'<div class="step">{name}<br>{line}</div>')
        else:
            rows.append(
                f'<tr><td>{step["id"]}</td><td class="dim">{step["action"]}</td>'
                f"<td>{line}</td></tr>"
            )
    return "".join(rows) if shape is NARROW else f"<table>{''.join(rows)}</table>"


def terminal(shape: dict[str, int], *lines: str) -> str:
    wrapped = [part for line in lines for part in wrap(line, shape["cols"])]
    return "<pre>" + "\n".join(paint(line) for line in wrapped) + "</pre>"


def inspector_crop(report: Path, browser: object, tmp: Path, shape: dict[str, int]) -> Path:
    """A plain crop of the report's real finding panel, down to its Required row."""
    css_width, last = (760, "Required") if shape is DESKTOP else (360, "Required")
    target = shape["width"] - 2 * shape["pad"]
    context = browser.new_context(  # type: ignore[attr-defined]
        viewport={"width": css_width, "height": 900},
        device_scale_factor=target / css_width,
        color_scheme="dark",
    )
    page = context.new_page()
    page.goto(report.as_uri())
    page.wait_for_timeout(300)
    clip = page.evaluate(
        """(last) => {
          const panel = document.querySelector('.findings-col').getBoundingClientRect();
          const label = [...document.querySelectorAll('.finding dt')]
            .find(d => d.textContent.trim() === last);
          const next = label.nextElementSibling.nextElementSibling;
          const top = panel.top + window.scrollY;
          return {x: panel.left, y: top, width: panel.width,
                  height: next.getBoundingClientRect().top + window.scrollY - 2 - top};
        }""",
        last,
    )
    path = tmp / f"crop-{shape['width']}.png"
    page.screenshot(path=str(path), full_page=True, clip=clip)
    context.close()
    return path


def scenes(
    out: dict[str, str], crop: Path, shape: dict[str, int]
) -> list[tuple[str, str, int, str]]:
    """Each scene's editorial note, body, milliseconds and accent."""
    finding = pick(out["check"], "  [violation]", "    steps[5]", "    required:")
    (rejected,) = pick(out["check"], "RESULT:")
    (passed,) = pick(out["recheck"], "RESULT:")
    assert "plan/artifact-identity" in finding[0]
    diff = [line for line in out["diff"].splitlines() if line.startswith(("<", ">"))]
    assert len(diff) == 2 and WRONG in diff[0] and RIGHT in diff[1]
    source = '<div class="dim file">{} · steps, reformatted</div>'
    return [
        (
            "Every required step is here. Tests and the scan use one build. Deploy uses another.",
            source.format(FAIL) + plan_rows(out["plan"], shape),
            3700,
            ACCENT,
        ),
        (
            "PlanLens rejects the plan and names the requirement it violates.",
            terminal(shape, f"$ planlens-agent check {FAIL}", OMITTED, rejected, OMITTED, *finding),
            3700,
            RED,
        ),
        (
            "The Inspector shows the references that disagree. Choosing the right one is yours.",
            f'<img class="crop" src="{crop.as_uri()}">',
            3900,
            ACCENT,
        ),
        (
            "A person edits a copy of the plan. PlanLens does not change plans.",
            terminal(shape, f"$ diff {FAIL} my-plan.json", OMITTED, *diff),
            3100,
            ACCENT,
        ),
        (
            "The recheck passes: the declared references agree. "
            "Nothing was built, tested or deployed.",
            terminal(shape, "$ planlens-agent check my-plan.json", OMITTED, passed)
            + plan_rows(out["fixed"], shape),
            3400,
            GREEN,
        ),
    ]


def scene_page(
    fonts: dict[str, Path],
    shape: dict[str, int],
    note: str,
    body: str,
    accent: str,
    n: int,
    of: int,
) -> str:
    dots = "".join(f'<i class="{"on" if k == n else ""}"></i>' for k in range(of))
    faces = "".join(
        f"@font-face{{font-family:{name};src:url({path.as_uri()})}}"
        for name, path in (
            ("Sans", fonts["plex"]),
            ("Mono", fonts["mono"]),
            ("MonoB", fonts["bold"]),
        )
    )
    pad = shape["pad"]
    tag = "Authored example" if shape is NARROW else "Authored example · declared references only"
    style = f"""{faces}
*{{box-sizing:border-box;margin:0}}
html,body{{width:{shape["width"]}px;height:{shape["height"]}px;background:{BG};overflow:hidden}}
body{{display:grid;grid-template-rows:auto 1fr auto;color:{FG};font:{shape["mono"]}px/1.42 Mono}}
header{{background:{STRIP_BG};border-left:8px solid {accent};padding:{pad * 0.62}px {pad}px}}
header span{{font:{shape["tag"]}px Mono;color:{DIM};display:block;margin-bottom:6px}}
header p{{font:{shape["note"]}px/1.28 Sans;color:{FG}}}
main{{padding:{pad * 0.75}px {pad}px 0;min-height:0}}
pre{{font:inherit;white-space:pre-wrap;margin-bottom:{pad * 0.6}px}}
b{{font-family:MonoB;font-weight:400}}
.dim{{color:{DIM}}}.prompt{{color:{PROMPT}}}.bad{{color:{RED}}}.good{{color:{GREEN}}}
.a{{color:{OTHER}}}.b{{color:{ACCENT}}}
.file{{margin-bottom:{pad * 0.35}px;font-size:{shape["tag"]}px}}
table{{border-collapse:collapse;width:100%}}
td{{padding:{shape["mono"] * 0.3}px {pad * 0.7}px {shape["mono"] * 0.3}px 0;
  border-bottom:1px solid #21262D;white-space:nowrap}}
.step{{padding:{shape["mono"] * 0.26}px 0;border-bottom:1px solid #21262D}}
.crop{{display:block;width:100%;border-radius:6px}}
footer{{display:flex;justify-content:space-between;align-items:center;
  padding:{pad * 0.42}px {pad}px;
  background:{STRIP_BG};color:{DIM};font-size:{shape["tag"]}px}}
footer div{{display:flex;gap:8px}}
i{{display:block;width:{shape["tag"] * 1.5}px;height:6px;background:#30363D;border-radius:3px}}
i.on{{background:{ACCENT}}}
"""
    return (
        f"<!doctype html><meta charset=utf-8><style>{style}</style>"
        f"<header><span>Editorial note</span><p>{html.escape(note)}</p></header>"
        f"<main>{body}</main>"
        f"<footer><div>{dots}</div><span>{tag}</span></footer>"
    )


def build_walkthrough(
    fonts: dict[str, Path], out: dict[str, str], report: Path, browser: object, tmp: Path
) -> None:
    for shape, stem in ((DESKTOP, "demo"), (NARROW, "demo-narrow")):
        crop = inspector_crop(report, browser, tmp, shape)
        planned = scenes(out, crop, shape)
        total = sum(ms for _, _, ms, _ in planned) / 1000
        assert 12 <= total <= 18, total
        context = browser.new_context(  # type: ignore[attr-defined]
            viewport={"width": shape["width"], "height": shape["height"]}
        )
        page = context.new_page()
        frames = []
        for n, (note, body, ms, accent) in enumerate(planned):
            path = tmp / f"{stem}-{n}.html"
            path.write_text(scene_page(fonts, shape, note, body, accent, n, len(planned)))
            page.goto(path.as_uri())
            page.wait_for_timeout(300)
            overflow = page.evaluate(
                "() => { const m = document.querySelector('main');"
                " return m.scrollHeight - m.clientHeight; }"
            )
            assert overflow <= 0, (stem, n, overflow)
            frames.append((Image.open(io.BytesIO(page.screenshot())).convert("RGB"), ms))
        context.close()
        width, height = frames[0][0].size
        sheet = Image.new("RGB", (width, height * len(frames)), BG)
        for n, (frame, _) in enumerate(frames):
            sheet.paste(frame, (0, n * height))
        palette = sheet.quantize(colors=96, dither=Image.Dither.NONE)
        images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f, _ in frames]
        images[0].save(
            MEDIA / f"{stem}.gif", save_all=True, append_images=images[1:],
            duration=[ms for _, ms in frames], loop=0, optimize=False, disposal=1,
        )  # fmt: skip
        sheet.quantize(colors=96, dither=Image.Dither.NONE).save(
            MEDIA / f"{stem}.png", optimize=True
        )
        print(f"{stem}.gif: {len(frames)} scenes, {total:.1f}s, {width}x{height}")


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
        f"<div><div class=w>{wordmark}</div><h1>Catch plan errors before execution.</h1></div>"
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


def build_finding_crops(report: Path, browser: object) -> None:
    """Plain crops of the report's own layouts, so text is near full size in the README."""
    crops = (("", 900, 2.0, "Next"), ("-narrow", 360, 3.0, "Why"))
    for suffix, width, scale, last in crops:
        for theme, ending in (("dark", ""), ("light", "-light")):
            context = browser.new_context(  # type: ignore[attr-defined]
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
                color_scheme=theme,
            )
            page = context.new_page()
            page.goto(report.as_uri())
            page.wait_for_timeout(300)
            clip = page.evaluate(
                """(last) => {
                  const panel = document.querySelector('.findings-col').getBoundingClientRect();
                  const label = [...document.querySelectorAll('.finding dt')]
                    .find(d => d.textContent.trim() === last);
                  const next = label.nextElementSibling.nextElementSibling;
                  const bottom = next.getBoundingClientRect().top + window.scrollY - 2;
                  const top = panel.top + window.scrollY;
                  return {x: panel.left, y: top, width: panel.width, height: bottom - top};
                }""",
                last,
            )
            path = MEDIA / f"inspector-finding{suffix}{ending}.png"
            page.screenshot(path=str(path), full_page=True, clip=clip)
            context.close()
            image = Image.open(path).convert("RGB")
            image.quantize(colors=200, dither=Image.Dither.NONE).save(path, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", default=CHROME)
    args = parser.parse_args()
    MEDIA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="planlens-media-") as directory:
        tmp = Path(directory).resolve()
        fonts = fetch_fonts(tmp)
        report, out = capture(tmp)
        build_brand(fonts)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chrome)
            build_walkthrough(fonts, out, report, browser, tmp)
            build_inspector(report, browser)
            build_finding_crops(report, browser)
            build_social(fonts, out, browser, tmp)
            browser.close()
    for path in sorted([*MEDIA.iterdir(), *BRAND.glob("*.svg")]):
        print(path.name, path.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
