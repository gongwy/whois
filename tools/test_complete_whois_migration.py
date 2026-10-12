import csv
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

import convert_rsdb_cernic as converter
from prepare_rsdb_import import parse_dump, validate_dump


class CompleteWhoisMigrationTests(unittest.TestCase):
    def test_preprocessing_decisions_drive_materialisation_without_rewriting_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "rsdb"
            output = root / "output"
            source.mkdir()
            (source / "CERNIC.db.pn.txt").write_text(
                "person: Test Person\nnic-hdl: TP1-CER\naddress: Test\n\n", encoding="utf-8")
            (source / "CERNIC.db.ro.txt").write_text("", encoding="utf-8")
            (source / "CERNIC.db.in.txt").write_text(
                "inetnum: 10.0.0.0/24\nnetname: TEST-NET\nconn-i: BJ000001\n"
                "descr: 甲大学\ndescr: University A\nadmin-c: TP1-CER\n"
                "notify: dbm@example.cn\n\n"
                "inetnum: 10.0.1.0 - 10.0.1.255\nnetname: TEST-NET-2\nconn-i: BJ000001\n"
                "descr: 乙大学\ndescr: University B\nadmin-c: TP1-CER\n"
                "notify: dbm@example.cn\n\n",
                encoding="utf-8")
            (source / "CERNIC.db.i6.txt").write_text(
                "*i6: 2001:db8::/48\nnetname: TEST6\nconn-i: BJ000001\n"
                "descr: 甲大学\ndescr: University A\nadmin-c: TP1-CER\n\n",
                encoding="utf-8")
            (source / "CERNIC.db.dn.txt").write_text(
                "domain: WWW.EXAMPLE.EDU.CN\nnetname: TEST-NET\nadmin-c: TP1-CER\n\n"
                "domain: orphan.example.edu.cn\nnetname: UNKNOWN\nadmin-c: TP1-CER\n\n",
                encoding="utf-8")
            (source / "CERNIC.db.ht.txt").write_text(
                "host: ns.example.edu.cn\nipnum: 10.0.0.1\n\n", encoding="utf-8")
            bootstrap = root / "bootstrap.db"
            bootstrap.write_text(
                "person: Operator\nnic-hdl: OP1-CERNIC\ne-mail: op@example.cn\n\n",
                encoding="utf-8")
            city_codes = root / "city.json"
            city_codes.write_text('{"cities":{},"locations":{},"provinces":{}}', encoding="utf-8")
            report = {
                "stage": "legacy-whois-org-preprocessing",
                "legacy": {
                    "organisations": [{
                        "conn_i": "BJ000001", "orgnames": "甲大学\n乙大学",
                        "orgname_count": 2, "resource_count": 3,
                    }],
                    "domains": [
                        {"domain": "WWW.EXAMPLE.EDU.CN", "status": "唯一匹配",
                         "matched_conn_i": "BJ000001", "matching_route": "netname"},
                        {"domain": "orphan.example.edu.cn", "status": "零匹配冲突",
                         "matched_conn_i": "", "matching_route": "无"},
                    ],
                },
                "selected_multi_name_orgnames": [{
                    "conn_i": "BJ000001", "selected_orgname": "甲大学",
                    "selected_at_stage": "ICP",
                }],
                "unresolved_multi_name_orgs": [],
            }
            report_path = root / "preprocess.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

            ip_workbook = root / "ip.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "IP分配信息"
            sheet.append([
                "分配方式", "使用单位名称", "使用单位证件号码", "使用单位详细地址",
                "紧急联系人", "紧急联系人电话", "紧急联系人电子邮件",
            ])
            book.save(ip_workbook)
            exclusions = root / "exclusions.tsv"
            with exclusions.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t")
                writer.writerow(["source_kind", "organisation_name", "cert_no", "action", "reason"])

            converter.main(
                source, output, bootstrap, None, None, city_codes,
                preprocess_report=report_path,
                preserve_source_primary_values=True,
                ip_workbook=ip_workbook,
                filing_exclusions=exclusions,
            )
            dump = output / "CERNIC-import-test.db"
            result = validate_dump(dump)
            self.assertEqual(result["status"], "passed")
            objects = list(parse_dump(dump))
            organisation = next(obj for obj in objects if converter.one(obj, "organisation") == "BJ000001")
            self.assertEqual(converter.one(organisation, "c-org-name"), "甲大学")
            self.assertEqual(converter.one(organisation, "org-name"), "University A")
            self.assertNotIn("descr", organisation)
            self.assertNotIn("remarks", organisation)
            self.assertEqual(organisation.get("notify"), ["dbm@example.cn"])
            self.assertIn("10.0.0.0/24", [converter.one(obj, "inetnum") for obj in objects])
            domains = {converter.one(obj, "domain"): converter.one(obj, "org") for obj in objects if "domain" in obj}
            self.assertEqual(domains["WWW.EXAMPLE.EDU.CN"], "BJ000001")
            self.assertTrue(domains["orphan.example.edu.cn"].startswith("FK"))


if __name__ == "__main__":
    unittest.main()
