#!/usr/bin/env bash
set -e

COURSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$COURSE_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

echo "=== Resetting course database ==="
echo "Course folder: $COURSE_DIR"

python3 scripts/init-course-db.py "$COURSE_DIR"

echo ""
echo "=== Done! ==="
