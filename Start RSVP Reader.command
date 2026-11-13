#!/usr/bin/env bash
# Double-click (in Finder) to start the RSVP Reader and open it in your browser.
# The reader runs in the background; this Terminal window closes by itself.
# To stop the reader, click Quit in the library page.
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

echo "Starting RSVP Reader... (the first start downloads what it needs and takes a few minutes)"
if ./rsvp.sh start; then
  # Close this Terminal window now that the reader runs in the background.
  (sleep 1; osascript -e 'tell application "Terminal" to close (every window whose name contains "Start RSVP Reader")' >/dev/null 2>&1) &
  exit 0
else
  echo
  echo "The reader couldn't start (see above)."
  read -r -p "Press Return to close."
fi
