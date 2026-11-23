#!/usr/bin/env python3
"""Generate a private, synthetic CERNET WHOIS dump for CRUD testing."""

from pathlib import Path
import os
import secrets
import string
import subprocess


OUT = Path(__file__).resolve().parents[1] / "outputs" / "minimal-cernet-test"
PASSWORD_FILE = OUT / "CERNET-DBM-MNT.password"
DUMP_FILE = OUT / "CERNET-import.db"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(OUT, 0o700)
    alphabet = string.ascii_letters + string.digits + "-_"
    password = "Tst!" + "".join(secrets.choice(alphabet) for _ in range(28))
    salt = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    password_hash = subprocess.run(
        ["openssl", "passwd", "-1", "-salt", salt, password],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    objects = [
        f"""person: CERNET WHOIS Test Operator
address: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
phone: +86 10 0000 0000
e-mail: whois-test@example.invalid
nic-hdl: TST1-CER
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        f"""mntner: CERNET-DBM-MNT
descr: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
admin-c: TST1-CER
upd-to: whois-test@example.invalid
auth: MD5-PW {password_hash}
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """organisation: ZZ999998
org-name: CERNET WHOIS Test Education Bureau
c-org-name: CERNET WHOIS 测试教育局
address: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
country: CN
admin-c: TST1-CER
e-mail: whois-test@example.invalid
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """organisation: ZZ999999
org-name: CERNET WHOIS Test School
c-org-name: CERNET WHOIS 测试学校
address: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
country: CN
admin-c: TST1-CER
e-mail: whois-test@example.invalid
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """inetnum: 192.0.2.0 - 192.0.2.255
netname: CERNET-TEST-ONLY-V4-PARENT
registration-date: 2026-01-01
org: ZZ999998
admin-c: TST1-CER
status: ALLOCATED PA
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
mnt-lower: CERNET-DBM-MNT
source: CERNET""",
        """inetnum: 192.0.2.0 - 192.0.2.127
netname: CERNET-TEST-ONLY-V4-SCHOOL
registration-date: 2026-01-02
org: ZZ999999
admin-c: TST1-CER
status: ASSIGNED PA
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """inet6num: 2001:db8::/32
netname: CERNET-TEST-ONLY-V6-PARENT
registration-date: 2026-01-01
org: ZZ999998
admin-c: TST1-CER
status: ALLOCATED-BY-LIR
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
mnt-lower: CERNET-DBM-MNT
source: CERNET""",
        """inet6num: 2001:db8:1::/48
netname: CERNET-TEST-ONLY-V6-SCHOOL
registration-date: 2026-01-02
org: ZZ999999
admin-c: TST1-CER
status: ASSIGNED
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """nameserver: ns1.example.edu.cn
ipnum: 192.0.2.53
notify: whois-test@example.invalid
source: CERNET""",
        """domain: example.edu.cn
netname: CERNET-TEST-ONLY-DOMAIN-PARENT
registration-date: 2026-01-01
org: ZZ999998
admin-c: TST1-CER
nserver: ns1.example.edu.cn
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
source: CERNET""",
        """domain: test-only.example.edu.cn
netname: CERNET-TEST-ONLY-DOMAIN
registration-date: 2026-01-02
org: ZZ999999
admin-c: TST1-CER
nserver: ns1.example.edu.cn
remarks: TEST ONLY - REMOVE BEFORE PRODUCTION IMPORT
mnt-by: CERNET-DBM-MNT
source: CERNET""",
    ]

    DUMP_FILE.write_text("\n\n".join(objects) + "\n", encoding="utf-8")
    PASSWORD_FILE.write_text(password + "\n", encoding="utf-8")
    os.chmod(DUMP_FILE, 0o600)
    os.chmod(PASSWORD_FILE, 0o600)
    print(f"Wrote {DUMP_FILE} ({len(objects)} synthetic objects)")
    print(f"Wrote private maintainer password to {PASSWORD_FILE}")


if __name__ == "__main__":
    main()
