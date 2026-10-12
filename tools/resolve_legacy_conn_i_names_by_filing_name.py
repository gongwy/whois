#!/usr/bin/env python3
"""Resolve disputed legacy conn-i names by IPFP legal-name existence.

The resolver does not compare IP ranges.  It builds a legal-name index from a
previously extracted non-reserved IPFP cache, then checks which of every
disputed conn-i's legacy organisation names occur in that index.  Exactly one
matched legacy name is selected; multiple matches are recorded as conflicts;
zero matches remain unresolved.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path

from migration_special_rules import legacy_resource_exclusion_for
from prepare_joint_rsdb_stage1 import (
    first,
    legacy_names,
    normalized,
    parse_rpsl_file,
)


RESULT_FILENAME = "legacy-conn-i-name-results-by-filing-name.tsv"
EVIDENCE_FILENAME = "legacy-conn-i-name-match-evidence.tsv"
CONFLICT_FILENAME = "legacy-conn-i-name-conflicts-by-filing-name.tsv"
UNMATCHED_FILENAME = "legacy-conn-i-name-unmatched-by-filing-name.tsv"
STATUS_FILENAME = "legacy-conn-i-name-resolution-by-filing-name-status.json"
DATA_FILENAME = "legacy-conn-i-name-resolution-by-filing-name-data.json"


def split_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(" | ") if part.strip()]


def join_values(values: set[str] | list[str]) -> str:
    return "\n".join(sorted({value for value in values if value}))


def load_filing_name_index(path: Path) -> dict[str, dict[str, object]]:
    """Load only legal-name evidence and reject any reserved cache row."""
    index: dict[str, dict[str, object]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        required = {
            "start_ip", "end_ip", "legal_name", "allocation_methods",
            "source_ids", "source_rows",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("IP备案法人名称缓存字段不完整")
        if any("cert" in header.casefold() for header in reader.fieldnames):
            raise ValueError("IP备案法人名称缓存不应包含证件字段")
        for row_number, row in enumerate(reader, 2):
            methods = split_values(row["allocation_methods"])
            if any(normalized(method) == normalized("预留") for method in methods):
                raise ValueError(f"IP备案法人名称缓存第{row_number}行包含预留记录")
            legal_name = row["legal_name"].strip()
            key = normalized(legal_name)
            if not key:
                continue
            record = index.setdefault(key, {
                "display_names": set(),
                "source_ids": set(),
                "source_rows": set(),
                "ip_ranges": set(),
                "record_count": 0,
            })
            record["display_names"].add(legal_name)
            record["source_ids"].update(split_values(row["source_ids"]))
            record["source_rows"].update(split_values(row["source_rows"]))
            record["ip_ranges"].add(f"{row['start_ip']} - {row['end_ip']}")
            record["record_count"] += 1
    return index


def load_disputed_legacy_names(
    rsdb_dir: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, set[str]]], dict[str, int]]:
    """Return disputed conn-i names, per-name sources and resource counts."""
    names: dict[str, dict[str, str]] = defaultdict(dict)
    sources: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    resource_keys: dict[str, set[str]] = defaultdict(set)
    specs = (
        ("inetnum", "CERNIC.db.in.txt", "inetnum:"),
        ("inet6num", "CERNIC.db.i6.txt", "*i6:"),
    )
    for object_type, filename, marker in specs:
        path = rsdb_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"缺少旧WHOIS文件: {path}")
        for obj in parse_rpsl_file(path, marker, object_type):
            if legacy_resource_exclusion_for(object_type, obj["raw_key"], obj["fields"]):
                continue
            conn_i = first(obj["fields"], "conn-i").upper()
            if not conn_i:
                continue
            chinese_name, _ = legacy_names(obj["fields"])
            key = normalized(chinese_name)
            if not key:
                continue
            names[conn_i].setdefault(key, chinese_name)
            sources[conn_i][key].add(
                f"{obj['source_file']}:{obj['source_line']} ({obj['raw_key']})")
            resource_keys[conn_i].add(f"{object_type}:{obj['raw_key']}")

    disputed = {
        conn_i: candidates for conn_i, candidates in names.items()
        if len(candidates) > 1
    }
    disputed_sources = {conn_i: sources[conn_i] for conn_i in disputed}
    resource_counts = {conn_i: len(resource_keys[conn_i]) for conn_i in disputed}
    return disputed, disputed_sources, resource_counts


def resolve(
    disputed: dict[str, dict[str, str]],
    legacy_sources: dict[str, dict[str, set[str]]],
    resource_counts: dict[str, int],
    filing_index: dict[str, dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    evidence_rows: list[dict[str, object]] = []
    for conn_i in sorted(disputed):
        candidates = disputed[conn_i]
        matched_keys = sorted(key for key in candidates if key in filing_index)
        if len(matched_keys) == 1:
            status = "唯一命中"
            final_name = candidates[matched_keys[0]]
        elif len(matched_keys) > 1:
            status = "冲突：多个旧名称在IP备案存在"
            final_name = ""
        else:
            status = "未匹配"
            final_name = ""

        matched_old_names = [candidates[key] for key in matched_keys]
        filing_variants: set[str] = set()
        filing_record_count = 0
        for key in matched_keys:
            filing = filing_index[key]
            filing_variants.update(filing["display_names"])
            filing_record_count += int(filing["record_count"])
            evidence_rows.append({
                "conn_i": conn_i,
                "result_status": status,
                "old_name_candidate": candidates[key],
                "all_old_names": join_values(list(candidates.values())),
                "filing_name_variants": join_values(filing["display_names"]),
                "filing_record_count": filing["record_count"],
                "filing_ip_ranges": join_values(filing["ip_ranges"]),
                "filing_source_ids": join_values(filing["source_ids"]),
                "filing_source_rows": join_values(filing["source_rows"]),
                "legacy_whois_sources": join_values(legacy_sources[conn_i][key]),
            })

        results.append({
            "conn_i": conn_i,
            "result_status": status,
            "old_name_count": len(candidates),
            "old_names": join_values(list(candidates.values())),
            "matched_old_name_count": len(matched_keys),
            "matched_old_names": join_values(matched_old_names),
            "filing_name_variants": join_values(filing_variants),
            "filing_record_count": filing_record_count,
            "final_org_name": final_name,
            "legacy_resource_count": resource_counts.get(conn_i, 0),
        })
    return results, evidence_rows


def write_tsv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter="\t", fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


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

    filing_index = load_filing_name_index(args.ip_name_cache.resolve())
    disputed, legacy_sources, resource_counts = load_disputed_legacy_names(
        args.rsdb_dir.resolve())
    results, evidence = resolve(
        disputed, legacy_sources, resource_counts, filing_index)
    conflicts = [
        row for row in evidence
        if row["result_status"] == "冲突：多个旧名称在IP备案存在"
    ]
    unmatched = [row for row in results if row["result_status"] == "未匹配"]

    result_headers = [
        "conn_i", "result_status", "old_name_count", "old_names",
        "matched_old_name_count", "matched_old_names", "filing_name_variants",
        "filing_record_count", "final_org_name", "legacy_resource_count",
    ]
    evidence_headers = [
        "conn_i", "result_status", "old_name_candidate", "all_old_names",
        "filing_name_variants", "filing_record_count", "filing_ip_ranges",
        "filing_source_ids", "filing_source_rows", "legacy_whois_sources",
    ]
    write_tsv(output_dir / RESULT_FILENAME, results, result_headers)
    write_tsv(output_dir / EVIDENCE_FILENAME, evidence, evidence_headers)
    write_tsv(output_dir / CONFLICT_FILENAME, conflicts, evidence_headers)
    write_tsv(output_dir / UNMATCHED_FILENAME, unmatched, result_headers)

    summary = {
        "stage": "legacy-conn-i-name-resolution-by-filing-name",
        "status": "completed_with_recorded_conflicts" if conflicts else "passed",
        "matching_rule": "旧WHOIS候选机构名称在非预留IP备案法人名称索引中存在",
        "normalization_only_for_matching": True,
        "final_name_preserves_legacy_whois_value": True,
        "disputed_conn_i": len(results),
        "resolved_unique": sum(row["result_status"] == "唯一命中" for row in results),
        "conflicted_conn_i": len({row["conn_i"] for row in conflicts}),
        "conflict_evidence_rows": len(conflicts),
        "unmatched_conn_i": len(unmatched),
        "filing_unique_normalized_names": len(filing_index),
        "reserved_filing_rows_used": 0,
    }
    (output_dir / DATA_FILENAME).write_text(
        json.dumps({
            "summary": summary,
            "results": results,
            "evidence": evidence,
            "conflicts": conflicts,
            "unmatched": unmatched,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / STATUS_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
