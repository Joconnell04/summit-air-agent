#!/bin/sh
# Replace our own lines in the user crontab with deploy/summit-air/crontab; anything else stays.
# grep exits 1 when it has nothing to print (an empty crontab, or one holding only our lines),
# which under set -e used to cut the pipe short and install an empty crontab.
set -eu
{
  crontab -l 2>/dev/null | grep -v "summit-air/app/deploy" | grep -v "^# Installed by deploy/summit-air" || true
  cat "$(dirname "$0")/crontab"
} | crontab -
