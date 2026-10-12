#!/usr/bin/env python3
"""Check unresolved legacy conn-i organisation names against ICP legal names.

This is a name-existence check, not a domain-to-WHOIS or IP-range join. It
continues only the IPFP multi-hit and zero-hit groups from the previous stage.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from compare_filing_legal_entities import (
    ICP_SHEET,
    make_icp_records,
    read_sheet,
    require_headers,
)
from prepare_joint_rsdb_stage1 import normalized


def lines(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").splitlines() if part.strip()]


def joined(values: list[str]) -> str:
    return "\n".join(dict.fromkeys(value for value in values if value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_icp(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = normalized(record["name"])
        if key:
            index[key].append(record)
    return index


def build_report(
    prior_results: list[dict[str, Any]], icp_records: list[dict[str, Any]]
) -> dict[str, Any]:
    icp_index = index_icp(icp_records)
    result_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    selected = [
        row for row in prior_results
        if str(row["result_status"]).startswith("冲突")
        or row["result_status"] == "未匹配"
    ]
    if len({row["conn_i"] for row in selected}) != len(selected):
        raise ValueError("前一步结果中存在重复 conn-i")

    for row in selected:
        conn_i = row["conn_i"]
        prior_status = row["result_status"]
        old_names = lines(row["old_names"])
        ip_names = lines(row["matched_old_names"])
        ip_keys = {normalized(name) for name in ip_names}
        if len({normalized(name) for name in old_names}) != len(old_names):
            raise ValueError(f"{conn_i} 的旧名称在规范化后重复")
        if not ip_keys.issubset({normalized(name) for name in old_names}):
            raise ValueError(f"{conn_i} 的 IP 备案命中名称不在旧名称候选中")
        if prior_status.startswith("冲突") and len(ip_keys) < 2:
            raise ValueError(f"{conn_i} 标记为 IP 备案冲突但命中不足两个名称")
        if prior_status == "未匹配" and ip_keys:
            raise ValueError(f"{conn_i} 标记为 IP 备案未匹配但存在命中名称")

        icp_names: list[str] = []
        both_names: list[str] = []
        for name in old_names:
            key = normalized(name)
            matches = icp_index.get(key, [])
            ip_hit = key in ip_keys
            icp_hit = bool(matches)
            if icp_hit:
                icp_names.append(name)
                if ip_hit:
                    both_names.append(name)
            candidate_rows.append({
                "conn_i": conn_i,
                "previous_result": prior_status,
                "old_name": name,
                "ip_filing_hit": "是" if ip_hit else "否",
                "icp_hit": "是" if icp_hit else "否",
                "icp_record_count": len(matches),
                "icp_legal_names": joined([record["name"] for record in matches]),
                "icp_domains": joined([record["domain_raw"] for record in matches]),
                "icp_source_rows": joined([str(record["source_row"]) for record in matches]),
            })
            for match in matches:
                evidence_rows.append({
                    "conn_i": conn_i,
                    "previous_result": prior_status,
                    "old_name": name,
                    "ip_filing_hit": "是" if ip_hit else "否",
                    "icp_legal_name": match["name"],
                    "icp_domain": match["domain_raw"],
                    "icp_cert_type": match["cert_type"],
                    "icp_cert_no": match["cert_no"],
                    "icp_source_row": match["source_row"],
                })

        if not icp_names:
            outcome = "ICP未命中"
        elif len(icp_names) > 1:
            outcome = "ICP多个旧名称命中"
        elif prior_status.startswith("冲突") and not both_names:
            outcome = "ICP唯一命中但不在IP候选中"
        elif prior_status.startswith("冲突"):
            outcome = "ICP唯一命中且与IP候选一致"
        else:
            outcome = "ICP唯一命中"

        result_rows.append({
            "conn_i": conn_i,
            "previous_result": prior_status,
            "icp_result": outcome,
            "old_name_count": len(old_names),
            "old_names": joined(old_names),
            "ip_hit_name_count": len(ip_names),
            "ip_hit_names": joined(ip_names),
            "icp_hit_name_count": len(icp_names),
            "icp_hit_names": joined(icp_names),
            "both_hit_name_count": len(both_names),
            "both_hit_names": joined(both_names),
            "unique_icp_candidate": icp_names[0] if len(icp_names) == 1 else "",
            "icp_evidence_rows": sum(
                len(icp_index[normalized(name)]) for name in icp_names
            ),
            "legacy_resource_count": row["legacy_resource_count"],
        })

    result_rows.sort(key=lambda item: item["conn_i"])
    candidate_rows.sort(key=lambda item: (item["conn_i"], item["old_name"]))
    evidence_rows.sort(key=lambda item: (
        item["conn_i"], item["old_name"], item["icp_source_row"]
    ))

    by_previous = {
        "IP多个命中": [
            row for row in result_rows if row["previous_result"].startswith("冲突")
        ],
        "IP未命中": [
            row for row in result_rows if row["previous_result"] == "未匹配"
        ],
    }
    summary = {
        "scope_conn_i": len(result_rows),
        "ip_conflict_conn_i": len(by_previous["IP多个命中"]),
        "ip_conflict_icp_unique": sum(
            row["icp_hit_name_count"] == 1 for row in by_previous["IP多个命中"]
        ),
        "ip_conflict_icp_multiple": sum(
            row["icp_hit_name_count"] > 1 for row in by_previous["IP多个命中"]
        ),
        "ip_conflict_icp_none": sum(
            row["icp_hit_name_count"] == 0 for row in by_previous["IP多个命中"]
        ),
        "ip_unmatched_conn_i": len(by_previous["IP未命中"]),
        "ip_unmatched_icp_unique": sum(
            row["icp_hit_name_count"] == 1 for row in by_previous["IP未命中"]
        ),
        "ip_unmatched_icp_multiple": sum(
            row["icp_hit_name_count"] > 1 for row in by_previous["IP未命中"]
        ),
        "ip_unmatched_icp_none": sum(
            row["icp_hit_name_count"] == 0 for row in by_previous["IP未命中"]
        ),
        "candidate_name_rows": len(candidate_rows),
        "icp_hit_candidate_names": sum(
            row["icp_hit"] == "是" for row in candidate_rows
        ),
        "icp_evidence_rows": len(evidence_rows),
        "icp_source_records": len(icp_records),
        "icp_unique_legal_names": len(icp_index),
        "outcome_counts": dict(sorted(Counter(
            row["icp_result"] for row in result_rows
        ).items())),
        "matching_rule": "旧WHOIS机构候选名称在ICP备案主体单位名称中存在",
        "name_normalization_only_for_matching": True,
        "icp_ip_used": False,
        "icp_domain_join_to_whois_used": False,
    }
    return {
        "summary": summary,
        "results": result_rows,
        "candidate_names": candidate_rows,
        "icp_evidence": evidence_rows,
    }


def save_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-json", type=Path, required=True)
    parser.add_argument("--icp-workbook", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    previous = json.loads(args.previous_json.read_text(encoding="utf-8"))
    if "results" not in previous:
        raise ValueError("前一步 JSON 缺少 results")
    source_rows = read_sheet(args.icp_workbook, ICP_SHEET)
    require_headers(
        source_rows,
        {"主体单位名称", "主办单位有效证件类型", "主办单位有效证件号码", "域名"},
        ICP_SHEET,
    )
    report = build_report(previous["results"], make_icp_records(source_rows))
    report["sources"] = {
        "previous_json": args.previous_json.name,
        "previous_json_sha256": sha256(args.previous_json),
        "icp_workbook": args.icp_workbook.name,
        "icp_workbook_sha256": sha256(args.icp_workbook),
        "icp_sheet": ICP_SHEET,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "conn-i-icp-name-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_tsv(args.output_dir / "conn-i-icp-results.tsv", report["results"])
    save_tsv(args.output_dir / "conn-i-icp-candidates.tsv", report["candidate_names"])
    save_tsv(args.output_dir / "conn-i-icp-evidence.tsv", report["icp_evidence"])
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
