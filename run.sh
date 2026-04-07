#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")" || exit

# Pull the latest changes from the git repository
echo "Pulling latest changes from GitHub..."
git pull origin main # Change 'main' to your branch name if different

# Run the python script
echo "Running gemini_query.py..."
# Assumes python3 is the executable. Use a virtual environment if needed:
# source venv/bin/activate
python3 gemini_query.py
