#!/bin/sh

set -e
echo "update-ubo-assets.sh"
echo

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BROWSERBUILD_DIR="${ROTUNDA_BROWSERBUILD_DIR:-$REPO_ROOT/browserbuild}"

# Download the LibreWolf uBOAssets.json
echo "-> Downloading LibreWolf uBOAssets.json"
assets=$(curl https://gitlab.com/librewolf-community/browser/source/-/raw/main/assets/uBOAssets.json)

# Remove specified filter lists
echo "-> Removing specified filter lists"
assets=$(echo "$assets" | jq 'del(.["ublock-badware"], .["urlhaus-1"], .["curben-phishing"])')

# Write the resulting json
echo "-> Writing to browserbuild/assets/uBOAssets.json"
echo "$assets" | jq . >"$BROWSERBUILD_DIR/assets/uBOAssets.json"

echo
echo "Done!"
