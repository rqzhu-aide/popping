#!/usr/bin/env bash
set -e

COURSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$COURSE_DIR/../.." && pwd)"
SLUG="$(basename "$COURSE_DIR")"
CLASS_DIR="$PROJECT_ROOT/classes/$SLUG"

cd "$PROJECT_ROOT"

echo "=== Resetting course database ==="
echo "Course config: $CLASS_DIR"

if [ ! -f "$CLASS_DIR/course.yaml" ]; then
    echo "Error: course.yaml not found at $CLASS_DIR"
    exit 1
fi

python3 scripts/init-course-db.py "$CLASS_DIR"

echo ""
echo "=== Done! ==="
