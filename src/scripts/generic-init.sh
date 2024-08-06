#!/bin/sh

# Generic initialization script that will duplex the logs
# and log the environment variables.

# Define the file to which stdout and stderr should be redirected
LOGFILE="/tmp/init.log"

# Redirect both stdout and stderr to the log file
exec > "$LOGFILE" 2>&1

# Environment variables can get opaque with pebble, log them
echo "generic-init.sh:: Printing environment."
printenv
echo ""

SCRIPT_PATH="$1"

if [ -f "$SCRIPT_PATH" ]; then
    if command -v bash >/dev/null 2>&1; then
        echo "generic-init.sh:: Running the initialization script at '$SCRIPT_PATH' with bash.\n"
        # Source the script using bash
        bash -c ". \"$SCRIPT_PATH\""
    else
        echo "generic-init.sh:: Running the initialization script at '$SCRIPT_PATH' with sh.\n"
        # Source the script using sh
        . "$SCRIPT_PATH"
    fi
else
    echo "generic-init.sh:: The script at '$SCRIPT_PATH' does not exist.\n"
    exit 1
fi

echo "\ngeneric-init.sh:: Initialization complete."
