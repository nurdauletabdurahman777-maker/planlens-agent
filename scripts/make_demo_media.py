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
import re
import subprocess
import tempfile
import textwrap
import urllib.request
from collections import Counter
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
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
BRAND_COLOURS = {
    "light": ("#20242C", "#CB7100"),
    "dark": ("#EEF0F4", "#F5A623"),
    "mono-light": ("#20242C", "#20242C"),
    "mono-dark": ("#EEF0F4", "#EEF0F4"),
}
MARK_PATH = (
    "M10 8H37C48.6 8 55 14.3 55 25S48.6 42 37 42H22V56H10Z"
    "M22 19V31H36C40.8 31 43 29.1 43 25S40.8 19 36 19Z"
)
BG, FG, DIM, PROMPT = "#0D1117", "#E6EDF3", "#8B949E", "#7EE787"
RED, GREEN, STRIP_BG, ACCENT, OTHER = "#FF7B72", "#56D364", "#161B22", "#F5A623", "#79C0FF"
# Scene geometry. The GIFs are shown at about 816 and 358 CSS pixels wide, so type stays readable.
DESKTOP = {"width": 1200, "height": 620, "mono": 27, "label": 21, "note": 31, "pad": 40, "cols": 66}
NARROW = {"width": 720, "height": 1260, "mono": 32, "label": 24, "note": 36, "pad": 32, "cols": 32}
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
        return f'<span class="prompt">$</span> {safe[2:]}'
    return safe


def references(plan: str) -> list[tuple[str, str]]:
    """Each step's declared artifact input, in plan order, from the plan itself."""
    steps = json.loads(plan)["steps"]
    return [(s["id"], s["inputs"]["artifact"]) for s in steps if "artifact" in s.get("inputs", {})]


def reference_rows(refs: list[tuple[str, str]], letters: dict[str, str], stacked: bool) -> str:
    """Letters, not only colour, tell the builds apart. A reference unlike the others is named."""
    common = Counter(a for _, a in refs).most_common(1)[0][0]
    rows = []
    for step, artifact in refs:
        letter = letters[artifact]
        note = '<span class="differs">different build</span>' if artifact != common else ""
        ref = f'<i class="badge {letter}">{letter}</i><b class="ref {letter}">{artifact}</b>'
        if stacked:
            rows.append(f'<div class="row"><div>{step} →</div><div>{ref}</div>{note}</div>')
        else:
            rows.append(
                f"<tr><td>{step}</td><td class=dim>→</td><td>{ref}</td><td>{note}</td></tr>"
            )
    return "".join(rows) if stacked else f"<table>{''.join(rows)}</table>"


def terminal(shape: dict[str, int], *lines: str) -> str:
    wrapped = [part for line in lines for part in wrap(line, shape["cols"])]
    return "<pre>" + "\n".join(paint(line) for line in wrapped) + "</pre>"


def inspector_crop(report: Path, browser: object, tmp: Path, shape: dict[str, int]) -> Path:
    """A plain crop of the report's real finding panel, down to its Required row."""
    css_width = 760 if shape is DESKTOP else 360
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
        """() => {
          const panel = document.querySelector('.findings-col').getBoundingClientRect();
          const label = [...document.querySelectorAll('.finding dt')]
            .find(d => d.textContent.trim() === 'Required');
          const next = label.nextElementSibling.nextElementSibling;
          const top = panel.top + window.scrollY;
          return {x: panel.left, y: top, width: panel.width,
                  height: next.getBoundingClientRect().top + window.scrollY - 2 - top};
        }"""
    )
    path = tmp / f"crop-{shape['width']}.png"
    page.screenshot(path=str(path), full_page=True, clip=clip)
    context.close()
    return path


