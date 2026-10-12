#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

async function loadArtifactTool() {
  if (process.env.ARTIFACT_TOOL_MODULE) {
    return import(pathToFileURL(path.resolve(process.env.ARTIFACT_TOOL_MODULE)).href);
  }
  try {
    return await import("@oai/artifact-tool");
  } catch (error) {
    throw new Error(
      "Unable to load @oai/artifact-tool. Install it or set ARTIFACT_TOOL_MODULE to artifact_tool.mjs.",
      { cause: error },
    );
  }
}

const { SpreadsheetFile, Workbook } = await loadArtifactTool();

const [reportPath, outputPath, previewDir] = process.argv.slice(2);
if (!reportPath || !outputPath) {
  throw new Error("Usage: build_joint_stage1_report.mjs <stage1-report.json> <output.xlsx> [preview-dir]");
}

const report = JSON.parse(await fs.readFile(reportPath, "utf8"));
const workbook = Workbook.create();
const font = "Arial";

const colors = {
  navy: "#174A6E",
  blue: "#2F75B5",
  paleBlue: "#DCEAF5",
  paleRed: "#FCE4E4",
  paleAmber: "#FFF1CC",
  paleGreen: "#E4F1E8",
  text: "#24313D",
  muted: "#5F6B76",
  border: "#D8DEE4",
};

function cellValue(value, limit = 30000) {
  if (value === null || value === undefined) return "";
  const output = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  return output.length <= limit ? output : `${output.slice(0, limit - 20)}\n[内容已截断]`;
}

function columnName(index) {
  let value = index + 1;
  let output = "";
  while (value) {
    const remainder = (value - 1) % 26;
    output = String.fromCharCode(65 + remainder) + output;
    value = Math.floor((value - 1) / 26);
  }
  return output;
}

function styleSheet(sheet, title, headers, rows, options = {}) {
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: font, size: 14, bold: true, color: colors.text };
  const lastColumn = columnName(headers.length - 1);
  sheet.getRange(`A3:${lastColumn}3`).format.borders = {
    bottom: { style: "thin", color: colors.blue },
  };
  sheet.getRangeByIndexes(3, 0, 1, headers.length).values = [headers];
  const header = sheet.getRangeByIndexes(3, 0, 1, headers.length);
  header.format = {
    fill: colors.navy,
    font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  header.format.rowHeight = 30;
  if (rows.length) {
    const matrix = rows.map((row) => headers.map((headerName) => cellValue(row[headerName])));
    const body = sheet.getRangeByIndexes(4, 0, matrix.length, headers.length);
    body.values = matrix;
    body.format.font = { name: font, size: 10, color: colors.text };
    body.format.verticalAlignment = "top";
    body.format.wrapText = Boolean(options.wrap);
    body.format.borders = {
      insideHorizontal: { style: "thin", color: colors.border },
    };
    body.format.rowHeight = options.rowHeight || 24;
  }
  sheet.freezePanes.freezeRows(4);
  if (options.freezeColumns) sheet.freezePanes.freezeColumns(options.freezeColumns);
  const widths = options.widths || [];
  headers.forEach((_, index) => {
    sheet.getRange(`${columnName(index)}:${columnName(index)}`).format.columnWidth = widths[index] || 18;
  });
  const tableEnd = 4 + Math.max(rows.length, 1);
  const table = sheet.tables.add(`A4:${lastColumn}${tableEnd}`, true, options.tableName);
  table.style = "TableStyleMedium2";
  table.showBandedColumns = false;
  table.showFilterButton = true;
  return sheet;
}

