#!/bin/zsh
cd "${0:A:h}" || exit 1

finish() {
  print ""
  read -r "reply?Press Return to close this window..."
}
trap finish EXIT

print "VC Workbook Enrichment"
print "======================"
if ! command -v python3 >/dev/null 2>&1; then
  print "Python 3 is missing. Install it from https://www.python.org/downloads/"
  print "Then double-click run.command again."
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  print "Setting up Python for the first time..."
  python3 -m venv .venv || exit 1
fi

print "Checking required packages (internet may be needed the first time)..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt || {
  print "Setup failed. Check your internet connection and try again."
  exit 1
}

.venv/bin/python run.py
