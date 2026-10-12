#!/usr/bin/env python3
"""Extract a reusable IP-range -> legal-name cache from the IPFP workbook.

Only IP ranges and organisation legal names participate in this stage.  No
certificate number or contact field is read into the cache.  The stage stops
before writing a successful cache if one canonical IP range maps to multiple
different legal names.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

from openpyxl import load_workbook

from prepare_joint_rsdb_stage1 import (
    IP_ALLOCATION_SHEET,
    meaningful,
    normalized,
    parse_ip_range,
    text,
)


CACHE_VERSION = "1.0"
CACHE_FILENAME = "ip-filing-legal-names.tsv"
META_FILENAME = "ip-filing-legal-names-meta.json"
CONFLICT_FILENAME = "ip-filing-legal-name-first-conflict.tsv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_tsv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, delimiter="\t", fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def extract(workbook_path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    if IP_ALLOCATION_SHEET not in workbook.sheetnames:
        workbook.close()
        raise ValueError(f"{workbook_path.name} 缺少工作表 {IP_ALLOCATION_SHEET}")
    sheet = workbook[IP_ALLOCATION_SHEET]
    source_rows = sheet.iter_rows(values_only=True)
    try:
        headers = [text(value) for value in next(source_rows)]
    except StopIteration as error:
        workbook.close()
        raise ValueError("IP分配信息工作表为空") from error
    index = {header: position for position, header in enumerate(headers)}
    required = {"ID", "IP地址类型", "起始IP", "终止IP", "使用单位名称", "分配方式"}
    missing = sorted(required - set(index))
    if missing:
        workbook.close()
        raise ValueError("IP分配信息缺少字段: " + ", ".join(missing))

    grouped: dict[tuple[int, int, int], list[dict[str, object]]] = defaultdict(list)
    stats = {
        "source_rows": 0,
        "reserved_rows_excluded": 0,
        "named_source_rows": 0,
        "missing_legal_name_rows": 0,
        "invalid_ip_rows": 0,
        "duplicate_same_name_rows": 0,
    }
    for excel_row, values in enumerate(source_rows, 2):
        row = {
            header: text(values[position]) if position < len(values) else ""
            for header, position in index.items()
        }
        if not any(row.values()):
            continue
        stats["source_rows"] += 1
        if normalized(row["分配方式"]) == normalized("预留"):
            stats["reserved_rows_excluded"] += 1
            continue
        legal_name = meaningful(row["使用单位名称"])
        if not legal_name:
            stats["missing_legal_name_rows"] += 1
            continue
        interval = parse_ip_range(row["起始IP"], row["终止IP"])
        if interval is None:
            stats["invalid_ip_rows"] += 1
            workbook.close()
            raise ValueError(
                f"IPFP第{excel_row}行IP区间无法解析: "
                f"{row['起始IP']} - {row['终止IP']}"
            )
        stats["named_source_rows"] += 1
        grouped[(interval.version, interval.first, interval.last)].append({
            "ip_version": interval.version,
            "start_ip": interval.display().split(" - ", 1)[0],
            "end_ip": interval.display().split(" - ", 1)[1],
            "legal_name": legal_name,
            "source_id": row["ID"],
            "source_row": excel_row,
            "allocation_method": row["分配方式"],
        })
    workbook.close()

    cache_rows: list[dict[str, object]] = []
    for key in sorted(grouped):
        records = grouped[key]
        names: dict[str, str] = {}
        for record in records:
            names.setdefault(normalized(record["legal_name"]), str(record["legal_name"]))
        if len(names) > 1:
            error = RuntimeError("同一规范化IP区间对应多个法人名称")
            error.conflict_records = records  # type: ignore[attr-defined]
            raise error
        if len(records) > 1:
            stats["duplicate_same_name_rows"] += len(records) - 1
        first = records[0]
        cache_rows.append({
            "ip_version": first["ip_version"],
            "start_ip": first["start_ip"],
            "end_ip": first["end_ip"],
            "legal_name": next(iter(names.values())),
            "allocation_methods": " | ".join(sorted({
                str(record["allocation_method"]) for record in records
                if record["allocation_method"]
            })),
            "source_ids": " | ".join(sorted({
                str(record["source_id"]) for record in records if record["source_id"]
            })),
            "source_rows": " | ".join(str(record["source_row"]) for record in records),
        })
    stats["cache_rows"] = len(cache_rows)
    return cache_rows, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip-workbook", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    workbook_path = args.ip_workbook.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"ERROR: 输出目录必须为空: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        rows, stats = extract(workbook_path)
    except RuntimeError as error:
        conflict_records = getattr(error, "conflict_records", [])
        write_tsv(
            output_dir / CONFLICT_FILENAME,
            conflict_records,
            ["ip_version", "start_ip", "end_ip", "legal_name", "source_id",
             "source_row", "allocation_method"],
        )
        (output_dir / META_FILENAME).write_text(json.dumps({
            "stage": "ip-filing-legal-name-extraction",
            "status": "blocked",
            "cache_version": CACHE_VERSION,
            "source_file": str(workbook_path),
            "source_sha256": sha256(workbook_path),
            "stop_reason": str(error),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(2)

    write_tsv(
        output_dir / CACHE_FILENAME,
        rows,
        ["ip_version", "start_ip", "end_ip", "legal_name", "allocation_methods",
         "source_ids", "source_rows"],
    )
    meta = {
        "stage": "ip-filing-legal-name-extraction",
        "status": "passed",
        "cache_version": CACHE_VERSION,
        "source_file": str(workbook_path),
        "source_sheet": IP_ALLOCATION_SHEET,
        "source_sha256": sha256(workbook_path),
        "contains_certificate_number": False,
        "contains_contact_data": False,
        "stats": stats,
    }
    (output_dir / META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "passed", "cache": str(output_dir / CACHE_FILENAME), "stats": stats,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
