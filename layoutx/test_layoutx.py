# -*- coding: utf-8 -*-
"""layoutx 回归测试：保护“新版不能比旧版差”的底线。

基线失败是预期，修复后应全部通过。
当前基线（diff.jsonl / comparison_summary.json）预计失败用例：
  - test_no_doc_level_new_worse          （category “新版更差” = 3）
  - test_no_field_level_new_worse        （authors new_worse=1, affiliations new_worse=2）
  - test_no_garbage_author_tokens        （新作者中出现 “조록”）
  - test_doc_13570535_author_fix
  - test_doc_13564429_title_and_authors
  - test_doc_13567494_affiliation_fix
  - test_doc_13568651_author_not_glued
另外 test_title_pollution_appended_person_names_is_new_worse 标记为
expectedFailure：基线已知缺口（题名追加人名被判 new_better），修复后应 XPASS。

本文件不联网、不调用 front_doc()、不写 out_layout / diff.jsonl；
A 层用 monkeypatch 提供合成页面数据，B 层只读现有结果文件。
运行：python -m pytest layoutx/test_layoutx.py -q  或  python layoutx/test_layoutx.py

新增守卫（保护“继续完善”后的底线）：裁定 reason 齐备、doc/字段级 new_worse 归零、
different_unresolved 不超过基线，以及两例合成判优回归（单渠道 OCR 异读、期刊名误作机构）。
"""

import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)
sys.path.insert(0, PIPE)
sys.path.insert(0, HERE)

import compare
import extract

DIFF_PATH = os.path.join(HERE, "diff.jsonl")
SUMMARY_PATH = os.path.join(HERE, "comparison_summary.json")
MANUAL_REVIEW_PATH = os.path.join(HERE, "manual_review.json")
EXTRACT_PATH = os.path.join(HERE, "extract.py")
COMPARE_PATH = os.path.join(HERE, "compare.py")

GARBAGE_AUTHOR_TOKENS = ("조록", "본체론", "알고리즘", "유형분류", "평가하였고")
AUTHOR_SUPERSET_BASELINE = 14

# “继续完善”守卫基线。manual_review 当前 32 条；超出部分视为新增裁定。
MANUAL_REVIEW_BASELINE = 32
MIN_REASON_LEN = 20
VAGUE_REASON_MARKERS = ("人工判定更好", "人工判定较优", "人工复核更优", "人工认为更好")
UNRESOLVED_TOTAL_BASELINE = 165
UNRESOLVED_FIELD_BASELINE = {
    "title": 14, "title_en": 20, "authors": 27,
    "affiliations": 53, "keywords": 35, "keywords_en": 16,
}


def _fake_load(mapping):
    """按路径后缀返回合成 JSON，替代 compare.load / extract.load。"""
    def load(path):
        key = str(path).replace("\\", "/")
        for suffix, data in mapping.items():
            if key.endswith(suffix):
                return data
        raise AssertionError("unexpected load path: %s" % path)
    return load


