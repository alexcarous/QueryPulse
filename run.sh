#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Navigate to the script's directory
# Use exit 1 to indicate failure to the caller (e.g. cron)
cd "$(dirname "$0")" || { echo "Error: Could not change directory to $(dirname "$0")"; exit 1; }

# Pull the latest changes from the git repository
echo "Pulling latest changes from GitHub..."
# Explicitly use the master branch
PRIMARY_BRANCH="master"

git checkout "$PRIMARY_BRANCH" 2>/dev/null || echo "Note: Could not checkout $PRIMARY_BRANCH, staying on current branch."
git pull origin "$PRIMARY_BRANCH" || echo "Warning: Failed to pull latest changes. Proceeding with local version."

# Run the python script (uv manages the venv and dependencies automatically)
echo "Running querypulse.py..."
uv run querypulse.py
