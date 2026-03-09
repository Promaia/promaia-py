#!/bin/bash
# Install launchd plist for calendar monitor daemon
# This script customizes the plist for the current user and Python environment

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Installing Promaia Calendar Monitor Daemon${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
TEMPLATE_PLIST="$SCRIPT_DIR/com.promaia.agent.plist"

# Destination
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
DEST_PLIST="$LAUNCH_AGENTS_DIR/com.promaia.agent.plist"

# Check if template exists
if [ ! -f "$TEMPLATE_PLIST" ]; then
    echo -e "${RED}Error: Template plist not found: $TEMPLATE_PLIST${NC}"
    exit 1
fi

# Create LaunchAgents directory if needed
mkdir -p "$LAUNCH_AGENTS_DIR"

# Get current Python path
PYTHON_PATH=$(which python3)
if [ -z "$PYTHON_PATH" ]; then
    echo -e "${RED}Error: python3 not found in PATH${NC}"
    exit 1
fi

echo -e "Python path: ${YELLOW}$PYTHON_PATH${NC}"
echo -e "Home directory: ${YELLOW}$HOME${NC}"
echo -e "Destination: ${YELLOW}$DEST_PLIST${NC}"
echo ""

# Customize plist
sed -e "s|/usr/local/bin/python3|$PYTHON_PATH|g" \
    -e "s|__HOME__|$HOME|g" \
    "$TEMPLATE_PLIST" > "$DEST_PLIST"

# Set permissions
chmod 644 "$DEST_PLIST"

echo -e "${GREEN}✓ Plist installed successfully${NC}"
echo ""

# Load the service
echo -e "Loading service..."
if launchctl load "$DEST_PLIST" 2>/dev/null; then
    echo -e "${GREEN}✓ Service loaded${NC}"
else
    if launchctl list | grep -q "com.promaia.agent"; then
        echo -e "${YELLOW}Service already loaded${NC}"
    else
        echo -e "${RED}Warning: Failed to load service${NC}"
        echo -e "You may need to manually load it: launchctl load $DEST_PLIST"
    fi
fi

echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Management commands:"
echo "  maia daemon status    - Check daemon status"
echo "  maia daemon start     - Start daemon manually"
echo "  maia daemon stop      - Stop daemon"
echo "  maia daemon logs      - View logs"
echo "  maia daemon enable    - Enable auto-start (via maia command)"
echo "  maia daemon disable   - Disable auto-start"
echo ""
echo "The daemon will auto-start on next login/reboot."