class UnitTests(unittest.TestCase):
    """只读导入函数 + 合成结构；不触碰任何输出文件。"""

    def test_no_doc_id_special_cases_in_extract_or_compare(self):
        patterns = (
            r'(?:==|!=|is)\s*["\']135\d{5}["\']',
            r'in\s*[\(\[\{]\s*["\']135\d{5}["\']',
            r'startswith\(\s*["\']135\d{5}',
            r'["\']135\d{5}["\']',
        )
        for path in (EXTRACT_PATH, COMPARE_PATH):
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for pattern in patterns:
                self.assertIsNone(
                    re.search(pattern, source),
                    "%s 存在按 doc-id 特判的分支: /%s/" % (os.path.basename(path), pattern),
                )

    def test_lower_page_isolated_short_text_is_not_author_evidence(self):
        """模拟 조록：paddle 片段 y≈771，无星标/罗马名/机构配对，且位于作者区之外。"""
        unified = {"w": 1000, "h": 1000, "unified": [
            {"type": "Text", "box": [155, 213, 760, 304],
             "main_text": "스팸 대응을 위한 성과지표 개선 방안 연구"},
        ]}
        merged = {"w": 1000, "h": 1000, "channels": {"paddle": [
            {"cat": "Text", "box": [534.4, 771.1, 585.5, 796.0], "text": "조록"},
            {"cat": "Text", "box": [260.3, 473.4, 486.7, 499.6], "text": "배성중(Sung-Jung Bae)"},
        ]}}
        mapping = {"unified/page_0.json": unified, "merged/page_0.json": merged}
        with mock.patch.object(compare.os.path, "exists", lambda p: True), \
                mock.patch.object(compare, "load", _fake_load(mapping)):
            raws, title_text = compare.author_zone_channels("TEST00001")

        texts = [r["text"] for r in raws]
        self.assertNotIn("조록", texts, "页面下部的孤立短文本不应进入作者区证据")
        self.assertIn("배성중(Sung-Jung Bae)", texts, "作者区内的正常署名应保留")
        self.assertFalse(compare.structured_author_support("조록", raws, title_text))

    @unittest.expectedFailure
    def test_title_pollution_appended_person_names_is_new_worse(self):
        """新题名 = 旧题名 + “人名, 人名” 尾段应判 new_worse（基线缺口，修复后 XPASS）。"""
        old_title = "스팸 대응을 위한 성과지표 개선 방안 연구"
        new_title = old_title + " 조일형, 유진호"
        old = {"title": {"value": old_title}, "title_en": {"value": None}}
        new = {"title": {"value": new_title}, "title_en": {"value": None}}
        raws = [
            {"page": 1, "channel": "mineru", "text": old_title},
            {"page": 1, "channel": "dots", "text": old_title},
            {"page": 1, "channel": "paddle", "text": old_title + "\n조일형*,유진호**"},
        ]
        with mock.patch.object(compare, "raw_channels", lambda doc: raws):
            result = compare.verdict("TEST00002", old, new)
        self.assertEqual("new_worse", result["fields"]["title"].get("assessment"))

    def test_affiliation_granularity_lig_and_team_stay_one_item(self):
        """LIG넥스원 + 레이다기술팀 连续出现时保持一项，不拆成两项判差。"""
        merged = {"w": 1000, "h": 1000, "channels": {"dots": [
            {"cat": "Text", "box": [100, 300, 900, 340],
             "text": "LIG넥스원, 레이다기술팀, 수석연구원\n경북대학교 전기공학과"},
        ]}}
        p0 = {"w": 1000, "h": 1000, "unified": [
            {"type": "Text", "box": [100, 200, 900, 240], "main_text": "제목"},
        ]}
        title = {"box": [100, 200, 900, 240], "main_text": "제목"}
        authors = [{"name": "권용욱", "roman": "Yongwook Kwon", "affiliation": None}]
        with mock.patch.object(extract.os.path, "exists", lambda p: True), \
                mock.patch.object(extract, "load", _fake_load({"merged/page_0.json": merged})):
            affs = extract.extract_affiliations(p0, title, authors, "TEST00003")

        both = [a for a in affs if "LIG넥스원" in a and "레이다기술팀" in a]
        self.assertEqual(1, len(both), affs)
        single = [a for a in affs if ("LIG넥스원" in a) ^ ("레이다기술팀" in a)]
        self.assertFalse(single, affs)
        self.assertEqual(compare.aff_norm("LIG넥스원, 레이다기술팀"),
                         compare.aff_norm("LIG넥스원 레이다기술팀"))

    def test_title_ocr_variant_prefer_multichannel(self):
        """旧题名仅单渠道命中（OCR 异读）、新题名被两渠道支持时应判 new_better。"""
        old_title = "멘로드라마의 전략과 정치성"
        new_title = "멜로드라마의 전략과 정치성"
        raws = [
            {"page": 1, "channel": "mineru", "text": old_title, "box": [0, 0, 100, 20]},
            {"page": 1, "channel": "dots", "text": new_title, "box": [0, 0, 100, 20]},
            {"page": 1, "channel": "paddle", "text": new_title, "box": [0, 0, 100, 20]},
        ]
        old = {"title": {"value": old_title}, "title_en": {"value": None}}
        new = {"title": {"value": new_title}, "title_en": {"value": None}}
        with mock.patch.object(compare, "raw_channels", lambda doc: raws):
            result = compare.verdict("TEST00004", old, new)
        title = result["fields"]["title"]
        self.assertEqual({"old": 1, "new": 2}, title["channel_votes"])
        self.assertEqual("new_better", title.get("assessment"))

    def test_affiliation_journal_name_not_better(self):
        """旧机构是期刊名/参考文献片段、新机构是真实大学机构时应判 new_better。"""
        old_affs = ["한국어문연구 제12집"]
        new_affs = ["서울대학교 국어국문학과"]
        raws = [
            {"page": 1, "channel": "mineru", "text": new_affs[0], "box": [0, 0, 100, 20]},
            {"page": 1, "channel": "dots", "text": "참고문헌: " + old_affs[0],
             "box": [0, 200, 100, 220]},
            {"page": 1, "channel": "paddle", "text": new_affs[0], "box": [0, 0, 100, 20]},
        ]
        old = {"title": {"value": "제목"}, "title_en": {"value": None},
               "affiliations": old_affs}
        new = {"title": {"value": "제목"}, "title_en": {"value": None},
               "affiliations": new_affs}
        with mock.patch.object(compare, "raw_channels", lambda doc: raws):
            result = compare.verdict("TEST00005", old, new)
        aff = result["fields"]["affiliations"]
        self.assertFalse(aff["same"])
        self.assertEqual("new_better", aff.get("assessment"))
        self.assertTrue(any(e["channel_supported"] and e["value"] == new_affs[0]
                            for e in aff["evidence"]))


