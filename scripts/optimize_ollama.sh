#!/bin/bash

# Optimize Ollama for M3 Max (36GB) memory management
# This ensures that only one large model is loaded into VRAM at a time,
# preventing swapping to system RAM which slows down inference.

echo "Setting OLLAMA_MAX_LOADED_MODELS to 1..."
launchctl setenv OLLAMA_MAX_LOADED_MODELS 1

echo "Ollama optimization complete."
echo "Note: You may need to restart Ollama for these changes to take effect if it's already running."
