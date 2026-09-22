# Find investor contacts from a list of VC firms

This program reads an Excel file of firms and makes a new Excel file with up to three investor contacts per firm. It works on Windows and Mac.

## Start here

1. Install [Python 3](https://www.python.org/downloads/) if it is not already installed. On Windows, select **Add python.exe to PATH** during installation. On Mac, accept the default settings.
2. Start the program:
   - **Windows:** Double-click **`run.bat`**.
   - **Mac:** Double-click **`run.command`**. If your Mac blocks it, right-click the file and choose **Open**, then **Open** again.
   A command window will appear. The first run installs the required packages and needs internet access.
3. If the program creates `Inputs/START_HERE.xlsx`, open that file in Excel. Add one firm on each new row. Fill in **`vc_name`** and **`website`** (for example, `Example Ventures` and `https://example.com`). Leave `first_name`, `last_name`, and `primary_email` blank. Save and close Excel, then double-click your launcher again. You can also put an existing `.xlsx` file in `Inputs`; its first row must have those five exact column names.
4. Type the number beside your file and press **Return**. Choose **1** for the simplest method: search public firm websites. No account or paid credits are needed. The program also accepts an `.xlsx` file dragged into the command window.
5. Wait for **Done**, then open the `Outputs` folder and the named Excel file. Follow the prompt to close the command window.

Your input file is never edited. Each website or API run gets a new result name, so an earlier result is not replaced.

Keep workbooks in `Inputs` and `Outputs`. The program code stays beside `run.bat` and `run.command`; checks live in `tests`. Older mock workbooks were moved to `tests/archive` on this computer.

## Other lookup choices

- **2 — RocketReach browser:** Requires a RocketReach account. Enter your email and password on first use; the password is hidden while you type. A browser opens so you can finish any CAPTCHA or emailed code. Revealing emails may use RocketReach credits. The program asks you to type `YES` before starting. If you stop midway, run it again and choose **Continue** to use its saved progress. A saved sign-in is kept in `.rocketreach-auth.json` on this computer; treat that file like a password.
- **3 — RocketReach API:** Requires a RocketReach API key. The program shows a free plan first and asks you to type `YES` before spending lookup credits.

The browser method may stop when RocketReach changes its website or reaches a daily search limit. If it reports a daily limit, wait until the limit resets and choose **Continue** on a later run. Any progress already saved remains in `Outputs`.

## If something goes wrong

| Message or problem | What to do |
| --- | --- |
| Python 3 is missing | Install it from the link above, then double-click `run.bat` on Windows or `run.command` on Mac again. |
| Setup failed | Check the internet connection, then double-click your launcher again. |
| Missing column | Check the first row of your Excel file. The five names in step 3 must be spelled exactly. |
| No firms to look up | Add at least one `vc_name` under the header row, save, and close Excel. |
| Browser sign-in times out | Run again, complete the CAPTCHA or emailed code in the browser within five minutes. |
| File will not open | Use an `.xlsx` Excel workbook, not `.csv` or an older `.xls` file. |

## For people comfortable with the command line

Run `run.bat` in Command Prompt on Windows or `./run.command` in Terminal on Mac. The entry points remain available directly: `website_enrich.py`, `auto_enrich.py`, and `rocketreach_web.py`; run each with `--help` for its options. Dependencies are in `requirements.txt`.

The output has a `contacts` sheet and, when needed, a `no_match_firms` sheet. Contact status is `complete` when an email is present, `found_no_email` when a matching investor has no email, and `no_match` when no matching investor was found. The browser method can also save `retryable_error` rows so a later run can resume. The script keeps at most three contacts per firm and does not assign generic inboxes to individuals.

Local checks (no paid lookups): `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`, then `.venv/bin/python tests/check_api.py`, `.venv/bin/python tests/check_website.py`, and `.venv/bin/python tests/check_rocketreach.py`. The mock checks use temporary workbooks, so they do not add files to the repository root.
