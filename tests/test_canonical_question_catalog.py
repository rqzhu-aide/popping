"""Unit regressions for the canonical weekly Markdown question source."""

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from question_catalog import (  # noqa: E402
    parse_week_questions,
    read_week_questions,
)


def test_parser_preserves_order_ids_titles_bodies_and_source_keys():
    source = """---
id: second-in-lecture
title: Second in lecture
---

Body with $x^2$ and a horizontal rule.

---

Still in the first body.

---
id: code-question
title: Code question
---

```python
print("hello")
```
"""

    questions = parse_week_questions(
        source, source_path="week-7-questions.md"
    )

    assert questions == [
        {
            "id": "second-in-lecture",
            "num": 1,
            "title": "Second in lecture",
            "content": (
                "Body with $x^2$ and a horizontal rule.\n\n"
                "---\n\nStill in the first body."
            ),
            "source_key": "week-7-q-second-in-lecture",
        },
        {
            "id": "code-question",
            "num": 2,
            "title": "Code question",
            "content": "```python\nprint(\"hello\")\n```",
            "source_key": "week-7-q-code-question",
        },
    ]


def test_reader_accepts_bom_and_enforces_question_limit(tmp_path):
    path = tmp_path / "week-3-questions.md"
    path.write_text(
        """---
id: first
title: First
---

First body.

---
id: second
title: Second
---

Second body.
""",
        encoding="utf-8-sig",
    )

    questions = read_week_questions(path)

    assert [question["id"] for question in questions] == ["first", "second"]
    assert [question["source_key"] for question in questions] == [
        "week-3-q-first", "week-3-q-second",
    ]
    with pytest.raises(ValueError, match="at most 1"):
        read_week_questions(path, max_questions=1)


@pytest.mark.parametrize(
    ("source", "message"),
    (
        (
            """---
id: repeated
title: First
---

First body.

---
id: REPEATED
title: Second
---

Second body.
""",
            "duplicate stable id",
        ),
        (
            """---
title: Missing ID
---

Body.
""",
            "missing stable id",
        ),
        (
            """---
id: empty-body
title: Empty body
---
""",
            "no content",
        ),
    ),
)
def test_parser_rejects_ambiguous_or_incomplete_identity(source, message):
    with pytest.raises(ValueError, match=message):
        parse_week_questions(source, week_num=1)


def test_parser_rejects_later_id_first_block_without_title():
    source = """---
id: valid
title: Valid question
---

Valid body.

---
id: missing-title
---

This later block must not be folded into the first body.
"""

    with pytest.raises(ValueError, match="frontmatter"):
        parse_week_questions(source, week_num=1)
