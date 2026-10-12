# 旧WHOIS与备案数据联合迁移

## 当前已实现阶段

`prepare_joint_rsdb_stage1.py`完成机构解析阶段。它读取六个旧RPSL文件、ICP备案工作簿、独立域名备案工作簿和IP分配工作簿，输出机构候选、资源匹配证据、冲突清单、未解析域名清单及人工决策模板。

日常重跑应优先使用统一入口`run_joint_rsdb_pipeline.py`。只需提供新的旧WHOIS目录、备案目录和空输出目录。脚本会自动识别包含`ICP备案数据`和`IP分配信息`工作表的文件，读取备案目录中的`迁移排除记录.tsv`，执行全部单元回归，生成规范JSON/TSV及XLSX审阅报告，并校验保存结果。

```bash
python3 tools/run_joint_rsdb_pipeline.py \
  --rsdb-dir /absolute/path/to/new-rsdb \
  --filing-dir /absolute/path/to/new-filing-data \
  --output-dir /absolute/path/to/empty-output
```

如果同一备案目录中有多个工作簿包含相同目标工作表，脚本不会猜测，会停止并要求显式指定：

```bash
python3 tools/run_joint_rsdb_pipeline.py \
  --rsdb-dir /absolute/path/to/new-rsdb \
  --filing-dir /absolute/path/to/new-filing-data \
  --icp-workbook /absolute/path/to/icp.xlsx \
  --domain-workbook /absolute/path/to/2026-09-21_full.xlsx \
  --ip-workbook /absolute/path/to/ip-allocation.xlsx \
  --filing-exclusions /absolute/path/to/迁移排除记录.tsv \
  --output-dir /absolute/path/to/empty-output
```

正式迁移默认运行回归测试并生成XLSX。`--skip-tests`和`--skip-xlsx`只用于开发诊断。机构冲突存在时，流水线仍完整生成审阅文件，然后以退出码`2`停在机构门禁；这不是运行失败，也不会继续生成联系人或最终dump。

### 自动化边界

- 已脚本化：输入发现与校验、旧RPSL解析、备案解析、多IP拆分、机构候选生成、WHOIS优先字段规则、预留资源规则、备案排除规则、冲突与来源追踪、JSON/TSV/XLSX输出、单元回归和输出结构校验。
- 当前门禁：机构身份、匹配或备案内部冲突未解决时停止。
- 尚未执行：联系人合并、organisation/person物化、inetnum/inet6num/domain最终关联和最终RPSL dump。这些步骤将在机构决策文件完成后接入同一入口，而不是另做手工流程。

脚本不修改源文件。只要存在任何机构、字段、匹配或联系人候选冲突，状态就是`blocked`，退出码为`2`，并且不会进入联系人合并、资源关联或最终dump生成。

```bash
python3 tools/prepare_joint_rsdb_stage1.py /absolute/path/to/rsdb \
  --icp-workbook /absolute/path/to/icp备案数据.xlsx \
  --domain-workbook /absolute/path/to/2026-09-21_full.xlsx \
  --ip-workbook /absolute/path/to/IPFP.xlsx \
  --filing-exclusions /absolute/path/to/迁移排除记录.tsv \
  --output-dir /absolute/path/to/empty-output
```

核心规则：

- 法人名称和证件号按目标机构逐级补全：第一优先级是`icp备案数据.xlsx`的完整域名精确匹配；没有完整身份时才查询独立域名备案文件（如`2026-09-21_full.xlsx`）；仍未补全的旧`conn-i`才按IP查询IPFP。
- 高优先级来源一旦唯一确定法人身份，后续来源只保留审计，不覆盖、不制造身份冲突；高优先级来源自身冲突时同样停止，不允许用低优先级来源自动消解。
- 域名备案文件使用`name`及`punycode`建立完整域名索引；法人名称取`registrantName`，为空时回退`registrantOrg`，证件号取`registrantCertNo`。
- 跨来源身份合并以规范化证件号码为主键；证件类型保留为字段但不作为跨表身份键，因为ICP备案、域名备案和IPFP的证件类型编码体系不同。
- 同一证件号命中多个旧`conn-i`表示同一法人拥有多个旧资源组，合并为一个机构候选，不再把它本身判定为冲突。
- WHOIS非空字段优先；备案只补WHOIS缺失字段。
- 备案值与WHOIS值不同，保留WHOIS值并输出冲突。
- “是否为同一机构”属于身份判断，不能用WHOIS优先自动解决。
- `netname`只作上下文，不作机构合并或域名归属键。
- 域名匹配仅统一大小写、尾点、URL主机名和IDN编码；保留`www`等真实DNS标签，`www.edu.cn`与`edu.cn`是两个不同对象。
- 旧`domain`、`inetnum`、`inet6num`等资源主键必须将源数据原值和匹配规范值分开保存。规范值只允许用于查找、比较和建立关联；最终落盘必须使用源数据原值。若新系统格式无法原样接收，流水线必须停止并输出冲突，禁止静默改写、合并、去重或丢弃资源对象。
- organisation不填`tech-c`；其`admin-c`只来自IP分配备案的紧急联系人。
- inetnum、inet6num、domain保留旧`admin-c`和`tech-c`。
- 未解析域名只在不存在矛盾真实机构候选时，才会在最终阶段创建带清理标记的fake机构。
- 未关联任何旧网段或旧域名的备案实体只保留审计证据，默认不物化为organisation，避免生成孤立机构。
- IP分配备案单元格内以分号分隔的多个IP会拆开逐一匹配；ICP单元格内的多个IP只拆分保留审计，不参与机构归属。
- ICP备案只使用域名以及备案主体名称、证件类型、证件号码、地区和地址等法人实体信息。ICP中的IP字段可能是网站部署或上游服务地址，不参与旧WHOIS网段匹配、organisation归属、IP冲突或资源物化；原值仅作为审计证据保留。ICP联系人字段不参与person匹配或创建。IP资源归属及备案联系人只使用IP分配备案和旧WHOIS。
- 同一IP范围同时存在`CERNET Provincial Address Allocations`省级分配容器和具体机构对象时，具体机构对象优先；容器只保留为层级审计证据，不产生第二个机构候选或匹配冲突。
- `分配方式=预留`的记录只有在IP已匹配旧WHOIS时才可补充现有机构。无使用单位，或IP在旧WHOIS中没有任何对应资源时，只保留审计，绝不创建新机构；原始记录写入`non-organisation-filing-records.tsv`。
- 已确认或疑似为历史残留的备案记录通过`--filing-exclusions`显式排除，不参与机构提取和物化；原始记录及排除原因写入`excluded-filing-records.tsv`。
- 已确认的旧WHOIS重复资源通过代码内的精确特殊规则排除：目前仅排除`219.243.192.0 - 219.243.199.255 / SIGCOMM-CN / BJ003772 / CERNET网络中心(会议地址)`，并保留同网段`CCONFERENCE-CN / BJ000000 / CERNET会议活动专用地址`。必须同时匹配对象类型、主键、`netname`、`conn-i`和指定`descr`才会生效；源文件不修改，审计记录写入`excluded-legacy-resources.tsv`及XLSX的“排除旧资源”工作表。

排除规则文件为制表符分隔文本，字段为`source_kind`、`organisation_name`、`cert_no`、`action`、`reason`。匹配时要求来源类型和机构名称一致；规则中提供证件号码时还必须证件号码一致，避免仅凭近似名称误排除。

## 后续阶段

机构冲突处理完成并提供决策覆盖文件后，下一阶段才会执行联系人匹配。联系人匹配通过后再物化organisation/person及资源关联，最后生成并校验RPSL dump。
