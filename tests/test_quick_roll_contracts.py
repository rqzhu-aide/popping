"""Source contracts for the instructor-only Quick Roll widget."""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path):
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _between(source, start, end):
    start_index = source.index(start)
    return source[start_index:source.index(end, start_index)]


def _css_rule(css, selector):
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", css, re.DOTALL)
    assert match, f"Missing CSS rule: {selector}"
    return match.group(1)


def test_quick_roll_markup_is_instructor_only_and_accessible():
    instructor = _read("templates/instructor.html")
    assert instructor.count('id="quick-roll-widget"') == 1
    assert "{% if session.instructor_id and session.slug %}" in instructor
    assert 'role="dialog" aria-modal="false"' in instructor
    assert 'aria-labelledby="quick-roll-title"' in instructor
    assert 'aria-describedby="quick-roll-description"' in instructor
    assert 'aria-haspopup="dialog" aria-expanded="false"' in instructor
    assert 'aria-controls="quick-roll-panel"' in instructor
    assert 'id="quick-roll-form" novalidate' in instructor
    assert 'onsubmit="return false"' in instructor
    assert 'type="number" min="1" max="1000000" step="1"' in instructor
    assert 'role="status" aria-live="polite" aria-atomic="true"' in instructor
    assert "nothing is saved" in instructor

    for template_path in (PROJECT_ROOT / "templates").glob("*.html"):
        if template_path.name == "instructor.html":
            continue
        assert "quick-roll-widget" not in template_path.read_text(
            encoding="utf-8"
        )


def test_quick_roll_logic_is_local_bounded_and_unbiased():
    source = _read("static/js/app.js")
    block = _between(
        source,
        "/* ===== INSTRUCTOR QUICK ROLL ===== */",
        "/* ===== END INSTRUCTOR QUICK ROLL ===== */",
    )

    assert "const QUICK_ROLL_MAXIMUM = 1000000;" in block
    assert "cryptoApi.getRandomValues(sample)" in block
    assert "const limit = range - (range % maximum);" in block
    assert "while (sample[0] >= limit);" in block
    assert "Math.random" not in block
    for forbidden in (
        "fetch(",
        "postJSON",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    ):
        assert forbidden not in block

    root_lookup = block.index(
        "const widget = document.getElementById('quick-roll-widget');"
    )
    null_guard = block.index("if (!widget) return;", root_lookup)
    child_lookup = block.index(
        "const panel = document.getElementById('quick-roll-panel');"
    )
    assert root_lookup < null_guard < child_lookup
    assert "event.key !== 'Escape'" in block
    assert "event.preventDefault();" in block
    assert "widget.contains(event.target)" in block


def test_quick_roll_visual_contract_avoids_existing_fixed_controls():
    css = _read("static/css/style.css")
    widget = _css_rule(css, ".quick-roll-widget")
    trigger = _css_rule(css, ".quick-roll-trigger")
    panel = _css_rule(css, ".quick-roll-panel")
    quick_roll_page = _css_rule(
        css, "main.instructor-container:has(.quick-roll-widget)"
    )

    assert "position: fixed;" in widget
    assert "left:" in widget
    assert "bottom:" in widget
    assert "right:" not in widget
    assert "z-index: 450;" in widget
    assert "height: 52px;" in trigger
    assert "width: 52px;" in trigger
    assert "bottom: 64px;" in panel
    assert "width: min(320px, calc(100vw - 36px));" in panel
    assert "max-height: calc(100dvh - 160px" in panel
    assert "overflow-y: auto;" in panel
    assert "padding-bottom: 108px;" in quick_roll_page
    assert ".quick-roll-trigger:focus-visible" in css
    assert "env(safe-area-inset-bottom)" in css
