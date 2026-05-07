#!/usr/bin/env bash
# Reset and initialize the Popping database
# Usage: bash scripts/init-db.sh [USERNAME] [NAME] [PIN]
# Example: bash scripts/init-db.sh tez "Ruoqing Zhu" mysecret123

set -e

# Defaults
USERNAME="${1:-instructor}"
NAME="${2:-Instructor}"
PIN="${3:-admin123}"

echo "=== Popping Database Initialization ==="

# Determine project directory (works locally or on Render)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Project directory: $PROJECT_DIR"

# Remove existing database
echo "Removing existing database (if any)..."
rm -f popping.db

# Initialize schema
echo "Initializing database schema..."
flask --app app init-db

# Seed default data with custom credentials
echo "Seeding default data..."
flask --app app seed --username "$USERNAME" --name "$NAME" --pin "$PIN"

echo ""
echo "=== Done! ==="
echo "Instructor login: $USERNAME / $PIN"
