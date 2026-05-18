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

# Ensure the virtual environment exists
if [ ! -d "venv" ]; then
    echo "Virtual environment 'venv' not found. Creating it..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Sync dependencies ONLY if requirements.txt has changed
if [ -f "requirements.txt" ]; then
    # Create a checksum of the requirements file
    REQ_HASH=$(md5sum requirements.txt | cut -d' ' -f1)
    
    # Check if we have a saved hash and if it matches
    if [ ! -f "venv/.requirements.hash" ] || [ "$(cat venv/.requirements.hash)" != "$REQ_HASH" ]; then
        echo "Syncing dependencies (requirements.txt changed)..."
        pip install -q --upgrade pip
        pip install -q -r requirements.txt
        # Save the new hash
        echo "$REQ_HASH" > venv/.requirements.hash
    else
        echo "Dependencies are up to date."
    fi
fi

# Run the python script
echo "Running querypulse.py..."
python3 querypulse.py

# Deactivate the virtual environment
deactivate