const overview = workbook.worksheets.add("概览");
overview.showGridLines = false;
overview.tabColor = colors.navy;
overview.getRange("A2").values = [["旧WHOIS与备案数据联合迁移：机构法人身份阶段"]];
overview.getRange("A2").format.font = { name: font, size: 14, bold: true, color: colors.text };
overview.getRange("A3:H3").format.borders = { bottom: { style: "thin", color: colors.blue } };
overview.getRange("A5:B5").values = [["状态", report.status === "blocked" ? "已阻断" : "已通过"]];
overview.getRange("A5:B5").format = {
  fill: report.status === "blocked" ? colors.paleRed : colors.paleGreen,
  font: { name: font, size: 11, bold: true, color: colors.text },
  verticalAlignment: "center",
};
overview.getRange("A5:B5").format.rowHeight = 28;
overview.getRange("A7:B7").values = [["指标", "数量"]];
overview.getRange("A7:B7").format = {
  fill: colors.navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
const metrics = [
  ["冲突总数", report.stats.conflicts],
  ["待人工决策冲突", report.stats.blocking_conflicts],
  ["身份/匹配冲突", report.stats.blocking_identity_conflicts],
  ["字段冲突（已按WHOIS优先处理）", report.stats.auto_resolved_field_conflicts],
  ["机构候选", report.stats.organisation_candidates],
  ["可物化机构候选", report.stats.materializable_organisation_candidates],
  ["未关联旧资源且不物化", report.stats.unlinked_filing_entities_not_materialized],
  ["非机构备案资源记录", report.stats.non_organisation_filing_records],
  ["预留且无使用单位", report.stats.reserved_records_excluded_from_org_extraction],
  ["具名预留且匹配旧WHOIS", report.stats.reserved_named_records_with_whois_match],
  ["具名预留但旧WHOIS无记录", report.stats.reserved_named_records_without_whois_match],
  ["按规则排除的疑似残留记录", report.stats.filing_records_excluded_by_rule],
  ["按规则排除的旧WHOIS资源", report.stats.legacy_resources_excluded_by_rule],
  ["含多个IP的ICP记录", report.stats.filing_icp_multi_ip_rows],
  ["ICP IP区间（仅审计）", report.stats.icp_ip_ranges_audit_only],
  ["已排除的省级分配容器匹配", report.stats.provincial_allocation_container_matches_ignored],
  ["备案机构实体", report.stats.filing_entities],
  ["ICP完整域名补全", report.stats.domain_identity_from_icp],
  ["域名备案文件补全", report.stats.domain_identity_from_domain_registry],
  ["IPFP最后一级补全", report.stats.organisation_identity_from_ipfp],
  ["未解析旧域名", report.stats.unresolved_legacy_domains],
  ["未匹配IP分配备案区间", report.stats.filing_ip_unmatched],
];
overview.getRange(`A8:B${7 + metrics.length}`).values = metrics;
overview.getRange(`A8:B${7 + metrics.length}`).format.font = { name: font, size: 10, color: colors.text };
overview.getRange(`B8:B${7 + metrics.length}`).format.numberFormat = "#,##0";
overview.getRange(`A7:B${7 + metrics.length}`).format.borders = {
  insideHorizontal: { style: "thin", color: colors.border },
};

overview.getRange("D5:E5").values = [["冲突类型", "数量"]];
overview.getRange("D5:E5").format = {
  fill: colors.navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
const conflictCounts = Object.entries(report.conflict_counts).map(([name, count]) => [name, count]);
overview.getRangeByIndexes(5, 3, conflictCounts.length, 2).values = conflictCounts;
overview.getRangeByIndexes(5, 3, conflictCounts.length, 2).format.font = { name: font, size: 10, color: colors.text };
overview.getRangeByIndexes(5, 3, conflictCounts.length, 2).format.borders = {
  insideHorizontal: { style: "thin", color: colors.border },
};
overview.getRange(`E6:E${5 + conflictCounts.length}`).format.numberFormat = "#,##0";

overview.getRange("G5:H5").values = [["关键规则", "执行方式"]];
overview.getRange("G5:H5").format = {
  fill: colors.navy,
  font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
};
const ruleRows = Object.entries(report.rules).map(([key, value]) => [key, value]);
overview.getRangeByIndexes(5, 6, ruleRows.length, 2).values = ruleRows;
overview.getRangeByIndexes(5, 6, ruleRows.length, 2).format = {
  font: { name: font, size: 10, color: colors.text },
  verticalAlignment: "top",
  wrapText: true,
};
overview.getRangeByIndexes(5, 6, ruleRows.length, 2).format.borders = {
  insideHorizontal: { style: "thin", color: colors.border },
};
const stopRow = Math.max(7 + metrics.length, 5 + conflictCounts.length, 5 + ruleRows.length) + 2;
overview.getRange(`A${stopRow}:H${stopRow}`).values = [[report.stop_reason || "机构阶段通过，可进入联系人阶段。"]];
overview.getRange(`A${stopRow}:H${stopRow}`).merge();
overview.getRange(`A${stopRow}`).format = {
  fill: report.status === "blocked" ? colors.paleAmber : colors.paleGreen,
  font: { name: font, size: 10, bold: true, color: colors.text },
  wrapText: true,
  verticalAlignment: "center",
};
overview.getRange(`A${stopRow}:H${stopRow}`).format.rowHeight = 36;
overview.getRange("A:A").format.columnWidth = 30;
overview.getRange("B:B").format.columnWidth = 14;
overview.getRange("C:C").format.columnWidth = 3;
overview.getRange("D:D").format.columnWidth = 38;
overview.getRange("E:E").format.columnWidth = 12;
overview.getRange("F:F").format.columnWidth = 3;
overview.getRange("G:G").format.columnWidth = 26;
overview.getRange("H:H").format.columnWidth = 54;

const conflictHeaders = [
  "conflict_id", "category", "subtype", "object_type", "object_key", "field",
  "whois_value", "filing_value", "source_refs", "explanation", "resolution_policy", "proposed_value",
];
const conflicts = styleSheet(workbook.worksheets.add("冲突明细"), "机构阶段冲突明细", conflictHeaders, report.conflicts, {
  wrap: true,
  rowHeight: 54,
  freezeColumns: 3,
  tableName: "OrganisationConflicts",
  widths: [15, 14, 30, 18, 22, 16, 42, 42, 36, 48, 30, 36],
});
conflicts.tabColor = "#C94C4C";
conflicts.getRange(`A5:A${4 + report.conflicts.length}`).format.font = { name: font, size: 10, bold: true, color: colors.blue };

const candidateHeaders = [
  "candidate_id", "filing_entity_id", "legacy_orgids", "status", "materialize", "whois_c_org_names", "whois_org_names",
  "filing_names", "org_types", "cert_types", "cert_numbers", "regions", "addresses",
  "ip_emergency_admin_candidates", "matched_domains", "source_record_count", "source_refs",
];
const candidates = styleSheet(workbook.worksheets.add("机构候选"), "机构候选与来源字段", candidateHeaders, report.candidates, {
  wrap: true,
  rowHeight: 48,
  freezeColumns: 4,
  tableName: "OrganisationCandidates",
  widths: [22, 22, 22, 22, 12, 36, 36, 38, 24, 24, 28, 28, 44, 44, 36, 14, 36],
});
candidates.tabColor = colors.blue;

const evidenceHeaders = ["evidence_type", "entity_id", "legacy_orgid", "resource", "whois_value", "filing_value", "source_ref", "result"];
styleSheet(workbook.worksheets.add("匹配证据"), "机构与资源匹配证据", evidenceHeaders, report.evidence, {
  wrap: true,
  rowHeight: 36,
  freezeColumns: 3,
  tableName: "OrganisationEvidence",
  widths: [22, 22, 18, 34, 40, 36, 38, 34],
});

const unresolvedHeaders = ["domain", "netname", "orgname", "c_orgname", "source_ref", "planned_action"];
styleSheet(workbook.worksheets.add("未解析域名"), "尚未关联到唯一机构的旧域名", unresolvedHeaders, report.unresolved_domains, {
  wrap: true,
  rowHeight: 34,
  freezeColumns: 2,
  tableName: "UnresolvedDomains",
  widths: [30, 24, 40, 36, 30, 54],
});

const auditHeaders = [
  "classification", "record_id", "source_kind", "organisation_name", "cert_no",
  "raw_ip_start", "raw_ip_end", "parsed_ip_ranges", "source_ref", "action", "reason",
];
const omittedRows = [
  ...(report.excluded_filing_records || []),
  ...(report.non_organisation_filing_records || []),
];
const omitted = styleSheet(
  workbook.worksheets.add("未参与机构提取"),
  "未参与机构提取的备案记录（保留完整审计证据）",
  auditHeaders,
  omittedRows,
  {
    wrap: true,
    rowHeight: 44,
    freezeColumns: 4,
    tableName: "OmittedFilingRecords",
    widths: [28, 20, 18, 42, 28, 38, 38, 48, 40, 30, 56],
  },
);
omitted.tabColor = "#7F8C8D";

const legacyExclusionHeaders = [
  "rule_id", "object_type", "primary_key", "netname", "conn_i", "descr",
  "source_ref", "action", "reason", "kept_resource",
];
const legacyExclusions = styleSheet(
  workbook.worksheets.add("排除旧资源"),
  "按已确认特殊规则排除的旧WHOIS资源（源文件未修改）",
  legacyExclusionHeaders,
  report.excluded_legacy_resources || [],
  {
    wrap: true,
    rowHeight: 54,
    freezeColumns: 3,
    tableName: "ExcludedLegacyResources",
    widths: [30, 16, 34, 24, 18, 40, 30, 32, 54, 62],
  },
);
legacyExclusions.tabColor = "#7F8C8D";

const decisionRows = report.conflicts.filter((item) => item.resolution_policy !== "WHOIS_VALUE_RETAINED").map((item) => ({
  conflict_id: item.conflict_id,
  category: item.category,
  subtype: item.subtype,
  object_type: item.object_type,
  object_key: item.object_key,
  decision: "",
  selected_orgid: "",
  selected_value: "",
  reviewer_note: "",
}));
const decisionHeaders = ["conflict_id", "category", "subtype", "object_type", "object_key", "decision", "selected_orgid", "selected_value", "reviewer_note"];
const decisions = styleSheet(workbook.worksheets.add("决策录入"), "机构冲突人工决策录入", decisionHeaders, decisionRows, {
  wrap: true,
  rowHeight: 32,
  freezeColumns: 5,
  tableName: "OrganisationDecisions",
  widths: [15, 16, 30, 18, 24, 28, 22, 42, 48],
});
decisions.tabColor = "#D9A300";
if (decisionRows.length) {
  const inputRange = decisions.getRange(`F5:I${4 + decisionRows.length}`);
  inputRange.format.fill = colors.paleAmber;
  decisions.getRange(`F5:F${4 + decisionRows.length}`).dataValidation = {
    rule: {
      type: "list",
      values: ["保留WHOIS", "确认合并", "确认拆分", "确认备案实体", "忽略该匹配", "备案覆盖WHOIS（需批准）"],
    },
  };
}

const sourceRows = report.sources.map((item) => ({
  filename: item.filename,
  role: item.role,
  included: item.included ? "是" : "否",
  bytes: item.bytes,
  sha256: item.sha256,
}));
const sourceSheet = styleSheet(workbook.worksheets.add("来源清单"), "本次运行使用的数据文件", ["filename", "role", "included", "bytes", "sha256"], sourceRows, {
  wrap: false,
  rowHeight: 24,
  tableName: "SourceManifest",
  widths: [38, 34, 12, 16, 72],
});
sourceSheet.getRange(`D5:D${4 + sourceRows.length}`).format.numberFormat = "#,##0";

workbook.recalculate();

const overviewInspection = await workbook.inspect({
  kind: "table",
  range: `概览!A1:H${stopRow + 2}`,
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
  maxChars: 10000,
});
const errorInspection = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const previewRanges = {
    "概览": `A1:H${stopRow + 2}`,
    "冲突明细": "A1:L14",
    "机构候选": "A1:Q14",
    "匹配证据": "A1:H14",
    "未解析域名": "A1:F14",
    "未参与机构提取": "A1:K22",
    "排除旧资源": "A1:J10",
    "决策录入": "A1:I14",
    "来源清单": "A1:E14",
  };
  for (const [sheetName, range] of Object.entries(previewRanges)) {
    const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(JSON.stringify({
  output: outputPath,
  sheets: workbook.worksheets.items.map((sheet) => sheet.name),
  overviewInspection: overviewInspection.ndjson,
  formulaErrors: errorInspection.ndjson,
}, null, 2));
