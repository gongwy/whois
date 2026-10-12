#!/usr/bin/env node
/** Render the five review workbooks for preprocess_legacy_whois_orgs.py. */
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

const { FileBlob, SpreadsheetFile, Workbook } = await loadArtifactTool();
const [reportPath, outputDir] = process.argv.slice(2);
if (!reportPath || !outputDir) throw new Error("Usage: render_legacy_org_preprocess_reports.mjs <report.json> <output-dir>");
const data = JSON.parse(await fs.readFile(reportPath, "utf8"));
const previewDir = path.join(outputDir, "previews");
await fs.mkdir(previewDir, { recursive: true });

const COLORS = { navy: "#174E6B", blue: "#1E719D", text: "#26333C", muted: "#65727C", border: "#D3DCE2", white: "#FFFFFF", amber: "#FFF1D0", red: "#FBE2E2", green: "#E4F2E9" };
function col(index) {
  let n=index+1, result="";
  while (n) { const r=(n-1)%26; result=String.fromCharCode(65+r)+result; n=Math.floor((n-1)/26); }
  return result;
}
function value(x) {
  if (x === null || x === undefined) return "";
  if (typeof x === "number") return x;
  return String(x).replace(/\r\n/g, "\n");
}
function safeCertificate(x) { return x ? `证号: ${x}` : ""; }
function wrappedLines(raw, width) {
  return String(raw || "").split("\n").reduce((count, line) => {
    let units=0;
    for (const char of line) units += /[^\u0000-\u007f]/.test(char) ? 2 : 1;
    return count + Math.max(1, Math.ceil(units / Math.max(12,width-4)));
  }, 0);
}

function addSheet(wb, spec) {
  const sheet=wb.worksheets.add(spec.name);
  sheet.showGridLines=false;
  const columns=spec.columns;
  const last=col(columns.length-1);
  sheet.getRange("A2").values=[[spec.title]];
  sheet.getRange("A2").format.font={name:"Arial",size:15,bold:true,color:COLORS.text};
  sheet.getRange(`A3:${last}3`).format.borders={bottom:{style:"thin",color:COLORS.blue}};
  sheet.getRange(`A4:${last}4`).merge();
  sheet.getRange("A4").values=[[spec.note]];
  sheet.getRange("A4").format.font={name:"Arial",size:10,italic:true,color:COLORS.muted};
  const headerRow=6;
  sheet.getRangeByIndexes(5,0,1,columns.length).values=[columns.map(c=>c.label)];
  const hdr=sheet.getRangeByIndexes(5,0,1,columns.length);
  hdr.format={fill:COLORS.navy,font:{name:"Arial",size:10,bold:true,color:COLORS.white},horizontalAlignment:"center",verticalAlignment:"center",wrapText:true,borders:{preset:"inside",style:"thin",color:COLORS.white}};
  hdr.format.rowHeight=33;
  const rows=spec.rows || [];
  if (rows.length) {
    const matrix=rows.map(row=>columns.map(c=>value(c.get ? c.get(row) : row[c.key])));
    const body=sheet.getRangeByIndexes(6,0,rows.length,columns.length);
    body.values=matrix;
    body.format={font:{name:"Arial",size:10,color:COLORS.text},verticalAlignment:"top",wrapText:true,borders:{insideHorizontal:{style:"thin",color:COLORS.border}}};
    body.format.rowHeight=spec.rowHeight || 33;
    if (spec.dynamicHeightKeys) {
      rows.forEach((row,i)=>{
        const lines=Math.max(...spec.dynamicHeightKeys.map(key=>{
          const column=columns.find(c=>c.key===key);
          return wrappedLines(row[key],column?.width || 40);
        }));
        const height=Math.min(240,Math.max(spec.rowHeight || 33,lines*17+8));
        if (height>(spec.rowHeight || 33)) sheet.getRange(`${i+7}:${i+7}`).format.rowHeight=height;
      });
    }
  }
  columns.forEach((c,i)=>{ sheet.getRange(`${col(i)}:${col(i)}`).format.columnWidth=c.width || 22; });
  const table=sheet.tables.add(`A6:${last}${6+Math.max(1,rows.length)}`,true,spec.tableName);
  table.style="TableStyleMedium2";
  table.showFilterButton=true;
  sheet.freezePanes.freezeRows(6);
  sheet.freezePanes.freezeColumns(Math.min(2,columns.length));
  sheet.tabColor=spec.tabColor || COLORS.blue;
  if (spec.statusColumn !== undefined) {
    const statusCol=col(spec.statusColumn);
    for (let i=0;i<rows.length;i++) {
      const status=String(rows[i][spec.statusKey] || "");
      const fill=status.includes("唯一") ? COLORS.green : status.includes("多") || status.includes("冲突") ? COLORS.red : COLORS.amber;
      sheet.getRange(`${statusCol}${i+7}`).format.fill=fill;
    }
  }
  return { sheet, rowCount:rows.length, last };
}

