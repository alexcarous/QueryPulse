#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")" || exit

# Pull the latest changes from the git repository
echo "Pulling latest changes from GitHub..."
# Automatically pull the current branch
git pull origin $(git branch --show-current)

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Run the python script
echo "Running gemini_query.py..."
python3 gemini_query.py

# Deactivate the virtual environment (optional but good practice)
deactivate
