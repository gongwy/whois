import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_joint_rsdb_stage1 import (
    FilingRecord,
    Stage1,
    canonical_domain,
    domain_tokens,
    legacy_names,
    most_specific_matches,
    normalized,
    parse_ip_range,
    parse_ip_ranges,
    parse_rpsl_file,
    person_evidence_for_merge,
    filing_identity,
    meaningful,
    organisation_matching_ip_ranges,
    prefer_institutional_matches,
    resolve_filing_identity,
    select_domain_identity,
)
from migration_special_rules import legacy_resource_exclusion_for


class NormalizationTest(unittest.TestCase):
    @staticmethod
    def filing_record(record_id, source_kind, name, cert_no, cert_type=""):
        return FilingRecord(
            record_id=record_id, source_file=f"{source_kind}.xlsx",
            source_sheet="Sheet1", source_row=2, source_kind=source_kind,
            name=name, cert_no=cert_no, cert_type=cert_type,
        )

    def test_complete_identity_requires_name_and_certificate_number(self):
        complete = self.filing_record("A", "icp", "测试大学", "123")
        missing_name = self.filing_record("B", "icp", "", "123")
        missing_cert = self.filing_record("C", "icp", "测试大学", "")
        self.assertEqual(filing_identity(complete), ("123", normalized("测试大学")))
        self.assertIsNone(filing_identity(missing_name))
        self.assertIsNone(filing_identity(missing_cert))

    def test_icp_identity_wins_over_domain_registry(self):
        icp = self.filing_record("ICP", "icp", "ICP法人", "ICP-1")
        registry = self.filing_record(
            "REG", "domain-registration", "域名备案法人", "REG-1")
        source, status, selected = select_domain_identity([icp], [registry])
        self.assertEqual((source, status), ("icp", "unique"))
        self.assertEqual([record.record_id for record in selected], ["ICP"])

    def test_domain_registry_is_used_only_when_icp_identity_is_incomplete(self):
        incomplete_icp = self.filing_record("ICP", "icp", "ICP法人", "")
        registry = self.filing_record(
            "REG", "domain-registration", "域名备案法人", "REG-1")
        source, status, selected = select_domain_identity([incomplete_icp], [registry])
        self.assertEqual((source, status), ("domain-registration", "unique"))
        self.assertEqual([record.record_id for record in selected], ["REG"])

    def test_icp_conflict_blocks_fallback_to_domain_registry(self):
        icp_a = self.filing_record("ICP-A", "icp", "法人甲", "A")
        icp_b = self.filing_record("ICP-B", "icp", "法人乙", "B")
        registry = self.filing_record(
            "REG", "domain-registration", "域名备案法人", "REG-1")
        source, status, selected = select_domain_identity([icp_a, icp_b], [registry])
        self.assertEqual((source, status), ("icp", "multiple-certificates"))
        self.assertEqual({record.record_id for record in selected}, {"ICP-A", "ICP-B"})

    def test_certificate_type_code_does_not_split_same_legal_identity(self):
        icp = self.filing_record("ICP", "icp", "测试大学", "123", "事业单位法人证书")
        registry = self.filing_record("REG", "domain-registration", "测试大学", "123", "10")
        status, selected = resolve_filing_identity([icp, registry])
        self.assertEqual(status, "unique")
        self.assertEqual(len(selected), 2)

    def test_confirmed_sigcomm_duplicate_is_excluded(self):
        fields = {
            "netname": ["SIGCOMM-CN"],
            "conn-i": ["BJ003772"],
            "descr": ["CERNET网络中心(会议地址)", "Special Interest Group"],
        }
        rule = legacy_resource_exclusion_for(
            "inetnum", "219.243.192.0 - 219.243.199.255", fields)
        self.assertEqual(rule["rule_id"], "LEGACY-RESOURCE-EXCLUDE-001")

    def test_retained_cconference_object_is_not_excluded(self):
        fields = {
            "netname": ["CCONFERENCE-CN"],
            "conn-i": ["BJ000000"],
            "descr": ["CERNET会议活动专用地址"],
        }
        self.assertIsNone(legacy_resource_exclusion_for(
            "inetnum", "219.243.192.0 - 219.243.199.255", fields))

    def test_org_normalization_handles_full_width_punctuation(self):
        self.assertEqual(normalized("天津市（教育） 学校"), normalized("天津市教育学校"))

    def test_domain_parses_url_and_idn(self):
        self.assertEqual(canonical_domain("https://www.例子.中国/path"), "www.xn--fsqu00a.xn--fiqs8s")
        self.assertEqual(domain_tokens("example.edu.cn；https://www.例子.中国/a"), [
            "example.edu.cn", "www.xn--fsqu00a.xn--fiqs8s",
        ])

    def test_www_label_is_not_folded_into_apex_domain(self):
        self.assertEqual(canonical_domain("WWW.EDU.CN"), "www.edu.cn")
        self.assertEqual(canonical_domain("edu.cn."), "edu.cn")
        self.assertNotEqual(canonical_domain("WWW.EDU.CN"), canonical_domain("edu.cn"))

    def test_domain_normalization_never_removes_dns_labels(self):
        self.assertEqual(canonical_domain("xxx.xxx.edu.cn"), "xxx.xxx.edu.cn")
        self.assertEqual(canonical_domain("xxx.edu.cn"), "xxx.edu.cn")
        self.assertNotEqual(
            canonical_domain("xxx.xxx.edu.cn"), canonical_domain("xxx.edu.cn"))

    def test_rpsl_primary_key_keeps_the_exact_source_value(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "domains.txt"
            source.write_text(
                "domain: WWW.EDU.CN\n"
                "orgname: Example\n\n"
                "domain: xxx.xxx.edu.cn\n"
                "orgname: Example 2\n",
                encoding="utf-8",
            )
            objects = parse_rpsl_file(source, "domain:", "domain")
            self.assertEqual(
                [obj["raw_key"] for obj in objects],
                ["WWW.EDU.CN", "xxx.xxx.edu.cn"],
            )
            self.assertEqual(objects[0]["key"], objects[0]["raw_key"])
            self.assertEqual(canonical_domain(objects[0]["raw_key"]), "www.edu.cn")

    def test_ip_range_accepts_cidr_and_range(self):
        self.assertEqual(parse_ip_range("192.0.2.0/24").display(), "192.0.2.0 - 192.0.2.255")
        self.assertEqual(parse_ip_range("192.0.2.9").display(), "192.0.2.9 - 192.0.2.9")
        self.assertEqual(parse_ip_range("2001:db8::", "2001:db8::ffff").version, 6)
        self.assertIsNone(parse_ip_range("192.0.2.9", "192.0.2.1"))

    def test_multi_ip_cell_parses_ipv4_and_ipv6_independently(self):
        ranges = parse_ip_ranges(
            "203.0.113.9;2001:db8::9",
            "203.0.113.9;2001:db8::9",
        )
        self.assertEqual([item.version for item in ranges], [4, 6])
        self.assertEqual([item.display() for item in ranges], [
            "203.0.113.9 - 203.0.113.9",
            "2001:db8::9 - 2001:db8::9",
        ])

    def test_icp_ip_ranges_never_establish_organisation_ownership(self):
        interval = parse_ip_range("2001:db8::/48")
        record = FilingRecord(
            record_id="ICP-1", source_file="icp.xlsx", source_sheet="ICP备案数据",
            source_row=2, source_kind="icp", name="测试机构", ip_ranges=[interval],
        )
        self.assertEqual(organisation_matching_ip_ranges(record), [])
        self.assertEqual(record.ip_ranges, [interval])

    def test_ip_allocation_ranges_remain_ownership_evidence(self):
        interval = parse_ip_range("192.0.2.0/24")
        record = FilingRecord(
            record_id="IP-1", source_file="ip.xlsx", source_sheet="IP分配信息",
            source_row=2, source_kind="ip-allocation", name="测试机构", ip_ranges=[interval],
        )
        self.assertEqual(organisation_matching_ip_ranges(record), [interval])

    def test_icp_contacts_never_participate_in_person_merge(self):
        record = FilingRecord(
            record_id="ICP-1", source_file="icp.xlsx", source_sheet="ICP备案数据",
            source_row=2, source_kind="icp", name="测试机构",
            person_evidence=[{"role": "ICP主体负责人", "name": "测试联系人"}],
        )
        self.assertEqual(person_evidence_for_merge(record), [])

    def test_ip_allocation_contacts_remain_person_evidence(self):
        evidence = [{"role": "IP紧急联系人", "name": "测试联系人"}]
        record = FilingRecord(
            record_id="IP-1", source_file="ip.xlsx", source_sheet="IP分配信息",
            source_row=2, source_kind="ip-allocation", name="测试机构",
            person_evidence=evidence,
        )
        self.assertEqual(person_evidence_for_merge(record), evidence)

    def test_placeholder_certificate_is_empty_evidence(self):
        self.assertEqual(meaningful("无"), "")
        self.assertEqual(meaningful("--"), "")
        self.assertEqual(meaningful("91110000123456789X"), "91110000123456789X")

    def test_ip_match_prefers_smallest_containing_allocation(self):
        target = parse_ip_range("192.0.2.10", "192.0.2.10")
        broad = parse_ip_range("192.0.0.0/16")
        specific = parse_ip_range("192.0.2.0/24")
        matches = most_specific_matches(target, [
            (broad, "BACKBONE", {}),
            (specific, "SCHOOL", {}),
        ])
        self.assertEqual([item[1] for item in matches], ["SCHOOL"])

    def test_equal_range_prefers_institution_over_provincial_container(self):
        target = parse_ip_range("180.85.12.2")
        allocation = parse_ip_range("180.85.0.0/17")
        container = {
            "fields": {"descr": ["CERNET Provincial Address Allocations"]},
        }
        institution = {
            "fields": {"descr": ["西南交通大学", "Southwest Jiaotong University"]},
        }
        matches = most_specific_matches(target, [
            (allocation, "BJ000000", container),
            (allocation, "CD000323", institution),
        ])
        self.assertEqual([item[1] for item in matches], ["CD000323"])

    def test_two_institutional_objects_remain_ambiguous(self):
        interval = parse_ip_range("192.0.2.0/24")
        matches = prefer_institutional_matches([
            (interval, "ORG1", {"fields": {"descr": ["机构甲"]}}),
            (interval, "ORG2", {"fields": {"descr": ["机构乙"]}}),
        ])
        self.assertEqual([item[1] for item in matches], ["ORG1", "ORG2"])

    def test_legacy_names_ignore_later_descr_values(self):
        self.assertEqual(legacy_names({"descr": ["机构甲", "Org A", "项目乙"]}), ("机构甲", "Org A"))
        self.assertEqual(legacy_names({"descr": ["only remark"]}), ("", ""))


class ConflictDeduplicationTest(unittest.TestCase):
    def test_duplicate_conflict_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Stage1(Path(temp), Path("icp.xlsx"), Path("ip.xlsx"), Path(temp) / "out")
            values = (
                "字段冲突", "WHOIS与备案机构名称不一致", "organisation", "BJ1", "c-org-name",
                "甲", "乙", "book.xlsx/Sheet!2", "说明", "WHOIS_VALUE_RETAINED", "甲",
            )
            stage.add_conflict(*values)
            stage.add_conflict(*values)
            self.assertEqual(len(stage.conflicts), 1)

    def test_exclusion_matches_source_name_and_certificate(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            rule = temp_path / "exclude.tsv"
            rule.write_text(
                "source_kind\torganisation_name\tcert_no\taction\treason\n"
                "ip-allocation\t测试学校\t123\texclude\t疑似残留\n",
                encoding="utf-8",
            )
            stage = Stage1(temp_path, Path("icp.xlsx"), Path("ip.xlsx"), temp_path / "out", rule)
            stage.load_exclusions()
            self.assertEqual(stage.exclusion_for("ip-allocation", "测试学校", "123")["reason"], "疑似残留")
            self.assertIsNone(stage.exclusion_for("icp", "测试学校", "123"))

    def test_unmatched_reserved_record_cannot_create_organisation_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            stage = Stage1(temp_path, Path("icp.xlsx"), Path("ip.xlsx"), temp_path / "out")
            stage.filing_records = [FilingRecord(
                record_id="IP-1", source_file="ip.xlsx", source_sheet="IP分配信息",
                source_row=2, source_kind="ip-allocation", name="测试学校",
                allocation_method="预留", raw_ip_start="192.0.2.0",
                raw_ip_end="192.0.2.255", ip_ranges=[parse_ip_range("192.0.2.0/24")],
            )]
            stage.filter_unmatched_reserved_records({4: []})
            self.assertEqual(stage.filing_records, [])
            self.assertEqual(len(stage.non_organisation_filing_records), 1)
            self.assertEqual(
                stage.non_organisation_filing_records[0]["action"],
                "不创建新机构；不参与机构提取",
            )

    def test_reserved_record_may_enrich_matching_whois_organisation(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            stage = Stage1(temp_path, Path("icp.xlsx"), Path("ip.xlsx"), temp_path / "out")
            interval = parse_ip_range("192.0.2.0/24")
            record = FilingRecord(
                record_id="IP-1", source_file="ip.xlsx", source_sheet="IP分配信息",
                source_row=2, source_kind="ip-allocation", name="测试学校",
                allocation_method="预留", ip_ranges=[interval],
            )
            stage.filing_records = [record]
            stage.filter_unmatched_reserved_records({4: [(interval, "BJ000001", {})]})
            self.assertEqual(stage.filing_records, [record])
            self.assertEqual(stage.stats["reserved_named_records_with_whois_match"], 1)


if __name__ == "__main__":
    unittest.main()
