#!/bin/bash

# Simple bash script that copies browser build additions into the source directory
# Must be ran from within the source directory

# Check if correct number of arguments are provided
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <version> <release>"
    exit 1
fi

# Assign command-line arguments to variables
version="$1"
release="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BROWSERBUILD_DIR="${ROTUNDA_BROWSERBUILD_DIR:-$REPO_ROOT/browserbuild}"

# Function to run commands and exit on failure
run() {
    echo "$ $1"
    eval "$1"
    if [ $? -ne 0 ]; then
        echo "Command failed: $1"
        exit 1
    fi
}

# Copy the search-config.json file
run "cp -v \"$BROWSERBUILD_DIR/assets/search-config.json\" services/settings/dumps/main/search-config.json"

# vs_pack.py issue... should be temporary
run "cp -v \"$BROWSERBUILD_DIR/patches/librewolf/pack_vs.py\" build/vs/"

# Apply most recent `settings` repository files
run 'mkdir -p lw'
pushd lw > /dev/null
run "cp -v \"$BROWSERBUILD_DIR/settings/rotunda.cfg\" ."
run "cp -v \"$BROWSERBUILD_DIR/settings/distribution/policies.json\" ."
run "cp -v \"$BROWSERBUILD_DIR/settings/defaults/pref/local-settings.js\" ."
run "cp -v \"$BROWSERBUILD_DIR/settings/chrome.css\" ."
run 'touch moz.build'
popd > /dev/null

# Generate Assets.car for macOS builds (if on macOS) or ensure it exists
if [[ ! -f "$BROWSERBUILD_DIR/additions/browser/branding/rotunda/Assets.car" ]]; then
    echo "Generating Assets.car..."
    bash ../scripts/generate-assets-car.sh
fi

# Copy ALL browser build additions into the Firefox source tree
run "cp -r \"$BROWSERBUILD_DIR/additions\"/* ."

# Provide a script that fetches and bootstraps Nightly and some mozconfigs
run 'cp -v ../scripts/mozfetch.sh lw/'

# Override the firefox version
for file in "browser/config/version.txt" "browser/config/version_display.txt"; do
    echo "${version}-${release}" > "$file"
done
