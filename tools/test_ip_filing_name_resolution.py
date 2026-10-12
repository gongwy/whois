from pathlib import Path
import sys
import tempfile
import unittest

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_ip_filing_legal_names import extract


class IpFilingLegalNameExtractionTest(unittest.TestCase):
    @staticmethod
    def workbook(path: Path, rows: list[list[object]]) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "IP分配信息"
        sheet.append(["ID", "IP地址类型", "起始IP", "终止IP", "使用单位名称", "分配方式"])
        for row in rows:
            sheet.append(row)
        workbook.save(path)

    def test_same_range_and_same_name_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.xlsx"
            self.workbook(path, [
                ["1", "IPv4", "192.0.2.0", "192.0.2.255", "测试大学", "分配且使用"],
                ["2", "IPv4", "192.0.2.0", "192.0.2.255", "测试大学", "分配且使用"],
            ])
            rows, stats = extract(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["legal_name"], "测试大学")
            self.assertEqual(stats["duplicate_same_name_rows"], 1)

    def test_same_range_with_different_names_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.xlsx"
            self.workbook(path, [
                ["1", "IPv4", "192.0.2.0", "192.0.2.255", "测试大学", "分配且使用"],
                ["2", "IPv4", "192.0.2.0", "192.0.2.255", "另一大学", "分配且使用"],
            ])
            with self.assertRaisesRegex(RuntimeError, "多个法人名称"):
                extract(path)

    def test_cache_contains_no_certificate_or_contact_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.xlsx"
            self.workbook(path, [[
                "1", "IPv6", "2001:db8::", "2001:db8::ffff", "测试大学", "分配且使用",
            ]])
            rows, _ = extract(path)
            self.assertEqual(set(rows[0]), {
                "ip_version", "start_ip", "end_ip", "legal_name",
                "allocation_methods", "source_ids", "source_rows",
            })

    def test_reserved_rows_are_excluded_even_when_they_have_a_name(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ip.xlsx"
            self.workbook(path, [
                ["1", "IPv4", "192.0.2.0", "192.0.2.255", "不应使用的机构", "预留"],
                ["2", "IPv4", "198.51.100.0", "198.51.100.255", "有效机构", "分配且使用"],
            ])
            rows, stats = extract(path)
            self.assertEqual([row["legal_name"] for row in rows], ["有效机构"])
            self.assertEqual(stats["reserved_rows_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
