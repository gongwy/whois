# Minimal synthetic CERNET import fixture

`CERNET-import.db` is an 11-object, synthetic-only seed for CRUD testing in a fresh, isolated WHOIS database. It contains one test person and maintainer, two test organisations, IPv4 and IPv6 parent/child ranges, one nameserver, and a parent/child domain pair. The records use `source: CERNET` and `mnt-by: CERNET-DBM-MNT`; names and reserved example IP/email values identify them as test data.

The dump includes an MD5-PW hash for `CERNET-DBM-MNT`. The matching plaintext password is deliberately kept out of GitHub. Generate a new local dump and private password with:

```bash
python3 tools/generate_minimal_cernet_test_dump.py
```

The generator writes both files under the ignored `outputs/minimal-cernet-test/` directory with mode `0600`. It generates a new random password each time. Use the generated password as the website's `WHOIS_PASSWORD` before exercising authenticated changes.

IPv6 status follows this WHOIS implementation's supported status enum: parent `ALLOCATED-BY-LIR`, child `ASSIGNED`. `ALLOCATED PA` and `ASSIGNED PA` are not valid `inet6num` statuses here.

Import this fixture only into a fresh, isolated test database. It must not be merged with real WHOIS data. `wisctl.sh import-data` is add-only and rejects a populated database or a second import.
