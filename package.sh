#!/bin/bash
# Packaging script for Krita Reveal plugin

# Ensure we're in the script's directory
cd "$(dirname "$0")"

# Remove existing zip if any
rm -f krita_reveal.zip

# Create the zip
# Krita expects krita_reveal/ and krita_reveal.desktop at the root of the zip
zip -r krita_reveal.zip krita_reveal krita_reveal.desktop -x "*.pyc" "__pycache__*" "*.DS_Store" "*/.git*" "*/.idea*"

echo "Package created: krita_reveal.zip"
