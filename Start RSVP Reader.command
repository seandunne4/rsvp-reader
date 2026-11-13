#!/usr/bin/env bash
# Double-click (in Finder) to start the RSVP Reader and open it in your browser.
# Keep the Terminal window open while you read; closing it stops the reader.
cd "$(dirname "$0")" || exit 1
# Finder-launched shells don't always load Homebrew's PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo
  echo "uv isn't installed yet. Install it with:   brew install uv"
  echo "then double-click this file again."
  echo
  read -r -p "Press Return to close."
  exit 1
fi

./rsvp.sh serve --open || { echo; echo "The reader stopped with an error (see above)."; read -r -p "Press Return to close."; }
