"""Filesystem validation for weekly discussion and presentation questions.

This module does not import the Flask application or touch a database.  It is
intended to be shared by course setup tools and the web application.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Optional, Union

import yaml


DISCUSSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
DISCUSSION_FILE_PATTERN = re.compile(r"^week-(\d+)-questions\.md$")
PRESENTATION_DIR_PATTERN = re.compile(r"^week(\d+)$")
PRESENTATION_ENTRY_PATTERN = re.compile(r"^(\d+)\.\s+(.+)$")
PRESENTATION_HTML_PATTERN = re.compile(r"^q\d+\.html$")
MAX_TITLE_LENGTH = 200


@dataclass(frozen=True)
class CatalogIssue:
    """One actionable validation failure."""

    code: str
    message: str
    path: str
    line: Optional[int] = None

    def as_dict(self):
        result = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }
        if self.line is not None:
            result["line"] = self.line
        return result


@dataclass(frozen=True)
class CatalogSectionStatus:
    """Readiness of one question source for one week."""

    ready: bool
    count: int
    path: str
    issues: tuple[CatalogIssue, ...]

    def as_dict(self):
        return {
            "ready": self.ready,
            "count": self.count,
            "path": self.path,
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class WeekCatalogStatus:
    """Discussion and presentation readiness for a selectable week."""

    week: int
    discussion: CatalogSectionStatus
    presentation: CatalogSectionStatus

    @property
    def ready(self):
        return self.discussion.ready and self.presentation.ready

    def as_dict(self):
        return {
            "week": self.week,
            "ready": self.ready,
            "discussion": self.discussion.as_dict(),
            "presentation": self.presentation.as_dict(),
        }


@dataclass(frozen=True)
class QuestionCatalogReport:
    """Validation result for all requested or discovered weeks."""

    course_dir: str
    weeks: tuple[WeekCatalogStatus, ...]

    @property
    def ready(self):
        return bool(self.weeks) and all(week.ready for week in self.weeks)

    def get_week(self, week_num):
        return next((week for week in self.weeks if week.week == week_num), None)

    def as_dict(self):
        return {
            "course_dir": self.course_dir,
            "ready": self.ready,
            "weeks": [week.as_dict() for week in self.weeks],
        }


def _issue(code, message, path, line=None):
    return CatalogIssue(code, message, str(path), line)


def _read_utf8(path, source_name):
    """Return decoded text and a possible read issue."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, _issue(
            f"{source_name}_missing",
            f"Required file is missing: {path.name}",
            path,
        )
    except OSError as exc:
        return None, _issue(
            f"{source_name}_unreadable",
            f"Could not read {path.name}: {exc}",
            path,
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, _issue(
            f"{source_name}_encoding",
            f"{path.name} is not valid UTF-8 near byte {exc.start}",
            path,
        )
    return text, None


def _title_error(title):
    if not isinstance(title, str) or not title.strip():
        return "Title must be a non-empty string"
    if len(title.strip()) > MAX_TITLE_LENGTH:
        return f"Title exceeds {MAX_TITLE_LENGTH} characters"
    return None


def _frontmatter_delimiters(lines):
    delimiters = []
    fence_char = None
    fence_length = 0
    fence_line = None

    for index, line in enumerate(lines):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char = marker[0]
                fence_length = len(marker)
                fence_line = index
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
                fence_line = None
            continue
        if fence_char is None and re.fullmatch(r"\s*---\s*", line):
            delimiters.append(index)

    return delimiters, fence_line


def _looks_like_frontmatter(text):
    first_line = next((line.strip() for line in text.splitlines()
                       if line.strip()), "")
    return bool(re.match(r"^(title|id|author)\s*:", first_line, re.IGNORECASE))


def _looks_like_title_frontmatter(text):
    first_line = text.split("\n", 1)[0] if text else ""
    return bool(re.match(r"^\s*title\s*:", first_line))


def _pair_question_headers(lines, delimiters, require_title, on_invalid=None):
    """Pair ``---`` delimiters into (opening, closing, frontmatter) headers.

    With ``require_title`` only blocks whose YAML mapping carries a non-empty
    title count as headers; otherwise any mapping does.  ``on_invalid`` is
    called with (opening, frontmatter, exception) for blocks whose YAML does
    not parse, and the opening delimiter is marked malformed when it returns
    a true value.
    """
    headers = []
    malformed = set()
    position = 0
    while position + 1 < len(delimiters):
        opening = delimiters[position]
        closing = delimiters[position + 1]
        frontmatter = "\n".join(lines[opening + 1:closing]).strip()
        try:
            metadata = yaml.safe_load(frontmatter) if frontmatter else None
        except yaml.YAMLError as exc:
            if on_invalid is not None and on_invalid(opening, frontmatter, exc):
                malformed.add(opening)
            position += 1
            continue

        if isinstance(metadata, dict) and (
                not require_title
                or str(metadata.get("title") or "").strip()):
            headers.append((opening, closing, frontmatter))
            position += 2
        else:
            position += 1
    return headers, malformed


def _stray_frontmatter_delimiters(
        lines, delimiters, headers, looks_stray, malformed=()):
    """Unused delimiters whose following content looks like frontmatter.

    Returns (delimiter, unclosed) pairs, where ``unclosed`` marks a delimiter
    with no later delimiter to close it.
    """
    used = {
        delimiter
        for opening, closing, _frontmatter in headers
        for delimiter in (opening, closing)
    }
    strays = []
    for index, delimiter in enumerate(delimiters):
        if delimiter in used or delimiter in malformed:
            continue
        next_delimiter = delimiters[index + 1] \
            if index + 1 < len(delimiters) else len(lines)
        candidate = "\n".join(lines[delimiter + 1:next_delimiter]).strip()
        if looks_stray(candidate):
            strays.append((delimiter, next_delimiter == len(lines)))
    return strays


def _question_blocks(lines, headers):
    """Split paired headers into (opening, closing, frontmatter, body)."""
    blocks = []
    for index, (opening, closing, frontmatter) in enumerate(headers):
        body_end = headers[index + 1][0] if index + 1 < len(headers) else len(lines)
        body = "\n".join(lines[closing + 1:body_end]).strip()
        blocks.append((opening, closing, frontmatter, body))
    return blocks


class QuestionParseError(ValueError):
    pass


def parse_question_blocks(text):
    """Parse repeated YAML-frontmatter blocks without splitting body Markdown.

    Strict variant used by the web app: raises QuestionParseError on the
    first structural problem and returns (frontmatter, body) pairs.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    lines = normalized.split("\n")
    delimiters, unclosed_fence_line = _frontmatter_delimiters(lines)

    if unclosed_fence_line is not None:
        raise QuestionParseError(
            f"Unclosed fenced code block near line {unclosed_fence_line + 1}"
        )

    headers, _malformed = _pair_question_headers(
        lines, delimiters, require_title=True
    )
    if not headers:
        raise QuestionParseError("No valid question frontmatter found")

    for delimiter, unclosed in _stray_frontmatter_delimiters(
            lines, delimiters, headers, _looks_like_title_frontmatter):
        if unclosed:
            raise QuestionParseError(
                f"Unclosed question frontmatter near line {delimiter + 1}"
            )
        raise QuestionParseError(
            f"Invalid question frontmatter near line {delimiter + 1}"
        )

    if any(line.strip() for line in lines[:headers[0][0]]):
        raise QuestionParseError("Unexpected content before the first question")

    return [
        (frontmatter, body)
        for _opening, _closing, frontmatter, body
        in _question_blocks(lines, headers)
    ]


def _parse_discussion_blocks(text, path):
    """Return recognized question blocks plus structural issues."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    delimiters, unclosed_fence_line = _frontmatter_delimiters(lines)
    issues = []

    if unclosed_fence_line is not None:
        issues.append(_issue(
            "discussion_code_fence_unclosed",
            "A fenced code block is not closed",
            path,
            unclosed_fence_line + 1,
        ))

    def _on_invalid_frontmatter(opening, frontmatter, exc):
        if not _looks_like_frontmatter(frontmatter):
            return False
        issues.append(_issue(
            "discussion_frontmatter_invalid",
            "Question frontmatter is invalid YAML: "
            f"{getattr(exc, 'problem', None) or 'parse error'}",
            path,
            opening + 1,
        ))
        return True

    headers, malformed = _pair_question_headers(
        lines, delimiters, require_title=False,
        on_invalid=_on_invalid_frontmatter,
    )

    if not headers:
        issues.append(_issue(
            "discussion_frontmatter_missing",
            "No question frontmatter blocks were found",
            path,
        ))
        return [], issues

    first_header_line = headers[0][0]
    if any(line.strip() for line in lines[:first_header_line]):
        issues.append(_issue(
            "discussion_content_before_first_question",
            "Content appears before the first question frontmatter",
            path,
            1,
        ))

    for delimiter, unclosed in _stray_frontmatter_delimiters(
            lines, delimiters, headers, _looks_like_frontmatter,
            malformed=malformed):
        code = "discussion_frontmatter_unclosed" \
            if unclosed \
            else "discussion_frontmatter_invalid"
        issues.append(_issue(
            code,
            "Question frontmatter is not a complete YAML block",
            path,
            delimiter + 1,
        ))

    blocks = [
        (opening + 1, yaml.safe_load(frontmatter), body)
        for opening, _closing, frontmatter, body
        in _question_blocks(lines, headers)
    ]
    return blocks, issues


def validate_discussion_week(course_dir, week_num):
    """Validate ``week-N-questions.md`` for one week."""
    course_path = Path(course_dir)
    path = course_path / f"week-{week_num}-questions.md"
    text, read_issue = _read_utf8(path, "discussion_file")
    if read_issue:
        return CatalogSectionStatus(False, 0, str(path), (read_issue,))
    if not text.strip():
        issue = _issue(
            "discussion_file_empty",
            "Discussion question file is empty",
            path,
        )
        return CatalogSectionStatus(False, 0, str(path), (issue,))

    blocks, issues = _parse_discussion_blocks(text, path)
    seen_ids = {}
    seen_titles = {}

    for position, (line, metadata, body) in enumerate(blocks, 1):
        title = metadata.get("title")
        title_error = _title_error(title)
        if title_error:
            issues.append(_issue(
                "discussion_title_invalid",
                f"Question {position}: {title_error}",
                path,
                line,
            ))
        else:
            normalized_title = " ".join(title.split()).casefold()
            if normalized_title in seen_titles:
                issues.append(_issue(
                    "discussion_title_duplicate",
                    f"Question {position} duplicates the title of question "
                    f"{seen_titles[normalized_title]}",
                    path,
                    line,
                ))
            else:
                seen_titles[normalized_title] = position

        if "id" not in metadata:
            issues.append(_issue(
                "discussion_id_missing",
                f"Question {position} has no stable id",
                path,
                line,
            ))
        else:
            question_id = metadata.get("id")
            if (not isinstance(question_id, str) or
                    not DISCUSSION_ID_PATTERN.fullmatch(question_id.strip())):
                issues.append(_issue(
                    "discussion_id_invalid",
                    f"Question {position} has an invalid id",
                    path,
                    line,
                ))
            else:
                normalized_id = question_id.strip().casefold()
                if normalized_id in seen_ids:
                    issues.append(_issue(
                        "discussion_id_duplicate",
                        f"Question {position} duplicates the id of question "
                        f"{seen_ids[normalized_id]}",
                        path,
                        line,
                    ))
                else:
                    seen_ids[normalized_id] = position

        if not body:
            issues.append(_issue(
                "discussion_body_empty",
                f"Question {position} has no content",
                path,
                line,
            ))

    return CatalogSectionStatus(
        ready=bool(blocks) and not issues,
        count=len(blocks),
        path=str(path),
        issues=tuple(issues),
    )


def validate_week_question_file(path):
    """Validate one already-resolved canonical weekly question file."""
    question_path = Path(path)
    match = DISCUSSION_FILE_PATTERN.fullmatch(question_path.name)
    if not match:
        issue = _issue(
            "discussion_file_name_invalid",
            "Weekly question filename must use week-N-questions.md",
            question_path,
        )
        return CatalogSectionStatus(
            False, 0, str(question_path), (issue,)
        )
    return validate_discussion_week(
        question_path.parent, int(match.group(1))
    )


def parse_week_questions(
        text, source_path="<weekly questions>", week_num=None,
        max_questions=None):
    """Parse and strictly validate one canonical weekly Markdown file."""
    if not isinstance(text, str) or not text.strip():
        raise QuestionParseError("Weekly question file is empty")

    try:
        entries = parse_question_blocks(text)
    except QuestionParseError:
        raise
    if max_questions is not None and len(entries) > max_questions:
        raise QuestionParseError(
            f"A weekly file may contain at most {max_questions} questions"
        )

    path = Path(source_path)
    resolved_week = week_num
    if resolved_week is None:
        match = DISCUSSION_FILE_PATTERN.fullmatch(path.name)
        resolved_week = int(match.group(1)) if match else None

    questions = []
    seen_ids = set()
    seen_titles = set()
    for position, (frontmatter, body) in enumerate(entries, 1):
        try:
            metadata = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError as exc:
            raise QuestionParseError(
                f"Question {position} has invalid frontmatter"
            ) from exc
        if not isinstance(metadata, dict):
            raise QuestionParseError(
                f"Question {position} frontmatter must be a mapping"
            )

        title = metadata.get("title")
        title_error = _title_error(title)
        if title_error:
            raise QuestionParseError(
                f"Question {position}: {title_error}"
            )
        title = title.strip()
        normalized_title = " ".join(title.split()).casefold()
        if normalized_title in seen_titles:
            raise QuestionParseError(
                f"Question {position} has a duplicate title"
            )
        seen_titles.add(normalized_title)

        question_id = metadata.get("id")
        if (not isinstance(question_id, str)
                or not DISCUSSION_ID_PATTERN.fullmatch(question_id.strip())):
            raise QuestionParseError(
                f"Question {position} has an invalid or missing stable id"
            )
        question_id = question_id.strip()
        normalized_id = question_id.casefold()
        if normalized_id in seen_ids:
            raise QuestionParseError(
                f"Question {position} has a duplicate stable id"
            )
        seen_ids.add(normalized_id)

        content = body.strip()
        if not content:
            raise QuestionParseError(
                f"Question {position} has no content"
            )
        source_key = (
            f"week-{resolved_week}-q-{question_id}"
            if resolved_week is not None else f"q-{question_id}"
        )
        questions.append({
            "id": question_id,
            "num": position,
            "title": title,
            "content": content,
            "source_key": source_key,
        })
    return questions


def read_week_questions(path, week_num=None, max_questions=None):
    """Read one canonical UTF-8 weekly file and return validated questions."""
    question_path = Path(path)
    text, read_issue = _read_utf8(question_path, "discussion_file")
    if read_issue:
        raise QuestionParseError(read_issue.message)
    return parse_week_questions(
        text, source_path=question_path, week_num=week_num,
        max_questions=max_questions,
    )


def parse_presentation_index(text):
    """Parse 'N. Question title' entries from a presentation index.md."""
    questions = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = PRESENTATION_ENTRY_PATTERN.fullmatch(line)
        if match:
            questions.append({
                "num": int(match.group(1)),
                "title": match.group(2).strip(),
            })
    return questions


def read_presentation_index(index_path):
    """Read a presentation index.md, or None when it does not exist."""
    path = Path(index_path)
    if not path.exists():
        return None
    return parse_presentation_index(path.read_text(encoding="utf-8-sig"))


def validate_presentation_week(course_dir, week_num):
    """Validate a presentation index and every referenced HTML file."""
    week_path = Path(course_dir) / f"week{week_num}"
    index_path = week_path / "index.md"
    text, read_issue = _read_utf8(index_path, "presentation_index")
    if read_issue:
        return CatalogSectionStatus(False, 0, str(index_path), (read_issue,))
    if not text.strip():
        issue = _issue(
            "presentation_index_empty",
            "Presentation question index is empty",
            index_path,
        )
        return CatalogSectionStatus(False, 0, str(index_path), (issue,))

    issues = []
    entries = {}
    for line_num, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PRESENTATION_ENTRY_PATTERN.fullmatch(line)
        if not match:
            issues.append(_issue(
                "presentation_index_line_invalid",
                "Index lines must use the form '1. Question title'",
                index_path,
                line_num,
            ))
            continue

        question_num = int(match.group(1))
        title = match.group(2).strip()
        if question_num < 1:
            issues.append(_issue(
                "presentation_number_invalid",
                "Presentation question numbers must be positive",
                index_path,
                line_num,
            ))
            continue
        title_error = _title_error(title)
        if title_error:
            issues.append(_issue(
                "presentation_title_invalid",
                title_error,
                index_path,
                line_num,
            ))
            continue
        if question_num in entries:
            issues.append(_issue(
                "presentation_number_duplicate",
                f"Question number {question_num} appears more than once",
                index_path,
                line_num,
            ))
            continue
        entries[question_num] = title

    if not entries:
        issues.append(_issue(
            "presentation_index_no_questions",
            "Presentation index contains no valid question entries",
            index_path,
        ))

    for question_num in entries:
        html_path = week_path / f"q{question_num:02d}.html"
        html, html_issue = _read_utf8(html_path, "presentation_html")
        if html_issue:
            issues.append(html_issue)
        elif not html.strip():
            issues.append(_issue(
                "presentation_html_empty",
                f"{html_path.name} is empty",
                html_path,
            ))

    expected_html_names = {
        f"q{question_num:02d}.html" for question_num in entries
    }
    try:
        unindexed_html = sorted(
            child
            for child in week_path.iterdir()
            if child.is_file()
            and PRESENTATION_HTML_PATTERN.fullmatch(child.name)
            and child.name not in expected_html_names
        )
    except OSError as exc:
        issues.append(_issue(
            "presentation_directory_unreadable",
            f"Could not inspect {week_path.name}: {exc}",
            week_path,
        ))
    else:
        for html_path in unindexed_html:
            issues.append(_issue(
                "presentation_html_unindexed",
                f"{html_path.name} is not listed in index.md",
                html_path,
            ))

    return CatalogSectionStatus(
        ready=bool(entries) and not issues,
        count=len(entries),
        path=str(index_path),
        issues=tuple(issues),
    )


def discover_catalog_weeks(course_dir, fallback_dir=None):
    """Discover canonical weekly Markdown files across primary and fallback."""
    course_path = Path(course_dir)
    fallback_path = Path(fallback_dir) if fallback_dir is not None else None
    roots = [
        path for path in (course_path, fallback_path)
        if path is not None and path.is_dir()
    ]
    if not roots:
        raise ValueError(f"Course directory does not exist: {course_path}")

    weeks = set()
    for root in roots:
        for child in root.iterdir():
            match = DISCUSSION_FILE_PATTERN.fullmatch(child.name)
            if child.is_file() and match:
                weeks.add(int(match.group(1)))
    return tuple(sorted(week for week in weeks if week > 0))


def _normalize_weeks(weeks: Iterable[int]):
    normalized = set()
    for week in weeks:
        if isinstance(week, bool) or not isinstance(week, int) or week < 1:
            raise ValueError("Week numbers must be positive integers")
        normalized.add(week)
    return tuple(sorted(normalized))


def validate_question_catalog(
        course_dir: Union[str, Path], weeks: Optional[Iterable[int]] = None,
        fallback_dir: Optional[Union[str, Path]] = None):
    """Validate the one effective weekly source used by both class phases."""
    course_path = Path(course_dir)
    fallback_path = Path(fallback_dir) if fallback_dir is not None else None
    selected_weeks = discover_catalog_weeks(course_path, fallback_path) \
        if weeks is None else _normalize_weeks(weeks)

    statuses = []
    for week in selected_weeks:
        filename = f"week-{week}-questions.md"
        primary = course_path / filename
        fallback = fallback_path / filename if fallback_path else None
        resolved = primary if primary.exists() else (fallback or primary)
        section = validate_week_question_file(resolved)
        statuses.append(WeekCatalogStatus(
            week=week,
            discussion=section,
            presentation=section,
        ))
    return QuestionCatalogReport(str(course_path), tuple(statuses))
