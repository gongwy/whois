#!/usr/bin/env node
/** Export all organisation objects from a WHOIS import file to a review workbook. */
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
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

const { FileBlob, SpreadsheetFile, Workbook } = await loadArtifactTool();

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("Usage: export_organisation_review.mjs <CERNIC-import.db> <output.xlsx>");
}

const COLORS = {
  navy: "#174E6B",
  blue: "#1E719D",
  text: "#26333C",
  muted: "#65727C",
  border: "#D3DCE2",
  white: "#FFFFFF",
  paleBlue: "#EAF4F8",
  amber: "#FFF1D0",
  amberText: "#7A5600",
  red: "#FBE2E2",
  redText: "#9C1C1C",
  green: "#E4F2E9",
  greenText: "#375623",
  gray: "#F3F5F6",
  input: "#FFF8D8",
};

function excelColumn(index) {
  let n = index + 1;
  let result = "";
  while (n) {
    const r = (n - 1) % 26;
    result = String.fromCharCode(65 + r) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

function parseAttributes(block) {
  const attrs = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line.trim() || /^\s*[#%]/.test(line)) continue;
    const match = line.match(/^([^:\s][^:]*?):\s?(.*)$/);
    if (!match) continue;
    attrs.push({ key: match[1].trim().toLowerCase(), value: match[2].trimEnd() });
  }
  return attrs;
}

function valuesFor(attrs, key) {
  return attrs.filter((item) => item.key === key).map((item) => item.value);
}

function unique(values) {
  return [...new Set(values)];
}

function first(attrs, key) {
  return valuesFor(attrs, key)[0] || "";
}

function joinedForReview(attrs, key, limit = 30000) {
  const items = unique(valuesFor(attrs, key));
  if (!items.length) return "";
  const complete = items.join("\n");
  if (complete.length <= limit) return complete;
  const kept = [];
  let size = 0;
  for (const item of items) {
    if (size + item.length + 1 > limit - 40) break;
    kept.push(item);
    size += item.length + 1;
  }
  kept.push("…（完整内容见“属性明细”工作表）");
  return kept.join("\n");
}

function migrationReason(attrs) {
  const prefix = "migration-match-failure:";
  return valuesFor(attrs, "remarks")
    .filter((value) => value.toLowerCase().startsWith(prefix))
    .map((value) => value.slice(prefix.length).trim())
    .join("\n");
}

function getObjectType(attrs) {
  return attrs[0]?.key || "";
}

const raw = await fs.readFile(inputPath, "utf8");
const checksum = crypto.createHash("sha256").update(raw).digest("hex");
const blocks = raw.split(/\r?\n\s*\r?\n+/);
const organisations = [];
const attributeRows = [];
const fieldStats = new Map();

for (const block of blocks) {
  const attrs = parseAttributes(block);
  if (!attrs.length || getObjectType(attrs) !== "organisation") continue;
  const organisation = first(attrs, "organisation");
  const category = organisation.startsWith("FK") ? "临时清理机构" : "正式机构";
  const valueCounters = new Map();
  const firstIndexes = new Map();
  const perFieldValues = new Map();

  attrs.forEach((attr, index) => {
    const pair = `${attr.key}\u0000${attr.value}`;
    valueCounters.set(pair, (valueCounters.get(pair) || 0) + 1);
    if (!firstIndexes.has(pair)) firstIndexes.set(pair, index + 1);
    if (!perFieldValues.has(attr.key)) perFieldValues.set(attr.key, []);
    perFieldValues.get(attr.key).push(attr.value);
  });

  for (const [pair, count] of valueCounters) {
    const [field, value] = pair.split("\u0000");
    attributeRows.push([organisation, category, field, value, count, firstIndexes.get(pair)]);
  }

  for (const [field, values] of perFieldValues) {
    if (!fieldStats.has(field)) fieldStats.set(field, { organisations: 0, occurrences: 0, multi: 0 });
    const stat = fieldStats.get(field);
    stat.organisations += 1;
    stat.occurrences += values.length;
    if (unique(values).length > 1) stat.multi += 1;
  }

  const distinctDuplicateCount = [...valueCounters.values()].reduce((sum, count) => sum + Math.max(0, count - 1), 0);
  const multiFieldCount = [...perFieldValues.values()].filter((values) => unique(values).length > 1).length;
  organisations.push({
    organisation,
    category,
    orgName: first(attrs, "org-name"),
    cOrgName: first(attrs, "c-org-name"),
    migrationReason: migrationReason(attrs),
    descr: joinedForReview(attrs, "descr"),
    remarks: joinedForReview(attrs, "remarks"),
    street: joinedForReview(attrs, "street"),
    cStreet: joinedForReview(attrs, "c-street"),
    city: joinedForReview(attrs, "city"),
    cCity: joinedForReview(attrs, "c-city"),
    province: joinedForReview(attrs, "province"),
    cProvince: joinedForReview(attrs, "c-province"),
    country: joinedForReview(attrs, "country"),
    cCityId: joinedForReview(attrs, "c-cityid"),
    adminC: joinedForReview(attrs, "admin-c"),
    notify: joinedForReview(attrs, "notify"),
    mntBy: joinedForReview(attrs, "mnt-by"),
    source: joinedForReview(attrs, "source"),
    rawLines: attrs.length,
    multiFields: multiFieldCount,
    duplicateOccurrences: distinctDuplicateCount,
  });
}

if (!organisations.length) throw new Error("No organisation objects found in the input file");

const officialCount = organisations.filter((row) => row.category === "正式机构").length;
const temporaryCount = organisations.length - officialCount;
const outputDir = path.dirname(outputPath);
const previewDir = path.join(outputDir, "previews");
await fs.mkdir(previewDir, { recursive: true });

const wb = Workbook.create();

function setTitle(sheet, title, note, lastColumn) {
  sheet.showGridLines = false;
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format = { font: { name: "Arial", size: 16, bold: true, color: COLORS.text }, verticalAlignment: "center" };
  sheet.getRange("2:2").format.rowHeight = 28;
  sheet.getRange(`A3:${lastColumn}3`).format.borders = { bottom: { style: "thin", color: COLORS.blue } };
  sheet.getRange(`A4:${lastColumn}4`).merge();
  sheet.getRange("A4").values = [[note]];
  sheet.getRange("A4").format = { font: { name: "Arial", size: 10, italic: true, color: COLORS.muted }, wrapText: true, verticalAlignment: "center" };
  sheet.getRange("4:4").format.rowHeight = 34;
}

function styleHeader(range) {
  range.format = {
    fill: COLORS.navy,
    font: { name: "Arial", size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: COLORS.white },
  };
  range.format.rowHeight = 34;
}

// 说明与汇总
const overview = wb.worksheets.add("说明与汇总");
setTitle(overview, "CERNIC organisation 信息核对表", `来源：${path.basename(inputPath)}；SHA-256：${checksum}。本工作簿仅整理 organisation 对象，不修改导入数据。`, "J");
overview.getRange("A6:B10").values = [
  ["核对指标", "数量"],
  ["organisation 总数", organisations.length],
  ["正式机构", officialCount],
  ["临时清理机构（FK）", temporaryCount],
  ["属性明细（去重后）", attributeRows.length],
];
styleHeader(overview.getRange("A6:B6"));
overview.getRange("A7:A10").format = { fill: COLORS.paleBlue, font: { name: "Arial", size: 10, bold: true, color: COLORS.text }, borders: { preset: "inside", style: "thin", color: COLORS.border } };
overview.getRange("B7:B10").format = { font: { name: "Arial", size: 11, bold: true, color: COLORS.text }, horizontalAlignment: "right", numberFormat: "#,##0", borders: { preset: "inside", style: "thin", color: COLORS.border } };
overview.getRange("D6:J6").merge();
overview.getRange("D6").values = [["核对说明"]];
styleHeader(overview.getRange("D6:J6"));
overview.getRange("D7:J10").merge();
overview.getRange("D7").values = [[
  "1. “机构清单”一行对应一个 organisation，可筛选正式/临时机构。\n" +
  "2. 黄色列供核对人员填写；“核对状态”带下拉选项。\n" +
  "3. 多值字段在清单中按去重值换行展示；完整原始值、首次顺序和重复次数见“属性明细”。\n" +
  "4. FK 开头对象是为未决域名迁移生成的临时清理机构，已单独标识，便于后续批量筛选或删除。"
]];
overview.getRange("D7:J10").format = { fill: COLORS.gray, font: { name: "Arial", size: 10, color: COLORS.text }, wrapText: true, verticalAlignment: "top", borders: { preset: "outside", style: "thin", color: COLORS.border } };

const fieldRows = [...fieldStats.entries()]
  .sort((a, b) => b[1].occurrences - a[1].occurrences || a[0].localeCompare(b[0]))
  .map(([field, stat]) => [field, stat.organisations, stat.occurrences, stat.multi, organisations.length - stat.organisations]);
overview.getRange("A13:E13").values = [["字段", "含该字段机构数", "总出现次数", "多值机构数", "缺失机构数"]];
styleHeader(overview.getRange("A13:E13"));
overview.getRangeByIndexes(13, 0, fieldRows.length, 5).values = fieldRows;
overview.getRange(`A14:E${13 + fieldRows.length}`).format = { font: { name: "Arial", size: 10, color: COLORS.text }, borders: { insideHorizontal: { style: "thin", color: COLORS.border } } };
overview.getRange(`B14:E${13 + fieldRows.length}`).format.numberFormat = "#,##0";
overview.tables.add(`A13:E${13 + fieldRows.length}`, true, "OrganisationFieldStats").style = "TableStyleMedium2";
overview.getRange("G13:J13").merge();
overview.getRange("G13").values = [["分类定义"]];
styleHeader(overview.getRange("G13:J13"));
overview.getRange("G14:J14").merge();
overview.getRange("G14").values = [["正式机构：organisation ID 不以 FK 开头。"]];
overview.getRange("G15:J16").merge();
overview.getRange("G15").values = [["临时清理机构：organisation ID 以 FK 开头，原数据通常包含 migration-cleanup / migration-match-failure 备注，用于承接未能唯一关联机构的旧域名。"]];
overview.getRange("G14:J16").format = { font: { name: "Arial", size: 10, color: COLORS.text }, fill: COLORS.amber, wrapText: true, verticalAlignment: "top", borders: { preset: "inside", style: "thin", color: COLORS.border } };
overview.getRange("15:16").format.rowHeight = 28;
for (const [column, width] of [["A", 25], ["B", 16], ["C", 16], ["D", 16], ["E", 16], ["F", 4], ["G", 19], ["H", 19], ["I", 19], ["J", 19]]) {
  overview.getRange(`${column}:${column}`).format.columnWidth = width;
}
overview.freezePanes.freezeRows(6);
overview.tabColor = COLORS.blue;

// 机构清单
const listSheet = wb.worksheets.add("机构清单");
const listColumns = [
  ["organisation", 16], ["机构类别", 16], ["英文名称（org-name）", 42], ["中文名称（c-org-name）", 42],
  ["临时机构匹配原因", 24], ["描述（descr）", 48], ["备注（remarks）", 52], ["英文地址（street）", 42],
  ["中文地址（c-street）", 42], ["城市（city）", 20], ["中文城市（c-city）", 20], ["省份代码（province）", 15],
  ["中文省份（c-province）", 18], ["国家（country）", 14], ["城市编码（c-cityid）", 17], ["管理联系人（admin-c）", 28],
  ["通知邮箱（notify）", 42], ["维护者（mnt-by）", 26], ["数据源（source）", 14], ["原始属性行数", 14],
  ["多值字段数", 13], ["重复出现次数", 14], ["核对状态", 14], ["建议修改", 36], ["核对人", 15], ["核对备注", 40],
];
const listLastColumn = excelColumn(listColumns.length - 1);
setTitle(listSheet, "organisation 机构清单", `共 ${organisations.length.toLocaleString("zh-CN")} 个机构对象；其中正式机构 ${officialCount.toLocaleString("zh-CN")} 个、FK 临时清理机构 ${temporaryCount.toLocaleString("zh-CN")} 个。多值字段已去重并换行展示，完整证据见“属性明细”。`, listLastColumn);
listSheet.getRangeByIndexes(5, 0, 1, listColumns.length).values = [listColumns.map((column) => column[0])];
styleHeader(listSheet.getRangeByIndexes(5, 0, 1, listColumns.length));
const listRows = organisations.map((row) => [
  row.organisation, row.category, row.orgName, row.cOrgName, row.migrationReason, row.descr, row.remarks, row.street,
  row.cStreet, row.city, row.cCity, row.province, row.cProvince, row.country, row.cCityId, row.adminC,
  row.notify, row.mntBy, row.source, row.rawLines, row.multiFields, row.duplicateOccurrences,
  "未核对", "", "", "",
]);
listSheet.getRangeByIndexes(6, 0, listRows.length, listColumns.length).values = listRows;
const listBody = listSheet.getRangeByIndexes(6, 0, listRows.length, listColumns.length);
listBody.format = { font: { name: "Arial", size: 10, color: COLORS.text }, verticalAlignment: "top", wrapText: true, borders: { insideHorizontal: { style: "thin", color: COLORS.border } } };
listBody.format.rowHeight = 38;
listColumns.forEach((column, index) => { listSheet.getRange(`${excelColumn(index)}:${excelColumn(index)}`).format.columnWidth = column[1]; });
listSheet.getRange(`A7:S${6 + listRows.length}`).format.numberFormat = "@";
listSheet.getRange(`T7:V${6 + listRows.length}`).format.numberFormat = "#,##0";
listSheet.getRange(`W7:Z${6 + listRows.length}`).format.fill = COLORS.input;
listSheet.getRange(`W7:W${6 + listRows.length}`).dataValidation = { rule: { type: "list", values: ["未核对", "正确", "需修改", "需删除", "待确认"] } };
listSheet.getRange(`B7:B${6 + listRows.length}`).conditionalFormats.add("containsText", { text: "临时清理机构", format: { fill: COLORS.amber, font: { color: COLORS.amberText, bold: true } } });
listSheet.getRange(`W7:W${6 + listRows.length}`).conditionalFormats.add("containsText", { text: "正确", format: { fill: COLORS.green, font: { color: COLORS.greenText, bold: true } } });
listSheet.getRange(`W7:W${6 + listRows.length}`).conditionalFormats.add("containsText", { text: "需修改", format: { fill: COLORS.red, font: { color: COLORS.redText, bold: true } } });
listSheet.getRange(`W7:W${6 + listRows.length}`).conditionalFormats.add("containsText", { text: "需删除", format: { fill: COLORS.red, font: { color: COLORS.redText, bold: true } } });
listSheet.getRange(`W7:W${6 + listRows.length}`).conditionalFormats.add("containsText", { text: "待确认", format: { fill: COLORS.amber, font: { color: COLORS.amberText, bold: true } } });
const listTable = listSheet.tables.add(`A6:${listLastColumn}${6 + listRows.length}`, true, "OrganisationReviewList");
listTable.style = "TableStyleMedium2";
listTable.showFilterButton = true;
listSheet.freezePanes.freezeRows(6);
listSheet.freezePanes.freezeColumns(2);
listSheet.tabColor = COLORS.blue;

// 属性明细：每个 organisation / 字段 / 原值一行，并保留重复出现次数。
const detailSheet = wb.worksheets.add("属性明细");
const detailColumns = [["organisation", 16], ["机构类别", 16], ["字段", 20], ["原始值", 80], ["出现次数", 13], ["首次出现顺序", 15]];
setTitle(detailSheet, "organisation 属性明细", `共 ${attributeRows.length.toLocaleString("zh-CN")} 条去重后的“机构—字段—原始值”记录；“出现次数”保留同一值在原始对象内重复出现的数量。`, "F");
detailSheet.getRange("A6:F6").values = [detailColumns.map((column) => column[0])];
styleHeader(detailSheet.getRange("A6:F6"));
detailSheet.getRangeByIndexes(6, 0, attributeRows.length, detailColumns.length).values = attributeRows;
const detailBody = detailSheet.getRangeByIndexes(6, 0, attributeRows.length, detailColumns.length);
detailBody.format = { font: { name: "Arial", size: 10, color: COLORS.text }, verticalAlignment: "top", wrapText: true, borders: { insideHorizontal: { style: "thin", color: COLORS.border } } };
detailBody.format.rowHeight = 30;
detailColumns.forEach((column, index) => { detailSheet.getRange(`${excelColumn(index)}:${excelColumn(index)}`).format.columnWidth = column[1]; });
detailSheet.getRange(`A7:D${6 + attributeRows.length}`).format.numberFormat = "@";
detailSheet.getRange(`E7:F${6 + attributeRows.length}`).format.numberFormat = "#,##0";
detailSheet.getRange(`B7:B${6 + attributeRows.length}`).conditionalFormats.add("containsText", { text: "临时清理机构", format: { fill: COLORS.amber, font: { color: COLORS.amberText, bold: true } } });
const detailTable = detailSheet.tables.add(`A6:F${6 + attributeRows.length}`, true, "OrganisationAttributeDetails");
detailTable.style = "TableStyleMedium2";
detailTable.showFilterButton = true;
detailSheet.freezePanes.freezeRows(6);
detailSheet.freezePanes.freezeColumns(2);
detailSheet.tabColor = COLORS.blue;

wb.recalculate();

const formulaErrors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
if (!formulaErrors.ndjson.includes("matched 0 entries")) {
  throw new Error(`Unexpected spreadsheet error: ${formulaErrors.ndjson}`);
}

for (const check of [
  { sheet: "说明与汇总", range: `A1:J${Math.min(22, 13 + fieldRows.length)}`, expected: "核对指标" },
  { sheet: "机构清单", range: "A1:Z14", expected: "organisation" },
  { sheet: "属性明细", range: "A1:F14", expected: "原始值" },
]) {
  const inspected = await wb.inspect({ kind: "table", range: `${check.sheet}!${check.range}`, include: "values,formulas", tableMaxRows: 14, tableMaxCols: 26, maxChars: 8000 });
  if (!inspected.ndjson.includes(check.expected)) throw new Error(`Inspection failed for ${check.sheet}`);
  const image = await wb.render({ sheetName: check.sheet, range: check.range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${check.sheet}.png`), new Uint8Array(await image.arrayBuffer()));
}

const blob = await SpreadsheetFile.exportXlsx(wb);
await blob.save(outputPath);

const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
if (reopened.worksheets.items.length !== 3) throw new Error("Re-import sheet count mismatch");
if (reopened.worksheets.getItem("机构清单").getRange("A6").values[0][0] !== "organisation") throw new Error("Re-import list header mismatch");
if (reopened.worksheets.getItem("机构清单").getRange(`A${6 + organisations.length}`).values[0][0] !== organisations.at(-1).organisation) throw new Error("Re-import list last row mismatch");
if (reopened.worksheets.getItem("属性明细").getRange(`A${6 + attributeRows.length}`).values[0][0] !== attributeRows.at(-1)[0]) throw new Error("Re-import detail last row mismatch");

console.log(JSON.stringify({
  input: inputPath,
  output: outputPath,
  sha256: checksum,
  organisations: organisations.length,
  official: officialCount,
  temporary: temporaryCount,
  attribute_rows: attributeRows.length,
  sheets: reopened.worksheets.items.map((sheet) => sheet.name),
}, null, 2));
