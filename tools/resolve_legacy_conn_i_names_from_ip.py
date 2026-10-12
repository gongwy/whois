#!/usr/bin/env python3
"""Resolve disputed legacy conn-i names from the cached IPFP legal names.

This is a strict gate.  It stops at the first ambiguous IP match or the first
conn-i that receives more than one legal name.  It never emits a partial final
mapping when blocked.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import sys

from migration_special_rules import legacy_resource_exclusion_for
from prepare_joint_rsdb_stage1 import (
    IpRange,
    first,
    legacy_names,
    most_specific_matches,
    normalized,
    parse_ip_range,
    parse_rpsl_file,
    prefer_institutional_matches,
)


FINAL_FILENAME = "legacy-conn-i-final-names.tsv"
CONFLICT_FILENAME = "legacy-conn-i-name-first-conflict.tsv"
STATUS_FILENAME = "legacy-conn-i-name-resolution-status.json"


def load_cache(path: Path) -> list[dict[str, object]]:
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {"ip_version", "start_ip", "end_ip", "legal_name", "source_ids", "source_rows"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("IP备案缓存字段不完整")
        if any("cert" in header.casefold() for header in reader.fieldnames):
            raise ValueError("IP备案缓存不应包含证件字段")
        for row in reader:
            interval = parse_ip_range(row["start_ip"], row["end_ip"])
            if interval is None:
                raise ValueError(
                    f"缓存IP区间无法解析: {row['start_ip']} - {row['end_ip']}")
            result.append({**row, "interval": interval})
    return result


def load_legacy(rsdb_dir: Path) -> tuple[
    dict[int, list[tuple[IpRange, str, dict]]],
    dict[str, list[tuple[IpRange, dict]]],
    dict[str, set[str]],
]:
    all_ranges: dict[int, list[tuple[IpRange, str, dict]]] = defaultdict(list)
    resources: dict[str, list[tuple[IpRange, dict]]] = defaultdict(list)
    names: dict[str, set[str]] = defaultdict(set)
    specs = (
        ("inetnum", "CERNIC.db.in.txt", "inetnum:", "inetnum"),
        ("inet6num", "CERNIC.db.i6.txt", "*i6:", "*i6"),
    )
    for object_type, filename, marker, primary_field in specs:
        path = rsdb_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"缺少旧WHOIS文件: {path}")
        for obj in parse_rpsl_file(path, marker, object_type):
            if legacy_resource_exclusion_for(object_type, obj["raw_key"], obj["fields"]):
                continue
            interval = parse_ip_range(first(obj["fields"], primary_field))
            orgid = first(obj["fields"], "conn-i").upper()
            if interval is None or not orgid:
                continue
            all_ranges[interval.version].append((interval, orgid, obj))
            resources[orgid].append((interval, obj))
            chinese, _ = legacy_names(obj["fields"])
            if chinese:
                names[orgid].add(chinese)
    return all_ranges, resources, names


def write_tsv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter="\t", fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def stop_with_conflict(
    output_dir: Path, conflict_type: str, object_key: str,
    evidence: list[dict[str, object]], explanation: str,
) -> None:
    headers = [
        "conflict_type", "conn_i", "old_names", "legal_name", "ip_range",
        "matched_whois_resources", "source_ids", "source_rows", "explanation",
    ]
    write_tsv(output_dir / CONFLICT_FILENAME, evidence, headers)
    status = {
        "stage": "legacy-conn-i-name-resolution-from-ip",
        "status": "blocked",
        "conflict_type": conflict_type,
        "object_key": object_key,
        "evidence_rows": len(evidence),
        "stop_reason": explanation,
        "partial_final_mapping_written": False,
    }
    (output_dir / STATUS_FILENAME).write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rsdb-dir", type=Path, required=True)
    parser.add_argument("--ip-name-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"ERROR: 输出目录必须为空: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    cache = load_cache(args.ip_name_cache.resolve())
    all_ranges, resources, old_names = load_legacy(args.rsdb_dir.resolve())
    disputed = {
        orgid for orgid, names in old_names.items()
        if len({normalized(name) for name in names if normalized(name)}) > 1
    }
    matches_by_orgid: dict[str, list[dict[str, object]]] = defaultdict(list)

    # First gate: one cached IP range must not resolve to multiple old conn-i
    # when any of those conn-i is in the disputed population.
    for record in cache:
        interval = record["interval"]
        structural = most_specific_matches(
            interval, all_ranges[interval.version], prefer_institutional=False)
        matches = prefer_institutional_matches(structural)
        orgids = sorted({orgid for _, orgid, _ in matches})
        disputed_orgids = [orgid for orgid in orgids if orgid in disputed]
        if len(orgids) > 1 and disputed_orgids:
            evidence = [{
                "conflict_type": "一个IP备案区间命中多个旧conn-i",
                "conn_i": " | ".join(orgids),
                "old_names": "",
                "legal_name": record["legal_name"],
                "ip_range": interval.display(),
                "matched_whois_resources": " | ".join(sorted({
                    f"{orgid}:{obj['raw_key']}" for _, orgid, obj in matches
                })),
                "source_ids": record["source_ids"],
                "source_rows": record["source_rows"],
                "explanation": "IP备案区间不能唯一归属旧conn-i。",
            }]
            stop_with_conflict(
                output_dir, "一个IP备案区间命中多个旧conn-i",
                interval.display(), evidence, evidence[0]["explanation"],
            )
        if len(orgids) != 1 or orgids[0] not in disputed:
            continue
        orgid = orgids[0]
        matches_by_orgid[orgid].append({
            "legal_name": record["legal_name"],
            "ip_range": interval.display(),
            "matched_whois_resources": " | ".join(sorted({
                obj["raw_key"] for _, _, obj in matches
            })),
            "source_ids": record["source_ids"],
            "source_rows": record["source_rows"],
        })

    final_rows = []
    # Second gate: strict deterministic order and stop at the first conn-i
    # with multiple distinct legal names.
    for orgid in sorted(disputed):
        records = matches_by_orgid.get(orgid, [])
        legal_names: dict[str, str] = {}
        for record in records:
            legal_names.setdefault(normalized(record["legal_name"]), str(record["legal_name"]))
        if len(legal_names) > 1:
            evidence = [{
                "conflict_type": "同一旧conn-i的IP匹配到多个法人名称",
                "conn_i": orgid,
                "old_names": " | ".join(sorted(old_names[orgid])),
                "legal_name": record["legal_name"],
                "ip_range": record["ip_range"],
                "matched_whois_resources": record["matched_whois_resources"],
                "source_ids": record["source_ids"],
                "source_rows": record["source_rows"],
                "explanation": "同一旧conn-i收到多个不同法人名称，不能自动选择最终机构名称。",
            } for record in records]
            stop_with_conflict(
                output_dir, "同一旧conn-i的IP匹配到多个法人名称", orgid,
                evidence, "同一旧conn-i收到多个不同法人名称，不能自动选择最终机构名称。",
            )
        final_rows.append({
            "conn_i": orgid,
            "old_names": " | ".join(sorted(old_names[orgid])),
            "final_legal_name": next(iter(legal_names.values())) if legal_names else "",
            "status": "唯一匹配" if legal_names else "IP备案无匹配",
            "matched_ip_count": len(records),
            "source_rows": " | ".join(str(record["source_rows"]) for record in records),
        })

    write_tsv(
        output_dir / FINAL_FILENAME, final_rows,
        ["conn_i", "old_names", "final_legal_name", "status", "matched_ip_count", "source_rows"],
    )
    status = {
        "stage": "legacy-conn-i-name-resolution-from-ip",
        "status": "passed",
        "disputed_conn_i": len(disputed),
        "resolved": sum(row["status"] == "唯一匹配" for row in final_rows),
        "unmatched": sum(row["status"] == "IP备案无匹配" for row in final_rows),
        "final_mapping": str(output_dir / FINAL_FILENAME),
    }
    (output_dir / STATUS_FILENAME).write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