async function exportBook(filename, sheets) {
  const wb=Workbook.create();
  const created=sheets.map(spec=>({spec,...addSheet(wb,spec)}));
  wb.recalculate();
  const error=await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:100},summary:"formula error scan"});
  if (!error.ndjson.includes("matched 0 entries")) throw new Error(`${filename}: unexpected spreadsheet error: ${error.ndjson}`);
  for (const {spec,last,rowCount} of created) {
    const check=await wb.inspect({kind:"table",range:`${spec.name}!A6:${last}${Math.min(10,6+Math.max(1,rowCount))}`,include:"values,formulas",tableMaxRows:5,tableMaxCols:12,maxChars:1800});
    if (!check.ndjson.includes(spec.columns[0].label)) throw new Error(`${filename}: header inspect failed for ${spec.name}`);
    const png=await wb.render({sheetName:spec.name,range:`A1:${last}${Math.min(13,6+Math.max(1,rowCount))}`,scale:1,format:"png"});
    await fs.writeFile(path.join(previewDir,`${path.parse(filename).name}-${spec.name}.png`),new Uint8Array(await png.arrayBuffer()));
  }
  const outputPath=path.join(outputDir,filename);
  const blob=await SpreadsheetFile.exportXlsx(wb);
  await blob.save(outputPath);
  const reopened=await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  if (reopened.worksheets.items.length!==sheets.length) throw new Error(`${filename}: re-import sheet count mismatch`);
  for (const {spec,rowCount,last} of created) {
    const sh=reopened.worksheets.getItem(spec.name);
    if (sh.getRange(`A6`).values[0][0]!==spec.columns[0].label) throw new Error(`${filename}: re-import header mismatch`);
    if (rowCount && !sh.getRange(`A${6+rowCount}`).values[0][0]) throw new Error(`${filename}: last row missing`);
  }
  return {filename,sheets:sheets.map(s=>s.name),rows:created.map(s=>s.rowCount)};
}

const C=(key,label,width,get)=>({key,label,width,get});
const oldOrgCols=[C("conn_i","旧 conn-i",17),C("orgname_count","名称数",11),C("orgnames","旧 WHOIS 机构名称候选",65),C("resource_count","IP 资源数",13),C("status","状态",20)];
const oldIpCols=[C("conn_i","旧 conn-i",17),C("object_type","资源类型",13),C("resource","原始 IP 资源键",37),C("netname","netname",27),C("orgname","第一条 descr（机构名候选）",55),C("english_name","第二条 descr",50),C("source_ref","旧 WHOIS 来源",29)];
const domainCols=[C("domain","旧 WHOIS 域名原值",42),C("netname","netname",25),C("c_orgname","c-orgname",49),C("matching_route","匹配路径",15),C("netname_candidates","netname 命中的 conn-i",32),C("c_orgname_candidates","c-orgname 命中的 conn-i",32),C("candidate_conn_i","最终候选 conn-i",32),C("candidate_count","候选数",11),C("status","状态",17),C("source_ref","旧 WHOIS 来源",29)];
const nameResultCols=[C("conn_i","旧 conn-i",17),C("prior_status","上一阶段结果",20),C("old_orgname_count","旧名称数",11),C("old_orgnames","全部旧机构名称",64),C("matched_name_count","本阶段命中数",14),C("matched_old_orgnames","本阶段命中的旧名称",53),C("selected_orgname","本阶段确定机构名称",53),C("status","本阶段状态",19),C("resource_count","IP 资源数",13)];
const candidateCols=[C("conn_i","旧 conn-i",17),C("old_orgname","旧 WHOIS 候选名称",58),C("matched","备案命中",13),C("filing_record_count","备案记录数",14),C("filing_names","备案主体名称原值",58),C("filing_sources","备案原始位置",58)];
const evidenceCols=[C("conn_i","旧 conn-i",17),C("old_orgname","旧 WHOIS 候选名称",56),C("filing_name","备案法人名称",56),C("record_id","备案记录 ID",24),C("source_ref","备案原始位置",50),C("domain","备案域名（仅来源）",50),C("ip_range_audit_only","备案 IP（仅来源）",45),C("certificate_type","证件类型",27),C("certificate_number","证件号码",34,s=>safeCertificate(s.certificate_number))];

