#!/usr/bin/env zsh
set -e
cd "$(dirname "$0")"
read "GARMIN_EMAIL?Garmin email: "
read -s "GARMIN_PASSWORD?Garmin password: "
echo
export GARMIN_EMAIL GARMIN_PASSWORD
.venv/bin/python3 server/main.py --test
