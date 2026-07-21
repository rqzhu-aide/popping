"""Small source-level contracts for classroom color accessibility."""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _theme_tokens(css, selector):
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", css, re.DOTALL)
    assert match, f"Missing theme block: {selector}"
    return dict(
        re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", match.group(1))
    )


def _luminance(color):
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.03928
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_semantic_palette_meets_classroom_contrast_targets():
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    dark = _theme_tokens(css, ":root")
    light = {**dark, **_theme_tokens(css, "html.light")}

    for theme in (dark, light):
        for fill in (
            "primary-solid",
            "danger-solid",
            "warning-solid",
            "phase-active-solid",
        ):
            assert _contrast("#ffffff", theme[fill]) >= 4.5

        assert _contrast(theme["primary"], theme["surface"]) >= 4.5
        assert _contrast(theme["text-muted"], theme["surface"]) >= 4.5
        assert _contrast(theme["text-muted"], theme["bg"]) >= 4.5
        assert _contrast(theme["warning-text"], theme["warning-soft"]) >= 4.5
        assert _contrast(theme["error-text"], theme["error-soft"]) >= 4.5
        assert _contrast(theme["rating-idle"], theme["bg"]) >= 4.5
        assert _contrast(theme["rating-active"], theme["bg"]) >= 4.5


def test_dynamic_team_colors_remain_decorative():
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    js = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )

    assert "teamName.style.color" not in js
    assert "banner.style.color = state.active_team.color" not in js
    assert 'style="background:${escapeAttrValue(s.team_color' not in js
    assert 'style="--team-color:${escapeAttrValue(s.team_color' in js
    assert "status.style.color = 'var(--warning-text)'" in js
    assert "status.style.color = 'var(--error-text)'" in js

    team_card = re.search(r"\.team-card\s*\{(.*?)\}", css, re.DOTALL)
    theme_toggle = re.search(r"\.theme-toggle\s*\{(.*?)\}", css, re.DOTALL)
    assert team_card and "color: var(--text)" in team_card.group(1)
    assert team_card and "font-family: inherit" in team_card.group(1)
    assert theme_toggle and "color: var(--text)" in theme_toggle.group(1)
    assert theme_toggle and "font-family: inherit" in theme_toggle.group(1)
    assert ".hljs .hljs-comment" in css

    assert "storedTheme === null || storedTheme === 'light'" in base
