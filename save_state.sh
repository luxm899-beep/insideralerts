#!/usr/bin/env bash
# Commit seen.json back to the repo, merging with whatever another run may
# have pushed while we were working.
git config user.name "alerts-bot"
git config user.email "actions@users.noreply.github.com"

for attempt in 1 2 3; do
  git fetch origin main
  git show origin/main:seen.json > remote.json 2>/dev/null || echo '{}' > remote.json
  python merge_state.py
  rm -f remote.json
  git reset origin/main
  git add seen.json
  if git diff --staged --quiet; then
    echo "nothing new to save"
    exit 0
  fi
  git commit -m "state $(date -u +%FT%TZ)"
  if git push origin HEAD:main; then
    exit 0
  fi
  echo "push raced with another run, retrying"
  sleep 5
done

echo "could not save state, will retry next run"
exit 0
