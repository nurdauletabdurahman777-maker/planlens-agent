"""Render inspector reports in a real browser and check how they behave.

Developer tool, not a test dependency. Needs Playwright and an installed
Chrome or Chromium:

    uv pip install playwright        # in a separate tools environment
    python scripts/browser_check.py REPORT_DIR SCREENSHOT_DIR

REPORT_DIR must contain the reports written by ``planlens-agent inspect`` under
these names: valid, reversed-review, artifact-mismatch, compound, invalid-plan,
invalid-policy, unsupported-rule, hostile, large-rejected, over-budget (each ``.html``).
Every check prints PASS or FAIL; the script exits 1 if any check fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def watch(page: Page) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {"errors": [], "requests": []}
    page.on("console", lambda m: seen["errors"].append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda exc: seen["errors"].append(str(exc)))
    page.on(
        "request", lambda r: None if r.url.startswith("file:") else seen["requests"].append(r.url)
    )
    return seen


def focused(page: Page) -> list[int]:
    return page.eval_on_selector_all(
        ".diagram .node.is-focus", "els => els.map(e => +e.dataset.step)"
    )


def main(reports: Path, shots: Path) -> int:
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        record("browser", True, f"Chrome {browser.version}")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})

        for name in (
            "valid",
            "reversed-review",
            "artifact-mismatch",
            "compound",
            "invalid-plan",
            "invalid-policy",
            "unsupported-rule",
            "hostile",
            "large-rejected",
            "over-budget",
        ):
            page = ctx.new_page()
            seen = watch(page)
            page.goto((reports / f"{name}.html").resolve().as_uri())
            page.wait_for_load_state("load")
            record(
                f"{name}: no console errors or CSP violations",
                not seen["errors"],
                "; ".join(seen["errors"])[:200],
            )
            record(
                f"{name}: no network requests",
                not seen["requests"],
                ", ".join(seen["requests"])[:200],
            )
            verdict = page.inner_text(".verdict .label")
            record(f"{name}: verdict shown", bool(verdict), verdict)
            page.screenshot(path=str(shots / f"{name}-desktop.png"), full_page=True)
            page.close()

        # artifact mismatch: the auto-selected finding focuses all three compared bindings
        page = ctx.new_page()
        page.goto((reports / "artifact-mismatch.html").resolve().as_uri())
        steps = focused(page)
        record(
            "artifact-mismatch: selection focuses steps 2, 3 and 5", steps == [2, 3, 5], str(steps)
        )
        producers = page.eval_on_selector_all(
            ".diagram .node.is-producer", "els => els.map(e => +e.dataset.step)"
        )
        record(
            "artifact-mismatch: producers of both compared artifacts emphasised",
            producers == [0, 1],
            str(producers),
        )
        badges = page.eval_on_selector_all(
            ".diagram .badge text", "els => els.map(e => e.textContent)"
        )
        record(
            "artifact-mismatch: diagram labels the groups A, A, B",
            badges == ["A", "A", "B"],
            str(badges),
        )
        groups = page.eval_on_selector_all(
            "[data-same] tbody tr", "rs => rs.map(r => r.innerText.replace(/\\s+/g, ' '))"
        )
        record(
            "artifact-mismatch: all three compared bindings listed with groups",
            len(groups) == 3 and sum("A" in g for g in groups) >= 2,
            json.dumps(groups),
        )
        note = page.inner_text("[data-same] .note:last-of-type")
        record(
            "artifact-mismatch: does not claim to know the wrong reference",
            "cannot tell which" in note,
            note[:120],
        )
        page.locator(".diagram").screenshot(path=str(shots / "artifact-mismatch-diagram.png"))
        page.locator("#findings").screenshot(path=str(shots / "artifact-mismatch-finding.png"))
        page.close()

        # reversed prerequisite: the selected rule's relation is drawn and marked "listed later"
        page = ctx.new_page()
        page.goto((reports / "reversed-review.html").resolve().as_uri())
        arcs = page.locator(".diagram .arc.pre.backward.is-focus").count()
        record(
            "reversed-review: selected rule's relation drawn as listed-later", arcs == 1, str(arcs)
        )
        rows = page.locator("tr[data-prereq].is-focus").count()
        record(
            "reversed-review: selected rule highlighted in the requirements", rows == 1, str(rows)
        )
        page.locator(".diagram").screenshot(path=str(shots / "reversed-review-diagram.png"))
        page.close()

        # mouse selection toggles
        page = ctx.new_page()
        page.goto((reports / "compound.html").resolve().as_uri())
        page.click('[data-finding-select="f2"]')
        record(
            "compound: clicking a finding selects it", focused(page) == [2, 3], str(focused(page))
        )
        page.click('[data-finding-select="f2"]')
        record(
            "compound: clicking it again clears the selection",
            page.evaluate("document.body.classList.contains('has-selection')") is False,
        )
        page.click('a[data-goto-finding="f3"]')
        record(
            "compound: a step's finding link selects that finding",
            focused(page) == [4],
            str(focused(page)),
        )
        page.close()

        # compound: keyboard moves between findings and the focus follows
        page = ctx.new_page()
        page.goto((reports / "compound.html").resolve().as_uri())
        page.keyboard.press("Tab")  # skip link
        box = page.evaluate("""() => { const r = document.activeElement.getBoundingClientRect();
            return [document.activeElement.textContent, r.width, r.height] }""")
        record(
            "compound: skip link becomes visible when focused",
            box[0] == "Skip to findings" and box[1] > 20 and box[2] > 10,
            str(box),
        )
        page.keyboard.press("Tab")  # first finding
        first = page.evaluate("document.activeElement.dataset.findingSelect")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        third = page.evaluate("document.activeElement.dataset.findingSelect")
        pressed = page.get_attribute(f'[data-finding-select="{third}"]', "aria-pressed")
        record(
            "compound: Tab reaches findings, arrows move selection",
            first == "f1" and third == "f3" and pressed == "true",
            f"{first} -> {third}",
        )
        outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        record(
            "compound: focused finding has a visible outline", outline not in ("none", ""), outline
        )
        record(
            "compound: selected unknown action focuses its step",
            focused(page) == [4],
            str(focused(page)),
        )
        page.screenshot(path=str(shots / "compound-keyboard.png"), full_page=False)
        page.keyboard.press("Enter")
        toggled = page.evaluate("document.body.classList.contains('has-selection')")
        page.keyboard.press("Enter")
        record(
            "compound: Enter toggles the focused finding",
            toggled is False
            and page.evaluate("document.body.classList.contains('has-selection')") is True,
        )
        page.keyboard.press("End")
        last = page.evaluate("document.activeElement.dataset.findingSelect")
        absent = page.eval_on_selector_all(
            "[data-required].is-focus", "els => els.map(e => e.innerText)"
        )
        record(
            "compound: missing action highlights the absent requirement, no step",
            last == "f4" and focused(page) == [] and any("absent" in a for a in absent),
            f"{last} {absent}",
        )
        page.keyboard.press("Escape")
        record(
            "compound: Escape clears the selection",
            page.evaluate("document.body.classList.contains('has-selection')") is False,
        )
        page.close()

        # invalid input and invalid policy: no invented graph
        for name in ("invalid-plan", "invalid-policy"):
            page = ctx.new_page()
            page.goto((reports / f"{name}.html").resolve().as_uri())
            record(
                f"{name}: no sequence diagram or step table rendered",
                page.locator(".diagram").count() == 0 and page.locator("[data-step]").count() == 0,
            )
            record(
                f"{name}: explains the plan was not evaluated",
                "Plan not evaluated" in page.inner_text("body"),
            )
            page.close()

        # hostile strings render as text and nothing they contain executes
        page = ctx.new_page()
        page.goto((reports / "hostile.html").resolve().as_uri())
        pwned = page.evaluate("window.__pwned === undefined")
        record("hostile: no injected script or handler executed", pwned)
        h1 = page.inner_text("h1")
        record(
            "hostile: title shown literally",
            "</script><script>" in h1 and "<b>bold</b>" in h1,
            h1[:120],
        )
        record(
            "hostile: no injected elements exist",
            page.locator("img, b, iframe, object, embed").count() == 0
            and page.locator("svg[onload]").count() == 0,
        )
        page.close()

        # narrow viewport
        narrow = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        for name in ("artifact-mismatch", "compound"):
            page = narrow.new_page()
            page.goto((reports / f"{name}.html").resolve().as_uri())
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            record(f"{name} @390px: no horizontal page scroll", overflow <= 0, f"{overflow}px")
            clipped = page.evaluate("""() => [...document.querySelectorAll('.tablewrap td')]
                .filter(c => c.getBoundingClientRect().right > innerWidth + 1).length""")
            record(f"{name} @390px: no table cell is cut off", clipped == 0, str(clipped))
            labels = page.evaluate("""() => getComputedStyle(
                document.querySelector('.tablewrap td[data-label]'), '::before').content""")
            record(
                f"{name} @390px: stacked cells carry their column label",
                labels not in ("none", "normal", '""'),
                labels,
            )
            font = page.evaluate("""() => {
                const t = document.querySelector('.diagram .node text:not(.idx)');
                return t.getBoundingClientRect().height }""")
            record(f"{name} @390px: diagram text stays readable", font >= 11, f"{font:.1f}px tall")
            page.locator("section[aria-labelledby=h-seq]").screenshot(
                path=str(shots / f"{name}-narrow-sequence.png")
            )
            page.screenshot(path=str(shots / f"{name}-narrow.png"), full_page=True)
            page.close()

        # raw evidence: closed at first, opened natively, printed in full
        page = ctx.new_page()
        page.goto((reports / "artifact-mismatch.html").resolve().as_uri())
        raw = page.locator("details.raw")
        visible_raw = "details.raw pre"
        record(
            "disclosure: raw data closed by default",
            raw.get_attribute("open") is None and not page.locator(visible_raw).first.is_visible(),
        )
        panel = page.inner_text("#findings")
        record(
            "disclosure: compact evidence stays visible",
            "build_hotfix.artifact" in panel
            and "input artifact" in panel
            and "cannot tell which reference" in panel,
            panel[:0],
        )
        page.locator("details.raw summary").focus()
        page.keyboard.press("Enter")
        record(
            "disclosure: Enter opens it",
            raw.get_attribute("open") is not None and page.locator(visible_raw).first.is_visible(),
        )
        page.locator("#findings").screenshot(path=str(shots / "disclosure-open.png"))
        page.keyboard.press("Enter")
        record("disclosure: Enter closes it", raw.get_attribute("open") is None)
        page.emulate_media(media="print")
        height = page.evaluate(
            "document.querySelector('details.raw pre').getBoundingClientRect().height"
        )
        record("disclosure: raw data is included when printing", height > 0, f"{height:.0f}px")
        page.close()

        # large reports: past ZZ, and over the diagram budget
        page = ctx.new_page()
        page.goto((reports / "large-rejected.html").resolve().as_uri())
        record(
            "large-rejected: AAA group label shown",
            page.locator('[aria-label="group AAA"]').count() >= 1,
        )
        record(
            "large-rejected: diagram omitted, table has all 726 steps",
            page.locator(".diagramwrap").count() == 0
            and page.locator("section[aria-labelledby=h-seq] tbody tr").count() == 726,
        )
        record(
            "large-rejected: finding selected on load",
            page.locator("tr[data-step].is-focus").count() >= 700,
        )
        short = page.inner_text("#findings")
        record("large-rejected: long evidence shortened", "more" in short, "")
        page.screenshot(path=str(shots / "large-rejected.png"), full_page=False)
        page.close()
        page = ctx.new_page()
        page.goto((reports / "over-budget.html").resolve().as_uri())
        body = page.inner_text("body")
        record(
            "over-budget: passes, explains the omitted diagram, keeps the tables",
            "PASS" in body and "too many relationships" in body and "review-first" in body,
        )
        page.close()

        # dark colour scheme
        dark = browser.new_context(color_scheme="dark", viewport={"width": 1440, "height": 1000})
        page = dark.new_page()
        page.goto((reports / "artifact-mismatch.html").resolve().as_uri())
        background = page.evaluate("getComputedStyle(document.body).backgroundColor")
        record(
            "artifact-mismatch: dark colour scheme applied",
            background != "rgb(246, 247, 249)",
            background,
        )
        page.screenshot(path=str(shots / "artifact-mismatch-dark.png"), full_page=False)
        page.close()

        # scripting disabled: findings remain readable
        nojs = browser.new_context(
            java_script_enabled=False, viewport={"width": 1280, "height": 900}
        )
        page = nojs.new_page()
        page.goto((reports / "artifact-mismatch.html").resolve().as_uri())
        text = page.inner_text("#findings").lower()  # innerText applies text-transform
        record(
            "artifact-mismatch, scripting off: finding details readable",
            "plan/artifact-identity" in text and "observed" in text and "next" in text,
        )
        closed = page.locator("details.raw pre").first.is_visible()
        page.click("details.raw summary")
        record(
            "scripting off: disclosure opens natively and shows the raw data",
            not closed
            and page.locator("details.raw pre").first.is_visible()
            and "refers_to" in page.inner_text("details.raw"),
        )
        page.screenshot(path=str(shots / "artifact-mismatch-noscript.png"), full_page=False)
        page.close()
        browser.close()

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)} of {len(checks)} browser checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
