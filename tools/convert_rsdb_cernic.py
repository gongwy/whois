#!/usr/bin/env python3
"""Convert legacy CERNIC RPSL files into an importable CERNIC test dump."""

from collections import Counter, defaultdict
import argparse
import csv
import ipaddress
import json
from pathlib import Path
import re
import unicodedata
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook

from migration_special_rules import (
    legacy_resource_exclusion_audit,
    legacy_resource_exclusion_for,
)

ROOT = Path(__file__).parent
SOURCE = ROOT / "rsdb"
OUT = ROOT / "rsdb-cernic-import-test"
WHOIS = ROOT.parent / "myTEST.db"
REVIEW = ROOT / "org_duplicates.xlsx"
WHOIS_SOURCE = "CERNIC"
MNTNER = "CERNIC-DBM-MNT"


class ReviewValidationError(ValueError):
    pass


def objects(path, first):
    current = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            if current:
                yield parse(current)
                current = []
            continue
        if raw.startswith(first) and current:
            yield parse(current)
            current = []
        current.append(raw)
    if current:
        yield parse(current)


def parse(lines):
    result = defaultdict(list)
    previous = None
    for raw in lines:
        if raw[:1].isspace() and previous:
            result[previous][-1] += " " + raw.strip()
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        previous = key.strip().lower()
        result[previous].append(value.strip())
    return result


def canonical_inetnum(value):
    value = value.strip()
    if "/" in value:
        network = ipaddress.ip_network(value, strict=False)
        return f"{network.network_address} - {network.broadcast_address}"
    start, end = re.split(r"\s*-\s*", value)
    return f"{ipaddress.ip_address(start.strip())} - {ipaddress.ip_address(end.strip())}"


def one(obj, key, default=""):
    return obj.get(key, [default])[0]


def add(lines, key, values):
    for value in values:
        if value:
            lines.append(f"{key}: {value}")


def unique_values(values):
    """Return non-empty values once, preserving their source order."""
    return list(dict.fromkeys(value for value in values if value))


def changed(obj):
    return min(obj.get("changed", ["99999999"]))


def handle_parts(value):
    match = re.fullmatch(r"([A-Za-z]{1,4})(\d*)-(?:CER|CN|AP)", value, re.I)
    return match.groups() if match else None


def column_number(reference):
    number = 0
    for character in re.match(r"[A-Z]+", reference).group():
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def read_review_overrides(path):
    if path is None:
        return {}
    if not path.is_file():
        raise FileNotFoundError(f"Organisation review overrides are missing: {path}")

    with path.open(encoding="utf-8", newline="") as file:
        rows = csv.DictReader(file)
        required = {"orgid", "c-org-name", "org-name"}
        if not required.issubset(rows.fieldnames or set()):
            raise ReviewValidationError("Override CSV must contain: orgid,c-org-name,org-name")
        return {
            row["orgid"].strip(): {
                "c-org-name": row["c-org-name"].strip(),
                "org-name": row["org-name"].strip(),
            }
            for row in rows
            if row.get("orgid", "").strip()
        }


def read_reviewed_organisations(review, overrides):
    if not review.is_file():
        raise FileNotFoundError(f"Reviewed organisation workbook is missing: {review}")

    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rels = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_rels = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    with ZipFile(review) as workbook:
        shared = ["".join(item.itertext()) for item in ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))]
        sheets = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships.findall(f"{package_rels}Relationship")}
        sheet = next(item for item in sheets.findall(f"{main}sheets/{main}sheet") if item.attrib["name"] == "核对结果")
        worksheet_path = "xl/" + targets[sheet.attrib[f"{rels}id"]]
        worksheet = ElementTree.fromstring(workbook.read(worksheet_path))

    reviewed, orgids_by_chinese_name, errors = {}, {}, []
    for row in worksheet.findall(f"{main}sheetData/{main}row"):
        values = {}
        for cell in row.findall(f"{main}c"):
            value = cell.find(f"{main}v")
            if value is None:
                continue
            cell_value = shared[int(value.text)] if cell.attrib.get("t") == "s" else value.text
            values[column_number(cell.attrib["r"])] = cell_value

        if values.get(1) == "序号" or not values.get(2) or not values.get(3):
            continue

        orgid = values[2].strip()
        chosen = split_reviewed_name(values[3])
        if chosen is None:
            override = overrides.get(orgid)
            if override:
                chosen = override["c-org-name"], override["org-name"]
            else:
                errors.append(f"- {orgid}: selected value needs c-org-name and org-name: {values[3]}")
                continue
        chosen_chinese_name, chosen_org_name = chosen
        old_names = set()
        candidate_names = set()
        for column, value in values.items():
            if column < 4 or not value:
                continue
            candidate = split_reviewed_name(value)
            if candidate is None:
                errors.append(f"- {orgid}: candidate value is invalid: {value}")
                continue
            candidate_names.add(candidate)
            old_names.add(candidate[1])
        candidate_names.add((chosen_chinese_name, chosen_org_name))
        old_names.add(chosen_org_name)
        reviewed[orgid] = {
            "c-org-name": chosen_chinese_name,
            "org-name": chosen_org_name,
            "old-org-names": old_names,
            "candidate-names": sorted(candidate_names),
        }

        for column, value in values.items():
            if column < 3 or not value:
                continue
            candidate = split_reviewed_name(value)
            if candidate:
                orgids_by_chinese_name.setdefault(candidate[0], set()).add(orgid)

    if errors:
        raise ReviewValidationError("Invalid organisation review data:\n" + "\n".join(errors))

    return reviewed, orgids_by_chinese_name