const outputs=[];
outputs.push(await exportBook("01_旧IP机构名称冲突.xlsx",[
  {name:"多名称conn-i",title:"同一旧 conn-i 包含多个机构名称",note:`共 ${data.summary.multi_name_conn_i} 个；机构名称取旧 IP 对象第一条 descr，以下列出原文，不自动择一。超长单元格可点选查看全文。`,columns:oldOrgCols,rows:data.legacy.org_conflicts,tableName:"OldOrgNameConflicts",dynamicHeightKeys:["orgnames"],statusColumn:4,statusKey:"status"},
  {name:"旧IP来源",title:"多名称 conn-i 的旧 IP 对象来源",note:"逐条保留 inetnum/inet6num 原始键、netname、两条 descr 和源文件行号。",columns:oldIpCols,rows:data.legacy.org_conflict_resources,tableName:"OldOrgResourceEvidence",rowHeight:38},
  {name:"名称缺失IP对象",title:"无法从 descr 提取机构名称的旧 IP 对象",note:"这些对象未被丢弃；仍可通过其 netname 参与旧域名关联，需后续核查名称。",columns:[C("conn_i","旧 conn-i",17),C("object_type","类型",13),C("resource","原始 IP 键",37),C("reason","原因",54),C("source_ref","来源",30)],rows:data.legacy.ignored_ip_without_orgname,tableName:"OldIPNoOrgName"},
  {name:"特殊排除",title:"已确认排除的重复旧资源",note:"原始旧 WHOIS 文件未修改；该规则按资源键、netname、conn-i 和 descr 同时精确识别。",columns:[C("rule_id","规则 ID",31),C("primary_key","原始 IP 键",37),C("netname","netname",25),C("conn_i","旧 conn-i",17),C("descr","机构描述",44),C("kept_resource","保留的资源",80),C("source_ref","来源",29),C("reason","排除原因",75)],rows:data.legacy.excluded_legacy_resources,tableName:"ConfirmedOldIPExclusions",rowHeight:55},
]));
outputs.push(await exportBook("02_旧域名机构关联冲突.xlsx",[
  {name:"域名零匹配",title:"旧域名无法关联任何旧 conn-i",note:`共 ${data.summary.domain_zero} 条。netname 优先；只有 netname 零候选时才查 c-orgname。零匹配不强行关联。`,columns:domainCols,rows:data.legacy.domain_zero,tableName:"OldDomainZeroMatch",dynamicHeightKeys:["netname_candidates","c_orgname_candidates","candidate_conn_i"],statusColumn:8,statusKey:"status"},
  {name:"域名多候选",title:"旧域名关联多个旧 conn-i",note:`共 ${data.summary.domain_multiple} 条。即使 c-orgname 能进一步缩小，也不覆盖已有的 netname 候选。`,columns:domainCols,rows:data.legacy.domain_multiple,tableName:"OldDomainMultiMatch",dynamicHeightKeys:["netname_candidates","c_orgname_candidates","candidate_conn_i"],statusColumn:8,statusKey:"status"},
]));

const labels={ICP:["03_ICP名称核对.xlsx","ICP 备案"],DOMAIN_REGISTRY:["04_域名备案名称核对.xlsx","域名备案"],IPFP:["05_IP备案名称核对.xlsx","非预留 IP 备案"]};
for(const stage of data.name_stages) {
  const [filename,label]=labels[stage.source_kind];
  outputs.push(await exportBook(filename,[
    {name:"核对结果",title:`旧 conn-i 机构名称与${label}核对`,note:`本阶段输入 ${stage.summary.input_conn_i} 个；唯一命中 ${stage.summary.unique}，多个名称命中 ${stage.summary.multiple}，未命中 ${stage.summary.none}。${stage.source_kind==="IPFP"?`已排除预留备案 ${data.summary.excluded_reserved_ipfp_rows} 条。`:""}仅唯一命中采用旧 WHOIS 名称原文。`,columns:nameResultCols,rows:stage.results,tableName:`NameResults${stage.source_kind}`,rowHeight:39,dynamicHeightKeys:["old_orgnames","matched_old_orgnames"],statusColumn:7,statusKey:"status"},
    {name:"候选名称逐项",title:`每个旧机构名称在${label}中的命中情况`,note:"一行一个旧 WHOIS 名称；名称规范化只用于比较，不改变最终机构名称原文。",columns:candidateCols,rows:stage.candidates,tableName:`NameCandidates${stage.source_kind}`,rowHeight:36,dynamicHeightKeys:["filing_names","filing_sources"]},
    {name:"备案命中证据",title:`${label}原始命中记录`,note:stage.source_kind==="IPFP"?"IP 备案的 IP 区间只作来源审计，本轮仅按法人名称存在性比较；“预留”已排除。":"备案域名只作来源审计，本轮不把备案域名与旧 WHOIS 域名或 conn-i 建立归属连接。",columns:evidenceCols,rows:stage.evidence,tableName:`NameEvidence${stage.source_kind}`,rowHeight:38},
    ...(stage.source_kind==="IPFP"?[{name:"已确认备案排除",title:"已确认的疑似过期备案排除",note:"这些记录不参与法人名称索引，保留原始位置和排除原因。",columns:[C("source_kind","来源类型",19),C("legal_name","法人名称",52),C("source_ref","原始位置",50),C("reason","排除原因",72)],rows:data.excluded_filing_rows,tableName:"ConfirmedFilingExclusions",rowHeight:44}]:[]),
  ]));
}
console.log(JSON.stringify({outputs},null,2));
