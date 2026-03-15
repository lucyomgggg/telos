#!/bin/bash
# scripts/run_unattended.sh
# Prevents sleep and runs telos in a way that persists.

# Check if caffeinate is available (default on macOS)
if ! command -v caffeinate &> /dev/null
then
    echo "caffeinate could not be found. This script is intended for macOS."
    exit 1
fi

# Check if tmux is available
if ! command -v tmux &> /dev/null
then
    echo "⚠️ tmux could not be found. Installing it via Homebrew is recommended: brew install tmux"
    echo "Running with caffeinate in the current terminal session..."
    caffeinate telos start "$@"
else
    # Check if session already exists
    if tmux has-session -t telos 2>/dev/null; then
        echo "⚠️ A tmux session named 'telos' is already running."
        echo "Attach to it with: tmux attach -t telos"
        exit 1
    fi

    echo "🚀 Starting telos in a tmux session named 'telos'..."
    # -d starts it in the background
    tmux new-session -d -s telos "caffeinate telos start $*"
    
    echo "--------------------------------------------------------"
    echo "✅ Telos is now running in the background."
    echo "💻 Your MacBook will NOT sleep as long as this process is active."
    echo ""
    echo "To monitor progress:"
    echo "   tmux attach -t telos"
    echo ""
    echo "To detach from the monitor (without stopping Telos):"
    echo "   Press Ctrl+B, then D"
    echo ""
    echo "To stop Telos:"
    echo "   tmux kill-session -t telos"
    echo "--------------------------------------------------------"
fi