def read_reviewed_organisation_ids(review):
    """Return duplicate organisation IDs without requiring review choices.

    This supports a conservative first import: every ID listed on the review
    sheet, including rows whose chosen name is still blank, is deferred.
    """
    if not review.is_file():
        raise FileNotFoundError(f"Organisation review workbook is missing: {review}")

    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rels = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package_rels = "{http://schemas.openxmlformats.org/package/2006/relationships}"

    with ZipFile(review) as workbook:
        shared = ["".join(item.itertext()) for item in ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))]
        sheets = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships.findall(f"{package_rels}Relationship")}
        sheet = next(item for item in sheets.findall(f"{main}sheets/{main}sheet") if item.attrib["name"] == "核对结果")
        worksheet_path = "xl/" + targets[sheet.attrib[f"{rels}id"]]
        worksheet = ElementTree.fromstring(workbook.read(worksheet_path))

    organisation_ids = set()
    for row in worksheet.findall(f"{main}sheetData/{main}row"):
        values = {}
        for cell in row.findall(f"{main}c"):
            value = cell.find(f"{main}v")
            if value is None:
                continue
            cell_value = shared[int(value.text)] if cell.attrib.get("t") == "s" else value.text
            values[column_number(cell.attrib["r"])] = cell_value
        if values.get(1) != "序号" and values.get(2):
            organisation_ids.add(values[2].strip().upper())
    return organisation_ids


def split_reviewed_name(value):
    normalized = value.replace("（", "(").replace("）", ")").strip()
    if "(" not in normalized:
        return None
    chinese_name, org_name = normalized.rsplit("(", 1)
    chinese_name = chinese_name.rstrip(" (").strip()
    org_name = org_name.rstrip(")").strip()
    return (chinese_name, org_name) if chinese_name and org_name else None


def normalized_location(value):
    return re.sub(r"[\s.。·、,，()（）]", "", value).removesuffix("市")


def normalized_name(value):
    return re.sub(
        r"[\s\-_.()（）,，、/\\:：]+", "",
        unicodedata.normalize("NFKC", str(value or "")).strip().casefold(),
    )


def meaningful_text(value):
    value = str(value or "").strip()
    return "" if value in {"", "-", "--", "无", "暂无", "不详", "未知"} else value


def filing_exclusion_rules(path):
    if path is None:
        return []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        return [dict(row) for row in rows]


def is_filing_excluded(rules, name, cert_no):
    for rule in rules:
        if normalized_name(rule.get("source_kind")) != normalized_name("ip-allocation"):
            continue
        if normalized_name(rule.get("organisation_name")) != normalized_name(name):
            continue
        expected = normalized_name(meaningful_text(rule.get("cert_no")))
        if not expected or expected == normalized_name(meaningful_text(cert_no)):
            return True
    return False


def read_ip_filing_rows(path):
    if path is None:
        return []
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "IP分配信息" not in workbook.sheetnames:
            raise ValueError(f"IP filing workbook has no IP分配信息 sheet: {path}")
        sheet = workbook["IP分配信息"]
        values = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(values)]
        required = {
            "分配方式", "使用单位名称", "使用单位证件号码", "使用单位详细地址",
            "紧急联系人", "紧急联系人电话", "紧急联系人电子邮件",
        }
        if not required.issubset(headers):
            raise ValueError("IP filing workbook is missing required emergency contact columns")
        return [
            (row_no, {header: str(row[index] or "").strip() if index < len(row) else ""
                      for index, header in enumerate(headers)})
            for row_no, row in enumerate(values, 2) if any(value is not None for value in row)
        ]
    finally:
        workbook.close()


def read_city_codes(path):
    if path is None or not path.is_file():
        raise FileNotFoundError("City code map is missing; generate it with tools/extract_city_code_map.mjs")
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def resolve_city_code(city_codes, city, province):
    city_key = normalized_location(city)
    province_key = normalized_location(province)

    city_matches = {
        code for name, code in city_codes["cities"].items()
        if normalized_location(name) == city_key
    }
    if len(city_matches) == 1:
        return next(iter(city_matches)), "city"

    location_matches = {
        code
        for name, codes in city_codes["locations"].items()
        if normalized_location(name) == city_key or normalized_location(name) in city_key
        for code in codes
    }
    if len(location_matches) == 1:
        return next(iter(location_matches)), "district"

    province_matches = {
        code for name, code in city_codes["provinces"].items()
        if normalized_location(name) == province_key or normalized_location(name).startswith(province_key)
    }
    if len(province_matches) == 1:
        return next(iter(province_matches)), "province-fallback"
    return "000000", "unknown-fallback"


