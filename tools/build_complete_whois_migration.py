#!/usr/bin/env python3
"""Build review reports and a complete importable CERNIC WHOIS dump in one run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import convert_rsdb_cernic as converter
from prepare_rsdb_import import validate_dump, write_validation_report


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
PREPROCESSOR = SCRIPT_DIR / "preprocess_legacy_whois_orgs.py"
CITY_BUILDER = SCRIPT_DIR / "extract_city_code_map.mjs"
JAVA_VALIDATOR = SCRIPT_DIR / "ValidateRpslDump.java"
REQUIRED_RSDB = [
    "CERNIC.db.pn.txt", "CERNIC.db.ro.txt", "CERNIC.db.in.txt",
    "CERNIC.db.i6.txt", "CERNIC.db.dn.txt", "CERNIC.db.ht.txt",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_inputs(args: argparse.Namespace) -> None:
    if not args.rsdb_dir.is_dir():
        raise FileNotFoundError(args.rsdb_dir)
    for filename in REQUIRED_RSDB:
        path = args.rsdb_dir / filename
        if not path.is_file() or path.stat().st_size == 0 and filename != "CERNIC.db.ro.txt":
            raise FileNotFoundError(f"Missing or empty legacy WHOIS input: {path}")
    for path in (
        args.icp_workbook, args.domain_workbook, args.ip_workbook,
        args.filing_exclusions, args.bootstrap_dump, args.constant_js,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"Output directory must be new or empty: {args.output_dir}")


def run(command: list[str], *, cwd: Path = REPO_DIR) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def run_tests() -> None:
    run([sys.executable, "-m", "unittest", "discover", "-s", "tools", "-p", "test_*.py"])


def count_tsv(path: Path, predicate=None) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        return sum(1 for row in rows if predicate is None or predicate(row))


def parse_dump(path: Path) -> list[dict[str, list[str]]]:
    return list(converter.objects(path, "__never_starts_a_new_object__"))


def additional_invariants(
    rsdb_dir: Path, preprocess_report: dict, import_dir: Path, dump: Path,
) -> dict[str, object]:
    generated = parse_dump(dump)
    domains = [converter.one(obj, "domain") for obj in generated if "domain" in obj]
    inet4 = [converter.one(obj, "inetnum") for obj in generated if "inetnum" in obj]
    inet6 = [converter.one(obj, "inet6num") for obj in generated if "inet6num" in obj]
    organisations = [obj for obj in generated if "organisation" in obj]
    real_organisations = [
        obj for obj in organisations
        if not converter.one(obj, "organisation").startswith("FK")
    ]

    old_domains = [
        converter.one(obj, "domain")
        for obj in converter.objects(rsdb_dir / "CERNIC.db.dn.txt", "domain:")
    ]
    old_inet4 = {
        converter.one(obj, "inetnum")
        for obj in converter.objects(rsdb_dir / "CERNIC.db.in.txt", "inetnum:")
    }
    old_inet6 = [
        converter.one(obj, "*i6")
        for obj in converter.objects(rsdb_dir / "CERNIC.db.i6.txt", "*i6:")
    ]

    checks = {
        "all_legacy_domains_preserved_exactly": domains == old_domains,
        "all_legacy_ipv6_primary_values_preserved_exactly": inet6 == old_inet6,
        "all_emitted_ipv4_primary_values_are_legacy_source_values": set(inet4).issubset(old_inet4),
        "no_organisation_has_tech_c": not any(obj.get("tech-c") for obj in organisations),
        "no_real_organisation_contains_legacy_name_evidence": not any(
            any(value.startswith("legacy-org-name:") for value in obj.get("descr", []))
            or any(value.startswith("legacy-c-org-name:") for value in obj.get("remarks", []))
            for obj in real_organisations
        ),
        "organisation_notify_values_are_unique": all(
            len(obj.get("notify", [])) == len(set(obj.get("notify", [])))
            for obj in organisations
        ),
        "all_50_unresolved_conn_i_use_reported_fallback": count_tsv(
            import_dir / "organisation-name-decisions.tsv",
            lambda row: row["decision_method"].startswith("未解决50条"),
        ) == preprocess_report["summary"]["remaining_multi_name_conn_i"],
        "all_domains_are_materialized": len(domains) == preprocess_report["summary"]["domain_total"],
        "preprocessing_counts_match_approved_baseline": (
            preprocess_report["summary"]["multi_name_conn_i"] == 455
            and preprocess_report["summary"]["domain_multiple"] == 52
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "status": "failed" if failed else "passed",
        "checks": checks,
        "failed_checks": failed,
        "raw_primary_counts": {
            "domain": len(domains), "inetnum": len(inet4), "inet6num": len(inet6),
        },
    }


def write_complete_report(
    output_dir: Path, dump: Path, structural: dict, invariants: dict,
    preprocessing: dict,
) -> None:
    fallback_count = preprocessing["summary"]["remaining_multi_name_conn_i"]
    report = {
        "status": "passed" if structural["status"] == "passed" and invariants["status"] == "passed" else "failed",
        "dump": str(dump),
        "sha256": sha256(dump),
        "bytes": dump.stat().st_size,
        "object_counts": structural["object_counts"],
        "preprocessing_summary": preprocessing["summary"],
        "invariants": invariants,
        "unresolved_name_fallback_count": fallback_count,
        "unresolved_domain_policy": "域名零匹配或多候选时不强绑真实机构，创建带migration-cleanup标记的FK机构",
    }
    (output_dir / "complete-migration-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = "\n".join(f"- {key}: {value}" for key, value in structural["object_counts"].items())
    checks = "\n".join(
        f"- {'PASS' if passed else 'FAIL'}: {name}"
        for name, passed in invariants["checks"].items())
    (output_dir / "complete-migration-report.md").write_text(
        "# Complete WHOIS migration report\n\n"
        f"- Status: **{report['status']}**\n"
        f"- Import dump: `{dump}`\n"
        f"- SHA-256: `{report['sha256']}`\n"
        f"- Unresolved multi-name conn-i using deterministic fallback: {fallback_count}\n"
        "- The 50 fallback choices are retained in organisation-name-decisions.tsv; no resource is skipped.\n"
        "- Unresolved domains use cleanup-friendly FK organisations rather than an unsafe real-organisation association.\n\n"
        "## Object counts\n\n" + counts + "\n\n"
        "## Invariants\n\n" + checks + "\n",
        encoding="utf-8",
    )
    if report["status"] != "passed":
        raise RuntimeError("Complete migration validation failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rsdb-dir", type=Path, required=True)
    parser.add_argument("--icp-workbook", type=Path, required=True)
    parser.add_argument("--domain-workbook", type=Path, required=True)
    parser.add_argument("--ip-workbook", type=Path, required=True)
    parser.add_argument("--filing-exclusions", type=Path, required=True)
    parser.add_argument("--bootstrap-dump", type=Path, default=REPO_DIR / "myTEST.db")
    parser.add_argument(
        "--constant-js", type=Path,
        default=REPO_DIR.parent / "whois_website/src/main/resources/static/constant.js")
    parser.add_argument("--whois-jar", type=Path, default=REPO_DIR / "whois-db/target/whois-db-1.0-SNAPSHOT.jar")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-java-validation", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.rsdb_dir = args.rsdb_dir.resolve()
    args.icp_workbook = args.icp_workbook.resolve()
    args.domain_workbook = args.domain_workbook.resolve()
    args.ip_workbook = args.ip_workbook.resolve()
    args.filing_exclusions = args.filing_exclusions.resolve()
    args.bootstrap_dump = args.bootstrap_dump.resolve()
    args.constant_js = args.constant_js.resolve()
    args.whois_jar = args.whois_jar.resolve()
    args.output_dir = args.output_dir.resolve()
    require_inputs(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    preprocess_dir = args.output_dir / "preprocess"
    import_dir = args.output_dir / "import"

    try:
        if not args.skip_tests:
            run_tests()
        run([
            sys.executable, str(PREPROCESSOR),
            "--rsdb-dir", str(args.rsdb_dir),
            "--icp-workbook", str(args.icp_workbook),
            "--domain-workbook", str(args.domain_workbook),
            "--ip-workbook", str(args.ip_workbook),
            "--filing-exclusions", str(args.filing_exclusions),
            "--output-dir", str(preprocess_dir),
        ])
        preprocess_json = preprocess_dir / "legacy-whois-org-preprocess.json"
        preprocessing = json.loads(preprocess_json.read_text(encoding="utf-8"))

        import_dir.mkdir()
        with tempfile.TemporaryDirectory(prefix="cernic-city-codes-") as temporary:
            city_codes = Path(temporary) / "city-codes.json"
            run(["node", str(CITY_BUILDER), str(args.constant_js), str(city_codes)])
            converter.main(
                args.rsdb_dir, import_dir, args.bootstrap_dump, None, None, city_codes,
                preprocess_report=preprocess_json,
                preserve_source_primary_values=True,
                ip_workbook=args.ip_workbook,
                filing_exclusions=args.filing_exclusions,
            )

        generated = import_dir / "CERNIC-import-test.db"
        dump = import_dir / "CERNIC-import.db"
        generated.rename(dump)
        structural = validate_dump(dump)
        write_validation_report(import_dir, structural, "complete-integrated", args.rsdb_dir)
        if structural["status"] != "passed":
            raise RuntimeError("Structural dump validation failed: " + "; ".join(structural["errors"]))
        invariants = additional_invariants(args.rsdb_dir, preprocessing, import_dir, dump)
        if not args.skip_java_validation:
            if not args.whois_jar.is_file():
                raise FileNotFoundError(args.whois_jar)
            # Parsing all objects detects malformed RPSL blocks.  Legacy field
            # syntax warnings are handled by loadDump and are not promoted here.
            run(["java", "-cp", str(args.whois_jar), str(JAVA_VALIDATOR), str(dump)])
        write_complete_report(args.output_dir, dump, structural, invariants, preprocessing)
        print(json.dumps({
            "status": "passed", "dump": str(dump),
            "object_counts": structural["object_counts"],
            "preprocessing": preprocessing["summary"],
        }, ensure_ascii=False, indent=2))
    except Exception as error:
        (args.output_dir / "pipeline-error.txt").write_text(
            f"{type(error).__name__}: {error}\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
