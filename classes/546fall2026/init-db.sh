#!/usr/bin/env bash
set -e

CLASS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CLASS_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

echo "=== Resetting course database ==="
echo "Course config: $CLASS_DIR"

python3 scripts/init-course-db.py "$CLASS_DIR"

echo ""
echo "=== Done! ==="