def evidence(out: dict[str, str]) -> dict[str, object]:
    """Every value the walkthrough shows, taken from the plan, its copy and the real output."""
    refs, fixed = references(out["plan"]), references(out["fixed"])
    assert [a for _, a in refs] == [RIGHT, RIGHT, WRONG], refs
    assert [a for _, a in fixed] == [RIGHT, RIGHT, RIGHT], fixed
    letters = {artifact: "AB"[n] for n, artifact in enumerate(dict.fromkeys(a for _, a in refs))}
    (rejected,) = pick(out["check"], "RESULT:")
    (violation,) = pick(out["check"], "  [violation]")
    (passed,) = pick(out["recheck"], "RESULT:")
    assert passed.startswith("RESULT: PASS (exit 0)")
    rule = re.fullmatch(r"\s*\[violation\] (\S+)\s+\(contract rule: ([^)]+)\)", violation)
    assert rule and rule.group(1) == "plan/artifact-identity", violation
    diff = [line for line in out["diff"].splitlines() if line.startswith(("<", ">"))]
    before, after = f'"artifact": "{WRONG}"', f'"artifact": "{RIGHT}"'
    # The edit scene shows exactly this change: deploy_prod's artifact, WRONG before, RIGHT after.
    assert len(diff) == 2 and before in diff[0] and after in diff[1] and "deploy_prod" in diff[0]
    return {
        "refs": refs, "fixed": fixed, "letters": letters, "rejected": rejected,
        "violation": violation.strip(), "passed": passed, "before": before, "after": after,
        "rule": rule.group(1), "contract_rule": f"contract rule: {rule.group(2)}",
    }  # fmt: skip


def edit(e: dict[str, object], source: bool = True, focus: bool = False) -> str:
    """The one change a person made, as artifact references, lettered as in the first scene."""
    letters = e["letters"]
    assert isinstance(letters, dict)
    where = "Excerpt · deploy_prod's artifact input in my-plan.json, a copy edited by hand"
    rows = "".join(
        f'<div class="change"><span class="dim">{when}</span>'
        f'<span><i class="badge {letters[ref]}">{letters[ref]}</i>'
        f'<b class="ref {letters[ref]}">{ref}</b></span></div>'
        for when, ref in (("before", WRONG), ("after", RIGHT))
    )
    css = "focus" if focus else ""
    return (
        f'<div class="source">{where}</div>' if source else ""
    ) + f'<div class="{css}">{rows}</div>'


def scenes(
    e: dict[str, object], crop: Path, shape: dict[str, int]
) -> list[tuple[str, str, str, int]]:
    """Each scene's label, one-sentence note, body and milliseconds."""
    letters = e["letters"]
    assert isinstance(letters, dict)
    source = f'<div class="source">Excerpt · {FAIL} · artifact inputs only</div>'
    return [
        (
            "Plan references",
            "Tests and the scan use one build. Deploy uses another.",
            source
            + '<div class="focus">'
            + reference_rows(e["refs"], letters, shape is NARROW)  # type: ignore[arg-type]
            + "</div>",
            3600,
        ),
        (
            "Diagnosis",
            "PlanLens rejects the plan and names the rule it breaks.",
            terminal(
                shape, f"$ planlens-agent check {FAIL}", str(e["rejected"]), str(e["violation"])
            ),
            3500,
        ),
        (
            "Inspector",
            "The report shows which references disagree.",
            f'<img class="crop" src="{crop.as_uri()}">',
            4000,
        ),
        (
            "Manual edit",
            "A person changes one reference in a copy. PlanLens does not edit plans.",
            edit(e, focus=True),
            3000,
        ),
        (
            "Recheck",
            "The references agree. Nothing was built, tested or deployed.",
            terminal(shape, "$ planlens-agent check my-plan.json", str(e["passed"]))
            + reference_rows(e["fixed"], letters, shape is NARROW),  # type: ignore[arg-type]
            3400,
        ),
    ]


