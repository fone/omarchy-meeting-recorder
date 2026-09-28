#!/bin/sh
# This fork is distributed as source; the original project's AUR package
# downloads upstream binaries and does not include the summary action.
set -eu
printf '%s\n' \
  'This fork does not provide a prebuilt package installer.' \
  'Build from source and configure the opt-in Obsidian action:' \
  'https://github.com/fone/omarchy-meeting-recorder/blob/main/docs/obsidian-summary.md' >&2
exit 1
