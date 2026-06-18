#!/bin/bash
cd /opt/igor

CRASH_MARKER=/opt/igor/.crash_detected

# If previous run crashed, restore last known good code
if [ -f "$CRASH_MARKER" ]; then
    echo "Crash detected on previous run - restoring last known good code"
    git checkout -- .
    rm -f "$CRASH_MARKER"
fi

# Syntax check - exclude venv
if ! python3 -m compileall -q -x venv . 2>/dev/null; then
    echo "Compile check failed - reverting to last good commit"
    git checkout -- .
    if ! python3 -m compileall -q -x venv . 2>/dev/null; then
        echo "Revert also failed - aborting startup"
        exit 1
    fi
fi

/opt/igor/venv/bin/python main.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "IGOR exited with code $EXIT_CODE - will restore code on next startup"
    touch "$CRASH_MARKER"
fi

exit $EXIT_CODE
