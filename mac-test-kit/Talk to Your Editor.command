#!/bin/bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="8765"

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
printf '\n'

printf 'Paste the runner job folder path, or press Enter for the built-in sample: '
IFS= read -r JOB_DIR || true

# Dragging a folder into Terminal can add edge spaces and escape inner spaces.
while [ "$JOB_DIR" != "${JOB_DIR% }" ]; do JOB_DIR="${JOB_DIR% }"; done
while [ "$JOB_DIR" != "${JOB_DIR# }" ]; do JOB_DIR="${JOB_DIR# }"; done

# A path pasted from Finder may arrive wrapped in quotes. Remove one pair.
case "$JOB_DIR" in
  \"*\") JOB_DIR="${JOB_DIR#\"}"; JOB_DIR="${JOB_DIR%\"}" ;;
  \'*\') JOB_DIR="${JOB_DIR#\'}"; JOB_DIR="${JOB_DIR%\'}" ;;
esac
JOB_DIR="${JOB_DIR//\\ / }"

if [ -z "$JOB_DIR" ]; then
  JOB_DIR="$REPO_ROOT/.sample-job"
  mkdir -p "$JOB_DIR"
  cp "$REPO_ROOT/tests/fixtures/sample_transcript.json" "$JOB_DIR/transcript.json"
  cp "$REPO_ROOT/tests/fixtures/sample_cuts.json" "$JOB_DIR/cuts.json"
  cp "$REPO_ROOT/tests/fixtures/sample_profile.json" "$JOB_DIR/profile.json"
  cp "$REPO_ROOT/tests/fixtures/sample_feedback.json" "$JOB_DIR/feedback.json"
  printf 'Using the built-in sample job.\n'
else
  printf 'Using this job folder: %s\n' "$JOB_DIR"
fi

if [ ! -d "$JOB_DIR" ]; then
  fail "That job folder does not exist: $JOB_DIR"
fi
if [ ! -f "$JOB_DIR/transcript.json" ] || [ ! -f "$JOB_DIR/cuts.json" ]; then
  fail "That folder is not a complete runner job folder. It must contain transcript.json and cuts.json."
fi

printf 'Optional: paste your Meta Model API key to use live Muse direction, or press Enter for the offline mock: '
IFS= read -r -s MODEL_API_KEY_INPUT || true
printf '\n'
if [ -n "$MODEL_API_KEY_INPUT" ]; then
  export MODEL_API_KEY="$MODEL_API_KEY_INPUT"
  printf 'Live Muse direction is on for this session only. The key was not saved.\n'
else
  unset MODEL_API_KEY || true
  printf 'Offline mock direction is on.\n'
fi
unset MODEL_API_KEY_INPUT

LOCAL_URL="http://127.0.0.1:$PORT"
LAN_IP=""
if command -v ipconfig >/dev/null 2>&1; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [ -z "$LAN_IP" ]; then
    LAN_IP="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
fi

printf '\nOpen Talk to Your Editor here on this Mac:\n  %s\n' "$LOCAL_URL"
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

exec tye serve --job-dir "$JOB_DIR" --host 0.0.0.0 --port "$PORT"
