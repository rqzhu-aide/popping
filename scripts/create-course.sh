#!/usr/bin/env bash
set -e

echo "=== Create New Popping Course ==="

read -p "Course slug (folder name, e.g. 432fall2026): " SLUG
read -p "Course name: " NAME
read -p "Course code (e.g. STAT 432): " CODE
read -p "Semester (e.g. Fall 2026): " SEMESTER
read -p "Course catalog URL (optional, press Enter to skip): " URL

if [[ ! "$SLUG" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Error: course slug may contain only letters, numbers, underscores, and hyphens."
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required before creating a course."
    exit 1
fi
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    echo "Error: PyYAML is required. Run: python3 -m pip install -r requirements.txt"
    exit 1
fi
if ! python3 - "$NAME" "$CODE" "$SEMESTER" <<'PY'
import sys

raise SystemExit(0 if all(value.strip() for value in sys.argv[1:]) else 1)
PY
then
    echo "Error: course name, code, and semester are required."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CLASS_DIR="$PROJECT_DIR/classes/$SLUG"

if [ -d "$CLASS_DIR" ]; then
    echo "Error: course folder already exists: $CLASS_DIR"
    exit 1
fi

mkdir -p "$CLASS_DIR"

# Write course.yaml through PyYAML so quotes, colons, Unicode, and other
# legitimate course metadata cannot produce malformed YAML.
python3 - "$CLASS_DIR/course.yaml" "$SLUG" "$NAME" "$CODE" "$SEMESTER" "$URL" <<'PY'
from pathlib import Path
import sys

import yaml

path, slug, name, code, semester, url = sys.argv[1:]
name, code, semester = (value.strip() for value in (name, code, semester))
url = url.strip()
document = {
    "slug": slug,
    "name": name,
    "code": code,
    "semester": semester,
    "active": False,
    "team_pool_size": 20,
    "max_teams": 6,
    "max_members_per_team": 10,
    "teams": [
        {"color": "#ef4444"},
        {"color": "#3b82f6"},
        {"color": "#10b981"},
        {"color": "#f59e0b"},
        {"color": "#8b5cf6"},
    ],
}
if url:
    document["url"] = url
Path(path).write_text(
    yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
PY

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
echo "  Local: initialize the database, change active to true, then run the app."
echo "  Render:"
echo "  1. Commit and deploy this course while active remains false."
echo "  2. From the repository root, initialize its database with:"
echo "       bash classes/$SLUG/init-db.sh"
echo "     (on Render, run that command in Render Shell)"
echo "  3. Change active to true in classes/$SLUG/course.yaml."
echo "  4. Commit and deploy again to publish the course."
