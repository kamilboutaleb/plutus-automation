from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import run


class LauncherTests(unittest.TestCase):
    def test_template_is_created_when_inputs_are_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Inputs"
            with patch.object(run, "INPUTS", folder):
                with self.assertRaises(SystemExit) as result:
                    run.choose_input()
            self.assertEqual(result.exception.code, 0)
            self.assertTrue((folder / "START_HERE.xlsx").is_file())

    def test_workbook_validation_and_fresh_output_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "firms.xlsx"
            book = Workbook()
            book.active.append(["vc_name", "first_name", "last_name",
                                "primary_email", "website"])
            book.active.append(["Example Ventures", "", "", "", "example.com"])
            book.save(source)
            self.assertEqual(run.check_workbook(source), 1)
            self.assertEqual(run.unused_output(source).name, "firms_2.xlsx")

    def test_api_requires_explicit_yes_before_paid_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "firms.xlsx"
            source.touch()
            responses = iter(["3", "no"])
            with (patch.object(run, "ROOT", root),
                  patch.object(run, "OUTPUTS", root / "Outputs"),
                  patch.object(run, "choose_input", return_value=source),
                  patch.object(run, "check_workbook", return_value=1),
                  patch.object(run, "ask", side_effect=lambda _: next(responses)),
                  patch.object(run.getpass, "getpass", return_value="key"),
                  patch.object(run.subprocess, "run") as execute):
                execute.return_value.returncode = 0
                self.assertEqual(run.main(), 0)
                self.assertEqual(execute.call_count, 1)
                self.assertNotIn("--go", execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
