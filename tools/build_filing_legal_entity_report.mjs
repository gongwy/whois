#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputJson, outputXlsx, previewDir] = process.argv.slice(2);
if (!inputJson || !outputXlsx || !previewDir) {
  throw new Error("Usage: build_filing_legal_entity_report.mjs report.json output.xlsx preview-dir");
}

const report = JSON.parse(await fs.readFile(inputJson, "utf8"));
const workbook = Workbook.create();
const fontName = "Arial";
const colors = {
  navy: "#17365D",
  header: "#0B5A78",
  headerLight: "#DDEBF7",
  line: "#B4C7DC",
  text: "#1F2937",
  redFill: "#FDE9E7",
  redText: "#9C0006",
  amberFill: "#FFF2CC",
  amberText: "#7F6000",
  blueFill: "#EAF2F8",
  grayFill: "#F2F2F2",
};

function colName(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function setColumnWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colName(index)}:${colName(index)}`).format.columnWidth = width;
  });
}

function baseSheet(name, title, subtitle, tabColor = null) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  if (tabColor) sheet.tabColor = tabColor;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: fontName, size: 14, bold: true, color: colors.navy };
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange("A3").format.font = { name: fontName, size: 10, italic: true, color: "#5B6573" };
  return sheet;
}

function addTableSheet({ name, title, subtitle, columns, rows, widths, tableName, tabColor = null, freezeColumns = 0, textColumns = [] }) {
  const sheet = baseSheet(name, title, subtitle, tabColor);
  const headerRow = 4;
  const values = [columns.map((column) => column.label)];
  for (const row of rows) {
    values.push(columns.map((column) => row[column.key] ?? ""));
  }
  const lastCol = colName(columns.length - 1);
  const lastRow = headerRow + values.length - 1;
  for (const index of textColumns) {
    sheet.getRange(`${colName(index)}${headerRow}:${colName(index)}${lastRow}`).format.numberFormat = "@";
  }
  sheet.getRange(`A${headerRow}`).write(values);
  for (const index of textColumns) {
    sheet.getRange(`${colName(index)}${headerRow}:${colName(index)}${lastRow}`).format.numberFormat = "@";
  }
  const used = sheet.getRange(`A2:${lastCol}${lastRow}`);
  used.format.font = { name: fontName, size: 10, color: colors.text };
  used.format.verticalAlignment = "center";
  const headers = sheet.getRange(`A${headerRow}:${lastCol}${headerRow}`);
  headers.format.fill = colors.header;
  headers.format.font = { name: fontName, size: 10, bold: true, color: "#FFFFFF" };
  headers.format.horizontalAlignment = "center";
  headers.format.verticalAlignment = "center";
  headers.format.wrapText = true;
  headers.format.rowHeight = 32;
  if (rows.length) {
    const body = sheet.getRange(`A${headerRow + 1}:${lastCol}${lastRow}`);
    body.format.borders = { insideHorizontal: { style: "thin", color: "#E6EDF3" } };
    body.format.rowHeight = 30;
    const table = sheet.tables.add(`A${headerRow}:${lastCol}${lastRow}`, true, tableName);
    table.style = "TableStyleMedium2";
    table.showBandedRows = true;
    table.showFilterButton = true;
  }
  sheet.freezePanes.freezeRows(headerRow);
  if (freezeColumns) sheet.freezePanes.freezeColumns(freezeColumns);
  setColumnWidths(sheet, widths);
  return { sheet, lastRow, lastCol };
}

const summary = report.summary;
const overview = baseSheet(
  "概览",
  "备案法人信息对比",
  "ICP仅使用完整域名和法人主体字段；IPFP仅使用法人主体字段。IP与联系人均未参与。",
  colors.navy,
);
overview.getRange("A5:B5").values = [["数据范围", "数量"]];
overview.getRange("A6:B17").values = [
  ["ICP源记录", summary.icp_source_rows],
  ["ICP拆分后域名记录", summary.icp_domain_rows],
  ["ICP唯一完整域名", summary.icp_unique_domains],
  ["ICP唯一法人身份", summary.icp_unique_identities],
  ["IPFP源记录", summary.ipfp_source_rows],
  ["IPFP有名称记录", summary.ipfp_named_rows],
  ["IPFP参与比较的非预留记录", summary.ipfp_comparison_rows],
  ["IPFP有名称的预留记录", summary.ipfp_reserved_named_rows],
  ["IPFP非预留但缺少单位名称", summary.ipfp_active_missing_name_rows],
  ["匹配法人身份", summary.matched_identities],
  ["仅ICP法人身份", summary.icp_only_identities],
  ["仅IPFP法人身份", summary.ipfp_only_identities],
];
overview.getRange("D5:E5").values = [["冲突类型", "条数"]];
const conflictSummaryRows = Object.entries(summary.conflict_check_counts);
overview.getRange("D6").write(conflictSummaryRows);
const differenceStart = 7 + conflictSummaryRows.length;
overview.getRange(`D${differenceStart}:E${differenceStart}`).values = [["字段差异类型", "条数"]];
overview.getRange(`D${differenceStart + 1}`).write(Object.entries(summary.differences_by_subtype));
overview.getRange("G5:J5").values = [["代表性高优先问题", "ICP机构", "IPFP机构", "证件号"]];
const highExamples = report.conflicts.filter((row) => row.severity === "高").slice(0, 10).map((row) => [
  `${row.conflict_id} ${row.subtype}`,
  row.icp_names,
  row.ipfp_names,
  row.icp_cert_nos || row.ipfp_cert_nos || row.match_key,
]);
if (highExamples.length) overview.getRange("G6").write(highExamples);

overview.getRange("A21").values = [["处理规则"]];
overview.mergeCells("A21:E21");
for (let index = 0; index < report.metadata.rules.length; index += 1) {
  const row = 22 + index;
  overview.mergeCells(`A${row}:E${row}`);
  overview.getRange(`A${row}`).values = [[`${index + 1}. ${report.metadata.rules[index]}`]];
}
overview.getRange("A30:B30").values = [["来源文件", "用途"]];
overview.getRange("A31:B32").values = [
  [path.basename(report.metadata.icp_source), "ICP法人主体与完整域名"],
  [path.basename(report.metadata.ipfp_source), "IPFP法人主体；IP字段未使用"],
];

for (const range of ["A5:B5", "D5:E5", `D${differenceStart}:E${differenceStart}`, "G5:J5", "A21:H21", "A30:B30"]) {
  overview.getRange(range).format.fill = colors.header;
  overview.getRange(range).format.font = { name: fontName, size: 10, bold: true, color: "#FFFFFF" };
  overview.getRange(range).format.horizontalAlignment = "center";
  overview.getRange(range).format.verticalAlignment = "center";
}
overview.getRange("A2:J32").format.font = { name: fontName, size: 10, color: colors.text };
overview.getRange("A2").format.font = { name: fontName, size: 14, bold: true, color: colors.navy };
overview.getRange("A3").format.font = { name: fontName, size: 10, italic: true, color: "#5B6573" };
overview.getRange("A6:B17").format.borders = { insideHorizontal: { style: "thin", color: "#E6EDF3" } };
overview.getRange(`D6:E${differenceStart + Object.keys(summary.differences_by_subtype).length}`).format.borders = { insideHorizontal: { style: "thin", color: "#E6EDF3" } };
overview.getRange("G6:J15").format.wrapText = true;
overview.getRange("A22:A27").format.wrapText = true;
overview.getRange("A22:A27").format.rowHeight = 30;
overview.getRange("J6:J15").format.numberFormat = "@";
setColumnWidths(overview, [34, 15, 3, 38, 12, 3, 34, 28, 28, 28]);

const conflictSheet = addTableSheet({
  name: "冲突与矛盾",
  title: "备案法人身份冲突与矛盾",
  subtitle: "同一证件、同一名称或同一完整域名出现不一致身份。中优先项可能包含历史更名，需结合来源行复核。",
  columns: [
    ["conflict_id", "冲突ID"], ["severity", "优先级"], ["category", "类别"], ["subtype", "冲突类型"], ["match_key", "匹配键"],
    ["icp_names", "ICP机构名称"], ["ipfp_names", "IPFP机构名称"], ["icp_cert_types", "ICP证件类型"], ["ipfp_cert_types", "IPFP证件类型"],
    ["icp_cert_nos", "ICP证件号码"], ["ipfp_cert_nos", "IPFP证件号码"], ["icp_natures", "ICP单位性质"], ["ipfp_natures", "IPFP单位性质"],
    ["icp_regions", "ICP行政区域"], ["ipfp_regions", "IPFP行政区域"], ["icp_addresses", "ICP地址"], ["ipfp_addresses", "IPFP地址"],
    ["icp_domains", "ICP完整域名"], ["icp_source_rows", "ICP源行"], ["ipfp_source_rows", "IPFP源行"], ["ipfp_record_ids", "IPFP记录ID"],
    ["explanation", "冲突说明"], ["recommended_action", "建议处理"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.conflicts,
  widths: [16, 9, 20, 28, 28, 28, 28, 20, 20, 24, 24, 18, 18, 26, 26, 34, 34, 30, 16, 16, 22, 42, 42],
  tableName: "ConflictTable",
  tabColor: "#C00000",
  freezeColumns: 4,
  textColumns: [4, 9, 10, 18, 19, 20],
});
conflictSheet.sheet.getRange(`B5:B${conflictSheet.lastRow}`).conditionalFormats.add("containsText", { text: "高", format: { fill: colors.redFill, font: { bold: true, color: colors.redText } } });
conflictSheet.sheet.getRange(`B5:B${conflictSheet.lastRow}`).conditionalFormats.add("containsText", { text: "中", format: { fill: colors.amberFill, font: { bold: true, color: colors.amberText } } });
conflictSheet.sheet.getRange(`D5:W${conflictSheet.lastRow}`).format.wrapText = true;

const differenceSheet = addTableSheet({
  name: "字段差异",
  title: "证件身份匹配后的字段差异",
  subtitle: "名称、单位性质和省级行政区域差异需要复核。地址差异仅供核查，不作为法人拆分依据。",
  columns: [
    ["difference_id", "差异ID"], ["priority", "优先级"], ["subtype", "差异类型"], ["identity_key", "法人身份键"], ["field", "字段"],
    ["icp_value", "ICP值"], ["ipfp_value", "IPFP值"], ["icp_source_rows", "ICP源行"], ["ipfp_source_rows", "IPFP源行"],
    ["ipfp_record_ids", "IPFP记录ID"], ["explanation", "说明"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.differences,
  widths: [16, 9, 28, 34, 14, 44, 44, 18, 18, 24, 48],
  tableName: "DifferenceTable",
  tabColor: "#ED7D31",
  freezeColumns: 3,
  textColumns: [3, 7, 8, 9],
});
differenceSheet.sheet.getRange(`B5:B${differenceSheet.lastRow}`).conditionalFormats.add("containsText", { text: "高", format: { fill: colors.redFill, font: { bold: true, color: colors.redText } } });
differenceSheet.sheet.getRange(`B5:B${differenceSheet.lastRow}`).conditionalFormats.add("containsText", { text: "中", format: { fill: colors.amberFill, font: { bold: true, color: colors.amberText } } });
differenceSheet.sheet.getRange(`F5:K${differenceSheet.lastRow}`).format.wrapText = true;

const matchSheet = addTableSheet({
  name: "匹配结果",
  title: "ICP与IPFP法人身份匹配结果",
  subtitle: "按规范化证件类型和证件号码精确匹配。名称或字段差异不会改写原始值。",
  columns: [
    ["identity_key", "法人身份键"], ["cert_type", "证件类型"], ["cert_no", "证件号码"], ["icp_names", "ICP机构名称"], ["ipfp_names", "IPFP机构名称"],
    ["icp_natures", "ICP单位性质"], ["ipfp_natures", "IPFP单位性质"], ["icp_regions", "ICP行政区域"], ["ipfp_regions", "IPFP行政区域"],
    ["icp_addresses", "ICP地址"], ["ipfp_addresses", "IPFP地址"], ["icp_domains", "ICP完整域名"], ["name_status", "名称状态"],
    ["nature_status", "性质状态"], ["province_status", "省级区域状态"], ["address_status", "地址状态"], ["icp_source_rows", "ICP源行"],
    ["ipfp_source_rows", "IPFP源行"], ["ipfp_record_ids", "IPFP记录ID"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.matches,
  widths: [34, 20, 24, 28, 28, 18, 18, 26, 26, 34, 34, 30, 12, 12, 14, 12, 16, 16, 24],
  tableName: "MatchTable",
  tabColor: "#70AD47",
  freezeColumns: 3,
  textColumns: [0, 2, 16, 17, 18],
});
matchSheet.sheet.getRange(`D5:S${matchSheet.lastRow}`).format.wrapText = true;
for (const col of ["M", "N", "O", "P"]) {
  matchSheet.sheet.getRange(`${col}5:${col}${matchSheet.lastRow}`).conditionalFormats.add("containsText", { text: "差异", format: { fill: colors.amberFill, font: { bold: true, color: colors.amberText } } });
}

const onlySheet = addTableSheet({
  name: "仅单侧存在",
  title: "仅在一个备案源出现的法人身份",
  subtitle: "这是覆盖范围差异，不自动视为数据冲突，也不据此创建或删除机构。",
  columns: [
    ["source", "来源"], ["identity_key", "法人身份键"], ["names", "机构名称"], ["cert_types", "证件类型"], ["cert_nos", "证件号码"],
    ["natures", "单位性质"], ["regions", "行政区域"], ["addresses", "地址"], ["domains", "ICP完整域名"], ["source_rows", "源行"],
    ["record_ids", "IPFP记录ID"], ["note", "说明"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.only_one_side,
  widths: [12, 34, 30, 20, 24, 18, 26, 36, 30, 18, 24, 28],
  tableName: "OnlyOneSideTable",
  freezeColumns: 3,
  textColumns: [1, 4, 9, 10],
});
onlySheet.sheet.getRange(`C5:L${onlySheet.lastRow}`).format.wrapText = true;

const icpSheet = addTableSheet({
  name: "ICP法人域名",
  title: "ICP备案提取的法人和完整域名",
  subtitle: `来源：${path.basename(report.metadata.icp_source)}。未读取IP或联系人字段；规范化域名不折叠www或子域名。`,
  columns: [
    ["source_row", "源行"], ["name", "主体单位名称"], ["nature", "主办单位性质"], ["cert_type", "证件类型"], ["cert_no", "证件号码"],
    ["region", "所属区域"], ["registered_address", "证件住所"], ["mailing_address", "通讯地址"], ["domain_raw", "原始域名字段"],
    ["domain_normalized", "规范化完整域名"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.icp_domain_rows,
  widths: [10, 32, 18, 22, 24, 28, 36, 36, 32, 32],
  tableName: "IcpEntityDomainTable",
  freezeColumns: 2,
  textColumns: [4, 8, 9],
});
icpSheet.sheet.getRange(`B5:J${icpSheet.lastRow}`).format.wrapText = true;

const ipfpSheet = addTableSheet({
  name: "IPFP法人",
  title: "IPFP提取的法人信息",
  subtitle: `来源：${path.basename(report.metadata.ipfp_source)}。未读取IP或联系人字段；“预留”记录保留但不参与比较。`,
  columns: [
    ["source_row", "源行"], ["record_id", "记录ID"], ["allocation_method", "分配方式"], ["included_in_comparison", "参与比较"],
    ["name", "使用单位名称"], ["nature", "使用单位性质"], ["classification", "使用单位分类"], ["industry", "行业分类"],
    ["cert_type", "证件类型"], ["cert_no", "证件号码"], ["region", "行政区域"], ["address", "详细地址"],
  ].map(([key, label]) => ({ key, label })),
  rows: report.ipfp_entity_rows,
  widths: [10, 20, 12, 12, 32, 18, 24, 14, 22, 24, 28, 40],
  tableName: "IpfpEntityTable",
  freezeColumns: 4,
  textColumns: [1, 9],
});
ipfpSheet.sheet.getRange(`E5:L${ipfpSheet.lastRow}`).format.wrapText = true;
ipfpSheet.sheet.getRange(`D5:D${ipfpSheet.lastRow}`).conditionalFormats.add("containsText", { text: "否", format: { fill: colors.grayFill, font: { color: "#666666" } } });

workbook.recalculate();

const inspections = {};
for (const [name, range] of [
  ["概览", "A1:J32"],
  ["冲突与矛盾", "A1:W10"],
  ["字段差异", "A1:K10"],
  ["匹配结果", "A1:S10"],
  ["仅单侧存在", "A1:L10"],
  ["ICP法人域名", "A1:J10"],
  ["IPFP法人", "A1:L10"],
]) {
  const result = await workbook.inspect({ kind: "table", range: `${name}!${range}`, include: "values,formulas", tableMaxRows: 10, tableMaxCols: 23, maxChars: 5000 });
  inspections[name] = result.ndjson;
}
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

await fs.mkdir(previewDir, { recursive: true });
for (const [name, range] of [
  ["概览", "A1:J32"],
  ["冲突与矛盾", "A1:W18"],
  ["字段差异", "A1:K18"],
  ["匹配结果", "A1:S16"],
  ["仅单侧存在", "A1:L16"],
  ["ICP法人域名", "A1:J16"],
  ["IPFP法人", "A1:L16"],
]) {
  const image = await workbook.render({ sheetName: name, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${name}.png`), new Uint8Array(await image.arrayBuffer()));
}

await fs.mkdir(path.dirname(outputXlsx), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputXlsx);

console.log(JSON.stringify({ outputXlsx, previews: previewDir, sheets: workbook.worksheets.items.map((sheet) => sheet.name), errors: errors.ndjson, inspections }, null, 2));