class IntegrationTests(unittest.TestCase):
    """只读 diff.jsonl / comparison_summary.json；主线重跑流水线后执行。"""

    @classmethod
    def setUpClass(cls):
        with open(SUMMARY_PATH, encoding="utf-8") as f:
            cls.summary = json.load(f)
        with open(DIFF_PATH, encoding="utf-8") as f:
            cls.records = [json.loads(line) for line in f if line.strip()]
        cls.by_doc = {r["doc"]: r for r in cls.records}

    def _new_values(self, doc, field):
        self.assertIn(doc, self.by_doc, "diff.jsonl 缺少文档 %s" % doc)
        return self.by_doc[doc]["fields"][field]["new"]

    def _author_names(self, doc):
        return [a.get("name") or "" for a in self._new_values(doc, "authors") or []]

    def test_total_953(self):
        self.assertEqual(953, self.summary["total"])

    def test_no_doc_level_new_worse(self):
        self.assertEqual(0, self.summary["categories"].get("新版更差", 0),
                         self.summary["categories"])

    def test_no_field_level_new_worse(self):
        bad = {field: counts.get("new_worse", 0)
               for field, counts in self.summary["fields"].items()
               if counts.get("new_worse", 0)}
        self.assertEqual({}, bad)

    def test_author_superset_better_not_increased(self):
        count = 0
        for record in self.records:
            field = record["fields"]["authors"]
            if field.get("assessment") != "new_better":
                continue
            old_names = {a.get("name") for a in field["old"] or []}
            new_names = {a.get("name") for a in field["new"] or []}
            if old_names < new_names:
                count += 1
        self.assertLessEqual(count, AUTHOR_SUPERSET_BASELINE,
                             "作者超集判优数量从基线 %d 增至 %d" % (AUTHOR_SUPERSET_BASELINE, count))

    def test_no_garbage_author_tokens(self):
        bad = []
        for record in self.records:
            for name in self._author_names(record["doc"]):
                for token in GARBAGE_AUTHOR_TOKENS:
                    if token in name:
                        bad.append((record["doc"], name, token))
        self.assertEqual([], bad)

    def test_doc_13570535_author_fix(self):
        names = self._author_names("13570535")
        self.assertNotIn("조록", names, names)
        self.assertIn("배성중", names, names)

    def test_doc_13564429_title_and_authors(self):
        title = self._new_values("13564429", "title") or ""
        self.assertNotIn("조일형, 유진호", title, title)
        self.assertTrue(self._author_names("13564429"), "修复后新作者不应为空")

    def test_doc_13567494_affiliation_fix(self):
        affs = self._new_values("13567494", "affiliations") or []
        self.assertFalse(any("대구가틀릭대학교" in a for a in affs), affs)

    def test_doc_13568651_author_not_glued(self):
        names = self._author_names("13568651")
        self.assertNotIn("타이푼카르타브", names, names)

    def test_doc_13569428_affiliation_merged(self):
        affs = self._new_values("13569428", "affiliations") or []
        both = [a for a in affs if "LIG넥스원" in a and "레이다기술팀" in a]
        self.assertEqual(1, len(both), affs)
        single = [a for a in affs if ("LIG넥스원" in a) ^ ("레이다기술팀" in a)]
        self.assertFalse(single, affs)

    def test_doc_13569417_authors_keep_four_roman(self):
        authors = self._new_values("13569417", "authors") or []
        self.assertEqual(4, len(authors), authors)
        self.assertTrue(all(a.get("roman") for a in authors), authors)

    def test_manual_review_entries_have_reasons(self):
        """每条“新版更优”裁定须有 >=20 字 reason；新增裁定不得空泛。"""
        with open(MANUAL_REVIEW_PATH, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertTrue(entries, "manual_review.json 不应为空")
        for idx, entry in enumerate(entries):
            if entry.get("verdict") != "新版更优":
                continue
            doc = entry.get("doc")
            reason = entry.get("reason")
            self.assertIsInstance(reason, str, "裁定 %s 的 reason 必须是字符串" % doc)
            reason = reason.strip()
            self.assertTrue(reason, "裁定 %s 的 reason 不能为空" % doc)
            self.assertGreaterEqual(
                len(reason), MIN_REASON_LEN,
                "裁定 %s 的 reason 不足 %d 字: %s" % (doc, MIN_REASON_LEN, reason))
            if idx >= MANUAL_REVIEW_BASELINE:
                for marker in VAGUE_REASON_MARKERS:
                    self.assertNotIn(
                        marker, reason,
                        "新增裁定 %s 的 reason 不能空泛（%s）: %s" % (doc, marker, reason))

    def test_no_new_worse_after_adjudication(self):
        """集成守卫：证据裁定/规则增强后 doc 级与字段级 new_worse 仍须为 0。"""
        doc_worse = [r["doc"] for r in self.records if r.get("category") == "新版更差"]
        self.assertEqual([], doc_worse, "doc 级出现新版更差: %s" % doc_worse)
        field_worse = [(r["doc"], f) for r in self.records
                       for f, fr in r["fields"].items()
                       if fr.get("assessment") == "new_worse"]
        self.assertEqual([], field_worse, "字段级出现 new_worse: %s" % field_worse)

    def test_unresolved_reduced(self):
        """字段级 different_unresolved 不得回归：总数与每字段都不超过基线。

        注意：UNRESOLVED_FIELD_BASELINE 是本轮（165 = 14+20+27+53+35+16）的实测值，
        总数基线即各字段基线之和，所以总数只能断言"不超过"，否则每条字段守卫
        都合规时该断言仍恒假。真正下降时把基线常量一起下调即可。"""
        counts = {f: 0 for f in UNRESOLVED_FIELD_BASELINE}
        for record in self.records:
            for field, fr in record["fields"].items():
                if fr.get("assessment") == "different_unresolved":
                    counts[field] = counts.get(field, 0) + 1
        total = sum(counts.values())
        self.assertLessEqual(
            total, UNRESOLVED_TOTAL_BASELINE,
            "different_unresolved 总数超过基线 %d: %d，明细=%s"
            % (UNRESOLVED_TOTAL_BASELINE, total, counts))
        for field, baseline in UNRESOLVED_FIELD_BASELINE.items():
            self.assertLessEqual(
                counts.get(field, 0), baseline,
                "%s 未决 %d 超过基线 %d" % (field, counts.get(field, 0), baseline))


if __name__ == "__main__":
    unittest.main(verbosity=2)
