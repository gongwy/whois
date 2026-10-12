#!/usr/bin/env python3
"""Explicit, auditable migration exceptions confirmed by the data owner."""

from __future__ import annotations

from typing import Mapping, Sequence


LEGACY_RESOURCE_EXCLUSIONS = (
    {
        "rule_id": "LEGACY-RESOURCE-EXCLUDE-001",
        "object_type": "inetnum",
        "primary_key": "219.243.192.0 - 219.243.199.255",
        "netname": "SIGCOMM-CN",
        "conn_i": "BJ003772",
        "descr": "CERNET网络中心(会议地址)",
        "action": "排除已确认的重复旧WHOIS资源",
        "reason": (
            "用户确认该旧记录为重复对象；同网段只保留 CCONFERENCE-CN / "
            "BJ000000 / CERNET会议活动专用地址。"
        ),
        "kept_resource": (
            "inetnum: 219.243.192.0 - 219.243.199.255; "
            "netname: CCONFERENCE-CN; conn-i: BJ000000; "
            "descr: CERNET会议活动专用地址"
        ),
    },
)


def _first(fields: Mapping[str, Sequence[str]], name: str) -> str:
    values = fields.get(name, ())
    return str(values[0]).strip() if values else ""


def legacy_resource_exclusion_for(
    object_type: str,
    primary_key: str,
    fields: Mapping[str, Sequence[str]],
) -> dict[str, str] | None:
    """Return an exclusion only when every discriminator matches exactly."""
    for rule in LEGACY_RESOURCE_EXCLUSIONS:
        if object_type != rule["object_type"]:
            continue
        if primary_key.strip() != rule["primary_key"]:
            continue
        if _first(fields, "netname") != rule["netname"]:
            continue
        if _first(fields, "conn-i") != rule["conn_i"]:
            continue
        if rule["descr"] not in [str(value).strip() for value in fields.get("descr", ())]:
            continue
        return dict(rule)
    return None


def legacy_resource_exclusion_audit(
    rule: Mapping[str, str],
    *,
    source_file: str,
    source_line: int | None,
) -> dict[str, str]:
    return {
        "rule_id": rule["rule_id"],
        "object_type": rule["object_type"],
        "primary_key": rule["primary_key"],
        "netname": rule["netname"],
        "conn_i": rule["conn_i"],
        "descr": rule["descr"],
        "source_ref": f"{source_file}:{source_line}" if source_line else source_file,
        "action": rule["action"],
        "reason": rule["reason"],
        "kept_resource": rule["kept_resource"],
    }
