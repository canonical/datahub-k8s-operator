#!/bin/sh

# Generic runner script that will duplex the logs
# and log the environment variables.

# Capture the command and its name
CMD="$*"
CMD_NAME=$(basename "$1")
RUNNER_NAME=$(basename "$0")

# Define log files
ENVFILE="/tmp/${RUNNER_NAME}__${CMD_NAME}.env"
LOGFILE="/tmp/${RUNNER_NAME}__${CMD_NAME}.log"

# Redirect stdout and stderr to LOGFILE
exec > >(tee -a "$LOGFILE") 2>&1

# Log the environment variables
echo "$RUNNER_NAME:: Printing environment to '$ENVFILE'."
printenv > "$ENVFILE"

# Check if the command exists
if command -v "$1" >/dev/null 2>&1; then
    # Check if bash is available
    if command -v bash >/dev/null 2>&1; then
        echo "$RUNNER_NAME:: Running the command '$CMD' with bash."
        # Run the command with bash, duplexing output to LOGFILE
        bash -c "$CMD"
    else
        echo "$RUNNER_NAME:: Running the command '$CMD' with sh."
        # Run the command with sh, duplexing output to LOGFILE
        sh -c "$CMD"
    fi
else
    echo "$RUNNER_NAME:: The command '$CMD_NAME' does not exist."
    exit 1
fi

echo "$RUNNER_NAME:: Run complete."
