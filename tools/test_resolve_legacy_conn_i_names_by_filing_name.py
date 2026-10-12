from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_joint_rsdb_stage1 import normalized
from resolve_legacy_conn_i_names_by_filing_name import resolve


class ResolveLegacyConnIByFilingNameTest(unittest.TestCase):
    @staticmethod
    def filing(*names: str) -> dict[str, dict[str, object]]:
        return {
            normalized(name): {
                "display_names": {name},
                "source_ids": {"1"},
                "source_rows": {"2"},
                "ip_ranges": {"192.0.2.0 - 192.0.2.255"},
                "record_count": 1,
            }
            for name in names
        }

    @staticmethod
    def inputs():
        disputed = {"BJ000001": {
            normalized("甲大学"): "甲大学",
            normalized("乙大学"): "乙大学",
        }}
        sources = {"BJ000001": {
            normalized("甲大学"): {"a:1"},
            normalized("乙大学"): {"a:2"},
        }}
        counts = {"BJ000001": 2}
        return disputed, sources, counts

    def test_one_existing_name_is_selected(self):
        results, evidence = resolve(*self.inputs(), self.filing("乙大学"))
        self.assertEqual(results[0]["result_status"], "唯一命中")
        self.assertEqual(results[0]["final_org_name"], "乙大学")
        self.assertEqual(len(evidence), 1)

    def test_multiple_existing_names_are_recorded_as_conflict(self):
        results, evidence = resolve(*self.inputs(), self.filing("甲大学", "乙大学"))
        self.assertEqual(results[0]["result_status"], "冲突：多个旧名称在IP备案存在")
        self.assertEqual(results[0]["final_org_name"], "")
        self.assertEqual(len(evidence), 2)

    def test_no_existing_name_remains_unmatched(self):
        results, evidence = resolve(*self.inputs(), self.filing("丙大学"))
        self.assertEqual(results[0]["result_status"], "未匹配")
        self.assertEqual(results[0]["final_org_name"], "")
        self.assertEqual(evidence, [])

    def test_normalization_matches_but_preserves_whois_spelling(self):
        disputed, sources, counts = self.inputs()
        disputed["BJ000001"] = {
            normalized("甲大学（深圳）"): "甲大学（深圳）",
            normalized("乙大学"): "乙大学",
        }
        sources["BJ000001"] = {
            normalized("甲大学（深圳）"): {"a:1"},
            normalized("乙大学"): {"a:2"},
        }
        results, _ = resolve(
            disputed, sources, counts, self.filing("甲大学(深圳)"))
        self.assertEqual(results[0]["final_org_name"], "甲大学（深圳）")


if __name__ == "__main__":
    unittest.main()
