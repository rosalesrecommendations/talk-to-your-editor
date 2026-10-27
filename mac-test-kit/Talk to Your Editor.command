#!/bin/bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="8765"
LAUNCH_HOME="$REPO_ROOT/.launch-home"

fail() {
  printf '\nTalk to Your Editor could not start.\n%s\n' "$1"
  printf 'Press Control-C if this window is still running.\n'
  exit 1
}

printf 'Talk to Your Editor - Personal Test Edition\n'
printf 'Setting things up. The first run can take a little while.\n\n'

if ! command -v python3 >/dev/null 2>&1; then
  fail "Python 3 was not found on this Mac. Install Python 3, then double-click this launcher again."
fi

cd "$REPO_ROOT"

if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  printf 'Creating the private app environment for this folder...\n'
  python3 -m venv "$REPO_ROOT/.venv" || fail "Python could not create the .venv folder. Check that the folder is writable, then try again."
fi

# Activate the environment for this Terminal session only.
# shellcheck disable=SC1091
. "$REPO_ROOT/.venv/bin/activate"

printf 'Installing or refreshing Talk to Your Editor...\n'
pip install --quiet -e . || fail "The app could not be installed into its private environment. Read the error above, fix that problem, and try again."
# The speech pack lets a dropped video be written down on this Mac
# itself, with no key and no cost. It is a bigger download (about
# 400 MB with its helpers), so it is best-effort: if it cannot be
# installed, the app still starts, and dropped videos can still be
# written down with a saved ElevenLabs key instead.
if ! pip install --quiet -e '.[speech]'; then
  printf 'The on-this-Mac speech pack could not be installed, so a dropped\n'
  printf 'video cannot be written down on this Mac itself. The app still\n'
  printf 'starts; a saved ElevenLabs key covers dropped videos instead.\n'
fi
printf '\n'

printf 'Quick map: the runner is part 1. It makes the first edit and gives you a job folder.\n'
printf 'This app is part 2: you watch that edit here and direct the fixes. It can also\n'
printf 'take a single raw video by itself and make that first edit right here.\n\n'

# Optional: a job folder path as the launcher's first argument starts
# the app on that job, the way it always has. With no argument the
# app opens with no job loaded, and the page takes the job in.
JOB_DIR="${1:-}"
if [ -n "$JOB_DIR" ]; then
  # Dragging a folder onto this launcher can add edge spaces and
  # escape inner spaces; a Finder path may arrive wrapped in quotes.
  while [ "$JOB_DIR" != "${JOB_DIR% }" ]; do JOB_DIR="${JOB_DIR% }"; done
  while [ "$JOB_DIR" != "${JOB_DIR# }" ]; do JOB_DIR="${JOB_DIR# }"; done
  case "$JOB_DIR" in
    \"*\") JOB_DIR="${JOB_DIR#\"}"; JOB_DIR="${JOB_DIR%\"}" ;;
    \'*\') JOB_DIR="${JOB_DIR#\'}"; JOB_DIR="${JOB_DIR%\'}" ;;
  esac
  JOB_DIR="${JOB_DIR//\\ / }"

  if [ -f "$JOB_DIR" ]; then
    fail "That is a file, not a folder: $JOB_DIR. This app (part 2) runs on the runner job folder itself, the folder that holds transcript.json and cuts.json, not on the video file. Or start the app with no argument and drop the folder on the page."
  fi
  if [ ! -d "$JOB_DIR" ]; then
    fail "That job folder does not exist: $JOB_DIR"
  fi
  if [ ! -f "$JOB_DIR/transcript.json" ] || [ ! -f "$JOB_DIR/cuts.json" ]; then
    fail "That folder is not a complete runner job folder. It must contain transcript.json and cuts.json."
  fi
  printf 'Starting on this job folder: %s\n\n' "$JOB_DIR"
else
  printf 'The app opens with no job loaded. Drop a runner job folder on the page,\n'
  printf 'drop a single video to make its first edit here, or try the built-in\n'
  printf 'sample. Nothing to paste here.\n\n'
fi

# A Meta key saved on this Mac is used without asking, as before.
# There is no key prompt anymore: with no saved key the app runs the
# offline mock, and a key can be pasted on the page for the session.
KEY_DIR="$HOME/.talk-to-your-editor"
KEY_FILE="$KEY_DIR/meta_key"
if [ -s "$KEY_FILE" ]; then
  IFS= read -r SAVED_KEY < "$KEY_FILE" || true
  if [ -n "$SAVED_KEY" ]; then
    export MODEL_API_KEY="$SAVED_KEY"
    printf 'Using the saved Meta Model API key from this Mac. Live Muse direction is on.\n\n'
  fi
  unset SAVED_KEY
fi
# An ElevenLabs key saved on this Mac the same way is used for
# writing down dropped videos (the spoken-words route for the first
# edit). With no key saved, the on-this-Mac speech pack covers it.
EL_KEY_FILE="$KEY_DIR/elevenlabs_key"
if [ -s "$EL_KEY_FILE" ]; then
  IFS= read -r SAVED_EL_KEY < "$EL_KEY_FILE" || true
  if [ -n "$SAVED_EL_KEY" ]; then
    export ELEVENLABS_API_KEY="$SAVED_EL_KEY"
    printf 'Using the saved ElevenLabs key from this Mac. Spoken words for dropped videos are on.\n\n'
  fi
  unset SAVED_EL_KEY
fi
if [ -z "${MODEL_API_KEY:-}" ]; then
  printf 'Offline mock direction is on. To use live Muse direction, paste your\n'
  printf 'Meta Model API key in the sign-in box on the page; it is kept for the\n'
  printf 'session only and never saved to a file.\n\n'
fi

LOCAL_URL="http://127.0.0.1:$PORT"
LAN_IP=""
if command -v ipconfig >/dev/null 2>&1; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [ -z "$LAN_IP" ]; then
    LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
fi

printf 'Open Talk to Your Editor here on this Mac:\n  %s\n' "$LOCAL_URL"
if [ -n "$LAN_IP" ]; then
  printf '\nTo test on your phone, connect the phone to the same Wi-Fi as this Mac and open:\n  http://%s:%s\n' "$LAN_IP" "$PORT"
else
  printf '\nNo Wi-Fi network address was found, so phone testing is not available yet.\n'
  printf 'Connect this Mac to Wi-Fi, restart this launcher, and then use the phone address it prints.\n'
fi
printf '\nKeep this Terminal window open while testing. Closing it stops Talk to Your Editor.\n\n'

if command -v open >/dev/null 2>&1; then
  open "$LOCAL_URL" >/dev/null 2>&1 || true
fi

if [ -n "$JOB_DIR" ]; then
  exec tye serve --job-dir "$JOB_DIR" --launch-home "$LAUNCH_HOME" --host 0.0.0.0 --port "$PORT"
else
  exec tye serve --launch-home "$LAUNCH_HOME" --host 0.0.0.0 --port "$PORT"
fi
