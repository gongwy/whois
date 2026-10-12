from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_joint_rsdb_stage1 import (
    DOMAIN_REGISTRATION_REQUIRED_HEADERS,
    ICP_SHEET,
    REQUIRED_RPSL,
)
from run_joint_rsdb_pipeline import (
    discover_domain_workbook,
    discover_workbook,
    validate_rsdb,
)


class WorkbookDiscoveryTest(unittest.TestCase):
    def test_discovers_the_only_workbook_with_required_sheet(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            selected = directory / "icp.xlsx"
            ignored = directory / "other.xlsx"
            selected.touch()
            ignored.touch()
            with patch(
                "run_joint_rsdb_pipeline.workbook_sheets",
                side_effect=lambda path: {ICP_SHEET} if path.name == "icp.xlsx" else {"其他"},
            ):
                self.assertEqual(discover_workbook(directory, ICP_SHEET, None), selected.resolve())

    def test_multiple_matching_workbooks_require_explicit_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "one.xlsx").touch()
            (directory / "two.xlsx").touch()
            with patch("run_joint_rsdb_pipeline.workbook_sheets", return_value={ICP_SHEET}):
                with self.assertRaisesRegex(ValueError, "发现多个"):
                    discover_workbook(directory, ICP_SHEET, None)

    def test_discovers_domain_registration_workbook_by_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            selected = directory / "2026-09-21_full.xlsx"
            ignored = directory / "icp.xlsx"
            selected.touch()
            ignored.touch()
            with patch(
                "run_joint_rsdb_pipeline.workbook_has_headers",
                side_effect=lambda path, headers: (
                    path.name == selected.name
                    and headers == DOMAIN_REGISTRATION_REQUIRED_HEADERS
                ),
            ):
                self.assertEqual(
                    discover_domain_workbook(directory, None), selected.resolve())


class RsdbValidationTest(unittest.TestCase):
    def test_requires_all_six_legacy_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaisesRegex(FileNotFoundError, "缺少文件"):
                validate_rsdb(directory)
            for filename, _ in REQUIRED_RPSL.values():
                (directory / filename).touch()
            validate_rsdb(directory)


if __name__ == "__main__":
    unittest.main()
