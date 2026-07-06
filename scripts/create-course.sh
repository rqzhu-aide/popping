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
CLASS_DIR="$PROJECT_DIR/classes/$SLUG"

if [ -d "$CLASS_DIR" ]; then
    echo "Error: course folder already exists: $CLASS_DIR"
    exit 1
fi

mkdir -p "$CLASS_DIR"

# Write course.yaml
cat > "$CLASS_DIR/course.yaml" <<EOF
slug: "$SLUG"
name: "$NAME"
code: "$CODE"
semester: "$SEMESTER"
active: true
max_teams: 5
max_members_per_team: 10
teams:
  - name: Team Alpha
    color: "#ef4444"
  - name: Team Beta
    color: "#3b82f6"
  - name: Team Gamma
    color: "#10b981"
  - name: Team Delta
    color: "#f59e0b"
  - name: Team Epsilon
    color: "#8b5cf6"
EOF
if [ -n "$URL" ]; then
  echo "url: \"$URL\"" >> "$CLASS_DIR/course.yaml"
fi

# Write per-course init-db.sh
cat > "$CLASS_DIR/init-db.sh" <<'SCRIPT'
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
SCRIPT

chmod +x "$CLASS_DIR/init-db.sh"

echo ""
echo "Course folder created: $CLASS_DIR"
echo ""
echo "Next steps:"
echo "  1. cd $CLASS_DIR"
echo "  2. bash init-db.sh"
echo "     (this will prompt for instructor credentials)"
