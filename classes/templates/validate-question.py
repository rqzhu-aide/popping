#!/usr/bin/env python3
"""Validate discussion question submissions against the format rules.

Usage:
    python validate-question.py <question-file.md>
    python validate-question.py classes/432fall2026/week-1-questions.md

Checks:
    - Required fields: title, author
    - Title: non-empty, max 50 characters
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


def validate_question(block_num, fm_text, body_text):
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

    # Title
    title = fm.get('title', '')
    if not title:
        issues.append("Missing 'title' field")
    elif len(str(title)) > MAX_TITLE_LENGTH:
        issues.append(f"Title too long: {len(title)} chars (max {MAX_TITLE_LENGTH})")

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
    i = 0

    while i + 1 < len(blocks):
        fm_block = blocks[i].strip()
        body_block = blocks[i + 1].strip()

        if fm_block:
            block_num += 1
            issues = validate_question(block_num, fm_block, body_block)
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
