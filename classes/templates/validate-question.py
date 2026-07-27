#!/usr/bin/env python3
"""Validate discussion question submissions against the format rules.

Usage:
    python validate-question.py <question-file.md>
    python validate-question.py classes/432fall2026/week-1-questions.md

Checks:
    - Required fields: id, title, author
    - ID: letters, numbers, hyphens, or underscores; unique within the file
    - Title: non-empty, max 50 characters; unique within the file
    - Author format: "Name (student-id)"
    - Content: max 200 words (excluding code blocks and math)
    - No forbidden LaTeX macros
    - Code blocks are properly fenced
"""

import sys
import re
import os


FORBIDDEN_MACROS = [
    r'\newcommand', r'\def', r'\renewcommand', r'\newenvironment',
    r'\renewenvironment', r'\input', r'\include', r'\usepackage',
    r'\documentclass', r'\begin', r'\end', r'\makeatletter', r'\makeatother'
]

FORBIDDEN_COMMANDS = [
    r'\write18', r'\immediate\write', r'\openout', r'\closeout',
    r'\shellescape', r'\read', r'\csname', r'\endcsname'
]

MAX_TITLE_LENGTH = 50
MAX_WORDS = 200
QUESTION_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


def count_words(text):
    """Count words excluding code blocks and math."""
    # Remove code blocks
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Remove display math
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.DOTALL)
    # Remove inline math
    text = re.sub(r'\$[^$]+\$', '', text)
    # Count remaining words
    words = text.split()
    return len(words)


def check_latex(content):
    """Check for forbidden LaTeX macros."""
    issues = []
    for macro in FORBIDDEN_MACROS + FORBIDDEN_COMMANDS:
        if re.search(re.escape(macro), content):
            issues.append(f"Forbidden LaTeX macro: {macro}")
    return issues


def check_code_blocks(content):
    """Check code blocks are properly fenced."""
    issues = []
    # Count backtick fences
    fences = re.findall(r'^```', content, re.MULTILINE)
    if len(fences) % 2 != 0:
        issues.append("Unclosed code block (odd number of ``` fences)")
    return issues


def validate_question(block_num, fm_text, body_text,
                      seen_question_ids=None, seen_question_titles=None):
    """Validate a single question block."""
    import yaml
    issues = []

    # Parse frontmatter
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    if not isinstance(fm, dict):
        return ["Frontmatter must be a YAML dictionary"]

    # Stable question ID
    question_id = fm.get('id')
    if question_id is None or question_id == '':
        issues.append("Missing 'id' field")
    elif (not isinstance(question_id, str) or
          not QUESTION_ID_PATTERN.fullmatch(question_id.strip())):
        issues.append(
            "ID may contain only letters, numbers, hyphens, and underscores"
        )
    elif seen_question_ids is not None:
        normalized_id = question_id.strip().casefold()
        first_block = seen_question_ids.get(normalized_id)
        if first_block is not None:
            issues.append(
                f"Duplicate 'id' field: matches question #{first_block}"
            )
        else:
            seen_question_ids[normalized_id] = block_num

    # Title
    title = fm.get('title')
    if not isinstance(title, str) or not title.strip():
        issues.append("Title must be a non-empty string")
    elif len(title.strip()) > MAX_TITLE_LENGTH:
        issues.append(
            f"Title too long: {len(title.strip())} chars "
            f"(max {MAX_TITLE_LENGTH})"
        )
    elif seen_question_titles is not None:
        normalized_title = " ".join(title.split()).casefold()
        first_block = seen_question_titles.get(normalized_title)
        if first_block is not None:
            issues.append(f"Duplicate title: matches question #{first_block}")
        else:
            seen_question_titles[normalized_title] = block_num

    # Author
    author = fm.get('author', '')
    if not author:
        issues.append("Missing 'author' field")
    elif not re.match(r'.+\(.+\)', str(author)):
        issues.append("Author format should be 'Name (student-id)'")

    # Content
    if not body_text or not body_text.strip():
        issues.append("Question content is empty")
    else:
        wc = count_words(body_text)
        if wc > MAX_WORDS:
            issues.append(f"Content too long: {wc} words (max {MAX_WORDS})")

        issues.extend(check_latex(body_text))
        issues.extend(check_code_blocks(body_text))

    return issues


def validate_file(filepath):
    """Validate all questions in a weekly question file."""
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        return 1

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Strip leading ---
    content = content.lstrip('-').lstrip()
    blocks = content.split('\n---\n')

    total_issues = 0
    block_num = 0
    seen_question_ids = {}
    seen_question_titles = {}
    i = 0

    while i + 1 < len(blocks):
        fm_block = blocks[i].strip()
        body_block = blocks[i + 1].strip()

        if fm_block:
            block_num += 1
            issues = validate_question(
                block_num, fm_block, body_block,
                seen_question_ids, seen_question_titles,
            )
            if issues:
                print(f"\n[FAIL] Question #{block_num} has {len(issues)} issue(s):")
                for issue in issues:
                    print(f"   - {issue}")
                total_issues += len(issues)
            else:
                print(f"[PASS] Question #{block_num}: OK")
        i += 2

    if block_num == 0:
        print("No questions found in file.")
        return 1

    print(f"\n{'='*40}")
    print(f"Total: {block_num} questions, {total_issues} issue(s)")
    return 0 if total_issues == 0 else 1


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(validate_file(sys.argv[1]))
