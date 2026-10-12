#!/usr/bin/env python3
"""Build the organisation-resolution stage for the joint legacy/filing migration.

This stage is deliberately read-only with respect to the source data.  It
normalises legacy RPSL and the three confirmed filing workbooks, builds
organisation candidates, records field provenance, and stops before person or
resource materialisation whenever a conflict exists.

The TSV/JSON files are canonical.  An XLSX review workbook can be built from
``stage1-report.json`` with ``build_joint_stage1_report.mjs``.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
import csv
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlsplit

from openpyxl import load_workbook

from migration_special_rules import (
    legacy_resource_exclusion_audit,
    legacy_resource_exclusion_for,
)


REQUIRED_RPSL = {
    "person": ("CERNIC.db.pn.txt", "person:"),
    "role": ("CERNIC.db.ro.txt", "role:"),
    "inetnum": ("CERNIC.db.in.txt", "inetnum:"),
    "inet6num": ("CERNIC.db.i6.txt", "*i6:"),
    "domain": ("CERNIC.db.dn.txt", "domain:"),
    "nameserver": ("CERNIC.db.ht.txt", "host:"),
}

ICP_SHEET = "ICP备案数据"
DOMAIN_REGISTRATION_REQUIRED_HEADERS = {
    "name", "punycode", "registrantName", "registrantCertType",
    "registrantCertNo", "registrantOrg",
}
IP_ALLOCATION_SHEET = "IP分配信息"
STAGE_VERSION = "2.0"


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalized(value: object) -> str:
    return re.sub(r"[\s\-_.()（）,，、/\\:：]+", "", text(value).casefold())


def normalized_value(value: object) -> str:
    return re.sub(r"\s+", " ", text(value)).strip().casefold()


PLACEHOLDER_VALUES = {"", "-", "--", "无", "暂无", "不详", "未知"}


def meaningful(value: object) -> str:
    candidate = text(value)
    return "" if normalized_value(candidate) in PLACEHOLDER_VALUES else candidate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12].upper()}"


def parse_rpsl_file(path: Path, first: str, object_type: str) -> list[dict]:
    objects: list[dict] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if not current:
            return
        fields: dict[str, list[str]] = defaultdict(list)
        previous = ""
        for _, raw in current:
            if raw[:1].isspace() and previous:
                fields[previous][-1] += " " + raw.strip()
                continue
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            previous = key.strip().lower()
            fields[previous].append(value.strip())
        primary_key = "*i6" if object_type == "inet6num" else ("host" if object_type == "nameserver" else object_type)
        primary = fields.get(primary_key, [""])[0]
        objects.append({
            "type": object_type,
            # ``raw_key`` is the only value a later materialisation stage may
            # write back as the resource primary key.  ``key`` remains as a
            # compatibility alias for existing stage-1 code.  Parsed/canonical
            # representations are matching indexes only and must never replace
            # this source value in generated WHOIS data.
            "raw_key": primary,
            "key": primary,
            "fields": dict(fields),
            "source_file": path.name,
            "source_line": current[0][0],
            "object_index": len(objects) + 1,
        })
        current.clear()

    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not raw.strip():
            flush()
            continue
        if raw.startswith(first) and current:
            flush()
        current.append((line_number, raw))
    flush()
    return objects


def read_sheet(path: Path, sheet_name: str) -> list[tuple[int, dict[str, str]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        workbook.close()
        raise ValueError(f"{path.name} 缺少工作表 {sheet_name}")
    sheet = workbook[sheet_name]
    rows = sheet.iter_rows(values_only=True)
    try:
        headers = [text(value) for value in next(rows)]
    except StopIteration:
        workbook.close()
        return []
    result = []
    for excel_row, values in enumerate(rows, 2):
        row = {header: text(values[index]) if index < len(values) else "" for index, header in enumerate(headers)}
        if any(row.values()):
            result.append((excel_row, row))
    workbook.close()
    return result


def require_headers(rows: list[tuple[int, dict[str, str]]], required: set[str], label: str) -> None:
    headers = set(rows[0][1]) if rows else set()
    missing = sorted(required - headers)
    if missing:
        raise ValueError(f"{label} 缺少字段: {', '.join(missing)}")


@dataclass(frozen=True)
class IpRange:
    version: int
    first: int
    last: int

    @property
    def size(self) -> int:
        return self.last - self.first + 1

    def display(self) -> str:
        return f"{ipaddress.ip_address(self.first)} - {ipaddress.ip_address(self.last)}"


def parse_ip_range(start: str, end: str = "") -> IpRange | None:
    try:
        if not end and "/" in start:
            network = ipaddress.ip_network(start.strip(), strict=False)
            return IpRange(network.version, int(network.network_address), int(network.broadcast_address))
        if not end and "-" in start:
            pieces = re.split(r"\s*-\s*", start.strip(), maxsplit=1)
            if len(pieces) == 2:
                return parse_ip_range(pieces[0], pieces[1])
        if not end:
            address = ipaddress.ip_address(start.strip())
            return IpRange(address.version, int(address), int(address))
        first = ipaddress.ip_address(start.strip())
        last = ipaddress.ip_address(end.strip())
        if first.version != last.version or int(first) > int(last):
            return None
        return IpRange(first.version, int(first), int(last))
    except ValueError:
        return None


def parse_ip_ranges(start: str, end: str = "") -> list[IpRange]:
    """Parse one or more filing IP ranges, including semicolon-separated cells."""
    starts = [text(item) for item in re.split(r"[;；]+", text(start)) if text(item)]
    ends = [text(item) for item in re.split(r"[;；]+", text(end)) if text(item)]
    if not starts:
        return []
    if not ends:
        pairs = [(item, "") for item in starts]
    elif len(starts) == len(ends):
        pairs = list(zip(starts, ends))
    elif len(ends) == 1:
        pairs = [(item, ends[0] if len(starts) == 1 else item) for item in starts]
    else:
        pairs = [(item, item) for item in starts]

    result: list[IpRange] = []
    seen: set[tuple[int, int, int]] = set()
    for first_value, last_value in pairs:
        interval = parse_ip_range(first_value, last_value)
        if interval is None:
            continue
        marker = (interval.version, interval.first, interval.last)
        if marker not in seen:
            seen.add(marker)
            result.append(interval)
    return result


def most_specific_matches(
    target: IpRange,
    candidates: list[tuple[IpRange, str, dict]],
    prefer_institutional: bool = True,
) -> list[tuple[IpRange, str, dict]]:
    exact = [item for item in candidates if item[0] == target]
    if exact:
        selected = exact
    else:
        containing = [
            item for item in candidates
            if item[0].first <= target.first and target.last <= item[0].last
        ]
        if not containing:
            return []
        smallest = min(interval.size for interval, _, _ in containing)
        selected = [item for item in containing if item[0].size == smallest]
    return prefer_institutional_matches(selected) if prefer_institutional else selected


def is_provincial_allocation_container(obj: dict) -> bool:
    """Return true for a regional allocation container, not an end institution."""
    descriptions = obj.get("fields", {}).get("descr", [])
    marker = normalized("CERNET Provincial Address Allocations")
    return any(normalized(value) == marker for value in descriptions)


def prefer_institutional_matches(
    matches: list[tuple[IpRange, str, dict]],
) -> list[tuple[IpRange, str, dict]]:
    """Prefer an institutional object over an equal-range provincial container.

    The legacy database often stores the same allocation twice: once as a
    provincial CERNET container (commonly conn-i BJ000000) and once as the
    actual institution.  The container is hierarchy context and must not create
    a second organisation candidate when an end-institution object exists.
    """
    institutional = [item for item in matches if not is_provincial_allocation_container(item[2])]
    return institutional or matches


def canonical_domain(value: object) -> str | None:
    """Return one canonical DNS name without changing its label hierarchy.

    Case, a trailing root dot, URL syntax and IDN spelling are normalized, but
    labels such as ``www`` are significant.  In particular, ``www.edu.cn`` is
    a different WHOIS domain object from ``edu.cn`` and must not be folded into
    the apex name.
    """
    candidate = text(value).strip().lower().rstrip(".")
    if not candidate:
        return None
    if "://" in candidate:
        try:
            candidate = urlsplit(candidate).hostname or ""
        except ValueError:
            return None
    candidate = candidate.split("/", 1)[0].split(":", 1)[0].strip().rstrip(".")
    try:
        canonical = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(canonical) > 253 or "." not in canonical:
        return None
    labels = canonical.split(".")
    if any(not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels):
        return None
    try:
        ipaddress.ip_address(canonical)
        return None
    except ValueError:
        return canonical


def domain_tokens(value: object) -> list[str]:
    raw = text(value)
    if not raw:
        return []
    candidates = re.split(r"[\s,，;；、]+", raw)
    result: list[str] = []
    for candidate in candidates:
        domain = canonical_domain(candidate)
        if domain and domain not in result:
            result.append(domain)
    return result


def first(fields: dict[str, list[str]], key: str) -> str:
    return fields.get(key, [""])[0]


def has_han(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def legacy_names(fields: dict[str, list[str]]) -> tuple[str, str]:
    """Extract the first Chinese and first likely English name from descr.

    ``descr`` also contains postal/address text.  Name extraction is therefore
    only candidate evidence; multiple candidates remain a blocking conflict.
    """
    descriptions = [text(value) for value in fields.get("descr", []) if text(value)]
    # The documented legacy layout is c-org-name, org-name, then
    # location/remarks.  Some c-org-name values are Latin identifiers, so the
    # position (not the character set) is authoritative.  A single descr value
    # was not treated as an organisation by the original converter.
    if len(descriptions) < 2:
        return "", ""
    return descriptions[0], descriptions[1]


@dataclass
class FilingRecord:
    record_id: str
    source_file: str
    source_sheet: str
    source_row: int
    source_kind: str
    name: str
    allocation_method: str = ""
    raw_ip_start: str = ""
    raw_ip_end: str = ""
    org_type: str = ""
    cert_type: str = ""
    cert_no: str = ""
    region: str = ""
    addresses: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    ip_ranges: list[IpRange] = field(default_factory=list)
    emergency_name: str = ""
    emergency_phone: str = ""
    emergency_email: str = ""
    person_evidence: list[dict[str, str]] = field(default_factory=list)
    entity_key: str = ""


def organisation_matching_ip_ranges(record: FilingRecord) -> list[IpRange]:
    """Return only IP-allocation ranges that may establish organisation ownership.

    ICP IP fields describe website/service deployment and are known to be
    unreliable for resource ownership.  They remain available on the record
    for audit, but must never connect a filing entity to a legacy conn-i.
    """
    return record.ip_ranges if record.source_kind == "ip-allocation" else []


def person_evidence_for_merge(record: FilingRecord) -> list[dict[str, str]]:
    """Return contact evidence allowed to participate in person resolution."""
    return record.person_evidence if record.source_kind == "ip-allocation" else []


def filing_identity(record: FilingRecord) -> tuple[str, str] | None:
    """Return the complete legal identity used by the ordered resolver.

    Certificate number is the identity key. Certificate type is deliberately
    not part of the key because the domain registry uses numeric codes while
    ICP/IPFP use Chinese labels for the same certificate type.
    """
    name = meaningful(record.name)
    cert_no = meaningful(record.cert_no)
    if not name or not cert_no:
        return None
    return normalized(cert_no), normalized(name)


def resolve_filing_identity(records: list[FilingRecord]) -> tuple[str, list[FilingRecord]]:
    """Resolve one source level without silently selecting an ambiguous row."""
    complete = [record for record in records if filing_identity(record) is not None]
    if not complete:
        return "missing", []
    cert_numbers = {filing_identity(record)[0] for record in complete}
    if len(cert_numbers) > 1:
        return "multiple-certificates", complete
    legal_names = {filing_identity(record)[1] for record in complete}
    if len(legal_names) > 1:
        return "multiple-names", complete
    return "unique", complete


def select_domain_identity(
    icp_records: list[FilingRecord],
    domain_registration_records: list[FilingRecord],
) -> tuple[str, str, list[FilingRecord]]:
    """Apply the user-approved domain source priority."""
    icp_status, icp_selected = resolve_filing_identity(icp_records)
    if icp_status != "missing":
        return "icp", icp_status, icp_selected
    registry_status, registry_selected = resolve_filing_identity(domain_registration_records)
    return "domain-registration", registry_status, registry_selected


@dataclass
class Conflict:
    conflict_id: str
    category: str
    subtype: str
    object_type: str
    object_key: str
    field: str
    whois_value: str
    filing_value: str
    source_refs: str
    explanation: str
    resolution_policy: str
    proposed_value: str = ""


class Stage1:
    def __init__(
        self, rsdb_dir: Path, icp_workbook: Path, ip_workbook: Path, output_dir: Path,
        filing_exclusions: Path | None = None,
        domain_workbook: Path | None = None,
    ) -> None:
        self.rsdb_dir = rsdb_dir
        self.icp_workbook = icp_workbook
        self.ip_workbook = ip_workbook
        self.domain_workbook = domain_workbook
        self.output_dir = output_dir
        self.filing_exclusions = filing_exclusions
        self.objects: dict[str, list[dict]] = {}
        self.filing_records: list[FilingRecord] = []
        self.conflicts: list[Conflict] = []
        self.evidence: list[dict[str, object]] = []
        self.candidates: list[dict[str, object]] = []
        self.non_organisation_filing_records: list[dict[str, object]] = []
        self.excluded_filing_records: list[dict[str, object]] = []
        self.excluded_legacy_resources: list[dict[str, str]] = []
        self.exclusion_rules: list[dict[str, str]] = []
        self.stats = Counter()
        self._conflict_keys: set[tuple] = set()
        self._conflict_index: dict[tuple, Conflict] = {}

    def load_exclusions(self) -> None:
        if self.filing_exclusions is None:
            return
        if not self.filing_exclusions.is_file():
            raise FileNotFoundError(f"缺少备案排除规则文件: {self.filing_exclusions}")
        with self.filing_exclusions.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            required = {"source_kind", "organisation_name", "cert_no", "action", "reason"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("备案排除规则必须包含: " + ", ".join(sorted(required)))
            self.exclusion_rules = [{key: text(value) for key, value in row.items()} for row in reader]

    def exclusion_for(self, source_kind: str, name: str, cert_no: str) -> dict[str, str] | None:
        for rule in self.exclusion_rules:
            if normalized(rule["source_kind"]) != normalized(source_kind):
                continue
            if normalized(rule["organisation_name"]) != normalized(name):
                continue
            rule_cert = normalized(meaningful(rule["cert_no"]))
            if rule_cert and rule_cert != normalized(meaningful(cert_no)):
                continue
            return rule
        return None

    @staticmethod
    def audit_filing_row(
        *, classification: str, source_file: str, source_sheet: str, source_row: int,
        record_id: str, source_kind: str, name: str, cert_no: str, raw_start: str,
        raw_end: str, ranges: list[IpRange], action: str, reason: str,
    ) -> dict[str, object]:
        return {
            "classification": classification,
            "record_id": record_id,
            "source_kind": source_kind,
            "organisation_name": name,
            "cert_no": meaningful(cert_no),
            "raw_ip_start": raw_start,
            "raw_ip_end": raw_end,
            "parsed_ip_ranges": " | ".join(interval.display() for interval in ranges),
            "source_ref": f"{source_file}/{source_sheet}!{source_row}",
            "action": action,
            "reason": reason,
        }

    def add_conflict(
        self, category: str, subtype: str, object_type: str, object_key: str, field: str,
        whois_value: str, filing_value: str, source_refs: str, explanation: str,
        resolution_policy: str, proposed_value: str = "",
    ) -> None:
        marker = (
            category, subtype, object_type, object_key, field,
            normalized_value(whois_value), normalized_value(filing_value),
        )
        if marker in self._conflict_keys:
            existing = self._conflict_index[marker]
            merged_refs = list(dict.fromkeys(filter(
                None, existing.source_refs.split("\n") + source_refs.split("\n"))))
            existing.source_refs = "\n".join(merged_refs)
            return
        self._conflict_keys.add(marker)
        conflict = Conflict(
            conflict_id="",
            category=category,
            subtype=subtype,
            object_type=object_type,
            object_key=object_key,
            field=field,
            whois_value=whois_value,
            filing_value=filing_value,
            source_refs=source_refs,
            explanation=explanation,
            resolution_policy=resolution_policy,
            proposed_value=proposed_value,
        )
        self.conflicts.append(conflict)
        self._conflict_index[marker] = conflict

    def load(self) -> None:
        self.load_exclusions()
        for object_type, (filename, first_marker) in REQUIRED_RPSL.items():
            path = self.rsdb_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"缺少旧数据文件: {path}")
            parsed = parse_rpsl_file(path, first_marker, object_type)
            retained = []
            for obj in parsed:
                rule = legacy_resource_exclusion_for(object_type, obj["raw_key"], obj["fields"])
                if rule is None:
                    retained.append(obj)
                    continue
                self.excluded_legacy_resources.append(legacy_resource_exclusion_audit(
                    rule,
                    source_file=obj["source_file"],
                    source_line=obj["source_line"],
                ))
            self.objects[object_type] = retained
            self.stats[f"legacy_{object_type}_source"] = len(parsed)
            self.stats[f"legacy_{object_type}"] = len(retained)
        self.stats["legacy_resources_excluded_by_rule"] = len(self.excluded_legacy_resources)

        icp_rows = read_sheet(self.icp_workbook, ICP_SHEET)
        require_headers(icp_rows, {
            "主体单位名称", "主办单位性质", "主办单位有效证件类型", "主办单位有效证件号码",
            "主办单位所属区域", "主办单位证件住所", "主办单位通讯地址", "域名",
            "起始IP地址", "终止IP地址",
        }, f"{self.icp_workbook.name}/{ICP_SHEET}")
        for excel_row, row in icp_rows:
            ip_ranges = parse_ip_ranges(row["起始IP地址"], row["终止IP地址"])
            if ";" in row["起始IP地址"] or "；" in row["起始IP地址"]:
                self.stats["filing_icp_multi_ip_rows"] += 1
            self.stats["filing_icp_parsed_ip_ranges"] += len(ip_ranges)
            name = meaningful(row["主体单位名称"])
            record_id = f"ICP-{excel_row}"
            if not name:
                self.non_organisation_filing_records.append(self.audit_filing_row(
                    classification="缺少机构名称", source_file=self.icp_workbook.name,
                    source_sheet=ICP_SHEET, source_row=excel_row, record_id=record_id,
                    source_kind="icp", name=row["主体单位名称"], cert_no=row["主办单位有效证件号码"],
                    raw_start=row["起始IP地址"], raw_end=row["终止IP地址"], ranges=ip_ranges,
                    action="不参与机构提取", reason="备案主体单位名称为空或为占位值。",
                ))
                continue
            exclusion = self.exclusion_for("icp", name, row["主办单位有效证件号码"])
            if exclusion:
                self.excluded_filing_records.append(self.audit_filing_row(
                    classification="迁移排除规则", source_file=self.icp_workbook.name,
                    source_sheet=ICP_SHEET, source_row=excel_row, record_id=record_id,
                    source_kind="icp", name=name, cert_no=row["主办单位有效证件号码"],
                    raw_start=row["起始IP地址"], raw_end=row["终止IP地址"], ranges=ip_ranges,
                    action=exclusion["action"], reason=exclusion["reason"],
                ))
                continue
            record = FilingRecord(
                record_id=record_id,
                source_file=self.icp_workbook.name,
                source_sheet=ICP_SHEET,
                source_row=excel_row,
                source_kind="icp",
                name=name,
                raw_ip_start=row["起始IP地址"],
                raw_ip_end=row["终止IP地址"],
                org_type=row["主办单位性质"],
                cert_type=row["主办单位有效证件类型"],
                cert_no=meaningful(row["主办单位有效证件号码"]),
                region=row["主办单位所属区域"],
                addresses=[value for value in (row["主办单位证件住所"], row["主办单位通讯地址"]) if value],
                domains=domain_tokens(row["域名"]),
                ip_ranges=ip_ranges,
                person_evidence=[],
            )
            self.filing_records.append(record)

        if self.domain_workbook is None:
            raise ValueError("缺少域名备案工作簿（例如 2026-09-21_full.xlsx）")
        domain_workbook = load_workbook(self.domain_workbook, read_only=True, data_only=True)
        matching_sheets = []
        for sheet in domain_workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            try:
                headers = {text(value) for value in next(rows)}
            except StopIteration:
                continue
            if DOMAIN_REGISTRATION_REQUIRED_HEADERS.issubset(headers):
                matching_sheets.append(sheet.title)
        domain_workbook.close()
        if len(matching_sheets) != 1:
            raise ValueError(
                f"{self.domain_workbook.name} 必须且只能有一个包含域名备案字段的工作表；"
                f"实际匹配: {', '.join(matching_sheets) or '无'}"
            )
        domain_sheet = matching_sheets[0]
        domain_rows = read_sheet(self.domain_workbook, domain_sheet)
        require_headers(
            domain_rows, DOMAIN_REGISTRATION_REQUIRED_HEADERS,
            f"{self.domain_workbook.name}/{domain_sheet}",
        )
        for excel_row, row in domain_rows:
            legal_name = meaningful(row["registrantName"]) or meaningful(row["registrantOrg"])
            record_id = f"DOMAINREG-{excel_row}"
            if not legal_name:
                self.non_organisation_filing_records.append(self.audit_filing_row(
                    classification="缺少法人名称", source_file=self.domain_workbook.name,
                    source_sheet=domain_sheet, source_row=excel_row, record_id=record_id,
                    source_kind="domain-registration", name="",
                    cert_no=row["registrantCertNo"], raw_start="", raw_end="", ranges=[],
                    action="不参与机构身份补全", reason="域名备案的registrantName和registrantOrg均为空。",
                ))
                continue
            exclusion = self.exclusion_for(
                "domain-registration", legal_name, row["registrantCertNo"])
            if exclusion:
                self.excluded_filing_records.append(self.audit_filing_row(
                    classification="迁移排除规则", source_file=self.domain_workbook.name,
                    source_sheet=domain_sheet, source_row=excel_row, record_id=record_id,
                    source_kind="domain-registration", name=legal_name,
                    cert_no=row["registrantCertNo"], raw_start="", raw_end="", ranges=[],
                    action=exclusion["action"], reason=exclusion["reason"],
                ))
                continue
            self.filing_records.append(FilingRecord(
                record_id=record_id,
                source_file=self.domain_workbook.name,
                source_sheet=domain_sheet,
                source_row=excel_row,
                source_kind="domain-registration",
                name=legal_name,
                cert_type=meaningful(row["registrantCertType"]),
                cert_no=meaningful(row["registrantCertNo"]),
                domains=domain_tokens(";".join(filter(None, [row["name"], row["punycode"]]))),
            ))

        ip_rows = read_sheet(self.ip_workbook, IP_ALLOCATION_SHEET)
        require_headers(ip_rows, {
            "ID", "起始IP", "终止IP", "使用单位名称", "使用单位性质", "使用单位行政区域",
            "使用单位详细地址", "紧急联系人", "紧急联系人电话", "紧急联系人电子邮件",
            "使用单位证件类型", "使用单位证件号码", "分配方式",
        }, f"{self.ip_workbook.name}/{IP_ALLOCATION_SHEET}")
        for excel_row, row in ip_rows:
            ip_ranges = parse_ip_ranges(row["起始IP"], row["终止IP"])
            self.stats["filing_ip_allocation_parsed_ip_ranges"] += len(ip_ranges)
            record_id = f"IP-{row['ID'] or excel_row}"
            name = meaningful(row["使用单位名称"])
            if not name:
                reserved = normalized(row["分配方式"]) == normalized("预留")
                classification = "预留网段（无使用单位）" if reserved else "缺少机构名称"
                reason = (
                    "分配方式为预留，且无使用单位；这是资源审计记录，不是机构对象。"
                    if reserved else "IP分配记录的使用单位名称为空或为占位值。"
                )
                self.non_organisation_filing_records.append(self.audit_filing_row(
                    classification=classification, source_file=self.ip_workbook.name,
                    source_sheet=IP_ALLOCATION_SHEET, source_row=excel_row, record_id=record_id,
                    source_kind="ip-allocation", name=row["使用单位名称"], cert_no=row["使用单位证件号码"],
                    raw_start=row["起始IP"], raw_end=row["终止IP"], ranges=ip_ranges,
                    action="不参与机构提取", reason=reason,
                ))
                if reserved:
                    self.stats["reserved_records_excluded_from_org_extraction"] += 1
                continue
            exclusion = self.exclusion_for("ip-allocation", name, row["使用单位证件号码"])
            if exclusion:
                self.excluded_filing_records.append(self.audit_filing_row(
                    classification="疑似过期备案记录", source_file=self.ip_workbook.name,
                    source_sheet=IP_ALLOCATION_SHEET, source_row=excel_row, record_id=record_id,
                    source_kind="ip-allocation", name=name, cert_no=row["使用单位证件号码"],
                    raw_start=row["起始IP"], raw_end=row["终止IP"], ranges=ip_ranges,
                    action=exclusion["action"], reason=exclusion["reason"],
                ))
                continue
            record = FilingRecord(
                record_id=record_id,
                source_file=self.ip_workbook.name,
                source_sheet=IP_ALLOCATION_SHEET,
                source_row=excel_row,
                source_kind="ip-allocation",
                name=name,
                allocation_method=row["分配方式"],
                raw_ip_start=row["起始IP"],
                raw_ip_end=row["终止IP"],
                org_type=row["使用单位性质"],
                cert_type=row["使用单位证件类型"],
                cert_no=meaningful(row["使用单位证件号码"]),
                region=row["使用单位行政区域"],
                addresses=[row["使用单位详细地址"]] if row["使用单位详细地址"] else [],
                ip_ranges=ip_ranges,
                emergency_name=row["紧急联系人"],
                emergency_phone=row["紧急联系人电话"],
                emergency_email=row["紧急联系人电子邮件"],
                person_evidence=[
                    {"role": "IP分配联系人", "name": row.get("联系人姓名", ""),
                     "phone": row.get("联系人电话", ""), "email": row.get("联系人电子邮件", "")},
                    {"role": "IP紧急联系人", "name": row.get("紧急联系人", ""),
                     "phone": row.get("紧急联系人电话", ""), "email": row.get("紧急联系人电子邮件", "")},
                ],
            )
            self.filing_records.append(record)
        self.stats["filing_icp_records"] = len(icp_rows)
        self.stats["filing_domain_registration_records"] = len(domain_rows)
        self.stats["filing_ip_allocation_records"] = len(ip_rows)
        self.stats["non_organisation_filing_records"] = len(self.non_organisation_filing_records)
        self.stats["filing_records_excluded_by_rule"] = len(self.excluded_filing_records)

    def filter_unmatched_reserved_records(
        self, legacy_ranges: dict[int, list[tuple[IpRange, str, dict]]],
    ) -> None:
        """Prevent reserved-only resources from creating new organisations.

        Named reserved rows may enrich an existing WHOIS organisation when at
        least one range matches.  If no legacy range matches, the row remains
        auditable but cannot enter filing entity grouping or materialisation.
        """
        self.stats.setdefault("reserved_named_records_with_whois_match", 0)
        self.stats.setdefault("reserved_named_records_without_whois_match", 0)
        retained: list[FilingRecord] = []
        for record in self.filing_records:
            is_reserved = (
                record.source_kind == "ip-allocation"
                and normalized(record.allocation_method) == normalized("预留")
            )
            if not is_reserved:
                retained.append(record)
                continue
            matched = any(
                most_specific_matches(interval, legacy_ranges[interval.version])
                for interval in record.ip_ranges
            )
            if matched:
                self.stats["reserved_named_records_with_whois_match"] += 1
                retained.append(record)
                continue
            self.stats["reserved_named_records_without_whois_match"] += 1
            self.non_organisation_filing_records.append(self.audit_filing_row(
                classification="预留网段（旧WHOIS无对应资源）",
                source_file=record.source_file,
                source_sheet=record.source_sheet,
                source_row=record.source_row,
                record_id=record.record_id,
                source_kind=record.source_kind,
                name=record.name,
                cert_no=record.cert_no,
                raw_start=record.raw_ip_start,
                raw_end=record.raw_ip_end,
                ranges=record.ip_ranges,
                action="不创建新机构；不参与机构提取",
                reason="分配方式为预留，且其IP范围在旧WHOIS中没有对应记录。",
            ))
        self.filing_records = retained
        self.stats["non_organisation_filing_records"] = len(self.non_organisation_filing_records)

    def group_filing_entities(self) -> dict[str, list[FilingRecord]]:
        cert_entities: dict[str, list[FilingRecord]] = defaultdict(list)
        name_only: list[FilingRecord] = []
        name_to_cert_keys: dict[str, set[str]] = defaultdict(set)

        for record in self.filing_records:
            cert_type = normalized(record.cert_type)
            cert_no = normalized(record.cert_no)
            if cert_type and cert_no:
                key = f"cert:{cert_type}:{cert_no}"
                cert_entities[key].append(record)
                if normalized(record.name):
                    name_to_cert_keys[normalized(record.name)].add(key)
            else:
                name_only.append(record)

        entities = dict(cert_entities)
        for record in name_only:
            name_key = normalized(record.name)
            candidates = name_to_cert_keys.get(name_key, set())
            if len(candidates) == 1:
                key = next(iter(candidates))
            elif len(candidates) > 1:
                key = f"ambiguous-name:{name_key}:{record.record_id}"
                self.add_conflict(
                    "身份冲突", "同名对应多个证件实体", "filing-organisation", record.record_id,
                    "identity", "\n".join(sorted(candidates)), record.name,
                    f"{record.source_file}/{record.source_sheet}!{record.source_row}",
                    "无证件号记录的机构名称同时命中多个不同证件实体，不能自动合并。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )
            else:
                key = f"name:{name_key or record.record_id.casefold()}"
            entities.setdefault(key, []).append(record)

        cert_no_types: dict[str, set[str]] = defaultdict(set)
        for record in self.filing_records:
            if normalized(record.cert_no):
                cert_no_types[normalized(record.cert_no)].add(normalized(record.cert_type))
        for cert_no, types in cert_no_types.items():
            clean_types = {value for value in types if value}
            if len(clean_types) > 1:
                refs = [record for record in self.filing_records if normalized(record.cert_no) == cert_no]
                self.add_conflict(
                    "备案内部冲突", "同一证件号码存在多个证件类型", "filing-organisation", cert_no,
                    "cert-type", "", "\n".join(sorted({record.cert_type for record in refs if record.cert_type})),
                    "\n".join(f"{record.source_file}/{record.source_sheet}!{record.source_row}" for record in refs),
                    "同一规范化证件号码在备案数据中对应多个证件类型。",
                    "FILING_INTERNAL_REVIEW_REQUIRED",
                )

        for key, records in entities.items():
            for record in records:
                record.entity_key = key
            names = {normalized(record.name): record.name for record in records if normalized(record.name)}
            if len(names) > 1:
                self.add_conflict(
                    "备案内部冲突", "同一证件实体存在多个机构名称", "filing-organisation", stable_id("FE", key),
                    "org-name", "", "\n".join(sorted(names.values())),
                    "\n".join(f"{record.source_file}/{record.source_sheet}!{record.source_row}" for record in records),
                    "同一证件类型和证件号码对应多个不同的备案机构名称。",
                    "FILING_INTERNAL_REVIEW_REQUIRED",
                )
        self.stats["filing_entities"] = len(entities)
        return entities

    def _run_pre_v2(self) -> dict:
        self.load()
        self.stats.setdefault("provincial_allocation_container_matches_ignored", 0)

        legacy_ranges: dict[int, list[tuple[IpRange, str, dict]]] = defaultdict(list)
        legacy_org_resources: dict[str, list[dict]] = defaultdict(list)
        for typ in ("inetnum", "inet6num"):
            for obj in self.objects[typ]:
                fields = obj["fields"]
                interval = parse_ip_range(first(fields, "inetnum" if typ == "inetnum" else "*i6"))
                orgid = first(fields, "conn-i").upper()
                if interval and orgid:
                    legacy_ranges[interval.version].append((interval, orgid, obj))
                    legacy_org_resources[orgid].append(obj)

        self.filter_unmatched_reserved_records(legacy_ranges)
        entities = self.group_filing_entities()

        legacy_domains: dict[str, list[dict]] = defaultdict(list)
        for obj in self.objects["domain"]:
            domain = canonical_domain(obj["raw_key"])
            if domain:
                legacy_domains[domain].append(obj)

        # Legacy conn-i is a starting cluster, not proof of one legal entity.
        legacy_org_names: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"c": set(), "e": set()})
        for orgid, resources in legacy_org_resources.items():
            for obj in resources:
                chinese, english = legacy_names(obj["fields"])
                if chinese:
                    legacy_org_names[orgid]["c"].add(chinese)
                if english:
                    legacy_org_names[orgid]["e"].add(english)
            # The legacy object extraction used the first descr value as the
            # organisation identity.  English-name spelling differences are
            # field evidence, not a separate split signal.
            values = legacy_org_names[orgid]["c"]
            norm_values = {normalized(value): value for value in values if normalized(value)}
            if len(norm_values) > 1:
                self.add_conflict(
                    "身份冲突", "同一旧conn-i包含多个机构名称", "organisation", orgid,
                    "c-org-name", "\n".join(sorted(values)), "",
                    "\n".join(f"{obj['source_file']}:{obj['source_line']}" for obj in resources),
                    "同一旧 conn-i 下的网段出现多个不同机构名称，可能需要拆分机构。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )

        entity_orgids: dict[str, set[str]] = defaultdict(set)
        orgid_entities: dict[str, set[str]] = defaultdict(set)
        entity_domains: dict[str, set[str]] = defaultdict(set)

        for record in self.filing_records:
            ref = f"{record.source_file}/{record.source_sheet}!{record.source_row}"
            if record.source_kind == "icp" and record.ip_ranges:
                self.stats["icp_ip_ranges_audit_only"] += len(record.ip_ranges)
                for record_range in record.ip_ranges:
                    self.evidence.append({
                        "evidence_type": "ICP IP原始记录（仅审计）",
                        "entity_id": stable_id("FE", record.entity_key),
                        "legacy_orgid": "",
                        "resource": record_range.display(),
                        "whois_value": "",
                        "filing_value": record.name,
                        "source_ref": ref,
                        "result": "不参与旧WHOIS网段匹配或organisation归属",
                    })
            for record_range in organisation_matching_ip_ranges(record):
                all_ranges = legacy_ranges[record_range.version]
                structural_matches = most_specific_matches(
                    record_range, all_ranges, prefer_institutional=False,
                )
                matches = prefer_institutional_matches(structural_matches)
                ignored_containers = [item for item in structural_matches if item not in matches]
                if ignored_containers:
                    self.stats["provincial_allocation_container_matches_ignored"] += len(ignored_containers)
                    self.evidence.append({
                        "evidence_type": "上级分配容器排除",
                        "entity_id": stable_id("FE", record.entity_key),
                        "legacy_orgid": " | ".join(sorted({orgid for _, orgid, _ in ignored_containers})),
                        "resource": record_range.display(),
                        "whois_value": " | ".join(sorted({
                            f"{orgid}: {obj['key']}"
                            for _, orgid, obj in ignored_containers
                        })),
                        "filing_value": record.name,
                        "source_ref": ref,
                        "result": "存在同范围机构对象，省级分配容器不参与机构归属判断",
                    })
                orgids = sorted({orgid for _, orgid, _ in matches})
                basis = "IP区间完全匹配" if any(interval == record_range for interval, _, _ in matches) else "IP区间唯一包含"
                if len(orgids) == 1:
                    orgid = orgids[0]
                    entity_orgids[record.entity_key].add(orgid)
                    orgid_entities[orgid].add(record.entity_key)
                    self.evidence.append({
                        "evidence_type": basis,
                        "entity_id": stable_id("FE", record.entity_key),
                        "legacy_orgid": orgid,
                        "resource": record_range.display(),
                        "whois_value": " | ".join(sorted({obj["key"] for _, _, obj in matches})),
                        "filing_value": record.name,
                        "source_ref": ref,
                        "result": "唯一关联",
                    })
                    matched_chinese = sorted({
                        legacy_names(obj["fields"])[0]
                        for _, _, obj in matches if legacy_names(obj["fields"])[0]
                    })
                    if record.name and matched_chinese and normalized(record.name) not in {
                        normalized(value) for value in matched_chinese
                    }:
                        self.add_conflict(
                            "字段冲突", "WHOIS与备案机构名称不一致", "organisation", orgid,
                            "c-org-name", "\n".join(matched_chinese), record.name, ref,
                            "资源证据把备案记录关联到该旧机构，但机构名称不一致。WHOIS值保留，冲突需复核。",
                            "WHOIS_VALUE_RETAINED", "\n".join(matched_chinese),
                        )
                elif len(orgids) > 1:
                    self.add_conflict(
                        "匹配冲突", "备案IP区间命中多个旧机构", "filing-organisation", record.record_id,
                        "ip-range", "\n".join(orgids), f"{record_range.display()}\n{record.name}", ref,
                        "备案IP区间被多个不同 conn-i 的WHOIS网段包含，不能自动选择机构。",
                        "MANUAL_IDENTITY_DECISION_REQUIRED",
                    )
                else:
                    self.stats["filing_ip_unmatched"] += 1
                    self.evidence.append({
                        "evidence_type": "IP区间",
                        "entity_id": stable_id("FE", record.entity_key),
                        "legacy_orgid": "",
                        "resource": record_range.display(),
                        "whois_value": "",
                        "filing_value": record.name,
                        "source_ref": ref,
                        "result": "未匹配（可创建新机构，不单独作为冲突）",
                    })

            for domain in record.domains:
                matches = legacy_domains.get(domain, [])
                if len(matches) == 1:
                    obj = matches[0]
                    entity_domains[record.entity_key].add(domain)
                    c_name = first(obj["fields"], "c-orgname")
                    self.evidence.append({
                        "evidence_type": "域名完全匹配",
                        "entity_id": stable_id("FE", record.entity_key),
                        "legacy_orgid": "",
                        "resource": domain,
                        "whois_value": c_name or first(obj["fields"], "orgname"),
                        "filing_value": record.name,
                        "source_ref": ref,
                        "result": "唯一关联",
                    })
                    if c_name and normalized(c_name) != normalized(record.name):
                        self.add_conflict(
                            "字段冲突", "WHOIS与备案域名机构名称不一致", "domain", domain,
                            "c-org-name", c_name, record.name, ref,
                            "同一域名的WHOIS机构名称和备案主体名称不一致。WHOIS值保留，关联关系需复核。",
                            "WHOIS_VALUE_RETAINED", c_name,
                        )
                elif len(matches) > 1:
                    self.add_conflict(
                        "匹配冲突", "旧WHOIS存在重复域名对象", "domain", domain,
                        "domain", "\n".join(f"{obj['source_file']}:{obj['source_line']}" for obj in matches),
                        record.name, ref,
                        "同一规范化域名对应多个旧WHOIS对象，不能建立唯一关联。",
                        "MANUAL_IDENTITY_DECISION_REQUIRED",
                    )
                else:
                    self.stats["filing_domain_unmatched"] += 1

        for entity_key, orgids in entity_orgids.items():
            if len(orgids) > 1:
                records = entities[entity_key]
                self.add_conflict(
                    "身份冲突", "一个备案实体连接多个旧conn-i", "filing-organisation", stable_id("FE", entity_key),
                    "identity", "\n".join(sorted(orgids)), "\n".join(sorted({record.name for record in records if record.name})),
                    "\n".join(f"{record.source_file}/{record.source_sheet}!{record.source_row}" for record in records),
                    "同一备案法律实体通过资源证据连接到多个旧 conn-i，需要确认合并还是保留多个机构。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )
        for orgid, entity_keys in orgid_entities.items():
            if len(entity_keys) > 1:
                names = sorted({record.name for key in entity_keys for record in entities[key] if record.name})
                refs = sorted({f"{record.source_file}/{record.source_sheet}!{record.source_row}" for key in entity_keys for record in entities[key]})
                self.add_conflict(
                    "身份冲突", "一个旧conn-i连接多个备案实体", "organisation", orgid,
                    "identity", "\n".join(sorted(entity_keys)), "\n".join(names), "\n".join(refs),
                    "同一旧 conn-i 通过资源证据连接到多个不同证件实体，可能需要拆分机构。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )

        # Filing fields are resolved only after entity identity is known.  A
        # non-empty WHOIS value wins; multiple filing values remain conflicts.
        for entity_key, records in sorted(entities.items()):
            orgids = sorted(entity_orgids.get(entity_key, set()))
            matched_entity_domains = sorted(entity_domains.get(entity_key, set()))
            materialize = bool(orgids or matched_entity_domains)
            candidate_id = (
                orgids[0]
                if len(orgids) == 1 and len(orgid_entities.get(orgids[0], set())) == 1
                else stable_id("NEWORG", entity_key)
            )

            def distinct(source_values: list[str]) -> list[str]:
                by_key = {}
                for value in source_values:
                    if normalized_value(value):
                        by_key.setdefault(normalized_value(value), value)
                return sorted(by_key.values())

            values = {
                "name": distinct([record.name for record in records]),
                "org_type": distinct([record.org_type for record in records]),
                "cert_type": distinct([record.cert_type for record in records]),
                "cert_no": distinct([record.cert_no for record in records]),
                "region": distinct([record.region for record in records]),
                "address": distinct([value for record in records for value in record.addresses]),
            }
            emergency_by_key = {}
            for record in records:
                if record.source_kind != "ip-allocation" or not record.emergency_name:
                    continue
                item = (record.emergency_name, record.emergency_phone, record.emergency_email)
                key = tuple(normalized_value(value) for value in item)
                emergency_by_key.setdefault(key, item)
            emergency = sorted(emergency_by_key.values())
            for field_name in ("org_type", "cert_type", "cert_no"):
                if len(values[field_name]) > 1:
                    self.add_conflict(
                        "备案内部冲突", "同一机构字段多值", "organisation", candidate_id,
                        field_name.replace("_", "-"), "", "\n".join(values[field_name]),
                        "\n".join(f"{record.source_file}/{record.source_sheet}!{record.source_row}" for record in records),
                        "同一候选机构从备案数据收到多个不同字段值，不能自动选择。",
                        "FILING_INTERNAL_REVIEW_REQUIRED",
                    )
            # admin-c is multi-valued.  Multiple emergency contacts are kept as
            # candidates and resolved/deduplicated in the person stage; their
            # mere multiplicity is not an organisation-stage conflict.
            identity_pending = len(orgids) > 1 or any(
                len(orgid_entities.get(orgid, set())) > 1 for orgid in orgids)
            if identity_pending:
                candidate_status = "身份冲突待处理"
            elif orgids:
                candidate_status = "关联旧机构"
            elif matched_entity_domains:
                candidate_status = "旧域名的新机构候选"
            else:
                candidate_status = "未关联旧资源（不物化）"
            self.candidates.append({
                "candidate_id": candidate_id,
                "filing_entity_id": stable_id("FE", entity_key),
                "legacy_orgids": " | ".join(orgids),
                "status": candidate_status,
                "materialize": "是" if materialize else "否",
                "whois_c_org_names": " | ".join(sorted({name for orgid in orgids for name in legacy_org_names[orgid]["c"]})),
                "whois_org_names": " | ".join(sorted({name for orgid in orgids for name in legacy_org_names[orgid]["e"]})),
                "filing_names": " | ".join(values["name"]),
                "org_types": " | ".join(values["org_type"]),
                "cert_types": " | ".join(values["cert_type"]),
                "cert_numbers": " | ".join(values["cert_no"]),
                "regions": " | ".join(values["region"]),
                "addresses": " | ".join(values["address"]),
                "ip_emergency_admin_candidates": " | ".join(" / ".join(filter(None, item)) for item in emergency),
                "matched_domains": " | ".join(matched_entity_domains),
                "source_record_count": len(records),
                "source_refs": " | ".join(f"{record.source_file}/{record.source_sheet}!{record.source_row}" for record in records),
            })

        linked_orgids = set(orgid_entities)
        for orgid in sorted(set(legacy_org_resources) - linked_orgids):
            self.candidates.append({
                "candidate_id": orgid,
                "filing_entity_id": "",
                "legacy_orgids": orgid,
                "status": "仅WHOIS机构",
                "materialize": "是",
                "whois_c_org_names": " | ".join(sorted(legacy_org_names[orgid]["c"])),
                "whois_org_names": " | ".join(sorted(legacy_org_names[orgid]["e"])),
                "filing_names": "", "org_types": "", "cert_types": "", "cert_numbers": "",
                "regions": "", "addresses": "", "ip_emergency_admin_candidates": "",
                "matched_domains": "", "source_record_count": 0,
                "source_refs": " | ".join(f"{obj['source_file']}:{obj['source_line']}" for obj in legacy_org_resources[orgid]),
            })

        # Domains without filing evidence remain explicitly unresolved.  They
        # are not netname-linked; the final stage may create cleanup-friendly
        # fake organisations for those with no contradictory candidate.
        matched_domains = {domain for values in entity_domains.values() for domain in values}
        unresolved_domains = []
        for domain, objects in sorted(legacy_domains.items()):
            if domain in matched_domains:
                continue
            for obj in objects:
                unresolved_domains.append({
                    "domain": domain,
                    "netname": first(obj["fields"], "netname"),
                    "orgname": first(obj["fields"], "orgname"),
                    "c_orgname": first(obj["fields"], "c-orgname"),
                    "source_ref": f"{obj['source_file']}:{obj['source_line']}",
                    "planned_action": "后续按名称证据解析；无唯一真实机构且无矛盾候选时创建可清理fake机构",
                })
        self.stats["unresolved_legacy_domains"] = len(unresolved_domains)

        self.conflicts.sort(key=lambda item: (
            item.category, item.subtype, item.object_type, item.object_key, item.field,
            item.source_refs,
        ))
        for index, conflict in enumerate(self.conflicts, 1):
            conflict.conflict_id = f"ORG-C{index:06d}"
        self.stats["conflicts"] = len(self.conflicts)
        self.stats["blocking_identity_conflicts"] = sum(
            conflict.category in {"身份冲突", "匹配冲突"} for conflict in self.conflicts)
        self.stats["auto_resolved_field_conflicts"] = sum(
            conflict.resolution_policy == "WHOIS_VALUE_RETAINED" for conflict in self.conflicts)
        self.stats["blocking_conflicts"] = sum(
            conflict.resolution_policy != "WHOIS_VALUE_RETAINED" for conflict in self.conflicts)
        self.stats["organisation_candidates"] = len(self.candidates)
        self.stats["materializable_organisation_candidates"] = sum(
            candidate["materialize"] == "是" for candidate in self.candidates)
        self.stats["unlinked_filing_entities_not_materialized"] = sum(
            candidate["status"] == "未关联旧资源（不物化）" for candidate in self.candidates)

        blocked = self.stats["blocking_conflicts"] > 0
        report = {
            "stage": "organisation-resolution",
            "stage_version": STAGE_VERSION,
            "status": "blocked" if blocked else "passed",
            "stop_reason": "机构阶段存在尚未解决的身份、匹配或备案内部冲突，按约定未进入联系人合并和资源物化。" if blocked else "",
            "rules": {
                "field_precedence": "WHOIS非空值优先；备案仅填空值；任何备案覆盖WHOIS必须另行批准。",
                "identity_conflicts": "来源优先级不用于判断是否为同一机构；拆分、合并、多候选均阻断。",
                "organisation_contacts": "organisation不填tech-c；admin-c只取IP分配备案紧急联系人。",
                "resource_contacts": "inetnum/inet6num/domain保留原admin-c和tech-c。",
                "netname": "仅保留为上下文，不作为机构身份或域名归属的自动合并键。",
                "fake_organisation": "仅在无唯一真实机构且不存在矛盾候选时，为未解析域名创建带清理标记的fake机构。",
                "filing_only_organisation": "未关联任何旧inetnum、inet6num或domain的备案实体仅保留审计证据，默认不物化为organisation。",
                "multi_ip_cells": "IP分配备案单元格中的多个IP按分号拆分后参与资源匹配；ICP多IP仅拆分保留审计。",
                "icp_data_scope": "ICP备案仅使用域名及备案主体、证件、地区、地址等法人实体信息；ICP中的IP只保留审计，联系人字段不参与person合并。",
                "reserved_resources": "预留记录只有在IP已匹配旧WHOIS时才能补充现有机构；无使用单位或旧WHOIS无对应资源时只保留审计，绝不创建新机构。",
                "filing_exclusions": "明确标记的疑似过期或残留备案记录不参与物化，但保留来源、原值和排除理由。",
                "legacy_resource_exclusions": "仅当旧WHOIS对象的类型、主键、netname、conn-i和指定descr全部精确命中已确认规则时排除；源对象及保留对象信息进入审计。",
                "ip_hierarchy": "同一IP范围同时存在省级分配容器和具体机构对象时，容器只作层级证据，不参与机构归属冲突判断。",
                "domain_normalization": "域名仅规范化大小写、尾点、URL主机名和IDN编码；保留www等真实DNS标签，不与根域合并。",
                "resource_value_preservation": "旧domain、inetnum、inet6num等资源主键落盘时必须使用源数据原值；规范化值只作匹配索引。目标格式无法原样表达时必须阻断并报告，禁止静默改写、合并或丢弃。",
            },
            "sources": self.source_manifest(),
            "stats": dict(sorted(self.stats.items())),
            "conflict_counts": dict(sorted(Counter(conflict.subtype for conflict in self.conflicts).items())),
            "blocking_conflict_counts": dict(sorted(Counter(
                conflict.subtype for conflict in self.conflicts
                if conflict.resolution_policy != "WHOIS_VALUE_RETAINED"
            ).items())),
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
            "candidates": self.candidates,
            "evidence": self.evidence,
            "unresolved_domains": unresolved_domains,
            "non_organisation_filing_records": self.non_organisation_filing_records,
            "excluded_filing_records": self.excluded_filing_records,
            "excluded_legacy_resources": self.excluded_legacy_resources,
        }
        return report

    def run(self) -> dict:
        """Resolve organisations with strict ICP -> domain registry -> IPFP priority."""
        self.load()
        self.stats.setdefault("provincial_allocation_container_matches_ignored", 0)

        def record_ref(record: FilingRecord) -> str:
            return f"{record.source_file}/{record.source_sheet}!{record.source_row}"

        def distinct(values: list[str]) -> list[str]:
            by_key: dict[str, str] = {}
            for value in values:
                if normalized_value(value):
                    by_key.setdefault(normalized_value(value), value)
            return sorted(by_key.values())

        def unique_records(records: list[FilingRecord]) -> list[FilingRecord]:
            return list({record.record_id: record for record in records}.values())

        def identity_display(records: list[FilingRecord]) -> str:
            return "\n".join(sorted({
                f"{record.name} / {record.cert_no}"
                for record in records if record.name or record.cert_no
            }))

        legacy_ranges: dict[int, list[tuple[IpRange, str, dict]]] = defaultdict(list)
        legacy_org_resources: dict[str, list[dict]] = defaultdict(list)
        for typ in ("inetnum", "inet6num"):
            for obj in self.objects[typ]:
                fields = obj["fields"]
                primary = first(fields, "inetnum" if typ == "inetnum" else "*i6")
                interval = parse_ip_range(primary)
                orgid = first(fields, "conn-i").upper()
                if interval and orgid:
                    legacy_ranges[interval.version].append((interval, orgid, obj))
                    legacy_org_resources[orgid].append(obj)

        self.filter_unmatched_reserved_records(legacy_ranges)

        legacy_org_names: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"c": set(), "e": set()})
        orgids_by_netname: dict[str, set[str]] = defaultdict(set)
        orgids_by_chinese_name: dict[str, set[str]] = defaultdict(set)
        for orgid, resources in legacy_org_resources.items():
            for obj in resources:
                chinese, english = legacy_names(obj["fields"])
                if chinese:
                    legacy_org_names[orgid]["c"].add(chinese)
                    orgids_by_chinese_name[normalized(chinese)].add(orgid)
                if english:
                    legacy_org_names[orgid]["e"].add(english)
                netname = normalized_value(first(obj["fields"], "netname"))
                if netname:
                    orgids_by_netname[netname].add(orgid)
            chinese_values = {
                normalized(value): value for value in legacy_org_names[orgid]["c"]
                if normalized(value)
            }
            if len(chinese_values) > 1:
                self.add_conflict(
                    "身份冲突", "同一旧conn-i包含多个机构名称", "organisation", orgid,
                    "c-org-name", "\n".join(sorted(chinese_values.values())), "",
                    "\n".join(
                        f"{obj['source_file']}:{obj['source_line']}" for obj in resources),
                    "同一旧 conn-i 下的旧WHOIS网段包含多个机构名称，需先确认是否拆分。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )

        legacy_domains: dict[str, list[dict]] = defaultdict(list)
        for obj in self.objects["domain"]:
            domain = canonical_domain(obj["raw_key"])
            if domain:
                legacy_domains[domain].append(obj)

        def domain_org_candidates(obj: dict) -> set[str]:
            fields = obj["fields"]
            # Preserve the established legacy-WHOIS association order:
            # a unique netname match is direct resource evidence and wins over
            # a less specific organisation-name index. Only when netname has
            # no candidate do we try the exact Chinese organisation name.
            netname_result = set(orgids_by_netname.get(
                normalized_value(first(fields, "netname")), set()))
            if netname_result:
                return netname_result
            chinese_name = normalized(first(fields, "c-orgname"))
            if chinese_name:
                return set(orgids_by_chinese_name.get(chinese_name, set()))
            return set()

        icp_by_domain: dict[str, list[FilingRecord]] = defaultdict(list)
        registry_by_domain: dict[str, list[FilingRecord]] = defaultdict(list)
        for record in self.filing_records:
            if record.source_kind not in {"icp", "domain-registration"}:
                continue
            target = icp_by_domain if record.source_kind == "icp" else registry_by_domain
            for domain in record.domains:
                target[domain].append(record)

        # cert number -> accumulated old resources and the exact filing rows
        identity_bundles: dict[str, dict[str, object]] = defaultdict(lambda: {
            "domains": set(), "orgids": set(), "records": {}, "sources": set(),
        })
        domain_resolution: dict[str, tuple[str, str, list[FilingRecord]]] = {}
        domain_linked_orgids: dict[str, set[str]] = defaultdict(set)
        higher_priority_blocked_orgids: set[str] = set()

        for domain, objects in sorted(legacy_domains.items()):
            if len(objects) > 1:
                self.add_conflict(
                    "匹配冲突", "旧WHOIS存在重复域名对象", "domain", domain, "domain",
                    "\n".join(
                        f"{obj['source_file']}:{obj['source_line']}" for obj in objects),
                    "", "",
                    "同一完整规范域名对应多个旧WHOIS对象，无法唯一关联机构。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )
                continue
            obj = objects[0]
            org_candidates = domain_org_candidates(obj)
            if len(org_candidates) > 1:
                self.add_conflict(
                    "匹配冲突", "旧域名无法唯一关联旧机构", "domain", domain,
                    "organisation", "\n".join(sorted(org_candidates)), "",
                    f"{obj['source_file']}:{obj['source_line']}",
                    "旧域名的netname或中文机构名命中多个旧conn-i，域名证据不能传播到机构。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )
            source_kind, status, selected = select_domain_identity(
                unique_records(icp_by_domain.get(domain, [])),
                unique_records(registry_by_domain.get(domain, [])),
            )
            source_label = "ICP备案" if source_kind == "icp" else "域名备案"
            if status == "missing":
                self.stats["legacy_domain_identity_unmatched"] += 1
                continue
            if status != "unique":
                subtype = (
                    f"同一域名在{source_label}对应多个证件号"
                    if status == "multiple-certificates"
                    else f"同一域名在{source_label}对应多个法人名称"
                )
                self.add_conflict(
                    "备案内部冲突", subtype, "domain", domain, "legal-identity",
                    first(obj["fields"], "c-orgname") or first(obj["fields"], "orgname"),
                    identity_display(selected),
                    "\n".join(record_ref(record) for record in selected),
                    f"{source_label}是当前优先级来源，但完整域名命中了多个法人身份；"
                    "不降级到后续来源，也不自动选值。",
                    "FILING_INTERNAL_REVIEW_REQUIRED",
                )
                if len(org_candidates) == 1:
                    higher_priority_blocked_orgids.update(org_candidates)
                continue

            selected = unique_records(selected)
            cert_key = filing_identity(selected[0])[0]
            domain_resolution[domain] = (cert_key, source_kind, selected)
            bundle = identity_bundles[cert_key]
            bundle["domains"].add(domain)
            bundle["sources"].add(source_kind)
            bundle["records"].update({record.record_id: record for record in selected})
            if len(org_candidates) == 1:
                domain_linked_orgids[next(iter(org_candidates))].add(cert_key)
            self.evidence.append({
                "evidence_type": "完整域名精确匹配",
                "entity_id": stable_id("FE", f"cert:{cert_key}"),
                "legacy_orgid": next(iter(org_candidates)) if len(org_candidates) == 1 else "",
                "resource": obj["raw_key"],
                "whois_value": first(obj["fields"], "c-orgname") or first(obj["fields"], "orgname"),
                "filing_value": identity_display(selected),
                "source_ref": "\n".join(record_ref(record) for record in selected),
                "result": f"采用{source_label}；原WHOIS域名保持不变",
            })
            whois_name = first(obj["fields"], "c-orgname")
            filing_names = distinct([record.name for record in selected])
            if whois_name and filing_names and normalized(whois_name) not in {
                normalized(value) for value in filing_names
            }:
                self.add_conflict(
                    "字段冲突", f"WHOIS与{source_label}法人名称不一致", "domain", domain,
                    "c-org-name", whois_name, "\n".join(filing_names),
                    "\n".join(record_ref(record) for record in selected),
                    "域名精确匹配成立，但机构名称不同；保留WHOIS名称，备案只提供法人身份。",
                    "WHOIS_VALUE_RETAINED", whois_name,
                )

        # A conn-i can receive domain evidence only when all uniquely related
        # domains agree on one certificate number.
        org_identity_key: dict[str, str] = {}
        for orgid, cert_keys in sorted(domain_linked_orgids.items()):
            if len(cert_keys) == 1:
                cert_key = next(iter(cert_keys))
                org_identity_key[orgid] = cert_key
                identity_bundles[cert_key]["orgids"].add(orgid)
            else:
                higher_priority_blocked_orgids.add(orgid)
                related_domains = sorted(
                    domain for domain, (cert_key, _, _) in domain_resolution.items()
                    if cert_key in cert_keys and orgid in domain_org_candidates(legacy_domains[domain][0])
                )
                self.add_conflict(
                    "身份冲突", "一个旧conn-i通过域名命中多个法人身份", "organisation",
                    orgid, "legal-identity", "\n".join(sorted(cert_keys)),
                    "\n".join(related_domains),
                    "\n".join(
                        f"{legacy_domains[domain][0]['source_file']}:"
                        f"{legacy_domains[domain][0]['source_line']}" for domain in related_domains),
                    "多个高优先级域名证据指向不同证件号；不使用IPFP覆盖或消解。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )

        # ICP IP is not parsed as ownership evidence. Preserve it for audit only.
        for record in self.filing_records:
            if record.source_kind != "icp" or not record.ip_ranges:
                continue
            self.stats["icp_ip_ranges_audit_only"] += len(record.ip_ranges)
            for interval in record.ip_ranges:
                self.evidence.append({
                    "evidence_type": "ICP IP原始记录（仅审计）",
                    "entity_id": "", "legacy_orgid": "", "resource": interval.display(),
                    "whois_value": "", "filing_value": record.name,
                    "source_ref": record_ref(record),
                    "result": "不参与机构匹配、冲突判断或资源归属",
                })

        ip_records_by_orgid: dict[str, dict[str, FilingRecord]] = defaultdict(dict)
        for record in self.filing_records:
            if record.source_kind != "ip-allocation":
                continue
            for record_range in record.ip_ranges:
                structural_matches = most_specific_matches(
                    record_range, legacy_ranges[record_range.version], prefer_institutional=False)
                matches = prefer_institutional_matches(structural_matches)
                ignored = [item for item in structural_matches if item not in matches]
                if ignored:
                    self.stats["provincial_allocation_container_matches_ignored"] += len(ignored)
                orgids = sorted({orgid for _, orgid, _ in matches})
                if len(orgids) > 1:
                    self.add_conflict(
                        "匹配冲突", "IPFP区间命中多个旧机构", "filing-organisation",
                        record.record_id, "ip-range", "\n".join(orgids),
                        f"{record_range.display()}\n{record.name}", record_ref(record),
                        "一个IPFP区间命中多个具体旧conn-i，不能自动选择。",
                        "MANUAL_IDENTITY_DECISION_REQUIRED",
                    )
                    continue
                if not orgids:
                    self.stats["filing_ip_unmatched"] += 1
                    continue
                orgid = orgids[0]
                lower_priority = orgid in org_identity_key or orgid in higher_priority_blocked_orgids
                self.evidence.append({
                    "evidence_type": (
                        "IP区间完全匹配" if any(
                            interval == record_range for interval, _, _ in matches)
                        else "IP区间唯一包含"),
                    "entity_id": stable_id("FE", f"ip-record:{record.record_id}"),
                    "legacy_orgid": orgid,
                    "resource": record_range.display(),
                    "whois_value": " | ".join(sorted({obj["raw_key"] for _, _, obj in matches})),
                    "filing_value": f"{record.name} / {record.cert_no}",
                    "source_ref": record_ref(record),
                    "result": (
                        "低优先级证据，仅审计；机构已由域名来源确定或阻断"
                        if lower_priority else "进入第三级IPFP法人身份候选"),
                })
                if not lower_priority:
                    ip_records_by_orgid[orgid][record.record_id] = record

        for orgid, record_map in sorted(ip_records_by_orgid.items()):
            status, selected = resolve_filing_identity(list(record_map.values()))
            if status == "missing":
                continue
            if status != "unique":
                subtype = (
                    "一个旧conn-i在IPFP命中多个证件号"
                    if status == "multiple-certificates"
                    else "一个旧conn-i在IPFP命中多个法人名称"
                )
                self.add_conflict(
                    "身份冲突", subtype, "organisation", orgid, "legal-identity",
                    "\n".join(sorted(legacy_org_names[orgid]["c"])),
                    identity_display(selected),
                    "\n".join(record_ref(record) for record in selected),
                    "该旧机构的多个IP资源对应不同法人身份，不能自动选择。",
                    "MANUAL_IDENTITY_DECISION_REQUIRED",
                )
                higher_priority_blocked_orgids.add(orgid)
                continue
            selected = unique_records(selected)
            cert_key = filing_identity(selected[0])[0]
            org_identity_key[orgid] = cert_key
            bundle = identity_bundles[cert_key]
            bundle["orgids"].add(orgid)
            bundle["sources"].add("ip-allocation")
            bundle["records"].update({record.record_id: record for record in selected})
            filing_names = distinct([record.name for record in selected])
            whois_names = sorted(legacy_org_names[orgid]["c"])
            if whois_names and filing_names and not {
                normalized(value) for value in whois_names
            }.intersection({normalized(value) for value in filing_names}):
                self.add_conflict(
                    "字段冲突", "WHOIS与IPFP法人名称不一致", "organisation", orgid,
                    "c-org-name", "\n".join(whois_names), "\n".join(filing_names),
                    "\n".join(record_ref(record) for record in selected),
                    "IP资源唯一关联成立，但机构名称不同；保留WHOIS名称。",
                    "WHOIS_VALUE_RETAINED", "\n".join(whois_names),
                )

        self.stats["filing_entities"] = len(identity_bundles)
        identity_name_conflicts: set[str] = set()
        for cert_key, bundle in sorted(identity_bundles.items()):
            records = list(bundle["records"].values())
            legal_names = distinct([record.name for record in records])
            if len({normalized(value) for value in legal_names}) > 1:
                identity_name_conflicts.add(cert_key)
                self.add_conflict(
                    "备案内部冲突", "同一证件号跨来源对应多个法人名称",
                    "filing-organisation", stable_id("FE", f"cert:{cert_key}"),
                    "legal-name", cert_key, "\n".join(legal_names),
                    "\n".join(record_ref(record) for record in records),
                    "同一证件号码在实际采用的备案证据中出现多个法人名称，需人工确认。",
                    "FILING_INTERNAL_REVIEW_REQUIRED",
                )

            orgids = sorted(bundle["orgids"])
            domains = sorted(bundle["domains"])
            candidate_id = (
                orgids[0] if len(orgids) == 1 and cert_key not in identity_name_conflicts
                else stable_id("NEWORG", f"cert:{cert_key}"))
            if cert_key in identity_name_conflicts:
                status = "法人名称冲突待处理"
            elif len(orgids) > 1:
                status = "同一证件号合并多个旧conn-i"
            elif orgids and domains:
                status = "域名备案优先补全旧机构"
            elif orgids:
                status = "IPFP补全旧机构"
            else:
                status = "域名备案确认的新机构"

            domain_objects = [legacy_domains[domain][0] for domain in domains]
            whois_c_names = {
                name for orgid in orgids for name in legacy_org_names[orgid]["c"]
            }
            whois_c_names.update(
                first(obj["fields"], "c-orgname") for obj in domain_objects
                if first(obj["fields"], "c-orgname"))
            whois_e_names = {
                name for orgid in orgids for name in legacy_org_names[orgid]["e"]
            }
            whois_e_names.update(
                first(obj["fields"], "orgname") for obj in domain_objects
                if first(obj["fields"], "orgname"))
            emergency = distinct([
                " / ".join(filter(None, (
                    record.emergency_name, record.emergency_phone, record.emergency_email)))
                for record in records
                if record.source_kind == "ip-allocation" and record.emergency_name
            ])
            self.candidates.append({
                "candidate_id": candidate_id,
                "filing_entity_id": stable_id("FE", f"cert:{cert_key}"),
                "legacy_orgids": " | ".join(orgids),
                "status": status,
                "materialize": "是",
                "whois_c_org_names": " | ".join(sorted(whois_c_names)),
                "whois_org_names": " | ".join(sorted(whois_e_names)),
                "filing_names": " | ".join(legal_names),
                "org_types": " | ".join(distinct([record.org_type for record in records])),
                "cert_types": " | ".join(distinct([record.cert_type for record in records])),
                "cert_numbers": " | ".join(distinct([record.cert_no for record in records])),
                "regions": " | ".join(distinct([record.region for record in records])),
                "addresses": " | ".join(distinct([
                    value for record in records for value in record.addresses])),
                "ip_emergency_admin_candidates": " | ".join(emergency),
                "matched_domains": " | ".join(domains),
                "source_record_count": len(records),
                "source_refs": " | ".join(sorted(record_ref(record) for record in records)),
            })

        resolved_orgids = {
            orgid for bundle in identity_bundles.values() for orgid in bundle["orgids"]
        }
        for orgid in sorted(set(legacy_org_resources) - resolved_orgids):
            blocked_org = orgid in higher_priority_blocked_orgids
            self.candidates.append({
                "candidate_id": orgid, "filing_entity_id": "", "legacy_orgids": orgid,
                "status": "身份冲突待处理" if blocked_org else "仅WHOIS机构（法人身份未补全）",
                "materialize": "是",
                "whois_c_org_names": " | ".join(sorted(legacy_org_names[orgid]["c"])),
                "whois_org_names": " | ".join(sorted(legacy_org_names[orgid]["e"])),
                "filing_names": "", "org_types": "", "cert_types": "",
                "cert_numbers": "", "regions": "", "addresses": "",
                "ip_emergency_admin_candidates": "", "matched_domains": "",
                "source_record_count": 0,
                "source_refs": " | ".join(
                    f"{obj['source_file']}:{obj['source_line']}"
                    for obj in legacy_org_resources[orgid]),
            })

        resolved_domains = set(domain_resolution)
        unresolved_domains = []
        for domain, objects in sorted(legacy_domains.items()):
            if domain in resolved_domains:
                continue
            for obj in objects:
                unresolved_domains.append({
                    "domain": obj["raw_key"],
                    "netname": first(obj["fields"], "netname"),
                    "orgname": first(obj["fields"], "orgname"),
                    "c_orgname": first(obj["fields"], "c-orgname"),
                    "source_ref": f"{obj['source_file']}:{obj['source_line']}",
                    "planned_action": (
                        "备案身份冲突，处理后重跑" if domain in icp_by_domain or domain in registry_by_domain
                        else "无备案身份；后续创建带清理标记的fake机构"),
                })
        self.stats["unresolved_legacy_domains"] = len(unresolved_domains)
        self.stats["domain_identity_from_icp"] = sum(
            source == "icp" for _, source, _ in domain_resolution.values())
        self.stats["domain_identity_from_domain_registry"] = sum(
            source == "domain-registration" for _, source, _ in domain_resolution.values())
        self.stats["organisation_identity_from_ipfp"] = sum(
            1 for bundle in identity_bundles.values()
            if "ip-allocation" in bundle["sources"])

        self.conflicts.sort(key=lambda item: (
            item.category, item.subtype, item.object_type, item.object_key,
            item.field, item.source_refs,
        ))
        for index, conflict in enumerate(self.conflicts, 1):
            conflict.conflict_id = f"ORG-C{index:06d}"
        self.stats["conflicts"] = len(self.conflicts)
        self.stats["blocking_identity_conflicts"] = sum(
            conflict.category in {"身份冲突", "匹配冲突"} for conflict in self.conflicts)
        self.stats["auto_resolved_field_conflicts"] = sum(
            conflict.resolution_policy == "WHOIS_VALUE_RETAINED" for conflict in self.conflicts)
        self.stats["blocking_conflicts"] = sum(
            conflict.resolution_policy != "WHOIS_VALUE_RETAINED" for conflict in self.conflicts)
        self.stats["organisation_candidates"] = len(self.candidates)
        self.stats["materializable_organisation_candidates"] = sum(
            candidate["materialize"] == "是" for candidate in self.candidates)
        self.stats["unlinked_filing_entities_not_materialized"] = 0

        blocked = self.stats["blocking_conflicts"] > 0
        return {
            "stage": "organisation-resolution",
            "stage_version": STAGE_VERSION,
            "status": "blocked" if blocked else "passed",
            "stop_reason": (
                "机构阶段存在尚未解决的身份、匹配或备案内部冲突，按约定未进入联系人合并和资源物化。"
                if blocked else ""),
            "rules": {
                "legal_identity_priority": (
                    "1) icp备案数据.xlsx仅按完整域名精确匹配法人名称和证件号；"
                    "2) 未补全目标按完整域名查询2026-09-21_full.xlsx；"
                    "3) 仍未补全的旧conn-i才按IP查询IPFP。"),
                "lower_priority": "高优先级已唯一确定或发生冲突时，低优先级来源只保留审计，不覆盖、不消解。",
                "field_precedence": "WHOIS非空值优先；备案仅补法人身份空值；任何备案覆盖WHOIS必须另行批准。",
                "certificate_identity": "证件号码是跨备案来源的身份键；证件类型仅作为字段保留，因为不同来源编码体系不同。",
                "organisation_contacts": "organisation不填tech-c；admin-c只取实际采用的IPFP紧急联系人。",
                "resource_contacts": "inetnum/inet6num/domain保留原admin-c和tech-c。",
                "icp_data_scope": "ICP只使用完整域名、法人名称、证件类型和证件号；ICP IP和联系人不参与匹配。",
                "domain_registry_scope": "域名备案使用name/punycode匹配，法人取registrantName，空时回退registrantOrg。",
                "ipfp_scope": "IPFP只对前两级仍未取得法人身份的旧conn-i提供最后一级补充。",
                "domain_normalization": "仅统一大小写、尾点、URL主机名和IDN；不删除www或任何DNS标签。",
                "resource_value_preservation": "旧domain、inetnum、inet6num主键始终保留源值，规范值只作索引。",
                "same_identity_multiple_conn_i": "同一证件号命中多个旧conn-i视为同一机构的多个资源组，不自动报冲突。",
                "fake_organisation": "无唯一真实机构的旧域名后续创建带清理标记的fake机构。",
                "filing_only_organisation": "没有关联任何旧资源的备案记录不创建organisation。",
                "reserved_resources": "IPFP预留记录仅在命中旧WHOIS资源时可补充，不创建孤立机构。",
                "legacy_resource_exclusions": "已确认的旧资源特殊排除规则继续生效并保留审计。",
            },
            "sources": self.source_manifest(),
            "stats": dict(sorted(self.stats.items())),
            "conflict_counts": dict(sorted(Counter(
                conflict.subtype for conflict in self.conflicts).items())),
            "blocking_conflict_counts": dict(sorted(Counter(
                conflict.subtype for conflict in self.conflicts
                if conflict.resolution_policy != "WHOIS_VALUE_RETAINED").items())),
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
            "candidates": self.candidates,
            "evidence": self.evidence,
            "unresolved_domains": unresolved_domains,
            "non_organisation_filing_records": self.non_organisation_filing_records,
            "excluded_filing_records": self.excluded_filing_records,
            "excluded_legacy_resources": self.excluded_legacy_resources,
        }

    def source_manifest(self) -> list[dict[str, object]]:
        paths = [self.rsdb_dir / filename for filename, _ in REQUIRED_RPSL.values()]
        paths.extend([self.icp_workbook, self.domain_workbook, self.ip_workbook])
        if self.filing_exclusions is not None:
            paths.append(self.filing_exclusions)
        result = []
        for path in paths:
            result.append({
                "path": str(path.resolve()),
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "included": True,
                "role": (
                    "legacy-whois" if path.parent == self.rsdb_dir
                    else "migration-decision" if self.filing_exclusions is not None and path == self.filing_exclusions
                    else "filing-evidence"
                ),
            })
        for path in sorted(self.icp_workbook.parent.glob("*.xlsx")):
            if path.resolve() not in {self.icp_workbook.resolve(), self.ip_workbook.resolve()}:
                result.append({
                    "path": str(path.resolve()),
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "included": False,
                    "role": "unclassified-workbook-not-used",
                })
        return result


def write_tsv(path: Path, rows: list[dict], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(output_dir: Path, report: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stage1-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "source-manifest.json").write_text(
        json.dumps(report["sources"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_tsv(output_dir / "organisation-conflicts.tsv", report["conflicts"], [
        "conflict_id", "category", "subtype", "object_type", "object_key", "field",
        "whois_value", "filing_value", "source_refs", "explanation", "resolution_policy", "proposed_value",
    ])
    write_tsv(output_dir / "organisation-candidates.tsv", report["candidates"], [
        "candidate_id", "filing_entity_id", "legacy_orgids", "status", "materialize", "whois_c_org_names",
        "whois_org_names", "filing_names", "org_types", "cert_types", "cert_numbers", "regions",
        "addresses", "ip_emergency_admin_candidates", "matched_domains", "source_record_count", "source_refs",
    ])
    write_tsv(output_dir / "organisation-evidence.tsv", report["evidence"], [
        "evidence_type", "entity_id", "legacy_orgid", "resource", "whois_value", "filing_value",
        "source_ref", "result",
    ])
    write_tsv(output_dir / "unresolved-domains.tsv", report["unresolved_domains"], [
        "domain", "netname", "orgname", "c_orgname", "source_ref", "planned_action",
    ])
    audit_headers = [
        "classification", "record_id", "source_kind", "organisation_name", "cert_no",
        "raw_ip_start", "raw_ip_end", "parsed_ip_ranges", "source_ref", "action", "reason",
    ]
    write_tsv(
        output_dir / "non-organisation-filing-records.tsv",
        report["non_organisation_filing_records"], audit_headers,
    )
    write_tsv(
        output_dir / "excluded-filing-records.tsv",
        report["excluded_filing_records"], audit_headers,
    )
    write_tsv(
        output_dir / "excluded-legacy-resources.tsv",
        report["excluded_legacy_resources"], [
            "rule_id", "object_type", "primary_key", "netname", "conn_i", "descr",
            "source_ref", "action", "reason", "kept_resource",
        ],
    )
    decision_rows = [{
        "conflict_id": item["conflict_id"],
        "decision": "",
        "selected_orgid": "",
        "selected_value": "",
        "reviewer_note": "",
    } for item in report["conflicts"] if item["resolution_policy"] != "WHOIS_VALUE_RETAINED"]
    write_tsv(output_dir / "organisation-decisions-template.tsv", decision_rows, [
        "conflict_id", "decision", "selected_orgid", "selected_value", "reviewer_note",
    ])
    summary = [
        "# 联合迁移：机构解析阶段",
        "",
        f"- 状态：**{report['status']}**",
        f"- 阶段版本：`{report['stage_version']}`",
        f"- 冲突总数：{report['stats'].get('conflicts', 0)}",
        f"- 待人工决策冲突：{report['stats'].get('blocking_conflicts', 0)}",
        f"- 身份/匹配冲突：{report['stats'].get('blocking_identity_conflicts', 0)}",
        f"- 已按WHOIS优先自动处理的字段冲突：{report['stats'].get('auto_resolved_field_conflicts', 0)}",
        f"- 机构候选：{report['stats'].get('organisation_candidates', 0)}",
        f"- 最终可物化机构候选：{report['stats'].get('materializable_organisation_candidates', 0)}",
        f"- 未关联旧资源且不物化的备案实体：{report['stats'].get('unlinked_filing_entities_not_materialized', 0)}",
        f"- 不属于机构对象的备案资源记录：{report['stats'].get('non_organisation_filing_records', 0)}",
        f"- 按迁移规则排除的疑似残留记录：{report['stats'].get('filing_records_excluded_by_rule', 0)}",
        f"- 按确认规则排除的旧WHOIS资源：{report['stats'].get('legacy_resources_excluded_by_rule', 0)}",
        f"- 含多个IP的ICP记录：{report['stats'].get('filing_icp_multi_ip_rows', 0)}",
        f"- 仅保留审计且不参与机构匹配的ICP IP区间：{report['stats'].get('icp_ip_ranges_audit_only', 0)}",
        f"- ICP完整域名补全：{report['stats'].get('domain_identity_from_icp', 0)}",
        f"- 独立域名备案文件补全：{report['stats'].get('domain_identity_from_domain_registry', 0)}",
        f"- IPFP最后一级补全：{report['stats'].get('organisation_identity_from_ipfp', 0)}",
        f"- 未解析旧域名：{report['stats'].get('unresolved_legacy_domains', 0)}",
        "",
        report["stop_reason"] or "本阶段通过，可进入联系人解析阶段。",
        "",
        "本阶段不会生成最终RPSL dump，也不会改写任何源文件。",
    ]
    (output_dir / "stage1-summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rsdb_dir", type=Path)
    parser.add_argument("--icp-workbook", type=Path, required=True)
    parser.add_argument("--domain-workbook", type=Path, required=True)
    parser.add_argument("--ip-workbook", type=Path, required=True)
    parser.add_argument("--filing-exclusions", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"ERROR: 输出目录必须为空: {output}")
    stage = Stage1(
        args.rsdb_dir.resolve(), args.icp_workbook.resolve(), args.ip_workbook.resolve(), output,
        args.filing_exclusions.resolve() if args.filing_exclusions else None,
        args.domain_workbook.resolve(),
    )
    try:
        report = stage.run()
        write_outputs(output, report)
    except Exception as error:
        output.mkdir(parents=True, exist_ok=True)
        (output / "stage1-error.txt").write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
        raise
    print(json.dumps({
        "status": report["status"],
        "output_dir": str(output),
        "conflicts": report["stats"].get("conflicts", 0),
        "candidates": report["stats"].get("organisation_candidates", 0),
    }, ensure_ascii=False))
    if report["status"] == "blocked":
        sys.exit(2)


if __name__ == "__main__":
    main()
