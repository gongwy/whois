import tempfile
import unittest
from pathlib import Path

from preprocess_legacy_whois_orgs import (
    build_report,
    matching_filing_exclusion,
    read_legacy,
    resolve_one_stage,
)


def ip_obj(key, netname, conn_i, orgname):
    return (
        f"inetnum: {key}\nnetname: {netname}\nconn-i: {conn_i}\n"
        f"descr: {orgname}\ndescr: English name\n\n"
    )


def domain_obj(domain, netname, c_orgname):
    return (
        f"domain: {domain}\nnetname: {netname}\n"
        f"c-orgname: {c_orgname}\n\n"
    )


def filing(name, source="ICP"):
    return {
        "legal_name": name,
        "record_id": f"{source}-2",
        "source_ref": f"{source}.xlsx/Sheet!2",
        "domain": "example.edu.cn",
        "certificate_type": "事业单位法人证书",
        "certificate_number": "123",
    }


class LegacyOrgPreprocessTests(unittest.TestCase):
    def test_domain_netname_precedes_c_orgname_and_zero_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "CERNIC.db.in.txt").write_text(
                ip_obj("10.0.0.0 - 10.0.0.255", "SHARED", "BJ000001", "甲大学")
                + ip_obj("10.0.1.0 - 10.0.1.255", "SHARED", "BJ000002", "乙大学"),
                encoding="utf-8",
            )
            (root / "CERNIC.db.i6.txt").write_text("", encoding="utf-8")
            (root / "CERNIC.db.dn.txt").write_text(
                domain_obj("ambiguous.edu.cn", "SHARED", "甲大学")
                + domain_obj("unique.edu.cn", "", "乙大学")
                + domain_obj("missing.edu.cn", "UNKNOWN", "丙大学"),
                encoding="utf-8",
            )
            legacy = read_legacy(root)
            by_domain = {item["domain"]: item for item in legacy["domains"]}
            self.assertEqual(by_domain["ambiguous.edu.cn"]["status"], "多候选冲突")
            self.assertEqual(by_domain["ambiguous.edu.cn"]["matching_route"], "netname")
            self.assertEqual(by_domain["unique.edu.cn"]["matched_conn_i"], "BJ000002")
            self.assertEqual(by_domain["missing.edu.cn"]["status"], "零匹配冲突")

    def test_priority_only_unresolved_continue_and_old_name_preserved(self):
        legacy = {
            "organisations": [], "org_conflicts": [
                {"conn_i": "BJ000001", "orgname_count": 2,
                 "orgnames": "甲（大学）\n乙大学", "resource_count": 2, "status": "同一conn-i多名称"},
                {"conn_i": "BJ000002", "orgname_count": 2,
                 "orgnames": "丙大学\n丁大学", "resource_count": 2, "status": "同一conn-i多名称"},
            ],
            "domains": [], "domain_multiple": [], "domain_zero": [],
            "excluded_legacy_resources": [],
            "ignored_ip_without_conn_i_or_range": [], "ignored_ip_without_orgname": [],
        }
        indexes = {
            "ICP": {"甲大学": [filing("甲(大学)")]},
            "DOMAIN_REGISTRY": {"乙大学": [filing("乙大学", "DOMAIN")],
                                "丁大学": [filing("丁大学", "DOMAIN")]},
            "IPFP": {"丙大学": [filing("丙大学", "IPFP")]},
        }
        report = build_report(legacy, indexes)
        self.assertEqual([stage["summary"]["input_conn_i"] for stage in report["name_stages"]], [2, 1, 0])
        self.assertEqual(report["selected_multi_name_orgnames"], [
            {"conn_i": "BJ000001", "selected_orgname": "甲（大学）", "selected_at_stage": "ICP"},
            {"conn_i": "BJ000002", "selected_orgname": "丁大学", "selected_at_stage": "DOMAIN_REGISTRY"},
        ])

    def test_multiple_hits_are_not_selected(self):
        stage = resolve_one_stage(
            [{"conn_i": "BJ000003", "orgnames": "甲大学\n乙大学",
              "resource_count": 2, "status": "旧WHOIS多名称"}],
            {"甲大学": [filing("甲大学")], "乙大学": [filing("乙大学")]},
            "ICP",
        )
        self.assertEqual(stage["results"][0]["status"], "多个名称命中")
        self.assertEqual(stage["results"][0]["selected_orgname"], "")

    def test_confirmed_filing_exclusion_matches_name_and_certificate(self):
        rules = [{"source_kind": "ip-allocation", "organisation_name": "旧学校",
                  "cert_no": "ABC123", "action": "exclude", "reason": "stale"}]
        self.assertIsNotNone(matching_filing_exclusion(rules, "ip-allocation", "旧学校", "ABC123"))
        self.assertIsNone(matching_filing_exclusion(rules, "ip-allocation", "旧学校", "OTHER"))


if __name__ == "__main__":
    unittest.main()
