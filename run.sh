#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")" || exit

# Pull the latest changes from the git repository
echo "Pulling latest changes from GitHub..."
# Get the name of the primary branch (e.g. master or main) and pull it
PRIMARY_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')

# Switch to the primary branch before pulling to ensure we run production code
git checkout "$PRIMARY_BRANCH" 2>/dev/null && \
    git pull origin "$PRIMARY_BRANCH" || \
    echo "Failed to checkout or pull $PRIMARY_BRANCH. Proceeding with local version."

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Run the python script
echo "Running gemini_query.py..."
python3 gemini_query.py

# Deactivate the virtual environment (optional but good practice)
deactivate