def main(
    source, out, whois, review, overrides, city_codes_path,
    skip_reviewed_organisations=False, preprocess_report=None,
    preserve_source_primary_values=False, ip_workbook=None,
    filing_exclusions=None,
):
    out.mkdir(parents=True, exist_ok=True)
    city_codes = read_city_codes(city_codes_path)
    preprocessing = None
    if preprocess_report is not None:
        preprocessing = json.loads(Path(preprocess_report).read_text(encoding="utf-8"))
        if preprocessing.get("stage") != "legacy-whois-org-preprocessing":
            raise ValueError("Unexpected preprocessing report type")
        disputed_organisations = set()
        reviewed_organisations, orgids_by_chinese_name = {}, {}
    elif skip_reviewed_organisations:
        disputed_organisations = read_reviewed_organisation_ids(review)
        reviewed_organisations, orgids_by_chinese_name = {}, {}
    else:
        disputed_organisations = set()
        try:
            reviewed_organisations, orgids_by_chinese_name = read_reviewed_organisations(
                review, read_review_overrides(overrides))
        except ReviewValidationError as error:
            (out / "review-validation-errors.md").write_text(f"# Review validation errors\n\n{error}\n", encoding="utf-8")
            raise
    conversion_stats = Counter()
    city_id_rows = []
    people = list(objects(source / "CERNIC.db.pn.txt", "person:"))
    roles = list(objects(source / "CERNIC.db.ro.txt", "role:"))
    inet4_source = list(objects(source / "CERNIC.db.in.txt", "inetnum:"))
    inet4 = []
    excluded_legacy_resources = []
    for obj in inet4_source:
        rule = legacy_resource_exclusion_for("inetnum", one(obj, "inetnum"), obj)
        if rule is None:
            inet4.append(obj)
            continue
        excluded_legacy_resources.append(legacy_resource_exclusion_audit(
            rule,
            source_file="CERNIC.db.in.txt",
            source_line=None,
        ))
    conversion_stats["legacy_resources_excluded_by_rule"] = len(excluded_legacy_resources)
    inet6 = list(objects(source / "CERNIC.db.i6.txt", "*i6:"))
    domains = list(objects(source / "CERNIC.db.dn.txt", "domain:"))
    hosts = list(objects(source / "CERNIC.db.ht.txt", "host:"))

    contacts = [("person", obj) for obj in people] + [("role", obj) for obj in roles]
    by_base = defaultdict(list)
    for typ, obj in contacts:
        old = one(obj, "nic-hdl").upper()
        parts = handle_parts(old)
        if parts:
            by_base[parts[0].upper() + (parts[1] or "")].append((typ, obj, old, parts))

    handle_map, used = {}, set()
    for base, entries in sorted(by_base.items()):
        entries.sort(key=lambda item: (not item[2].endswith("-CER"), changed(item[1]), item[2]))
        for index, (typ, obj, old, parts) in enumerate(entries):
            space, number = parts[0].upper(), max(1, int(parts[1] or 0))
            if index == 0 and f"{space}{number}-CER" not in used:
                new = f"{space}{number}-CER"
            else:
                candidate = max(1, number)
                while f"{space}{candidate}-CER" in used:
                    candidate += 1
                new = f"{space}{candidate}-CER"
            used.add(new)
            handle_map[old] = new

    def ref(value):
        return handle_map.get(value.upper(), value.rsplit("-", 1)[0].upper() + "-CER")

    references = Counter()
    for obj in inet4 + inet6 + domains + [obj for _, obj in contacts]:
        for key in ("admin-c", "tech-c", "zone-c"):
            for value in obj.get(key, []):
                references[ref(value)] += 1

    primary = set(handle_map.values())
    placeholders = sorted(set(references) - primary)
    fallback_handle = "WIO1-CER"
    default_notify = "integration@example.invalid"
    for obj in objects(whois, "person:"):
        if one(obj, "nic-hdl").upper().endswith("-CERNIC"):
            fallback_handle = one(obj, "nic-hdl").upper().replace("-CERNIC", "-CER")
            default_notify = one(obj, "e-mail", default_notify)
            break

    org_resources = defaultdict(list)
    org_resources_by_upper_id = defaultdict(list)
    for obj in inet4 + inet6:
        if one(obj, "conn-i"):
            org_resources[one(obj, "conn-i")].append(obj)
            org_resources_by_upper_id[one(obj, "conn-i").upper()].append(obj)
    org_id_map, used_org = {}, set()
    invalid_index = 1
    for old in sorted(set(org_resources) | set(reviewed_organisations)):
        candidate = old.upper()
        match = re.fullmatch(r"([A-Z]{2,4})(\d+)", candidate)
        if match and int(match.group(2)) > 0:
            prefix, number = match.group(1), int(match.group(2))
            while candidate in used_org:
                number += 1
                candidate = f"{prefix}{number}"
        else:
            while f"LG{invalid_index:06d}" in used_org:
                invalid_index += 1
            candidate = f"LG{invalid_index:06d}"
            invalid_index += 1
        org_id_map[old] = candidate
        used_org.add(candidate)
    normalized_org_id_map = {old.upper(): org_id for old, org_id in org_id_map.items()}

    def most_common(values):
        values = [value for value in values if value]
        return Counter(values).most_common(1)[0][0] if values else ""

    organisation_name_decisions = []
    if preprocessing is not None:
        selected_by_conn_i = {
            row["conn_i"].upper(): (row["selected_orgname"], row["selected_at_stage"])
            for row in preprocessing.get("selected_multi_name_orgnames", [])
        }
        unresolved_ids = {
            row["conn_i"].upper()
            for row in preprocessing.get("unresolved_multi_name_orgs", [])
        }
        preprocessed_orgs = {
            row["conn_i"].upper(): row
            for row in preprocessing["legacy"]["organisations"]
        }

        def candidate_pair(related, chinese_name):
            matching = [
                obj for obj in related
                if obj.get("descr") and normalized_name(obj["descr"][0]) == normalized_name(chinese_name)
            ]
            pool = matching or related
            english = most_common([
                obj["descr"][1]
                for obj in pool if len(obj.get("descr", [])) >= 2 and obj["descr"][1]
            ])
            return chinese_name, english or chinese_name or ""

        for old, org_id in sorted(org_id_map.items()):
            old_upper = old.upper()
            related = org_resources.get(old, [])
            source_row = preprocessed_orgs.get(old_upper)
            candidates = source_row["orgnames"].splitlines() if source_row else []
            if old_upper in selected_by_conn_i:
                selected, stage = selected_by_conn_i[old_upper]
                method = f"备案唯一命中:{stage}"
            elif old_upper in unresolved_ids:
                counts = Counter(
                    obj["descr"][0]
                    for obj in related if len(obj.get("descr", [])) >= 2 and obj["descr"][0]
                )
                selected = counts.most_common(1)[0][0] if counts else (candidates[0] if candidates else "")
                method = "未解决50条:按旧IP出现频次最高回退（并列取源文件先出现）"
            elif len(candidates) == 1:
                selected = candidates[0]
                method = "旧WHOIS唯一名称"
            else:
                selected = ""
                method = "旧WHOIS无可用机构名称"
            c_org_name, org_name = candidate_pair(related, selected)
            if not org_name:
                org_name = f"Legacy organisation {org_id}"
            candidate_pairs = [candidate_pair(related, item) for item in candidates]
            reviewed_organisations[old_upper] = {
                "c-org-name": c_org_name,
                "org-name": org_name,
                "old-org-names": {pair[1] for pair in candidate_pairs if pair[1]},
                "candidate-names": sorted(set(candidate_pairs)),
            }
            organisation_name_decisions.append({
                "legacy_orgid": old_upper,
                "organisation": org_id,
                "selected_c_org_name": c_org_name,
                "selected_org_name": org_name,
                "decision_method": method,
                "candidate_count": len(candidates),
                "candidates": " | ".join(candidates),
            })

    deferred_org_ids = {
        normalized_org_id_map[old]
        for old in disputed_organisations
        if old in normalized_org_id_map
    }
    reviewed_by_generated_orgid = {
        normalized_org_id_map[old.upper()]: reviewed
        for old, reviewed in reviewed_organisations.items()
        if old.upper() in normalized_org_id_map
    }

    orgids_by_netname = defaultdict(set)
    orgids_by_english_name = defaultdict(set)
    for obj in inet4 + inet6:
        orgid = normalized_org_id_map[one(obj, "conn-i").upper()]
        if orgid in deferred_org_ids:
            continue
        netname = one(obj, "netname").strip().casefold()
        if netname:
            orgids_by_netname[netname].add(orgid)
        for name in obj.get("descr", []):
            if name:
                orgids_by_english_name[name.strip().casefold()].add(orgid)

    orgids_by_person_chinese_name = defaultdict(set)
    for _, obj in contacts:
        chinese_name = one(obj, "c-orgname").strip()
        english_name = one(obj, "orgname").strip().casefold()
        if chinese_name and english_name:
            orgids_by_person_chinese_name[chinese_name].update(orgids_by_english_name.get(english_name, set()))

    def domain_organisation(obj):
        netname_orgids = orgids_by_netname.get(one(obj, "netname").strip().casefold(), set())
        if len(netname_orgids) == 1:
            conversion_stats["domain_netname_matches"] += 1
            return next(iter(netname_orgids)), "netname"

        chinese_name = one(obj, "c-orgname").strip()
        chinese_orgids = set(orgids_by_person_chinese_name.get(chinese_name, set()))
        chinese_orgids.update(
            normalized_org_id_map.get(orgid.upper(), orgid)
            for orgid in orgids_by_chinese_name.get(chinese_name, set()))
        if len(chinese_orgids) == 1:
            conversion_stats["domain_c_orgname_matches"] += 1
            return next(iter(chinese_orgids)), "c-orgname"

        if one(obj, "c-province").strip() == "新疆":
            conversion_stats["domain_xinjiang_fallback_matches"] += 1
            return normalized_org_id_map["BJ000035"], "xinjiang-fallback"

        if netname_orgids or chinese_orgids:
            conversion_stats["domain_ambiguous_organisation_matches"] += 1
            return None, "ambiguous organisation match"
        else:
            conversion_stats["domain_unmatched_organisations"] += 1
            return None, "no safe organisation match"

    domain_assignments = {}
    domains_by_org = defaultdict(list)
    unresolved_domains = []
    preprocessed_domains = {}
    if preprocessing is not None:
        preprocessed_domains = {
            row["domain"]: row for row in preprocessing["legacy"]["domains"]
        }
        if len(preprocessed_domains) != len(preprocessing["legacy"]["domains"]):
            raise ValueError("Preprocessing report contains duplicate raw domain keys")
    for obj in domains:
        if preprocessing is None:
            org_id, reason = domain_organisation(obj)
        else:
            raw_domain = one(obj, "domain")
            row = preprocessed_domains.get(raw_domain)
            if row is None:
                raise ValueError(f"Domain is missing from preprocessing report: {raw_domain}")
            if row["status"] == "唯一匹配":
                org_id = normalized_org_id_map.get(row["matched_conn_i"].upper())
                if org_id is None:
                    raise ValueError(
                        f"Preprocessed domain points to unknown conn-i: {raw_domain} -> {row['matched_conn_i']}")
                reason = row["matching_route"]
                conversion_stats[f"domain_{row['matching_route'].replace('-', '_')}_matches"] += 1
            else:
                org_id = None
                reason = row["status"]
                conversion_stats[
                    "domain_ambiguous_organisation_matches"
                    if row["status"] == "多候选冲突"
                    else "domain_unmatched_organisations"
                ] += 1
        domain_assignments[id(obj)] = org_id
        if org_id:
            domains_by_org[org_id].append(obj)
        else:
            unresolved_domains.append((obj, reason))

    fake_organisations = {}
    if not skip_reviewed_organisations:
        unresolved_groups = defaultdict(list)
        for obj, reason in unresolved_domains:
            chinese_name = one(obj, "c-orgname").strip()
            english_name = one(obj, "orgname").strip()
            netname = one(obj, "netname").strip()
            if chinese_name or english_name:
                group_key = ("name", chinese_name.casefold(), english_name.casefold())
            elif netname:
                group_key = ("netname", netname.casefold())
            else:
                group_key = ("domain", one(obj, "domain").strip().casefold())
            unresolved_groups[group_key].append((obj, reason))

        fake_index = 1
        for group_key in sorted(unresolved_groups):
            while f"FK{fake_index:06d}" in used_org:
                fake_index += 1
            fake_org_id = f"FK{fake_index:06d}"
            fake_index += 1
            used_org.add(fake_org_id)
            entries = unresolved_groups[group_key]
            fake_organisations[fake_org_id] = entries
            for obj, _ in entries:
                domain_assignments[id(obj)] = fake_org_id
                domains_by_org[fake_org_id].append(obj)

    skipped_domains = unresolved_domains if skip_reviewed_organisations else []

    org_admin_contacts = defaultdict(set)
    new_filing_contacts = []
    filing_contact_audit = []
    if preprocessing is not None and ip_workbook is not None:
        orgids_by_final_name = defaultdict(set)
        for legacy_orgid, reviewed in reviewed_organisations.items():
            generated = normalized_org_id_map.get(legacy_orgid)
            if generated and reviewed.get("c-org-name"):
                orgids_by_final_name[normalized_name(reviewed["c-org-name"])].add(generated)

        old_contacts_by_name = defaultdict(list)
        for typ, obj in contacts:
            old = one(obj, "nic-hdl").upper()
            if old not in handle_map:
                continue
            for name in obj.get(typ, []) + obj.get("c-name", []):
                if normalized_name(name):
                    old_contacts_by_name[normalized_name(name)].append((obj, handle_map[old]))

        rules = filing_exclusion_rules(filing_exclusions)
        new_by_identity = {}
        filing_index = 1

        def phone_key(value):
            return re.sub(r"\D", "", value or "")

        def emails(value):
            return {part.strip().casefold() for part in re.split(r"[,，;；\s]+", value or "") if "@" in part}

        for row_no, row in read_ip_filing_rows(ip_workbook):
            if normalized_name(row.get("分配方式")) == normalized_name("预留"):
                continue
            legal_name = meaningful_text(row.get("使用单位名称"))
            if not legal_name or is_filing_excluded(rules, legal_name, row.get("使用单位证件号码", "")):
                continue
            target_orgids = sorted(orgids_by_final_name.get(normalized_name(legal_name), set()))
            emergency_name = meaningful_text(row.get("紧急联系人"))
            if not emergency_name or not target_orgids:
                continue
            base_audit = {
                "source_row": row_no,
                "legal_name": legal_name,
                "emergency_name": emergency_name,
                "phone": row.get("紧急联系人电话", ""),
                "email": row.get("紧急联系人电子邮件", ""),
                "target_organisations": " | ".join(target_orgids),
            }
            if len(target_orgids) != 1:
                filing_contact_audit.append({
                    **base_audit, "result": "未关联",
                    "contact_handle": "",
                    "reason": "同一最终机构名称对应多个organisation，未盲目添加admin-c",
                })
                continue

            candidates = old_contacts_by_name.get(normalized_name(emergency_name), [])
            matched = candidates
            if len(candidates) > 1:
                filing_phone = phone_key(row.get("紧急联系人电话", ""))
                filing_emails = emails(row.get("紧急联系人电子邮件", ""))
                evidence_matched = []
                for obj, handle in candidates:
                    old_phones = {phone_key(value) for value in obj.get("phone", []) if phone_key(value)}
                    old_emails = {value.casefold() for value in obj.get("e-mail", [])}
                    if (filing_phone and filing_phone in old_phones) or (filing_emails & old_emails):
                        evidence_matched.append((obj, handle))
                if evidence_matched:
                    matched = evidence_matched
            unique_handles = sorted({handle for _, handle in matched})
            if len(unique_handles) == 1:
                handle = unique_handles[0]
                result = "复用旧WHOIS联系人"
                reason = "姓名唯一；重名时以电话或邮箱缩小到唯一旧联系人"
            elif candidates:
                filing_contact_audit.append({
                    **base_audit, "result": "未关联", "contact_handle": "",
                    "reason": "同名旧WHOIS联系人仍有多个，未盲目新建或选择",
                })
                continue
            else:
                identity = (
                    normalized_name(emergency_name),
                    phone_key(row.get("紧急联系人电话", "")),
                    tuple(sorted(emails(row.get("紧急联系人电子邮件", "")))),
                )
                handle = new_by_identity.get(identity)
                if handle is None:
                    while f"FPC{filing_index}-CER" in used:
                        filing_index += 1
                    handle = f"FPC{filing_index}-CER"
                    filing_index += 1
                    used.add(handle)
                    new_by_identity[identity] = handle
                    new_filing_contacts.append({
                        "handle": handle,
                        "name": emergency_name,
                        "phone": row.get("紧急联系人电话", ""),
                        "email": row.get("紧急联系人电子邮件", ""),
                        "address": row.get("使用单位详细地址", "") or "IP filing emergency contact",
                    })
                result = "新建备案紧急联系人"
                reason = "旧WHOIS中没有同名联系人"
            org_admin_contacts[target_orgids[0]].add(handle)
            filing_contact_audit.append({
                **base_audit, "result": result, "contact_handle": handle, "reason": reason,
            })

    dump = []
    dump.extend([
        "person: Local Import Operator", "address: Local import test", "phone: +86 10000000000",
        f"e-mail: {default_notify}", f"nic-hdl: {fallback_handle}", f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", "",
        f"mntner: {MNTNER}", f"admin-c: {fallback_handle}", f"upd-to: {default_notify}",
        "auth: MD5-PW $1$cernic$7zLTR8wrxSBc4jojaxXrg1", f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""
    ])

    for handle in placeholders:
        dump += [f"role: Legacy contact placeholder", "address: Legacy data migration", f"e-mail: {default_notify}",
                 f"nic-hdl: {handle}", "remarks: Created for an unresolved legacy contact reference", f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]

    for typ, obj in contacts:
        old = one(obj, "nic-hdl").upper()
        if old not in handle_map:
            continue
        lines = [f"{typ}: {one(obj, typ)}"]
        if typ == "person":
            add(lines, "c-name", obj.get("c-name", []))
            address = obj.get("address", []) or [" ".join(filter(None, [one(obj, "province"), one(obj, "city"), one(obj, "street")]))]
            add(lines, "address", address)
        else:
            add(lines, "address", obj.get("address", []) or ["Legacy data migration"])
        add(lines, "phone", obj.get("phone", []))
        add(lines, "fax-no", obj.get("fax-no", []))
        add(lines, "e-mail", obj.get("e-mail", []) or ([default_notify] if typ == "role" else []))
        add(lines, "abuse-mailbox", obj.get("trouble", []))
        if typ == "role":
            for key in ("admin-c", "tech-c"):
                add(lines, key, [ref(value) for value in obj.get(key, [])])
        lines.append(f"nic-hdl: {handle_map[old]}")
        add(lines, "remarks", [f"legacy-changed: {value}" for value in obj.get("changed", [])])
        add(lines, "notify", obj.get("notify", []))
        lines += [f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)

    for item in new_filing_contacts:
        ascii_name = item["name"] if item["name"].isascii() else f"IP Filing Contact {item['handle'].split('-')[0]}"
        lines = [f"person: {ascii_name}"]
        if item["name"] != ascii_name:
            lines.append(f"c-name: {item['name']}")
        lines.append(f"address: {item['address']}")
        add(lines, "phone", [part.strip() for part in re.split(r"[,，;；]+", item["phone"]) if part.strip()])
        add(lines, "e-mail", [part.strip() for part in re.split(r"[,，;；\s]+", item["email"]) if "@" in part])
        lines += [
            f"nic-hdl: {item['handle']}",
            "remarks: Created from non-reserved IP filing emergency contact",
            f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", "",
        ]
        dump.extend(lines)

    for old, org_id in sorted(org_id_map.items()):
        if org_id in deferred_org_ids:
            conversion_stats["deferred_organisations"] += 1
            continue
        related = org_resources.get(old, [])
        related_domains = domains_by_org.get(org_id, [])
        reviewed = reviewed_organisations.get(old.upper())
        if preprocessing is not None:
            english_names = [
                obj["descr"][1] for obj in related if len(obj.get("descr", [])) >= 2
            ]
            source_chinese_names = [
                obj["descr"][0] for obj in related if len(obj.get("descr", [])) >= 2
            ]
        else:
            english_names = [value for obj in related for value in obj.get("descr", [])]
            source_chinese_names = []
        english_names.extend(one(obj, "orgname") for obj in related_domains)
        chinese_names = source_chinese_names + [one(obj, "c-orgname") for obj in related_domains]
        org_name = reviewed["org-name"] if reviewed else most_common(english_names) or f"Legacy organisation {org_id}"
        c_org_name = reviewed["c-org-name"] if reviewed else most_common(chinese_names)
        lines = [f"organisation: {org_id}", f"org-name: {org_name}"]
        if c_org_name:
            lines.append(f"c-org-name: {c_org_name}")
        # Alternative legacy names remain available in the preprocessing and
        # organisation-name decision reports.  They are review evidence, not
        # attributes of the materialised organisation object.
        location_values = {}
        for key in ("street", "c-street", "city", "c-city", "province", "c-province", "country"):
            # Legacy WHOIS wins.  Domain values are only a fallback when the
            # associated IP organisation has no value for the field.
            value = most_common([one(obj, key) for obj in related])
            if not value:
                value = most_common([one(obj, key) for obj in related_domains])
            location_values[key] = value
            if value:
                lines.append(f"{key}: {value}")
        city_id, city_id_resolution = resolve_city_code(
            city_codes,
            location_values["c-city"], location_values["c-province"])
        lines.append(f"c-cityid: {city_id}")
        conversion_stats[f"city_id_{city_id_resolution}"] += 1
        city_id_rows.append((org_id, location_values["c-city"], location_values["c-province"], city_id, city_id_resolution))
        add(lines, "admin-c", sorted(org_admin_contacts.get(org_id, set())))
        add(lines, "notify", unique_values(
            value for obj in related + related_domains for value in obj.get("notify", [])))
        lines += [f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)
        if reviewed:
            conversion_stats["reviewed_organisations"] += 1

    for fake_org_id, entries in sorted(fake_organisations.items()):
        related_domains = [obj for obj, _ in entries]
        failure_reasons = sorted({reason for _, reason in entries})
        lines = [
            f"organisation: {fake_org_id}",
            f"org-name: FAKE MIGRATION ORGANISATION {fake_org_id}",
            "remarks: migration-cleanup: unresolved-domain",
        ]
        add(lines, "remarks", [f"migration-match-failure: {reason}" for reason in failure_reasons])
        add(lines, "descr", [
            f"legacy-org-name: {name}"
            for name in sorted({one(obj, "orgname") for obj in related_domains if one(obj, "orgname")})
        ])
        add(lines, "remarks", [
            f"legacy-c-org-name: {name}"
            for name in sorted({one(obj, "c-orgname") for obj in related_domains if one(obj, "c-orgname")})
        ])
        location_values = {}
        for key in ("street", "c-street", "city", "c-city", "province", "c-province", "country"):
            value = most_common([one(obj, key) for obj in related_domains])
            location_values[key] = value
            if value:
                lines.append(f"{key}: {value}")
        city_id, city_id_resolution = resolve_city_code(
            city_codes, location_values["c-city"], location_values["c-province"])
        lines.append(f"c-cityid: {city_id}")
        conversion_stats[f"city_id_{city_id_resolution}"] += 1
        city_id_rows.append((
            fake_org_id, location_values["c-city"], location_values["c-province"],
            city_id, f"fake-{city_id_resolution}"))
        add(lines, "notify", unique_values(
            value for obj in related_domains for value in obj.get("notify", [])))
        lines += [f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)

    def resource(obj, typ, primary_key, netname_required=False):
        primary_value = one(obj, primary_key)
        if typ == "inetnum" and not preserve_source_primary_values:
            primary_value = canonical_inetnum(primary_value)
        lines = [f"{typ}: {primary_value}"]
        netname = one(obj, "netname")
        if netname_required or (typ == "domain" and netname):
            lines.append(f"netname: {netname}")
        org_id = org_id_map.get(one(obj, "conn-i"))
        if typ == "domain":
            org_id = domain_assignments[id(obj)]
        if org_id:
            lines.append(f"org: {org_id}")
        add(lines, "conn-p", obj.get("conn-p", []))
        add(lines, "descr", obj.get("descr", []))
        reviewed = reviewed_organisations.get(one(obj, "conn-i").upper())
        if typ == "domain":
            reviewed = reviewed_by_generated_orgid.get(org_id)
        if reviewed:
            old_names = set(obj.get("descr", [])) & reviewed["old-org-names"]
            if typ == "domain" and one(obj, "orgname"):
                old_names.add(one(obj, "orgname"))
            legacy_names = sorted(old_names - {reviewed["org-name"]})
            add(lines, "descr", [f"legacy-org-name: {name}" for name in legacy_names])
            conversion_stats["legacy_org_name_descriptions"] += len(legacy_names)
        add(lines, "country", obj.get("country", []))
        for key in ("admin-c", "tech-c", "zone-c"):
            add(lines, key, [ref(value) for value in obj.get(key, [])])
        add(lines, "nserver", obj.get("nserver", []))
        add(lines, "remarks", obj.get("remarks", []) + [f"legacy-changed: {value}" for value in obj.get("changed", [])])
        add(lines, "notify", obj.get("notify", []))
        if typ != "nameserver":
            lines.append(f"mnt-by: {MNTNER}")
        lines.append(f"source: {WHOIS_SOURCE}")
        lines.append("")
        dump.extend(lines)

    selected_inet4 = {}
    duplicate_inet4_rows = []
    for obj in inet4:
        key = canonical_inetnum(one(obj, "inetnum"))
        if key not in selected_inet4:
            selected_inet4[key] = obj
        elif changed(obj) > changed(selected_inet4[key]):
            previous = selected_inet4[key]
            duplicate_inet4_rows.append({
                "canonical_key": key,
                "kept_raw_key": one(obj, "inetnum"),
                "excluded_raw_key": one(previous, "inetnum"),
                "kept_changed": changed(obj),
                "excluded_changed": changed(previous),
                "reason": "同一IPv4规范范围重复，保留changed较新的源对象；落盘主键仍使用被保留对象的源值",
            })
            selected_inet4[key] = obj
        else:
            duplicate_inet4_rows.append({
                "canonical_key": key,
                "kept_raw_key": one(selected_inet4[key], "inetnum"),
                "excluded_raw_key": one(obj, "inetnum"),
                "kept_changed": changed(selected_inet4[key]),
                "excluded_changed": changed(obj),
                "reason": "同一IPv4规范范围重复，保留changed较新的源对象；落盘主键仍使用被保留对象的源值",
            })
    for obj in selected_inet4.values():
        if org_id_map.get(one(obj, "conn-i")) in deferred_org_ids:
            conversion_stats["deferred_inetnums"] += 1
            continue
        resource(obj, "inetnum", "inetnum", True)
    for obj in inet6:
        if org_id_map.get(one(obj, "conn-i")) in deferred_org_ids:
            conversion_stats["deferred_inet6nums"] += 1
            continue
        resource(obj, "inet6num", "*i6", True)
    for obj in domains:
        if domain_assignments[id(obj)] is None:
            continue
        if domain_assignments[id(obj)] in deferred_org_ids:
            conversion_stats["deferred_domains"] += 1
            continue
        resource(obj, "domain", "domain")
    for obj in hosts:
        lines = [f"nameserver: {one(obj, 'host')}"]
        add(lines, "ipnum", obj.get("ipnum", []))
        add(lines, "notify", obj.get("notify", []) or [default_notify])
        lines += [f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)

    (out / "CERNIC-import-test.db").write_text("\n".join(dump), encoding="utf-8")
    with (out / "skipped-domains.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(["reason", "domain", "netname", "orgname", "c-orgname", "province", "c-province", "city", "c-city"])
        for obj, reason in skipped_domains:
            writer.writerow([reason] + [one(obj, key) for key in ("domain", "netname", "orgname", "c-orgname", "province", "c-province", "city", "c-city")])
    with (out / "fake-organisation-domains.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow([
            "fake-organisation", "domain", "reason", "netname", "orgname", "c-orgname",
            "province", "c-province", "city", "c-city",
        ])
        for obj, reason in sorted(unresolved_domains, key=lambda item: one(item[0], "domain")):
            fake_org_id = domain_assignments[id(obj)]
            if fake_org_id is None:
                continue
            writer.writerow(
                [fake_org_id, one(obj, "domain"), reason]
                + [one(obj, key) for key in (
                    "netname", "orgname", "c-orgname", "province", "c-province", "city", "c-city")])
    with (out / "fake-organisation-cleanup.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(["delete-order", "object-type", "primary-key", "fake-organisation"])
        cleanup_order = 1
        for fake_org_id, entries in sorted(fake_organisations.items()):
            for obj, _ in sorted(entries, key=lambda item: one(item[0], "domain")):
                writer.writerow([cleanup_order, "domain", one(obj, "domain"), fake_org_id])
                cleanup_order += 1
        for fake_org_id in sorted(fake_organisations):
            writer.writerow([cleanup_order, "organisation", fake_org_id, fake_org_id])
            cleanup_order += 1
    with (out / "organisation-city-codes.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(["organisation", "c-city", "c-province", "c-cityid", "resolution"])
        writer.writerows(city_id_rows)
    with (out / "excluded-legacy-resources.tsv").open("w", encoding="utf-8", newline="") as file:
        headers = [
            "rule_id", "object_type", "primary_key", "netname", "conn_i", "descr",
            "source_ref", "action", "reason", "kept_resource",
        ]
        writer = csv.DictWriter(file, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        writer.writerows(excluded_legacy_resources)
    with (out / "deduplicated-ipv4-resources.tsv").open("w", encoding="utf-8", newline="") as file:
        headers = [
            "canonical_key", "kept_raw_key", "excluded_raw_key",
            "kept_changed", "excluded_changed", "reason",
        ]
        writer = csv.DictWriter(file, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        writer.writerows(duplicate_inet4_rows)
    if preprocessing is not None:
        with (out / "organisation-name-decisions.tsv").open("w", encoding="utf-8", newline="") as file:
            headers = [
                "legacy_orgid", "organisation", "selected_c_org_name", "selected_org_name",
                "decision_method", "candidate_count", "candidates",
            ]
            writer = csv.DictWriter(file, fieldnames=headers, delimiter="\t")
            writer.writeheader()
            writer.writerows(organisation_name_decisions)
        with (out / "ip-filing-emergency-contact-decisions.tsv").open("w", encoding="utf-8", newline="") as file:
            headers = [
                "source_row", "legal_name", "emergency_name", "phone", "email",
                "target_organisations", "result", "contact_handle", "reason",
            ]
            writer = csv.DictWriter(file, fieldnames=headers, delimiter="\t")
            writer.writeheader()
            writer.writerows(filing_contact_audit)
    if not skip_reviewed_organisations:
        with (out / "organisation-conflicts.tsv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file, delimiter="\t")
            writer.writerow([
                "legacy-orgid", "organisation", "selected-c-org-name", "selected-org-name",
                "candidate-count", "candidates", "inetnum-count", "inet6num-count", "domain-count",
            ])
            for legacy_orgid, reviewed in sorted(reviewed_organisations.items()):
                org_id = normalized_org_id_map.get(legacy_orgid.upper(), "")
                resources = org_resources_by_upper_id.get(legacy_orgid.upper(), [])
                candidates = [
                    f"{chinese_name} ({english_name})"
                    for chinese_name, english_name in reviewed["candidate-names"]
                ]
                writer.writerow([
                    legacy_orgid,
                    org_id,
                    reviewed["c-org-name"],
                    reviewed["org-name"],
                    len(candidates),
                    " | ".join(candidates),
                    sum("inetnum" in obj for obj in resources),
                    sum("*i6" in obj for obj in resources),
                    len(domains_by_org.get(org_id, [])),
                ])
    (out / "conversion-summary.md").write_text(
        "# Conversion summary\n\n"
        f"- Source is normalized to `{WHOIS_SOURCE}`.\n"
        "- All legacy contact handles and references are normalized to `-CER`.\n"
        "- Organisations merge legacy inetnum, inet6num and safely-associated domain details.\n"
        "- Legacy zero-valued contact and organisation indexes are reassigned to valid positive indexes; all generated references follow the reassignment.\n"
        "- Unresolved contact references receive placeholder role objects.\n"
        "- IPv4 CIDR and range representations are normalized before deduplication; conflicting records prefer the newest legacy changed value.\n"
        + ("- Retained IPv4, IPv6 and domain primary keys are written using their legacy source text; normalized values are comparison-only.\n"
           if preserve_source_primary_values else "")
        + f"- Explicitly excluded legacy WHOIS resources: {conversion_stats['legacy_resources_excluded_by_rule']} (see `excluded-legacy-resources.tsv`).\n"
        f"- Duplicate logical IPv4 source objects excluded: {len(duplicate_inet4_rows)} (see `deduplicated-ipv4-resources.tsv`).\n"
        f"- Reviewed organisation names applied: {conversion_stats['reviewed_organisations']}.\n"
        + (f"- Reviewed organisation conflicts exported: {len(reviewed_organisations)} (see `organisation-conflicts.tsv`).\n"
           if not skip_reviewed_organisations else "")
        + f"- Deferred duplicate organisations: {conversion_stats['deferred_organisations']}; their associated inetnum records: {conversion_stats['deferred_inetnums']}; inet6num records: {conversion_stats['deferred_inet6nums']}; domains: {conversion_stats['deferred_domains']}.\n"
        f"- Resource legacy organisation descriptions added: {conversion_stats['legacy_org_name_descriptions']}.\n"
        f"- Domain organisation matches by netname: {conversion_stats['domain_netname_matches']}.\n"
        f"- Domain organisation matches by c-orgname: {conversion_stats['domain_c_orgname_matches']}.\n"
        f"- Domain organisation matches by Xinjiang fallback: {conversion_stats['domain_xinjiang_fallback_matches']}.\n"
        f"- Domains without a safe organisation match: {conversion_stats['domain_unmatched_organisations']}.\n"
        f"- Domains with ambiguous organisation matches: {conversion_stats['domain_ambiguous_organisation_matches']}.\n"
        f"- Fake organisations generated for unresolved domains: {len(fake_organisations)}.\n"
        f"- Unresolved domains assigned to fake organisations: {sum(len(entries) for entries in fake_organisations.values())} (see `fake-organisation-domains.tsv` and `fake-organisation-cleanup.tsv`).\n"
        f"- Skipped domains: {len(skipped_domains)} (see `skipped-domains.tsv`).\n"
        f"- Organisation city IDs matched by city: {conversion_stats['city_id_city']}; by district: {conversion_stats['city_id_district']}; province fallback: {conversion_stats['city_id_province-fallback']}; unknown fallback: {conversion_stats['city_id_unknown-fallback']} (see `organisation-city-codes.tsv`).\n"
        f"- Generated organisation IDs: {len(org_id_map) + len(fake_organisations)}; exported organisations: {len(org_id_map) - conversion_stats['deferred_organisations'] + len(fake_organisations)}.\n"
        f"- Generated placeholder roles: {len(placeholders)}.\n"
        + (f"- New persons created from non-reserved IP filing emergency contacts: {len(new_filing_contacts)}; organisations receiving admin-c: {len(org_admin_contacts)} (see `ip-filing-emergency-contact-decisions.tsv`).\n"
           if preprocessing is not None else ""),
        encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE, help="Directory containing legacy CERNIC.db.*.txt files")
    parser.add_argument("--review-workbook", type=Path, default=REVIEW, help="Reviewed duplicate organisation workbook")
    parser.add_argument("--review-overrides", type=Path, help="Private CSV for selected values that cannot be parsed from the workbook")
    parser.add_argument("--output-dir", type=Path, default=OUT, help="Directory for the generated private import dump")
    parser.add_argument("--bootstrap-dump", type=Path, default=WHOIS, help="Existing dump used to obtain the import operator contact")
    parser.add_argument("--city-code-map", type=Path, required=True, help="JSON map generated from the website's constant.js")
    parser.add_argument("--skip-reviewed-organisations", action="store_true", help="Defer every organisation listed on the review sheet, without requiring selected names")
    args = parser.parse_args()
    main(args.source_dir, args.output_dir, args.bootstrap_dump, args.review_workbook, args.review_overrides, args.city_code_map, args.skip_reviewed_organisations)