def style(fonts: dict[str, Path], shape: dict[str, int]) -> str:
    faces = "".join(
        f"@font-face{{font-family:{name};src:url({path.as_uri()})}}"
        for name, path in (
            ("Sans", fonts["plex"]),
            ("Mono", fonts["mono"]),
            ("MonoB", fonts["bold"]),
        )
    )
    pad, mono = shape["pad"], shape["mono"]
    return f"""{faces}
*{{box-sizing:border-box;margin:0}}
html,body{{width:{shape["width"]}px;height:{shape["height"]}px;background:{BG};overflow:hidden}}
body{{color:{FG};font:{mono}px/1.45 Mono}}
b{{font-family:MonoB;font-weight:400}}
.dim{{color:{DIM}}}.prompt{{color:{PROMPT}}}.bad{{color:{RED}}}.good{{color:{GREEN}}}
.label{{font:{shape["label"]}px/1 MonoB;color:{ACCENT};letter-spacing:.06em;
  text-transform:uppercase}}
.count{{font:{shape["label"]}px/1 Mono;color:{DIM}}}
.note{{font:{shape["note"]}px/1.25 Sans;color:{FG};margin-top:{pad * 0.3}px}}
.source{{font-size:{shape["label"]}px;color:{DIM};margin-bottom:{pad * 0.45}px}}
pre{{font:inherit;white-space:pre-wrap}}
pre+pre,pre+table,pre+.row{{margin-top:{pad * 0.6}px}}
table{{border-collapse:collapse}}
td{{padding:{mono * 0.28}px {pad * 0.5}px {mono * 0.28}px 0;white-space:nowrap}}
.row{{padding:{mono * 0.3}px 0;border-bottom:1px solid #21262D}}
.badge{{display:inline-block;font:{mono * 0.78}px/1.3 MonoB;font-style:normal;border-radius:4px;
  padding:0 .35em;margin-right:.5em;color:{BG};vertical-align:.08em}}
.badge.A{{background:{OTHER}}}.badge.B{{background:{ACCENT}}}
.ref.A{{color:{OTHER}}}.ref.B{{color:{ACCENT}}}
.differs{{font:{shape["label"]}px Mono;color:{ACCENT};border:1px solid {ACCENT};border-radius:4px;
  padding:.1em .45em;white-space:nowrap}}
.crop{{display:block;width:100%;border-radius:6px}}
main>table,main>.focus{{align-self:flex-start}}
.change{{display:flex;flex-wrap:wrap;gap:0 .8em;padding:{mono * 0.2}px 0}}
.change .dim{{min-width:4.2em}}
{".change{flex-direction:column}" if shape is NARROW else ""}
"""


def scene_page(
    fonts: dict[str, Path], shape: dict[str, int], label: str, note: str, body: str, n: int
) -> str:
    pad = shape["pad"]
    css = (
        style(fonts, shape)
        + f"""
body{{display:grid;grid-template-rows:auto 1fr}}
header{{padding:{pad * 0.8}px {pad}px {pad * 0.6}px;border-bottom:1px solid #21262D}}
header div{{display:flex;justify-content:space-between;align-items:baseline}}
main{{display:flex;flex-direction:column;justify-content:center;min-height:0;
  padding:{pad * 0.5}px {pad}px {pad * 0.9}px}}
.focus{{font-size:1.15em}}
{"main{justify-content:flex-start;padding-top:" + str(pad * 1.1) + "px}" if shape is NARROW else ""}
"""
    )
    return (
        f"<!doctype html><meta charset=utf-8><style>{css}</style>"
        f'<header><div><span class="label">{label}</span>'
        f'<span class="count">{n + 1} / 5</span></div>'
        f'<p class="note">{html.escape(note)}</p></header><main>{body}</main>'
    )


