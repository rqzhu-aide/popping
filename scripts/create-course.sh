#!/usr/bin/env bash
set -e

echo "=== Create New Popping Course ==="

read -p "Course slug (folder name, e.g. 432fall2026): " SLUG
read -p "Course name: " NAME
read -p "Course code (e.g. STAT 432): " CODE
read -p "Semester (e.g. Fall 2026): " SEMESTER
read -p "Course catalog URL (optional, press Enter to skip): " URL

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COURSE_DIR="$PROJECT_DIR/data/$SLUG"

if [ -d "$COURSE_DIR" ]; then
    echo "Error: course folder already exists: $COURSE_DIR"
    exit 1
fi

mkdir -p "$COURSE_DIR"

# Write course.json
if [ -n "$URL" ]; then
  URL_LINE="\"url\": \"$URL\","
else
  URL_LINE=""
fi

cat > "$COURSE_DIR/course.json" <<EOF
{
  "slug": "$SLUG",
  "name": "$NAME",
  $URL_LINE
  "code": "$CODE",
  "semester": "$SEMESTER",
  "teams": [
    {"name": "Team Alpha", "color": "#ef4444"},
    {"name": "Team Beta", "color": "#3b82f6"},
    {"name": "Team Gamma", "color": "#10b981"},
    {"name": "Team Delta", "color": "#f59e0b"},
    {"name": "Team Epsilon", "color": "#8b5cf6"}
  ]
}
EOF

# Write per-course init-db.sh
cat > "$COURSE_DIR/init-db.sh" <<'SCRIPT'
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
SCRIPT

chmod +x "$COURSE_DIR/init-db.sh"

echo ""
echo "Course folder created: $COURSE_DIR"
echo ""
echo "Next steps:"
echo "  1. cd $COURSE_DIR"
echo "  2. bash init-db.sh"
echo "     (this will prompt for instructor credentials)"
