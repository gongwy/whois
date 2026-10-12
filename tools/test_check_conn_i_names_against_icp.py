import unittest

from check_conn_i_names_against_icp import build_report


def prior(conn_i, status, old_names, ip_names=""):
    return {
        "conn_i": conn_i,
        "result_status": status,
        "old_names": "\n".join(old_names),
        "matched_old_names": "\n".join(ip_names),
        "legacy_resource_count": 2,
    }


def icp(name, row, domain="example.edu.cn"):
    return {
        "name": name,
        "source_row": row,
        "domain_raw": domain,
        "cert_type": "事业单位法人证书",
        "cert_no": f"CERT-{row}",
    }


class CheckConnINamesAgainstICPTests(unittest.TestCase):
    def test_conflict_unique_icp_hit_keeps_old_whois_name(self):
        report = build_report(
            [prior("BJ000001", "冲突：多个旧名称在IP备案存在", ["甲（大学）", "乙大学"], ["甲（大学）", "乙大学"])],
            [icp("甲(大学)", 7)],
        )
        result = report["results"][0]
        self.assertEqual(result["icp_result"], "ICP唯一命中且与IP候选一致")
        self.assertEqual(result["unique_icp_candidate"], "甲（大学）")
        self.assertEqual(result["both_hit_name_count"], 1)
        self.assertEqual(report["icp_evidence"][0]["icp_source_row"], 7)

    def test_conflict_multiple_icp_hits_remains_conflict(self):
        report = build_report(
            [prior("BJ000002", "冲突：多个旧名称在IP备案存在", ["甲大学", "乙大学"], ["甲大学", "乙大学"])],
            [icp("甲大学", 3), icp("乙大学", 4)],
        )
        self.assertEqual(report["results"][0]["icp_result"], "ICP多个旧名称命中")
        self.assertEqual(report["results"][0]["unique_icp_candidate"], "")

    def test_unmatched_prior_can_gain_one_icp_candidate(self):
        report = build_report(
            [prior("BJ000003", "未匹配", ["甲大学", "乙大学"])],
            [icp("乙大学", 9)],
        )
        self.assertEqual(report["results"][0]["icp_result"], "ICP唯一命中")
        self.assertEqual(report["results"][0]["unique_icp_candidate"], "乙大学")
        hits = {row["old_name"]: row["icp_hit"] for row in report["candidate_names"]}
        self.assertEqual(hits, {"甲大学": "否", "乙大学": "是"})

    def test_no_icp_hit_stays_unmatched(self):
        report = build_report(
            [prior("BJ000004", "未匹配", ["甲大学", "乙大学"])],
            [icp("丙大学", 2)],
        )
        self.assertEqual(report["results"][0]["icp_result"], "ICP未命中")
        self.assertEqual(report["summary"]["ip_unmatched_icp_none"], 1)

    def test_unique_from_previous_stage_not_reprocessed(self):
        report = build_report(
            [prior("BJ000005", "唯一命中", ["甲大学", "乙大学"], ["甲大学"])],
            [icp("乙大学", 2)],
        )
        self.assertEqual(report["results"], [])

    def test_inconsistent_previous_stage_rejected(self):
        with self.assertRaisesRegex(ValueError, "IP 备案未匹配"):
            build_report(
                [prior("BJ000006", "未匹配", ["甲大学", "乙大学"], ["甲大学"])],
                [],
            )


if __name__ == "__main__":
    unittest.main()