def summary_page(fonts: dict[str, Path], shape: dict[str, int], e: dict[str, object]) -> str:
    """One compact still of the same steps, the size of the GIF, for reduced motion."""
    pad, wide = shape["pad"], shape is DESKTOP
    mono = round(shape["mono"] * (0.78 if shape is DESKTOP else 0.8))
    columns = 3 if wide else 1
    gap = pad * 0.5
    inner = (shape["width"] - 2 * pad - (columns - 1) * gap) / columns - 2 * pad * 0.6
    small = {**shape, "mono": mono, "cols": int(inner / (mono * 0.6)) - 1}
    letters = e["letters"]
    assert isinstance(letters, dict)
    refs = reference_rows(e["refs"], letters, not wide)  # type: ignore[arg-type]
    rule, contract = str(e["contract_rule"]).split(": ", 1)
    diagnosis = terminal(small, str(e["rejected"]), str(e["rule"]), f"{rule}:", contract)
    panels = [
        ("1 · Plan references", refs),
        ("2 · Diagnosis", diagnosis),
        ("3 · Manual edit", edit(e, source=False)),
        ("4 · Recheck", terminal(small, str(e["passed"]))),
    ]
    css = (
        style(fonts, small)
        + f"""
body{{display:grid;grid-template-rows:auto 1fr;gap:{gap}px;
  padding:{pad * 0.6}px {pad}px {pad * 0.6}px}}
.top{{font-size:{shape["label"]}px;color:{DIM}}}
.grid{{display:grid;grid-template-columns:repeat({columns},1fr);gap:{gap}px;min-height:0;
  grid-template-rows:{"auto 1fr" if wide else "auto auto auto 1fr"}}}
section{{background:{STRIP_BG};border:1px solid #21262D;border-radius:8px;padding:{pad * 0.6}px;
  overflow:hidden;min-height:0}}
section:first-child{{grid-column:1/-1}}
section .label{{display:block;margin-bottom:{pad * 0.3}px}}
section .change{{flex-direction:column}}
"""
    )
    body = "".join(f'<section><span class="label">{t}</span>{c}</section>' for t, c in panels)
    return (
        f"<!doctype html><meta charset=utf-8><style>{css}</style>"
        f'<div class="top">Summary of the walkthrough · authored example · {FAIL}</div>'
        f'<div class="grid">{body}</div>'
    )


def shot(page: object, tmp: Path, markup: str, name: str, check: str) -> Image.Image:
    """Screenshot one page, refusing it if any checked element overflows."""
    path = tmp / f"{name}.html"
    path.write_text(markup)
    page.goto(path.as_uri())  # type: ignore[attr-defined]
    page.wait_for_timeout(300)  # type: ignore[attr-defined]
    overflow = page.evaluate(  # type: ignore[attr-defined]
        f"() => Math.max(...[...document.querySelectorAll('{check}')]"
        ".flatMap(m => [m.scrollHeight - m.clientHeight, m.scrollWidth - m.clientWidth]))"
    )
    assert overflow <= 0, (name, overflow)
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")  # type: ignore[attr-defined]


def build_walkthrough(
    fonts: dict[str, Path], out: dict[str, str], report: Path, browser: object, tmp: Path
) -> None:
    e = evidence(out)
    for shape, stem in ((DESKTOP, "demo"), (NARROW, "demo-narrow")):
        crop = inspector_crop(report, browser, tmp, shape)
        planned = scenes(e, crop, shape)
        total = sum(ms for *_, ms in planned) / 1000
        assert 12 <= total <= 18, total
        context = browser.new_context(  # type: ignore[attr-defined]
            viewport={"width": shape["width"], "height": shape["height"]}
        )
        page = context.new_page()

        frames = [
            (
                shot(
                    page, tmp, scene_page(fonts, shape, label, note, body, n), f"{stem}-{n}", "main"
                ),
                ms,
            )
            for n, (label, note, body, ms) in enumerate(planned)
        ]
        still = shot(page, tmp, summary_page(fonts, shape, e), f"{stem}-summary", "section")
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
        assert still.size == (width, height)
        still.quantize(colors=96, dither=Image.Dither.NONE).save(
            MEDIA / f"{stem}.png", optimize=True
        )
        print(
            f"{stem}.gif: {len(frames)} scenes, {total:.1f}s, {width}x{height};",
            f"{stem}.png same size",
        )


