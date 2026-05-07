#!/usr/bin/env bash
# Reset and initialize the Popping database
# Usage: bash scripts/init-db.sh
# Prompts interactively for instructor credentials (PIN is hidden)

set -e

echo "=== Popping Database Initialization ==="

# Determine project directory (works locally or on Render)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "Project directory: $PROJECT_DIR"

# Prompt for credentials (no defaults, no history)
echo ""
echo "Create the first instructor account:"
read -p "  Username (login ID): " USERNAME
read -p "  Display name:        " NAME
read -s -p "  PIN / password:      " PIN
echo ""

if [ -z "$USERNAME" ] || [ -z "$NAME" ] || [ -z "$PIN" ]; then
    echo "Error: all fields are required."
    exit 1
fi

# Remove existing database
echo ""
echo "Removing existing database (if any)..."
rm -f popping.db

# Initialize schema
echo "Initializing database schema..."
flask --app app init-db

# Seed with your custom credentials
echo "Seeding instructor account..."
flask --app app seed --username "$USERNAME" --name "$NAME" --pin "$PIN"

echo ""
echo "=== Done! ==="
echo "Instructor login: $USERNAME / [hidden]"
