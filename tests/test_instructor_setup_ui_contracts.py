"""Focused source contracts for the instructor Setup roster and Tools menu."""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path):
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _between(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def _css_rule(css, selector):
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", css, re.DOTALL)
    assert match, f"Missing CSS rule: {selector}"
    return match.group(1)


def test_setup_roster_combines_instructor_identity_with_turn_count():
    template = _read("templates/instructor.html")
    setup_roster = _between(
        template,
        "<h2>Team and Members</h2>",
        "<h2>Student Management</h2>",
    )

    assert 'class="roster-member setup-roster-member"' in setup_roster
    assert 'class="setup-roster-member-body"' in setup_roster
    assert 'class="setup-roster-member-topline"' in setup_roster
    assert re.search(
        r'class="setup-roster-member-identity"[^>]*>\s*'
        r'\{\{ s \| instructor_display_name \}\}',
        setup_roster,
    )
    assert re.search(
        r'class="[^"]*\bsetup-roster-team-turns\b[^"]*"',
        setup_roster,
    )
    assert 'class="setup-roster-member-id"' not in setup_roster
    assert 'class="setup-roster-member-name"' not in setup_roster
    assert "s | display_name" not in setup_roster

    # DOM order is also the screen-reader reading order.
    assert (
        setup_roster.index("setup-roster-member-identity")
        < setup_roster.index("setup-roster-team-turns")
    )


def test_setup_roster_live_refresh_uses_the_same_explicit_layout():
    source = _read("static/js/app.js")

    assert (
        "function instructorSetupRosterMemberHtml(member, teamColor) {"
        in source
    )
    helper = _between(
        source,
        "function instructorSetupRosterMemberHtml(member, teamColor) {",
        "function renderRosterGrid(",
    )
    for class_name in (
        "setup-roster-member",
        "setup-roster-member-body",
        "setup-roster-member-topline",
        "setup-roster-member-identity",
        "setup-roster-team-turns",
    ):
        assert class_name in helper
    assert "instructorDisplayName(member)" in helper
    assert "escapeHtmlValue(displayName)" in helper
    assert "presentation_count" in helper
    assert "challenger_count" not in helper
    assert "formatStudentDisplay" not in helper

    assert re.search(
        r"function renderRosterGrid\(container, teams, myId, options\s*=\s*\{\}\)",
        source,
    )
    assert "options.instructorSetup === true" in source
    assert re.search(
        r"renderRosterGrid\(\s*setupRosterGrid,\s*rosterData,\s*null,\s*"
        r"\{\s*instructorSetup:\s*true\s*\}\s*\)",
        source,
    )

    # The student roster keeps its existing renderer mode. Participation
    # counts remain protected by the role-specific API contract elsewhere.
    assert (
        "renderRosterGrid(document.getElementById('roster-table'), "
        "teams, dashboard.dataset.you);"
        in source
    )


def test_setup_roster_css_keeps_the_topline_usable_in_narrow_team_cards():
    css = _read("static/css/style.css")

    member = _css_rule(css, ".instructor .setup-roster-member")
    assert "align-items: flex-start;" in member
    assert "flex-wrap: nowrap;" in member

    body = _css_rule(css, ".setup-roster-member-body")
    assert "display: flex;" in body
    assert "flex-direction: column;" in body
    assert "min-width: 0;" in body

    topline = _css_rule(css, ".setup-roster-member-topline")
    assert "display: flex;" in topline
    assert "align-items: center;" in topline
    assert "min-width: 0;" in topline

    identity = _css_rule(css, ".setup-roster-member-identity")
    assert "min-width: 0;" in identity
    assert "overflow-wrap: anywhere;" in identity

    turns = _css_rule(css, ".setup-roster-team-turns")
    assert "flex: 0 0 auto;" in turns


def test_tools_menu_visually_and_semantically_separates_reset_action():
    template = _read("templates/base.html")
    css = _read("static/css/style.css")
    tools_menu = _between(
        template,
        '<div class="nav-dropdown-menu" id="tools-menu">',
        '<form method="post" action="{{ url_for(\'logout\') }}"',
    )

    separator = re.search(
        r'<div\s+class="nav-dropdown-separator"\s+role="separator"\s*></div>',
        tools_menu,
    )
    assert separator
    reset_button = re.search(
        r'<button[^>]*class="[^"]*\bnav-dropdown-danger\b[^"]*"[^>]*'
        r'id="reset-course-data-menu-item"[^>]*>\s*Reset Course Data\s*</button>',
        tools_menu,
        re.DOTALL,
    )
    assert reset_button
    assert separator.start() < reset_button.start()
    assert 'type="button"' in reset_button.group(0)
    assert "resetData()" in reset_button.group(0)

    disabled_reset = re.search(
        r'<span[^>]*class="[^"]*\bnav-dropdown-danger\b[^"]*\bdisabled\b[^"]*"'
        r'[^>]*id="reset-course-data-menu-item"[^>]*aria-disabled="true"[^>]*>',
        tools_menu,
        re.DOTALL,
    )
    assert disabled_reset

    separator_rule = _css_rule(css, ".nav-dropdown-separator")
    assert "border-top:" in separator_rule

    danger_rule = _css_rule(css, ".nav-dropdown-danger")
    assert "color: var(--error-text);" in danger_rule
    assert ".nav-dropdown-danger:not(.disabled):hover" in css
    assert ".nav-dropdown-danger:focus-visible" in css
    assert "background: var(--error-soft);" in css


def test_tools_menu_groups_the_roster_download_with_roster_actions():
    template = _read("templates/base.html")
    tools_menu = _between(
        template,
        '<div class="nav-dropdown-menu" id="tools-menu">',
        '<form method="post" action="{{ url_for(\'logout\') }}"',
    )

    non_demo = tools_menu.index("{% if not session.get('is_demo') %}")
    download = tools_menu.index("Download Student Roster")
    upload = tools_menu.index("Upload Student Roster")
    roster_template = tools_menu.index("Download Roster Template")
    separator = tools_menu.index('class="nav-dropdown-separator"')
    assert non_demo < download < upload < roster_template < separator
    assert "url_for('download_student_roster', slug=session.slug)" in tools_menu
    assert 'aria-label="Download Student Roster as CSV"' in tools_menu
    assert '<span aria-hidden="true">⬇</span>' in tools_menu
