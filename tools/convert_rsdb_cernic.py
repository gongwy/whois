#!/usr/bin/env python3
"""Convert legacy CERNIC RPSL files into an importable CERNIC test dump."""

from collections import Counter, defaultdict
import ipaddress
from pathlib import Path
import re

ROOT = Path(__file__).parent
SOURCE = ROOT / "rsdb"
OUT = ROOT / "rsdb-cernic-import-test"
WHOIS = ROOT.parent / "myTEST.db"
WHOIS_SOURCE = "CERNIC"
MNTNER = "CERNIC-DBM-MNT"


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


def main():
    OUT.mkdir(exist_ok=True)
    people = list(objects(SOURCE / "CERNIC.db.pn.txt", "person:"))
    roles = list(objects(SOURCE / "CERNIC.db.ro.txt", "role:"))
    inet4 = list(objects(SOURCE / "CERNIC.db.in.txt", "inetnum:"))
    inet6 = list(objects(SOURCE / "CERNIC.db.i6.txt", "*i6:"))
    domains = list(objects(SOURCE / "CERNIC.db.dn.txt", "domain:"))
    hosts = list(objects(SOURCE / "CERNIC.db.ht.txt", "host:"))

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
    for obj in objects(WHOIS, "person:"):
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
    for old in sorted(org_resources):
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
        lines.append(f"nic-hdl: {handle_map[old]}")
        add(lines, "remarks", [f"legacy-changed: {value}" for value in obj.get("changed", [])])
        add(lines, "notify", obj.get("notify", []))
        lines += [f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)

    for old, org_id in sorted(org_id_map.items()):
        related = org_resources[old]
        names = [value for obj in related for value in obj.get("descr", [])]
        admins = [ref(value) for obj in related for value in obj.get("admin-c", []) if ref(value) in primary or ref(value) in placeholders]
        admin = Counter(admins).most_common(1)[0][0] if admins else fallback_handle
        org_name = Counter(names).most_common(1)[0][0] if names else f"Legacy organisation {org_id}"
        dump += [f"organisation: {org_id}", f"org-name: {org_name}", "c-cityid: 110000", f"admin-c: {admin}", f"mnt-by: {MNTNER}", f"source: {WHOIS_SOURCE}", ""]

    def resource(obj, typ, primary_key, netname_required=False):
        primary_value = one(obj, primary_key)
        if typ == "inetnum":
            primary_value = canonical_inetnum(primary_value)
        lines = [f"{typ}: {primary_value}"]
        if netname_required:
            lines.append(f"netname: {one(obj, 'netname')}")
        if one(obj, "conn-i") in org_id_map:
            lines.append(f"org: {org_id_map[one(obj, 'conn-i')]}")
        add(lines, "conn-p", obj.get("conn-p", []))
        add(lines, "descr", obj.get("descr", []))
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
        resource(obj, "domain", "domain")
    for obj in hosts:
        lines = [f"nameserver: {one(obj, 'host')}"]
        add(lines, "ipnum", obj.get("ipnum", []))
        add(lines, "notify", obj.get("notify", []) or [default_notify])
        lines += [f"source: {WHOIS_SOURCE}", ""]
        dump.extend(lines)

    (OUT / "CERNIC-import-test.db").write_text("\n".join(dump), encoding="utf-8")
    (OUT / "conversion-summary.md").write_text(
        "# Conversion summary\n\n"
        f"- Source is normalized to `{WHOIS_SOURCE}`.\n"
        "- All legacy contact handles and references are normalized to `-CER`.\n"
        "- Reused `conn-i` values produce one organisation; its fields are selected from the most complete associated records.\n"
        "- Legacy zero-valued contact and organisation indexes are reassigned to valid positive indexes; all generated references follow the reassignment.\n"
        "- Unresolved contact references receive placeholder role objects.\n"
        "- IPv4 CIDR and range representations are normalized before deduplication; conflicting records prefer the newest legacy changed value.\n"
        f"- Generated organisations: {len(org_id_map)}.\n"
        f"- Generated placeholder roles: {len(placeholders)}.\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
