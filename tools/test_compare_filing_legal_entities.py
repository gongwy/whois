#!/usr/bin/env python3

import unittest

from compare_filing_legal_entities import (
    canonical_domain,
    domain_tokens,
    identity_key,
    normalized,
    province,
    text,
)


class FilingEntityComparisonTests(unittest.TestCase):
    def test_domain_preserves_full_hostname(self) -> None:
        self.assertEqual(canonical_domain("WWW.EDU.CN."), "www.edu.cn")
        self.assertNotEqual(canonical_domain("WWW.EDU.CN."), "edu.cn")

    def test_domain_tokens_split_without_parent_domain_reduction(self) -> None:
        self.assertEqual(
            domain_tokens("www.edu.cn；service.example.edu.cn"),
            ["www.edu.cn", "service.example.edu.cn"],
        )

    def test_zero_width_characters_do_not_change_identity(self) -> None:
        self.assertEqual(normalized("\u200c1244010045534845X6"), "1244010045534845x6")
        self.assertEqual(text("A\u200bB"), "AB")

    def test_identity_uses_type_and_number(self) -> None:
        self.assertNotEqual(
            identity_key("事业单位法人证书", "123"),
            identity_key("统一社会信用代码证书", "123"),
        )

    def test_province_uses_first_region_component(self) -> None:
        self.assertEqual(province("广东省-深圳市-罗湖区"), "广东省")


if __name__ == "__main__":
    unittest.main()
