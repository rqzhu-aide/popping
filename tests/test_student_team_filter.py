"""Regression coverage for the instructor Student Management team filter."""

from html.parser import HTMLParser

from test_workflow_safety import _connect, _instructor_client, course_env


class _TeamFilterOptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_team_filter = False
        self.select_attributes = {}
        self.current_option = None
        self.options = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "select" and attributes.get("id") == "team-filter-select":
            self.in_team_filter = True
            self.select_attributes = attributes
        elif tag == "option" and self.in_team_filter:
            self.current_option = {
                "raw_attrs": attrs,
                "attributes": attributes,
                "text": [],
            }

    def handle_data(self, data):
        if self.current_option is not None:
            self.current_option["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self.current_option is not None:
            self.options.append(
                (
                    self.current_option["raw_attrs"],
                    self.current_option["attributes"],
                    "".join(self.current_option["text"]).strip(),
                )
            )
            self.current_option = None
        elif tag == "select" and self.in_team_filter:
            self.in_team_filter = False


def test_native_team_filter_safely_renders_quoted_team_name(course_env):
    """Quoted names remain option text and never become event attributes."""
    team_id = course_env["teams"]["Team 1"]
    team_name = 'Team "Alpha" O\'Brien'
    with _connect(course_env) as db:
        db.execute(
            "UPDATE teams SET name = ? WHERE id = ?",
            (team_name, team_id),
        )
        db.commit()

    page = _instructor_client(course_env).get(
        f"/instructor/{course_env['slug']}"
    )
    assert page.status_code == 200

    parser = _TeamFilterOptionParser()
    parser.feed(page.get_data(as_text=True))
    matches = [
        (raw_attrs, attributes, text)
        for raw_attrs, attributes, text in parser.options
        if attributes.get("value") == str(team_id)
    ]

    assert parser.select_attributes["id"] == "team-filter-select"
    assert len(matches) == 1
    raw_attrs, attributes, text = matches[0]
    assert text == team_name
    assert len(raw_attrs) == len(attributes), "duplicate attributes were parsed"
    assert set(attributes) == {"value"}
    assert not any(name.lower().startswith("on") for name in attributes)


def test_student_api_filters_numeric_team_and_unassigned_with_totals(
        course_env):
    client = _instructor_client(course_env)
    team_1 = course_env["teams"]["Team 1"]

    first_page = client.get(
        "/api/students",
        query_string={"team": team_1, "page": 1, "per_page": 1},
    )
    assert first_page.status_code == 200
    first_payload = first_page.get_json()
    assert [student["student_id"] for student in first_payload["students"]] == [
        "s1"
    ]
    assert first_payload["total"] == 2
    assert first_payload["total_pages"] == 2
    assert first_payload["course_total"] == 4

    second_payload = client.get(
        "/api/students",
        query_string={"team": team_1, "page": 2, "per_page": 1},
    ).get_json()
    assert [student["student_id"] for student in second_payload["students"]] == [
        "s2"
    ]
    assert second_payload["total"] == 2

    unassigned = client.get(
        "/api/students",
        query_string={"team": "none", "per_page": 100},
    )
    assert unassigned.status_code == 200
    unassigned_payload = unassigned.get_json()
    assert [
        student["student_id"] for student in unassigned_payload["students"]
    ] == ["s3"]
    assert unassigned_payload["total"] == 1
    assert unassigned_payload["total_pages"] == 1
    assert unassigned_payload["course_total"] == 4