def text_path(font: TTFont, text: str, size: float, x: float, baseline: float) -> tuple[str, float]:
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    for char in text:
        name = cmap[ord(char)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        x += hmtx[name][0] * scale
    return pen.getCommands(), x


def mark_svg(ink: str, accent: str, viewbox: str = "0 0 64 64", lettering: str = "") -> str:
    """The approved "Planform" mark, exactly as supplied, with optional lettering."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}"\n'
        '     role="img" aria-label="PlanLens">\n'
        f'  <path fill="{ink}" fill-rule="evenodd"\n'
        f'    d="{MARK_PATH}"/>\n'
        f'  <rect x="43" y="47" width="12" height="12" rx="3" fill="{accent}"/>\n'
        f"{lettering}</svg>\n"
    )


def text_bounds(
    font: TTFont, text: str, size: float, x: float, baseline: float
) -> tuple[float, ...]:
    """Bounds of the same outlines text_path draws, from the glyph curves themselves."""
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = size / font["head"].unitsPerEm
    pen = BoundsPen(glyphs)
    for char in text:
        name = cmap[ord(char)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        x += hmtx[name][0] * scale
    assert pen.bounds is not None
    return tuple(pen.bounds)


def build_brand(fonts: dict[str, Path]) -> None:
    BRAND.mkdir(exist_ok=True)
    plex = TTFont(fonts["plex"])
    outline, _ = text_path(plex, "PlanLens", 34, 62, 35)
    x0, y0, x1, y1 = text_bounds(plex, "PlanLens", 34, 62, 35)
    left, top, right, bottom = x0 * 1.35, y0 * 1.35 - 1, x1 * 1.35, y1 * 1.35 - 1
    assert 55 < left and right <= 286 and 0 <= top and bottom <= 64, (left, top, right, bottom)
    for name, (ink, accent) in BRAND_COLOURS.items():
        lettering = (
            '  <g transform="translate(0,-1) scale(1.35)">\n'
            f'    <path fill="{ink}" d="{outline}"/>\n'
            "  </g>\n"
        )
        (BRAND / f"planlens-mark-{name}.svg").write_text(mark_svg(ink, accent))
        (BRAND / f"planlens-wordmark-{name}.svg").write_text(
            mark_svg(ink, accent, "0 0 286 64", lettering)
        )
    print(f"lockup lettering spans x {left:.1f}-{right:.1f}, y {top:.1f}-{bottom:.1f} of 286x64")


def build_social(fonts: dict[str, Path], out: dict[str, str], browser: object, tmp: Path) -> None:
    e = evidence(out)
    letters = e["letters"]
    assert isinstance(letters, dict)
    lockup = (BRAND / "planlens-wordmark-dark.svg").read_text()
    rows = reference_rows(e["refs"], letters, False)  # type: ignore[arg-type]
    css = (
        style(fonts, {**DESKTOP, "width": 1280, "height": 640, "mono": 26})
        + f"""
body{{padding:64px 80px;display:flex;flex-direction:column;justify-content:space-between}}
.lockup svg{{height:60px;width:auto;display:block}}
h1{{font:58px/1.12 Sans;margin-top:30px;letter-spacing:-.5px}}
.result{{margin-top:16px}}
.tag{{position:absolute;right:80px;top:84px;font:20px Mono;color:{DIM}}}
"""
    )
    (tmp / "social.html").write_text(
        f"<!doctype html><meta charset=utf-8><style>{css}</style>"
        f'<div><div class="lockup">{lockup}</div><h1>Catch plan errors before execution.</h1></div>'
        f'<div>{rows}<pre class="result">{paint(str(e["rejected"]))}\n'
        f"<b>{html.escape(str(e['rule']))}</b>  {html.escape(str(e['contract_rule']))}</pre></div>"
        '<div class="tag">Authored example</div>'
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
