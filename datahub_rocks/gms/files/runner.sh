#!/bin/bash

# Generic runner script that will duplex the logs
# and log the environment variables.

# Capture the command and its name
CMD="$*"
RAW_CMD_NAME=$(basename -- "$1")
CMD_NAME=$(printf '%s' "$RAW_CMD_NAME" | tr -cs 'A-Za-z0-9._-' '_')
if [ -z "$CMD_NAME" ]; then
    CMD_NAME="unknown-cmd"
fi
RUNNER_NAME=$(basename "$0")

# Define log files
ENVFILE="/tmp/${RUNNER_NAME}__${CMD_NAME}.env"
LOGFILE="/tmp/${RUNNER_NAME}__${CMD_NAME}.log"

# Create a named pipe (FIFO) for logging
# We do this for POSIX compliance to pass CI checks
PIPE="/tmp/${RUNNER_NAME}__${CMD_NAME}.pipe"
mkfifo "$PIPE"
tee -a "$LOGFILE" < "$PIPE" &
exec > "$PIPE" 2>&1

# Log the environment variables
echo "$RUNNER_NAME:: Printing environment to '$ENVFILE'."
printenv > "$ENVFILE"

# Check if the command exists
if command -v "$1" >/dev/null 2>&1; then
    # Check if bash is available
    if command -v bash >/dev/null 2>&1; then
        echo "$RUNNER_NAME:: Running the command '$CMD' with bash."
        # Run the command with bash
        bash -c "$CMD"
    else
        echo "$RUNNER_NAME:: Running the command '$CMD' with sh."
        # Run the command with sh
        sh -c "$CMD"
    fi
else
    echo "$RUNNER_NAME:: The command '$CMD_NAME' does not exist."
    exit 1
fi

# Clean up the pipe
rm "$PIPE"

echo "$RUNNER_NAME:: Run complete."
