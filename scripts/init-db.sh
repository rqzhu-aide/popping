#!/usr/bin/env bash
# Reset and initialize the Popping database
# Usage: bash scripts/init-db.sh

set -e

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

# Seed default data
echo "Seeding default data (instructor + demo courses)..."
flask --app app seed

echo ""
echo "=== Done! ==="
echo "Instructor login: instructor / admin123"
