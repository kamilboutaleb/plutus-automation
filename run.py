"""Small, interactive entry point for people who do not use a terminal."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "Inputs"
OUTPUTS = ROOT / "Outputs"
REQUIRED = {"vc_name", "first_name", "last_name", "primary_email", "website"}


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nCancelled.") from None


def choose_input() -> Path:
    INPUTS.mkdir(exist_ok=True)
    files = sorted(INPUTS.glob("*.xlsx"), key=lambda p: p.name.lower())
    if not files:
        from openpyxl import Workbook

        template = INPUTS / "START_HERE.xlsx"
        book = Workbook()
        book.active.title = "firms"
        book.active.append(["vc_name", "first_name", "last_name",
                            "primary_email", "website"])
        book.save(template)
        print(f"\nCreated {template}.")
        print("Open it in Excel, add one firm per row, save it, then run this again.")
        raise SystemExit(0)
    print("\nPut your Excel file in the Inputs folder, then choose it below.")
    for index, path in enumerate(files, 1):
        print(f"  {index}. {path.name}")
    print("You can also drag an .xlsx file into this window and press Return.")
    while True:
        raw = ask("File number or file path: ")
        if raw.isdigit() and 1 <= int(raw) <= len(files):
            path = files[int(raw) - 1]
        else:
            # Terminal escapes spaces in paths when a file is dragged in.
            path = Path(raw.strip("'\"").replace("\\ ", " ")).expanduser()
        if path.is_file() and path.suffix.lower() == ".xlsx":
            return path.resolve()
        print("I cannot find that .xlsx file. Check the name and try again.")


def check_workbook(path: Path) -> int:
    import pandas as pd

    try:
        sheet = pd.read_excel(path)
    except Exception as exc:
        raise ValueError(f"Cannot open this Excel file: {exc}") from exc
    missing = REQUIRED.difference(sheet.columns)
    if missing:
        raise ValueError("Missing column(s): " + ", ".join(sorted(missing)))
    count = int(sheet["vc_name"].notna().sum())
    if not count:
        raise ValueError("The vc_name column has no firms to look up.")
    return count


def choose_method() -> str:
    print("\nHow should contacts be found?")
    print("  1. Firm websites — no account or paid credits")
    print("  2. RocketReach browser — sign-in required; email reveals may use credits")
    print("  3. RocketReach API — API key required; lookups may use credits")
    while True:
        choice = ask("Choose 1, 2, or 3 [1]: ") or "1"
        if choice in {"1", "2", "3"}:
            return choice
        print("Please type 1, 2, or 3.")


def unused_output(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def main() -> int:
    path = choose_input()
    try:
        count = check_workbook(path)
    except ValueError as exc:
        print(f"\nInput problem: {exc}")
        print("The first row must include vc_name, first_name, last_name, primary_email, website.")
        print("Fill vc_name and website for each firm; the other columns can be blank.")
        return 1
    print(f"\nFound {count} rows with a firm name in {path.name}.")
    method = choose_method()
    OUTPUTS.mkdir(exist_ok=True)
    label = {"1": "Website", "2": "RocketReach", "3": "API"}[method]
    base = OUTPUTS / f"{path.stem}_{label}_Enriched.xlsx"
    env = os.environ.copy()

    if method == "2":
        # This is the checkpoint path for this workbook. Keep it stable across runs.
        output = base
        if output.exists():
            print(f"\nAn earlier RocketReach run exists: {output.name}")
            print("Continuing skips firms already saved in that file.")
            choice = ask("Continue it? [Y/n]: ").lower()
            if choice not in {"", "y", "yes"}:
                output = unused_output(base)
        session = ROOT / ".rocketreach-auth.json"
        if session.is_file():
            print("A saved RocketReach sign-in was found.")
            email = ask("Press Return to reuse it, or enter your email to sign in again: ")
        else:
            email = ask("RocketReach email: ")
        if email:
            env["ROCKETREACH_EMAIL"] = email
            env["ROCKETREACH_PASSWORD"] = getpass.getpass("RocketReach password (hidden): ")
            if not env["ROCKETREACH_PASSWORD"]:
                print("A password is required when entering an email address.")
                return 1
        elif not session.is_file():
            print("An email address is required for first sign-in.")
            return 1
        print("A browser will open. Complete any CAPTCHA or email code there.")
        print("Revealing emails in RocketReach may spend account credits.")
        if ask("Start RocketReach lookup? Type YES: ") != "YES":
            print("Cancelled before lookup.")
            return 0
        print("Checking Chromium browser installation...")
        install = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], cwd=ROOT
        )
        if install.returncode:
            print("Could not install Chromium. Check your internet connection and try again.")
            return install.returncode
        command = [sys.executable, "rocketreach_web.py", "--input", str(path),
                   "--output", str(output), "--headful", "--timeout", "300", "--resume"]
        if email and session.is_file():
            command.append("--fresh-login")
    elif method == "3":
        output = unused_output(base)
        key = getpass.getpass("RocketReach API key (hidden): ")
        if not key:
            print("An API key is required.")
            return 1
        env["ROCKETREACH_API_KEY"] = key
        command = [sys.executable, "auto_enrich.py", "--input", str(path),
                   "--output", str(output)]
        print("\nFree preview of the lookup plan:")
        if subprocess.run(command, cwd=ROOT, env=env).returncode:
            return 1
        if ask("Spend RocketReach credits on this lookup? Type YES: ") != "YES":
            print("Cancelled before paid lookup.")
            return 0
        command.append("--go")
    else:
        output = unused_output(base)
        command = [sys.executable, "website_enrich.py", "--input", str(path),
                   "--output", str(output)]
        print("\nSearching public firm websites. This may take a while...")

    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode == 0 and output.is_file():
        print(f"\nDone. Open Outputs, then open {output.name}.")
        return 0
    print("\nThe run did not finish. Read the error above and try again.")
    if method == "2" and output.is_file():
        print(f"Progress is saved in {output.name}; choose Continue on the next run.")
    if method == "2":
        print("If the saved sign-in expired, run again and enter your email and password.")
    return result.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
