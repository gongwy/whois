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
user.handle.suffix.config=CERNET:CER,AP:CER,CN:CER
```

含义：

- 当当前 `whois.source` 为 `CERNET`、`AP` 或 `CN` 时，自动生成的 NIC-HDL 后缀使用 `CER`。
- 其他未配置的 source 仍使用原有默认规则，即使用当前 source 作为后缀。
- 配置值中的后缀可以写成 `CER`，也兼容写成 `-CER`，代码会去掉开头的 `-`。
- 配置项为空时不改变原有行为。

示例：

```properties
whois.source=CERNET
user.handle.suffix.config=CERNET:CER,AP:CER,CN:CER
```

此时提交：

```text
person:     TEST USER
nic-hdl:    AUTO-1
source:     CERNET
```

生成结果会使用类似：

```text
nic-hdl:    TU1-CER
```

显式提交的 NIC-HDL 也同步支持该配置。例如：

```text
nic-hdl:    XX1-CER
source:     CERNET
```

在 `whois.source=CERNET` 且配置了 `CERNET:CER` 时会被认为合法。

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

## 数据加载

准备测试数据：

```bash
sudo cp whois-scheduler/src/test/resources/TEST.db /home/dbase/
```

使用 jmxterm：

```bash
java --add-exports jdk.jconsole/sun.tools.jconsole=ALL-UNNAMED -jar jmxterm-1.0.4-uber.jar
open <PID>
bean net.ripe.db.whois:name=Bootstrap
run loadDump comment TEST.db
```

看到 `220 succeed` 表示加载成功。

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
