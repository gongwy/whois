#!/usr/bin/env python3
"""Convert legacy CERNIC RPSL files into an importable CERNIC test dump."""

from collections import Counter, defaultdict
import argparse
import csv
import ipaddress
import json
from pathlib import Path
import re
from xml.etree import ElementTree
from zipfile import ZipFile

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
        for column, value in values.items():
            if column < 4 or not value:
                continue
            candidate = split_reviewed_name(value)
            if candidate is None:
                errors.append(f"- {orgid}: candidate value is invalid: {value}")
                continue
            old_names.add(candidate[1])
        old_names.add(chosen_org_name)
        reviewed[orgid] = {
            "c-org-name": chosen_chinese_name,
            "org-name": chosen_org_name,
            "old-org-names": old_names,
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


def main(source, out, whois, review, overrides, city_codes_path):
    out.mkdir(parents=True, exist_ok=True)
    city_codes = read_city_codes(city_codes_path)
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
    inet4 = list(objects(source / "CERNIC.db.in.txt", "inetnum:"))
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
    for obj in inet4 + inet6:
        if one(obj, "conn-i"):
            org_resources[one(obj, "conn-i")].append(obj)
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
    reviewed_by_generated_orgid = {
        normalized_org_id_map[old.upper()]: reviewed
        for old, reviewed in reviewed_organisations.items()
        if old.upper() in normalized_org_id_map
    }

    orgids_by_netname = defaultdict(set)
    orgids_by_english_name = defaultdict(set)
    for obj in inet4 + inet6:
        orgid = normalized_org_id_map[one(obj, "conn-i").upper()]
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
    skipped_domains = []
    for obj in domains:
        org_id, reason = domain_organisation(obj)
        domain_assignments[id(obj)] = org_id
        if org_id:
            domains_by_org[org_id].append(obj)
        else:
            skipped_domains.append((obj, reason))

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

    def most_common(values):
        values = [value for value in values if value]
        return Counter(values).most_common(1)[0][0] if values else ""

    for old, org_id in sorted(org_id_map.items()):
        related = org_resources.get(old, [])
        related_domains = domains_by_org.get(org_id, [])
        reviewed = reviewed_organisations.get(old.upper())
        english_names = [value for obj in related for value in obj.get("descr", [])]
        english_names.extend(one(obj, "orgname") for obj in related_domains)
        chinese_names = [one(obj, "c-orgname") for obj in related_domains]
        org_name = reviewed["org-name"] if reviewed else most_common(english_names) or f"Legacy organisation {org_id}"
        c_org_name = reviewed["c-org-name"] if reviewed else most_common(chinese_names)
        lines = [f"organisation: {org_id}", f"org-name: {org_name}"]
        if c_org_name:
            lines.append(f"c-org-name: {c_org_name}")
        legacy_names = sorted(set(english_names) - {org_name})
        add(lines, "descr", [f"legacy-org-name: {name}" for name in legacy_names])
        legacy_chinese_names = sorted(set(chinese_names) - {c_org_name})
        add(lines, "remarks", [f"legacy-c-org-name: {name}" for name in legacy_chinese_names])
        location_values = {}
        for key in ("street", "c-street", "city", "c-city", "province", "c-province", "country"):
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
        add(lines, "notify", [value for obj in related + related_domains for value in obj.get("notify", [])])
        lines += [f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)
        if reviewed:
            conversion_stats["reviewed_organisations"] += 1

    def resource(obj, typ, primary_key, netname_required=False):
        primary_value = one(obj, primary_key)
        if typ == "inetnum":
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
    for obj in inet4:
        key = canonical_inetnum(one(obj, "inetnum"))
        if key not in selected_inet4 or changed(obj) > changed(selected_inet4[key]):
            selected_inet4[key] = obj
    for obj in selected_inet4.values():
        resource(obj, "inetnum", "inetnum", True)
    for obj in inet6:
        resource(obj, "inet6num", "*i6", True)
    for obj in domains:
        if domain_assignments[id(obj)] is None:
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
    with (out / "organisation-city-codes.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(["organisation", "c-city", "c-province", "c-cityid", "resolution"])
        writer.writerows(city_id_rows)
    (out / "conversion-summary.md").write_text(
        "# Conversion summary\n\n"
        f"- Source is normalized to `{WHOIS_SOURCE}`.\n"
        "- All legacy contact handles and references are normalized to `-CER`.\n"
        "- Organisations merge legacy inetnum, inet6num and safely-associated domain details.\n"
        "- Legacy zero-valued contact and organisation indexes are reassigned to valid positive indexes; all generated references follow the reassignment.\n"
        "- Unresolved contact references receive placeholder role objects.\n"
        "- IPv4 CIDR and range representations are normalized before deduplication; conflicting records prefer the newest legacy changed value.\n"
        f"- Reviewed organisation names applied: {conversion_stats['reviewed_organisations']}.\n"
        f"- Resource legacy organisation descriptions added: {conversion_stats['legacy_org_name_descriptions']}.\n"
        f"- Domain organisation matches by netname: {conversion_stats['domain_netname_matches']}.\n"
        f"- Domain organisation matches by c-orgname: {conversion_stats['domain_c_orgname_matches']}.\n"
        f"- Domain organisation matches by Xinjiang fallback: {conversion_stats['domain_xinjiang_fallback_matches']}.\n"
        f"- Domains without a safe organisation match: {conversion_stats['domain_unmatched_organisations']}.\n"
        f"- Domains with ambiguous organisation matches: {conversion_stats['domain_ambiguous_organisation_matches']}.\n"
        f"- Skipped domains: {len(skipped_domains)} (see `skipped-domains.tsv`).\n"
        f"- Organisation city IDs matched by city: {conversion_stats['city_id_city']}; by district: {conversion_stats['city_id_district']}; province fallback: {conversion_stats['city_id_province-fallback']}; unknown fallback: {conversion_stats['city_id_unknown-fallback']} (see `organisation-city-codes.tsv`).\n"
        f"- Generated organisations: {len(org_id_map)}.\n"
        f"- Generated placeholder roles: {len(placeholders)}.\n",
        encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE, help="Directory containing legacy CERNIC.db.*.txt files")
    parser.add_argument("--review-workbook", type=Path, default=REVIEW, help="Reviewed duplicate organisation workbook")
    parser.add_argument("--review-overrides", type=Path, help="Private CSV for selected values that cannot be parsed from the workbook")
    parser.add_argument("--output-dir", type=Path, default=OUT, help="Directory for the generated private import dump")
    parser.add_argument("--bootstrap-dump", type=Path, default=WHOIS, help="Existing dump used to obtain the import operator contact")
    parser.add_argument("--city-code-map", type=Path, required=True, help="JSON map generated from the website's constant.js")
    args = parser.parse_args()
    main(args.source_dir, args.output_dir, args.bootstrap_dump, args.review_workbook, args.review_overrides, args.city_code_map)
