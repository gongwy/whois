# RIPE Whois Database 修改说明

本文档记录本项目基于 [RIPE-NCC/whois](https://github.com/RIPE-NCC/whois) 的定制修改、关键配置和常用运行方式。

## 修改概览

1. 适配本地特定数据格式和对象规则。
2. 删减部分 businessRule 认证逻辑。
3. 增加 MD5 密码生成脚本示例。
4. 修改 `domain` 类型的存储与索引逻辑。
5. 额外搭建邮件服务器，用于接收 email-update 请求。
6. 增加 `nserver` 类型，并实现相关索引和查询。
7. 修改默认编码为 UTF-8。
8. 修改 `organisationid` 生成规则：按省份代码命名并全局自增。
9. 支持 HTTPS 启动。
10. 新增 NIC-HDL 后缀按 source 可配置生成规则。
11. 固定部分英文日期输出的 Locale，避免中文系统环境下 build/test 失败。

## NIC-HDL 后缀配置

默认 RIPE 逻辑中，自动生成 NIC-HDL 时后缀来自当前 `whois.source`。本项目新增配置项：

```properties
user.handle.suffix.config=CERNIC:CER,AP:CER,CN:CER
```

含义：

- 当当前 `whois.source` 为 `CERNIC`、`AP` 或 `CN` 时，自动生成的 NIC-HDL 后缀使用 `CER`。
- 其他未配置的 source 仍使用原有默认规则，即使用当前 source 作为后缀。
- 配置值中的后缀可以写成 `CER`，也兼容写成 `-CER`，代码会去掉开头的 `-`。
- 配置项为空时不改变原有行为。

示例：

```properties
whois.source=CERNIC
user.handle.suffix.config=CERNIC:CER,AP:CER,CN:CER
```

此时提交：

```text
person:     TEST USER
nic-hdl:    AUTO-1
source:     CERNIC
```

生成结果会使用类似：

```text
nic-hdl:    TU1-CER
```

显式提交的 NIC-HDL 也同步支持该配置。例如：

```text
nic-hdl:    XX1-CER
source:     CERNIC
```

在 `whois.source=CERNIC` 且配置了 `CERNIC:CER` 时会被认为合法。

相关代码：

- `whois-update/src/main/java/net/ripe/db/whois/update/autokey/AbstractAutoKeyFactory.java`
- `whois-update/src/main/java/net/ripe/db/whois/update/autokey/NicHandleFactory.java`
- `whois-update/src/main/java/net/ripe/db/whois/update/domain/NicHandle.java`

覆盖范围：

- `person`、`role` 等以 `nic-hdl` 作为主键的对象都会走同一套 NIC-HDL 解析和生成逻辑。
- 未配置 source 的行为保持兼容，不影响默认 source 后缀生成。

相关测试：

```bash
mvn -pl whois-update -am -Dtest=NicHandleFactoryTest,NicHandleTest -Dsurefire.failIfNoSpecifiedTests=false test
```

## Locale 相关修复

在中文系统 Locale 下，部分日期 formatter 会输出中文星期/月，导致测试期望的英文日期失败。本项目已固定为 `Locale.ENGLISH`：

- `whois-commons/src/main/java/net/ripe/db/whois/common/FormatHelper.java`
- `whois-api/src/main/java/net/ripe/db/whois/api/mail/dequeue/MessageParser.java`

相关测试：

```bash
mvn -pl whois-commons -Dtest=FormatHelperTest test
mvn -pl whois-api -Dtest=MessageParserTest test
```

## 构建环境

参考官方文档：

- [Building whois](https://apps.db.ripe.net/docs/Installation-and-Development/Building-whois/)
- [Installation instructions](https://apps.db.ripe.net/docs/Installation-and-Development/Installation-instructions/)

基础依赖：

```bash
sudo apt install openjdk-21-jdk
sudo update-alternatives --config java
sudo apt-get install git maven
sudo apt-get install mariadb-client mariadb-server
sudo apt-get install sendmail
```

构建：

```bash
mvn clean install -Prelease
```

本地已验证：

```bash
mvn clean install -Prelease
mvn clean install -Prelease -rf :whois-api
```

最终结果：`BUILD SUCCESS`。

## 安装与运行

编译产物：

```bash
whois-db/target/whois-db-1.0-SNAPSHOT.jar
```

部署示例：

```bash
sudo mkdir -p /home/dbase
sudo mv whois-db/target/whois-db-1.0-SNAPSHOT.jar /home/dbase/whois.jar
sudo cp tools/hazelcast.xml /home/dbase/
sudo cp whois-commons/src/test/resources/log4j2.xml /home/dbase/
sudo cp whois-commons/src/test/resources/whois.properties /home/dbase/properties
sudo chown dbase:dbase -R /home/dbase/
```

MySQL 建议配置：

```text
max_allowed_packet = 20M
innodb_buffer_pool_size = 2G
```

重启 MySQL：

```bash
sudo service mysql restart
```

机构名称索引使用 `utf8mb4`，并支持最长 2048 个字符的 `org-name`、`c-org-name`。新建数据库会自动使用该结构；已有数据库在停止 whois 后执行一次：

```bash
mysql -u <数据库用户> -p <WHOIS数据库名> < tools/upgrade_org_name_indexes.sql
```

创建数据库并导入 schema/data：

```sql
CREATE DATABASE WHOIS_LOCAL;
CREATE DATABASE MAILUPDATES_LOCAL;
CREATE DATABASE ACL_LOCAL;
CREATE DATABASE INTERNALS_LOCAL;
CREATE DATABASE NRTM_LOCAL;

USE WHOIS_LOCAL;
SOURCE ./whois-commons/src/main/resources/whois_schema.sql;
SOURCE ./whois-commons/src/main/resources/whois_data.sql;

USE MAILUPDATES_LOCAL;
SOURCE ./whois-commons/src/main/resources/mailupdates_schema.sql;

USE ACL_LOCAL;
SOURCE ./whois-commons/src/main/resources/acl_schema.sql;

USE INTERNALS_LOCAL;
SOURCE ./whois-commons/src/main/resources/internals_schema.sql;
SOURCE ./whois-commons/src/main/resources/internals_data.sql;

USE NRTM_LOCAL;
SOURCE ./whois-commons/src/main/resources/nrtm_schema.sql;
SOURCE ./whois-commons/src/main/resources/nrtm_data.sql;
```

启动：

```bash
/usr/bin/java \
  -Dwhois \
  -Djsse.enableSNIExtension=false \
  -Dcom.sun.management.jmxremote \
  -Dhazelcast.jmx=true \
  -Dcom.sun.management.jmxremote.authenticate=false \
  -Dcom.sun.management.jmxremote.port=1099 \
  -Xms1024m \
  -Xmx8g \
  -Dwhois.config=properties \
  -Duser.timezone=UTC \
  -Dhazelcast.config=hazelcast.xml \
  -Dlog4j.configurationFile=file:log4j2.xml \
  -jar whois.jar
```

验证：

```bash
telnet localhost 1043
```

## 旧数据转换与导入

### 当前统一迁移入口（旧 WHOIS 预处理 + 完整数据物化）

`tools/build_complete_whois_migration.py` 是当前规则的统一入口。它按顺序完成：

1. 从旧 IPv4/IPv6 的 `conn-i` 和前两条 `descr` 提取机构名称候选，并生成冲突核对表；
2. 以 `netname` 优先、`c-orgname` 次之关联旧域名；零匹配和多候选均不强制关联真实机构；
3. 按 ICP、域名备案、非“预留”IP 备案的顺序，用法人名称解决同一 `conn-i` 的多名称问题；
4. 405 个唯一命中的 `conn-i` 使用核对结果；剩余 50 个不再阻断，按旧 IP 对象中名称出现频次最高者回退，并在 `organisation-name-decisions.tsv` 中完整记录；
5. 物化 organisation、person、role、inetnum、inet6num、domain、nameserver 和维护者对象；
6. 无法唯一关联真实机构的域名写入带 `migration-cleanup: unresolved-domain` 标记的 `FK` 机构，且生成按依赖顺序删除的清单；
7. 保留旧 domain、inetnum、inet6num 主键的源文本；规范化值只用于匹配和逻辑重复检测；
8. 运行引用、主键、来源、资源保留和 Java RPSL 解析回归检查。

示例：

```bash
python3 tools/build_complete_whois_migration.py \
  --rsdb-dir /absolute/path/to/rsdb \
  --icp-workbook /absolute/path/to/icp备案数据.xlsx \
  --domain-workbook /absolute/path/to/2026-09-21_full.xlsx \
  --ip-workbook /absolute/path/to/IPFP.xlsx \
  --filing-exclusions /absolute/path/to/迁移排除记录.tsv \
  --output-dir /absolute/path/to/new-empty-output
```

最终可导入文件为 `import/CERNIC-import.db`。只有
`complete-migration-report.md` 与 `import/validation-report.md` 都显示 `passed`
时才允许进入 `Bootstrap.loadDump`。

旧系统数据转换脚本为 `tools/convert_rsdb_cernic.py`。脚本不会修改原始数据；输出目录默认为脚本旁的 `rsdb-cernic-import-test/`，其中包含导入文件和转换摘要。输出文件含业务数据，不应提交到 Git 或上传到公共位置。

### 转换旧数据

推荐使用统一入口脚本。它会检查六个旧数据文件、自动从网站常量生成城市编码、调用转换器、检查主键重复及联系人/机构悬空引用，并生成机器可读和人工可读的验收报告：

```bash
python3 tools/prepare_rsdb_import.py /absolute/path/to/new-rsdb \
  --output-dir /absolute/path/to/cernic-import-output \
  --mode safe \
  --review-workbook /absolute/path/to/org_duplicates.xlsx
```

`safe` 模式仅用于阶段性联调，会跳过核对表中有争议的机构及其关联资源，不作为最终导入数据。最终导入使用 `complete` 模式：机构名称存在冲突也不会跳过，而是采用核对结果中的主名称，并按需提供覆盖 CSV：

```bash
python3 tools/prepare_rsdb_import.py /absolute/path/to/new-rsdb \
  --output-dir /absolute/path/to/cernic-import-complete \
  --mode complete \
  --review-workbook /absolute/path/to/org_duplicates.xlsx \
  --review-overrides /absolute/path/to/org_duplicates_overrides.csv
```

输出目录必须为空，以避免把两次转换结果混在一起。转换成功后除导入文件和原有摘要外，还会生成：

```text
validation-report.md
validation-report.json
organisation-conflicts.tsv
fake-organisation-domains.tsv
fake-organisation-cleanup.tsv
```

`organisation-conflicts.tsv` 由 `complete` 模式生成，逐个列出发生名称冲突的旧机构 ID、转换后的机构 ID、最终选择的中英文名称、全部候选名称，以及关联 IPv4、IPv6、域名数量。该报告仅用于复核，不会从导入 dump 中排除这些机构或资源。

`complete` 模式不会跳过无法唯一关联机构的域名。脚本按旧 `c-orgname`、`orgname` 组合分组；缺少名称时按 `netname` 分组，仍缺少时才按单个域名分组，并为每组建立 `FK000001` 形式的临时机构。临时机构统一使用 `FAKE MIGRATION ORGANISATION` 名称和 `migration-cleanup: unresolved-domain` 标记。`fake-organisation-domains.tsv` 保存域名与临时机构的对应关系；`fake-organisation-cleanup.tsv` 按“先 domain、后 organisation”的依赖顺序列出待删除对象，便于后续批量清理。

统一入口默认使用 `complete` 模式；只有显式传入 `--mode safe` 时才会跳过争议机构。

只有终端出现 `PASS` 且报告的 `Status` 为 `passed`，该 dump 才进入后续独立数据库导入测试。核对表仍是必要的业务输入：新 rsdb 若出现核对表之外的新机构冲突，需要先更新核对表；脚本不会擅自替用户选择机构名称。

准备旧数据目录、重复 organisation 核对工作簿和一个空输出目录。旧数据目录中应包含以下文件：

```text
CERNIC.db.pn.txt
CERNIC.db.ro.txt
CERNIC.db.in.txt
CERNIC.db.i6.txt
CERNIC.db.dn.txt
CERNIC.db.ht.txt
```

核对工作簿使用 `核对结果` 页：`orgid` 位于 B 列，最终选择位于 C 列，格式为 `c-org-name (org-name)`。如选中的单元格不包含完整的一对中英文名称，使用本地私有 CSV 补全，格式如下；该文件含业务数据，不提交 Git：

```csv
orgid,c-org-name,org-name
...
```

先从网站项目的行政区划常量生成通用城市编码映射：

```bash
node tools/extract_city_code_map.mjs \
  /absolute/path/to/whois_website/src/main/resources/static/constant.js \
  /absolute/path/to/city-codes.json
```

再执行转换（路径按实际数据调整）：

```bash
python3 tools/convert_rsdb_cernic.py \
  --source-dir /absolute/path/to/rsdb \
  --review-workbook /absolute/path/to/org_duplicates.xlsx \
  --review-overrides /absolute/path/to/org_duplicates_overrides.csv \
  --city-code-map /absolute/path/to/city-codes.json \
  --output-dir /absolute/path/to/cernic-import-output \
  --bootstrap-dump /absolute/path/to/myTEST.db
```

如重复 organisation 尚未核对完成，可先生成保守的第一批导入文件：它以 `核对结果` 页的全部 ID 作为待处理集合，不读取 C 列的选择结果，并排除这些 organisation 及其关联的 inetnum、inet6num 和 domain。联系人、主机和其余无争议 organisation 会继续导出。该批次适合先在独立的测试数据库中联调；后续完成核对后，应在新的空目标库重新生成并导入完整文件，不能把完整文件直接叠加到这批导入库。

```bash
python3 tools/convert_rsdb_cernic.py \
  --source-dir /absolute/path/to/rsdb \
  --review-workbook /absolute/path/to/org_duplicates.xlsx \
  --city-code-map /absolute/path/to/city-codes.json \
  --skip-reviewed-organisations \
  --output-dir /absolute/path/to/cernic-import-safe
```

生成：

```text
/absolute/path/to/cernic-import-output/CERNIC-import-test.db
/absolute/path/to/cernic-import-output/conversion-summary.md
/absolute/path/to/cernic-import-output/organisation-city-codes.tsv
```

转换规则：

- 所有对象的 `source` 统一为 `CERNIC`。
- 旧 `person`、`role` 的 NIC-HDL 及全部 `admin-c`、`tech-c`、`zone-c` 引用统一改为 `-CER`；发生同名冲突时为后续对象分配可用数字。
- `conn-i` 转为 `organisation` 主键；同一 `conn-i` 只保留一个 organisation。已核对的重复 ID 使用工作簿最终选择的 `org-name` 与 `c-org-name`。
- organisation 汇总关联的 inetnum、inet6num、domain 信息：保留可用的中英文名称、历史名称、地区字段和 notify；统一入口不写 `tech-c`，仅把非“预留”IP 备案中能够唯一确定的紧急联系人写入 `admin-c`。直接调用旧转换器且未提供预处理报告时，仍沿用不写 organisation 联系人的兼容行为。每个 organisation 必须写入 `c-cityid`：优先以旧 `c-city` 匹配网站行政区划表中的城市或区县编码，无法精确匹配时退回 `c-province` 的省级编码，仍无法匹配时写入 `000000`；逐条结果写入私有的 `organisation-city-codes.tsv`。
- domain 保留旧主键和 `netname`。统一入口直接采用预处理阶段的唯一匹配结果；零匹配和多候选均生成带统一清理标记的 `FK` 临时 organisation，不再使用地区兜底或强绑真实机构。只有直接调用旧转换器且未提供预处理报告时，才保留历史的新疆地区兜底兼容逻辑。
- 旧数据中为零的联系人或 organisation 索引改为有效正数，所有引用同步更新。
- 无法解析的旧联系人引用生成占位 `role`，避免对象引用断裂。
- IPv4 CIDR 和区间仅用于匹配及逻辑重复检测，最终主键保留所选旧记录的原始文本；逻辑重复记录按较新的 `changed` 值保留，并逐条写入 `deduplicated-ipv4-resources.tsv`。
- 导入维护者为 `CERNIC-DBM-MNT`；脚本写入其 MD5-PW 密码哈希，实际密码仅在本地私有环境保存。
- 转换前会校验核对工作簿；失败时只生成 `review-validation-errors.md`，不会生成可导入 dump。

### 本机启动并通过 JMX 导入

导入目标必须是已按上一节 schema/data 初始化、但没有 RPSL 业务对象的独立数据库。不要对已有生产数据或 `myTEST.db` 执行本操作。`loadDump` 只新增对象，不会清库；校验或加载失败时安全模式会回滚本次导入。

部署配置至少应包含：

```properties
whois.source=CERNIC
user.handle.suffix.config=CERNIC:CER
```

启动 whois，并开启仅本地可访问的 JMX：

```bash
/usr/bin/java \
  -Dwhois \
  -Djsse.enableSNIExtension=false \
  -Dcom.sun.management.jmxremote \
  -Dcom.sun.management.jmxremote.authenticate=false \
  -Dcom.sun.management.jmxremote.ssl=false \
  -Dcom.sun.management.jmxremote.port=1099 \
  -Dcom.sun.management.jmxremote.rmi.port=1099 \
  -Djava.rmi.server.hostname=127.0.0.1 \
  -Xms1024m \
  -Xmx8g \
  -Dwhois.config=properties \
  -Duser.timezone=UTC \
  -Dhazelcast.config=hazelcast.xml \
  -Dlog4j.configurationFile=file:log4j2.xml \
  -jar whois.jar
```

本机联调可直接在项目目录运行。以下示例使用 `run-local/properties-cityid-test`，其数据库名、端口与生产配置隔离：

```bash
cd /absolute/path/to/whois/run-local
/usr/bin/java \
  -Dwhois \
  -Djsse.enableSNIExtension=false \
  -Dcom.sun.management.jmxremote \
  -Dcom.sun.management.jmxremote.authenticate=false \
  -Dcom.sun.management.jmxremote.ssl=false \
  -Dcom.sun.management.jmxremote.port=1100 \
  -Dcom.sun.management.jmxremote.rmi.port=1100 \
  -Djava.rmi.server.hostname=127.0.0.1 \
  -Xms1024m \
  -Xmx8g \
  -Dwhois.config=properties-cityid-test \
  -Duser.timezone=UTC \
  -Dhazelcast.config=hazelcast.xml \
  -Dlog4j.configurationFile=file:log4j2.xml \
  -jar ../whois-db/target/whois-db-1.0-SNAPSHOT.jar
```

本机启动网站，`WHOIS_URL_PREFIX` 必须指向上面 whois 的 REST 端口：

```bash
cd /absolute/path/to/whois_website
mvn package -DskipTests
WHOIS_URL_PREFIX=http://127.0.0.1:1081 \
WHOIS_SOURCE=CERNIC \
WHOIS_MNTNER=CERNIC-DBM-MNT \
WHOIS_PASSWORD=<CERNIC-DBM-MNT密码> \
/usr/bin/java -jar target/whois-website-1.0.0.jar --spring.profiles.active=local
```

本机地址：网站为 `http://127.0.0.1:8091/home`，WHOIS REST 为 `http://127.0.0.1:1081`，传统 WHOIS 查询端口为 `1044`。

下载并调用 jmxterm：

```bash
curl -fLO https://repo1.maven.org/maven2/org/cyclopsgroup/jmxterm/1.0.4/jmxterm-1.0.4-uber.jar
java --add-exports jdk.jconsole/sun.tools.jconsole=ALL-UNNAMED \
  -jar jmxterm-1.0.4-uber.jar
```

在 jmxterm 中执行，导入文件使用绝对路径：

```text
open 127.0.0.1:1099
bean net.ripe.db.whois:name=Bootstrap
run loadDump cernic-import /absolute/path/to/CERNIC-import-test.db
```

也可不进入交互界面，直接执行导入：

```bash
printf 'open 127.0.0.1:1100\nbean net.ripe.db.whois:name=Bootstrap\nrun loadDump cernic-import /absolute/path/to/CERNIC-import-test.db\nquit\n' \
  | java --add-exports jdk.jconsole/sun.tools.jconsole=ALL-UNNAMED \
      -jar /absolute/path/to/jmxterm-1.0.4-uber.jar -n
```

返回内容中应包含：

```text
Ran in transactional, safe mode: committing DB changes
FINISHED
<对象数量> succeeded
0 failed in pass 1
0 failed in pass 2
```

`<对象数量>` 取决于实际旧数据，不是示例数据的固定值。导入后可查询联系人和网段验证引用，例如：

```bash
{ printf 'AA2-CER\r\n'; sleep 1; } | nc -w 8 127.0.0.1 <port.query>
curl http://127.0.0.1:1080/whois/CERNIC/person/AA2-CER
```

## SyncUpdates 示例

请求体示例：

```text
----------------------------466af99a9520
Content-Disposition: form-data; name="DATA"

person:         TEST GWY
nic-hdl:        AUTO-1
address:        Singel 258, 1016 AB Amsterdam
phone:          +31205354444
e-mail:         user@example.net
remarks:        This Object is only an example.
source:         TEST
mnt-by:         TEST-DBM-MNT
password:       yourpassword
----------------------------466af99a9520--
```

新增：

```bash
curl -v -4 -X POST \
  -H "Content-Type: multipart/form-data; boundary=--------------------------466af99a9520" \
  --data-binary @create_person.txt \
  "http://localhost:1080/whois/syncupdates/test?NEW=yes"
```

修改：

```bash
curl -v -4 -X POST \
  -H "Content-Type: multipart/form-data; boundary=--------------------------466af99a9520" \
  --data-binary @create_person.txt \
  "http://localhost:1080/whois/syncupdates/test"
```

删除：

```bash
curl -v -4 -X POST \
  -H "Content-Type: multipart/form-data; boundary=--------------------------466af99a9520" \
  --data-binary @delete_object.txt \
  "http://localhost:1080/whois/syncupdates/test?DELETE=yes"
```

## REST 示例

新增对象：

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  --data @create_person_json.txt \
  "http://localhost:1080/whois/test/person?password=xxx"
```

查询对象：

```bash
curl -X GET \
  -H "Content-Type: application/json" \
  "http://localhost:1080/whois/test/person/GWY1-TEST"
```

修改对象：

```bash
curl -X PUT \
  -H "Content-Type: application/json" \
  --data @create_person_json.txt \
  "http://localhost:1080/whois/test/person?password=xxx"
```

删除对象：

```bash
curl -v -4 -X DELETE \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  --data @delete_person_json.txt \
  "http://localhost:1080/whois/test/person/GWY1-TEST?password=xxx"
```

搜索：

```text
http://localhost:1080/whois/search?query-string=0.0.0.0
http://localhost:1080/whois/search?query-string=ns.example.net&inverse-attribute=nserver
```

## 邮件更新

邮件更新链路涉及：

- `whois-api/src/main/java/net/ripe/db/whois/api/mail/dequeue/MessageDequeue.java`
- `whois-api/src/main/java/net/ripe/db/whois/api/mail/dao/MailMessageDao.java`
- `whois-api/src/main/java/net/ripe/db/whois/api/mail/dao/MailMessageDaoJdbc.java`
- `whois-api/src/main/java/net/ripe/db/whois/api/mail/dequeue/BouncedMessageParser.java`
- `whois-api/src/main/java/net/ripe/db/whois/api/UpdateCreator.java`
- `whois-update/src/main/java/net/ripe/db/whois/update/domain/UpdateContext.java`

Postfix/Dovecot 示例：

```bash
sudo apt-get install -y postfix dovecot-imapd dovecot-pop3d dovecot-lmtpd mailutils
sudo systemctl restart postfix dovecot
```

基础连通性测试：

```bash
telnet localhost 25
telnet localhost 110
telnet localhost 143
```

邮件更新示例：

```text
telnet test-cernet.com smtp
helo test-cernet.com
mail from: gwy@test-cernet.com
rcpt to: dbase@test-cernet.com
data
From: gwy <gwy@test-cernet.com>
To: dbase@test-cernet.com
Subject: NEW


password: yourpassword
person:         TEST GWY
nic-hdl:        AUTO-1
address:        Singel 258
phone:          +31-1234567890
e-mail:         user@example.net
mnt-by:         TEST-DBM-MNT
source:         TEST
.
quit
```

注意：email-update 中每条更新数据之间需要空行分隔。

## MD5 密码生成示例

```python
# python2
import crypt

password = 'emptypassword'
salt = '$1$N2zhyJ3g$'
print(crypt.crypt(password, salt))
```

输出示例：

```text
$1$N2zhyJ3g$hzX7XTL84DtBkCWhBZE2c/
```

## HTTPS 配置

相关代码：

```text
whois-api/src/main/java/net/ripe/db/whois/api/httpserver/WhoisKeystore.java
```

配置项示例：

```properties
whois.private.keys=/home/dbase/tmp_server_tra.key
whois.certificates=/home/dbase/tmp_server.crt
whois.keystore=/home/dbase/certbot/server.jks
port.api.secure=0
```

证书生成示例：

```bash
keytool -genkeypair \
  -alias whois \
  -keyalg RSA \
  -keysize 4096 \
  -storetype JKS \
  -keystore whois.jks \
  -validity 3650 \
  -storepass 20240731

keytool -export \
  -alias whois \
  -keystore whois.jks \
  -storetype JKS \
  -storepass 20240731 \
  -rfc \
  -file whois.cer

keytool -v -importkeystore \
  -srckeystore whois.jks \
  -srcstoretype jks \
  -srcstorepass 20240731 \
  -destkeystore whois.pfx \
  -deststoretype pkcs12 \
  -deststorepass 20240731 \
  -destkeypass 20240731

openssl pkcs12 -in whois.pfx -nocerts -nodes -out whois.pri.key
```

## nserver 修改线索

搜索入口：

```text
whois-api/src/main/java/net/ripe/db/whois/api/rest/WhoisSearchService.java
whois-api/src/main/java/net/ripe/db/whois/api/rest/RpslObjectStreamer.java
whois-query/src/main/java/net/ripe/db/whois/query/handler/QueryHandler.java
whois-query/src/main/java/net/ripe/db/whois/query/executor/SearchQueryExecutor.java
```

用途：

- 支持 `nserver` 类型。
- 支持基于 `nserver` 的 inverse search。
- 适配 domain 对象相关存储和索引。
