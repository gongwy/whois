#!/usr/bin/env python3
"""One-command preparation and validation of a legacy CERNIC rsdb export."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import convert_rsdb_cernic as converter


REQUIRED_SOURCE_FILES = {
    "person": "CERNIC.db.pn.txt",
    "role": "CERNIC.db.ro.txt",
    "inetnum": "CERNIC.db.in.txt",
    "inet6num": "CERNIC.db.i6.txt",
    "domain": "CERNIC.db.dn.txt",
    "nameserver": "CERNIC.db.ht.txt",
}
REQUIRED_NONEMPTY_SOURCE_TYPES = {
    "person", "inetnum", "inet6num", "domain", "nameserver",
}
PRIMARY_KEYS = {
    "person": "nic-hdl",
    "role": "nic-hdl",
    "mntner": "mntner",
    "organisation": "organisation",
    "inetnum": "inetnum",
    "inet6num": "inet6num",
    "domain": "domain",
    "nameserver": "nameserver",
}


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def validate_inputs(source: Path, review: Path, bootstrap: Path, constant_js: Path) -> None:
    if not source.is_dir():
        fail(f"rsdb directory does not exist: {source}")
    problems = []
    for object_type, filename in REQUIRED_SOURCE_FILES.items():
        path = source / filename
        if not path.is_file():
            problems.append(f"missing {filename}")
        elif object_type in REQUIRED_NONEMPTY_SOURCE_TYPES and path.stat().st_size == 0:
            problems.append(f"empty {filename}")
    if problems:
        fail("invalid rsdb directory: " + "; ".join(problems))
    if not review.is_file():
        fail(f"organisation review workbook does not exist: {review}")
    if not bootstrap.is_file():
        fail(f"bootstrap dump does not exist: {bootstrap}")
    if not constant_js.is_file():
        fail(f"website city data does not exist: {constant_js}")


def parse_dump(path: Path):
    current = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            if current:
                yield converter.parse(current)
                current = []
        else:
            current.append(raw)
    if current:
        yield converter.parse(current)


def validate_dump(path: Path) -> dict:
    counts = Counter()
    keys = set()
    duplicate_keys = []
    invalid_sources = []
    contact_refs = set()
    organisation_refs = set()
    contacts = set()
    organisations = set()
    unknown_city_ids = 0

    for index, obj in enumerate(parse_dump(path), 1):
        object_type = next((name for name in PRIMARY_KEYS if name in obj), None)
        if object_type is None:
            fail(f"generated dump object #{index} has no supported primary key")
        counts[object_type] += 1
        primary_name = PRIMARY_KEYS[object_type]
        primary_value = converter.one(obj, primary_name).upper()
        key = (object_type, primary_value)
        if key in keys:
            duplicate_keys.append(f"{object_type}:{primary_value}")
        keys.add(key)
        if set(obj.get("source", [])) != {converter.WHOIS_SOURCE}:
            invalid_sources.append(f"{object_type}:{primary_value}")
        if object_type in {"person", "role"}:
            contacts.add(primary_value)
        if object_type == "organisation":
            organisations.add(primary_value)
            unknown_city_ids += sum(value == "000000" for value in obj.get("c-cityid", []))
        for name in ("admin-c", "tech-c", "zone-c"):
            contact_refs.update(value.upper() for value in obj.get(name, []))
        organisation_refs.update(value.upper() for value in obj.get("org", []))

    missing_types = sorted(name for name in REQUIRED_NONEMPTY_SOURCE_TYPES if counts[name] == 0)
    missing_contacts = sorted(contact_refs - contacts)
    missing_organisations = sorted(organisation_refs - organisations)
    errors = []
    if missing_types:
        errors.append("zero generated objects for: " + ", ".join(missing_types))
    if duplicate_keys:
        errors.append(f"duplicate primary keys: {len(duplicate_keys)}")
    if invalid_sources:
        errors.append(f"objects with a non-CERNIC source: {len(invalid_sources)}")
    if missing_contacts:
        errors.append(f"dangling contact references: {len(missing_contacts)}")
    if missing_organisations:
        errors.append(f"dangling organisation references: {len(missing_organisations)}")

    result = {
        "status": "failed" if errors else "passed",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "object_counts": dict(sorted(counts.items())),
        "total_objects": sum(counts.values()),
        "unknown_city_ids": unknown_city_ids,
        "duplicate_primary_keys": duplicate_keys[:100],
        "missing_contact_references": missing_contacts[:100],
        "missing_organisation_references": missing_organisations[:100],
        "errors": errors,
    }
    return result


def write_validation_report(output: Path, result: dict, mode: str, source: Path) -> None:
    (output / "validation-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    count_lines = "\n".join(
        f"- {name}: {count}" for name, count in result["object_counts"].items())
    (output / "validation-report.md").write_text(
        "# RSDB conversion validation\n\n"
        f"- Status: **{result['status']}**\n"
        f"- Mode: `{mode}`\n"
        f"- Source directory: `{source.resolve()}`\n"
        f"- Total objects: {result['total_objects']}\n"
        f"- Dump bytes: {result['bytes']}\n"
        f"- SHA-256: `{result['sha256']}`\n"
        f"- Organisations using fallback city ID `000000`: {result['unknown_city_ids']}\n\n"
        "## Object counts\n\n" + count_lines + "\n",
        encoding="utf-8")


def resolve_review(source: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    candidates = [source / "org_duplicates.xlsx", source.parent / "org_duplicates.xlsx"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    fail("organisation review workbook was not found; pass --review-workbook")


def main() -> None:
    tools_dir = Path(__file__).resolve().parent
    repository = tools_dir.parent
    workspace = repository.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rsdb_dir", type=Path, help="Directory containing CERNIC.db.*.txt")
    parser.add_argument("--output-dir", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--mode", choices=("safe", "complete"), default="complete")
    parser.add_argument("--review-workbook", type=Path)
    parser.add_argument("--review-overrides", type=Path)
    parser.add_argument("--bootstrap-dump", type=Path, default=repository / "myTEST.db")
    parser.add_argument(
        "--constant-js", type=Path,
        default=workspace / "whois_website/src/main/resources/static/constant.js")
    args = parser.parse_args()

    source = args.rsdb_dir.resolve()
    output = args.output_dir.resolve()
    review = resolve_review(source, args.review_workbook)
    validate_inputs(source, review, args.bootstrap_dump, args.constant_js)
    if args.mode == "complete" and args.review_overrides and not args.review_overrides.is_file():
        fail(f"review overrides do not exist: {args.review_overrides}")
    if output.exists() and not output.is_dir():
        fail(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        fail(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cernic-city-codes-") as temporary:
        city_codes = Path(temporary) / "city-codes.json"
        try:
            subprocess.run(
                ["node", str(tools_dir / "extract_city_code_map.mjs"),
                 str(args.constant_js.resolve()), str(city_codes)], check=True)
        except FileNotFoundError:
            fail("Node.js is required to generate the city-code map")
        except subprocess.CalledProcessError as error:
            fail(f"city-code generation failed with exit code {error.returncode}")
        converter.main(
            source, output, args.bootstrap_dump.resolve(), review.resolve(),
            args.review_overrides.resolve() if args.review_overrides else None,
            city_codes, args.mode == "safe")

    dump = output / "CERNIC-import-test.db"
    result = validate_dump(dump)
    write_validation_report(output, result, args.mode, source)
    if result["errors"]:
        fail(
            "generated dump validation failed; see validation-report.md: "
            + "; ".join(result["errors"]))
    print(f"PASS: generated {result['total_objects']} objects")
    print(f"Dump: {dump}")
    print(f"Report: {output / 'validation-report.md'}")


if __name__ == "__main__":
    main()
