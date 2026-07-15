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

    headers = []
    malformed_lines = set()
    position = 0
    while position + 1 < len(delimiters):
        opening = delimiters[position]
        closing = delimiters[position + 1]
        frontmatter = "\n".join(lines[opening + 1:closing]).strip()
        try:
            metadata = yaml.safe_load(frontmatter) if frontmatter else None
        except yaml.YAMLError as exc:
            if _looks_like_frontmatter(frontmatter):
                malformed_lines.add(opening)
                issues.append(_issue(
                    "discussion_frontmatter_invalid",
                    "Question frontmatter is invalid YAML: "
                    f"{getattr(exc, 'problem', None) or 'parse error'}",
                    path,
                    opening + 1,
                ))
            position += 1
            continue

        if isinstance(metadata, dict):
            headers.append((opening, closing, metadata))
            position += 2
        else:
            position += 1

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

    used_delimiters = {
        delimiter
        for opening, closing, _metadata in headers
        for delimiter in (opening, closing)
    }
    for index, delimiter in enumerate(delimiters):
        if delimiter in used_delimiters or delimiter in malformed_lines:
            continue
        next_delimiter = delimiters[index + 1] \
            if index + 1 < len(delimiters) else len(lines)
        candidate = "\n".join(lines[delimiter + 1:next_delimiter]).strip()
        if _looks_like_frontmatter(candidate):
            code = "discussion_frontmatter_unclosed" \
                if next_delimiter == len(lines) \
                else "discussion_frontmatter_invalid"
            issues.append(_issue(
                code,
                "Question frontmatter is not a complete YAML block",
                path,
                delimiter + 1,
            ))

    blocks = []
    for index, (opening, closing, metadata) in enumerate(headers):
        body_end = headers[index + 1][0] if index + 1 < len(headers) else len(lines)
        body = "\n".join(lines[closing + 1:body_end]).strip()
        blocks.append((opening + 1, metadata, body))
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

        if "id" in metadata:
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


def discover_catalog_weeks(course_dir):
    """Discover week numbers from discussion files and presentation folders."""
    course_path = Path(course_dir)
    if not course_path.is_dir():
        raise ValueError(f"Course directory does not exist: {course_path}")

    weeks = set()
    for child in course_path.iterdir():
        discussion_match = DISCUSSION_FILE_PATTERN.fullmatch(child.name)
        if child.is_file() and discussion_match:
            weeks.add(int(discussion_match.group(1)))
            continue
        presentation_match = PRESENTATION_DIR_PATTERN.fullmatch(child.name)
        if child.is_dir() and presentation_match:
            weeks.add(int(presentation_match.group(1)))
    return tuple(sorted(week for week in weeks if week > 0))


def _normalize_weeks(weeks: Iterable[int]):
    normalized = set()
    for week in weeks:
        if isinstance(week, bool) or not isinstance(week, int) or week < 1:
            raise ValueError("Week numbers must be positive integers")
        normalized.add(week)
    return tuple(sorted(normalized))


def validate_question_catalog(
        course_dir: Union[str, Path], weeks: Optional[Iterable[int]] = None):
    """Validate all discovered weeks or an explicit set of selectable weeks."""
    course_path = Path(course_dir)
    selected_weeks = discover_catalog_weeks(course_path) \
        if weeks is None else _normalize_weeks(weeks)
    statuses = tuple(
        WeekCatalogStatus(
            week=week,
            discussion=validate_discussion_week(course_path, week),
            presentation=validate_presentation_week(course_path, week),
        )
        for week in selected_weeks
    )
    return QuestionCatalogReport(str(course_path), statuses)
