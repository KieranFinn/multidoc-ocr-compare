# -*- coding: utf-8 -*-
import io
import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
from unittest import mock

import merge_out
import pipeline
import qc
import stages
import unify
import viewer
import workspaces
import golden


class UnifiedSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.out_root = self.root / "out"
        self.doc_dir = self.out_root / "paper"
        self.udir = self.doc_dir / "unified"
        self.udir.mkdir(parents=True)
        self.page = self.udir / "page_0.json"
        self.page.write_text(json.dumps({"unified": [{"uid": "u0"}]}), encoding="utf-8")
        self.cfg = self.root / "channels.json"
        self.cfg.write_text("{}", encoding="utf-8")
        self.sources = {}
        for ch in ("mineru", "dots", "paddle"):
            src = self.root / "inbox" / ch
            src.mkdir(parents=True)
            self.sources[ch] = str(src)
        self.saved_cfg = (pipeline.CFG, pipeline.CFG_FP, pipeline._io.CFG, pipeline._io.CFG_FP)

    def tearDown(self):
        pipeline.CFG, pipeline.CFG_FP, pipeline._io.CFG, pipeline._io.CFG_FP = self.saved_cfg
        self.tmp.cleanup()

    def test_stale_unified_is_preserved_but_not_loaded(self):
        pages, driven = pipeline._load_fresh_unified(str(self.udir), time.time())
        self.assertIsNone(pages)
        self.assertFalse(driven)
        self.assertTrue(self.page.is_file())

    def test_fresh_unified_is_loaded(self):
        summary = self.udir / "_unified_summary.json"
        summary.write_text("{}", encoding="utf-8")
        now = time.time()
        os.utime(summary, (now + 1, now + 1))
        pages, driven = pipeline._load_fresh_unified(str(self.udir), now)
        self.assertTrue(driven)
        self.assertEqual(pages, {0: [{"uid": "u0"}]})

    def _run_main(self, extra=()):
        with mock.patch.object(pipeline, "log"), \
                mock.patch.object(pipeline, "render_pages", return_value=[(100, 100)]), \
                mock.patch.object(pipeline, "scan_inbox", return_value=self.sources), \
                mock.patch.object(pipeline, "preserve_raw", return_value=0), \
                mock.patch.object(pipeline, "write_calib_marker"), \
                mock.patch.object(pipeline, "adapt_mineru", return_value={0: []}), \
                mock.patch.object(pipeline, "adapt_dots", return_value={0: []}), \
                mock.patch.object(pipeline, "adapt_paddle", return_value={0: []}), \
                mock.patch.object(pipeline, "build_viewer"), \
                mock.patch.object(pipeline, "build_compare_viewer") as compare, \
                mock.patch.object(merge_out, "merge_doc", return_value={"pages": 1, "blocks": 0}):
            pipeline.main([str(self.root / "paper.pdf"), "--out", str(self.doc_dir),
                           "--channels", str(self.cfg), *extra])
        return compare

    def test_no_unify_preserves_existing_directory(self):
        compare = self._run_main(("--no-unify",))
        self.assertTrue(self.page.is_file())
        self.assertIsNone(compare.call_args.kwargs["unified_pages"])
        self.assertFalse(compare.call_args.kwargs["unified_driven"])

    def test_unify_failure_preserves_directory_and_uses_out_root(self):
        with mock.patch.object(unify, "unify_doc", side_effect=RuntimeError("mock failure")) as call:
            self._run_main()
        self.assertTrue(self.page.is_file())
        call.assert_called_once_with("paper", os.path.dirname(os.path.abspath(self.doc_dir)))

    def test_citation_hover_target_covers_nonreview_block(self):
        block = {"cat": "Text", "box": [10, 10, 90, 40], "text": "原文[1]"}
        unified = [
            {"uid": "u0_1", "page": 1, "type": "Text", "box": [10, 10, 90, 40],
             "cross_page": {"spans": False}, "members": {"mineru": [block]},
             "readings": [{"ch": "mineru", "text": "原文[1]", "dice": 1.0, "flags": []}],
             "main_text": "原文[1]", "divergence": {"kind": "none", "detail": ""},
             "confidence": {"level": "high", "basis": ""}, "review": False},
            {"uid": "u0_2", "page": 1, "type": "Text", "box": [10, 50, 90, 80],
             "cross_page": {"spans": False}, "members": {"mineru": [block]},
             "readings": [{"ch": "mineru", "text": "原文[1]", "dice": 1.0, "flags": []}],
             "main_text": "原文[1]", "divergence": {"kind": "none", "detail": ""},
             "confidence": {"level": "high", "basis": ""}, "review": False},
            {"uid": "u0_3", "page": 1, "type": "Text", "box": [10, 90, 90, 120],
             "cross_page": {"spans": False}, "members": {"mineru": [block]},
             "readings": [{"ch": "mineru", "text": "原文[1]", "dice": 1.0, "flags": []}],
             "main_text": "原文[1]", "divergence": {"kind": "none", "detail": ""},
             "confidence": {"level": "high", "basis": ""}, "review": False},
        ]
        meta = {"citation_spans": [{"page": 1, "uid": "u0_1", "kind": "numeric",
                                    "sentence": "原文[1]", "raw": "原文[1]",
                                    "citation_text": "[1]", "offset": [2, 5],
                                    "linked_refs": [], "confidence": "exact"}],
                "footnote_refs": [{"page": 1, "uid": "u0_2", "num": "1",
                                   "confidence": {"level": "high"}}],
                "references": [{"page": 1, "uid": "u0_3", "num": 1,
                                "confidence": {"level": "high"}}]}
        out = self.root / "cite_viewer.html"
        viewer.build_compare_viewer(
            str(out), "cite test", [(100, 100)],
            {"mineru": {0: [block]}, "dots": None, "paddle": None},
            unified_pages={0: unified}, unified_driven=True, meta=meta)
        html = out.read_text(encoding="utf-8")
        self.assertEqual(html.count('<rect class="r cite-target"'), 3)
        for uid in ("u0_1", "u0_2", "u0_3"):
            self.assertIn('<rect class="r cite-target" data-u="%s"' % uid, html)
        self.assertEqual(html.count("e.target.closest('.mf[data-u]')"), 2)
        self.assertIn("var u=UBOX[mf.dataset.u],bx=u&&u.box", html)
        self.assertIn("r.setAttribute('class','r cite-target')", html)
        self.assertIn("svg.appendChild(r);r.classList.add('cite-on')", html)
        self.assertIn("document.querySelector(target)", html)
        self.assertIn("rect.cite-target[data-bx=", html)
        self.assertIn("mf.dataset.bx||''", html)
        self.assertIn("citeHover(mf,true)", html)
        self.assertIn("citeHover(mf,false)", html)
        self.assertIn(".r.cite-target.cite-on", html)
        self.assertIn("r.classList.add('flash')", html)   # 点击定位统一走 .flash 通道(旧 cite-on 点击分支已收敛删除)
        self.assertIn('id="citepop"', html)
        self.assertIn('id="citesvg"', html)
        self.assertIn("cs.setAttribute('viewBox'", html)
        self.assertIn('class="cpbox"', html)
        self.assertIn(".pane:not(.meta) > .pmask", html)

    def test_metadata_fields_have_individual_evidence_anchors(self):
        units = [{"uid": "u0_1", "main_text": "Alexandre Moine"},
                  {"uid": "u1_1", "main_text": "Université de Franche-Comté\nLaboratoire THÉMA UMR 6049 CNRS"},
                  {"uid": "u1_2", "main_text": "ACTORS, GEOGRAPHICAL SPACE, SYSTEM, TERRITORY"}]
        finder = viewer._meta_evidence_finder({0: units[:1] + [{"uid": "u0_0", "main_text": "621"}],
                                                1: units[1:]})
        self.assertEqual(finder.span("621-626"), "u0_0")
        self.assertEqual(finder("palavras-chave: ACTORS, GEOGRAPHICAL SPACE, SYSTEM, TERRITORY"), "u1_2")
        meta = {
            "title": {"value": "Titre", "uid": "u0_1"},
            "authors": [{"name": "Alexandre Moine", "uid": "u0_1", "page": 1,
                         "box": [20.0, 30.0, 220.0, 55.0]},
                        {"name": "Second Author", "uid": "u0_2"}],
            "affiliations": ["Université de Franche-Comté", "Laboratoire THÉMA UMR 6049 CNRS"],
            "keywords": ["ACTORS", "GEOGRAPHICAL SPACE", "SYSTEM", "TERRITORY"],
            "abstract": {"value": "Résumé", "page": 2,
                         "confidence": {"level": "mid", "uid_head": "u1_2"}},
        }
        panel = viewer._meta_cell(meta, 1, evidence=finder)
        self.assertEqual(panel.count('<b>作者</b>'), 2)
        self.assertEqual(panel.count('<b>机构</b>'), 2)
        self.assertEqual(panel.count('data-u="u1_1"'), 2)
        self.assertIn('data-u="u0_2"', panel)
        self.assertIn('data-bx="0.0,20.0,30.0,220.0,55.0"', panel)
        overview = viewer._meta_overview(meta, evidence=finder)
        self.assertIn('<b>关键词 4 个</b>', overview)
        self.assertIn('data-u="u1_2"', overview)
        self.assertIn('data-bx="0.0,20.0,30.0,220.0,55.0"', overview)

    def test_citation_sentences_are_grouped_without_losing_citations(self):
        sentence = "原文[1]与[2]"
        meta = {
            "references": [
                {"page": 2, "num": 1, "title": "First"},
                {"page": 2, "num": 2, "title": "Second"},
            ],
            "citation_spans": [
                {"page": 1, "uid": "u0_1", "kind": "numeric", "sentence": sentence,
                 "offset": [2, 5], "linked_refs": [{"index": 0}], "confidence": "exact"},
                {"page": 1, "uid": "u0_1", "kind": "numeric", "sentence": sentence,
                 "offset": [6, 9], "linked_refs": [{"index": 1}], "confidence": "partial"},
                {"page": 1, "uid": "u0_2", "kind": "numeric", "sentence": sentence,
                 "offset": [2, 5], "linked_refs": [{"index": 0}], "confidence": "exact"},
            ],
        }
        panel = viewer._meta_cell(meta, 1)
        self.assertIn("本页引文句 2 条", panel)
        self.assertIn("3 处引文", panel)
        self.assertEqual(panel.count('<tr class="mf '), 2)
        self.assertEqual(panel.count("<mark>"), 3)
        self.assertIn('<tr class="mf mid" data-u="u0_1"', panel)
        self.assertIn("→ #1 First", panel)
        self.assertIn("→ #2 Second", panel)
        overview = viewer._meta_overview(meta)
        self.assertIn("引文句 2 条</b> · 3 处引文", overview)

    def test_meta_overview_entity_checklist(self):
        meta = {
            "title": {"value": "제목"}, "keywords": ["k1"],
            "abstract": {"value": "요약 본문"},
            "authors": [{"name": "홍길동", "affiliation": "서울대학교"}],
            "affiliations": ["서울대학교"],
            "journal": {"value": "학술지"}, "year": {"value": "2025"},
            "references": [{"page": 2}], "citation_spans": [{"page": 1}],
        }
        ents = {1: {"u0_1": [{"kind": "funding", "name": "NRF", "slice": "", "ann": []},
                             {"kind": "dataset", "name": "CORPUS", "slice": "", "ann": []}]}}
        band = viewer._meta_overview(meta, ents=ents, body_chars=900)
        self.assertIn("实体清单", band)
        for g in ("题录", "来源", "内容", "引证", "文献", "实体"):
            self.assertIn('<span class="ckg">%s</span>' % g, band, g)
        for ok_item in ("标题", "关键词", "摘要", "作者", "机构", "期刊", "出版年",
                        "正文", "参考文献", "引文句", "科学数据集", "基金"):
            self.assertIn('<span class="ck ok">✓ %s</span>' % ok_item, band, ok_item)
        for no_item in ("卷", "期", "页码", "研究软件", "脚注"):
            self.assertIn('<span class="ck no">✗ %s</span>' % no_item, band, no_item)

    def test_footnote_shows_only_raw_text_without_biblio_fields(self):
        meta = {"footnote_refs": [{"page": 1, "uid": "u0_1", "num": "36",
                                   "raw": "산성의 위치와 복원 경위를 설명한다.", "title": "조선소설사",
                                   "authors": ["김태준"], "grammar": "quoted",
                                   "confidence": {"level": "low"}}]}
        panel = viewer._meta_cell(meta, 1)
        self.assertIn("本页脚注 1 条(原文)", panel)
        self.assertIn('<tr class="mf low"', panel)
        self.assertIn('<i class="md"></i>#36', panel)
        self.assertIn('<span class="mftag">原文</span>', panel)
        self.assertIn("산성의 위치와 복원 경위를 설명한다.", panel)
        self.assertNotIn("김태준", panel)
        self.assertNotIn("조선소설사", panel)
        self.assertNotIn("quoted", panel)

    def test_footnote_renders_safe_source_tags(self):
        meta = {"footnote_refs": [{"page": 1, "uid": "u0_1", "num": "1",
                                   "raw": "본문 <DIVE>제목</DIVE> 이후<sup>3</sup> <심청전 Dive 편>",
                                   "confidence": {"level": "mid"}}]}
        panel = viewer._meta_cell(meta, 1)
        self.assertIn("<DIVE>제목</DIVE>", panel)
        self.assertIn("<sup>3</sup>", panel)
        self.assertNotIn("&lt;DIVE&gt;", panel)
        self.assertNotIn("&lt;sup&gt;", panel)
        self.assertIn("&lt;심청전 Dive 편&gt;", panel)

    def test_footnote_raw_keeps_parenthesized_publication_year(self):
        from metapy import refs as meta_refs
        text = "1. 동아일보 연재 『조선소설사』(1930년 10월 31일~1931년 2월 14일)"
        block = {"uid": "u0_fn", "page": 1, "type": "Footnote", "box": [10, 10, 90, 40],
                 "main_text": text, "cross_page": {"spans": False}, "members": {}, "readings": [],
                 "divergence": {"kind": "none", "detail": ""},
                 "confidence": {"level": "high", "basis": ""}, "review": False}
        self.page.write_text(json.dumps({"page": 1, "w": 100, "h": 100, "in_ref": False,
                                         "unified": [block]}, ensure_ascii=False), encoding="utf-8")
        meta_refs.frefs_doc("paper", str(self.out_root))
        meta = json.loads((self.doc_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["footnote_refs"][0]["raw"], text[3:])

    def _fn_unified(self, uid, page, text, members=None, cat="Footnote"):
        return {"uid": uid, "page": page, "type": cat, "box": [10, 10, 90, 40],
                "main_text": text, "cross_page": {"spans": False}, "members": members or {},
                "readings": [], "divergence": {"kind": "none", "detail": ""},
                "confidence": {"level": "high", "basis": ""}, "review": False}

    def test_cross_page_footnote_uses_only_footnote_members_from_mixed_block(self):
        from metapy import refs as meta_refs
        head = self._fn_unified("u0_fn", 1, "1 조수아, 「기념")
        mixed = self._fn_unified(
            "u1_fn", 2, "두 번째 사례는 본문이다. 비」, 『한경BUSINESS』, 2025.\n2 손미정, 「기사」, 2025.",
            {"mineru": [{"cat": "Text", "text": "두 번째 사례는 본문이다."},
                        {"cat": "Footnote", "text": "비」, 『한경BUSINESS』, 2025."},
                        {"cat": "Footnote", "text": "2 손미정, 「기사」, 2025."}]})
        self.page.write_text(json.dumps({"page": 1, "w": 100, "h": 100, "in_ref": False,
                                         "unified": [head]}, ensure_ascii=False), encoding="utf-8")
        (self.udir / "page_1.json").write_text(json.dumps(
            {"page": 2, "w": 100, "h": 100, "in_ref": False, "unified": [mixed]}, ensure_ascii=False), encoding="utf-8")
        meta_refs.frefs_doc("paper", str(self.out_root))
        refs = json.loads((self.doc_dir / "metadata.json").read_text(encoding="utf-8"))["footnote_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["raw"], "조수아, 「기념비」, 『한경BUSINESS』, 2025.")
        self.assertNotIn("본문", refs[0]["raw"])
        self.assertEqual(refs[1]["raw"], "손미정, 「기사」, 2025.")

    def test_complete_footnote_does_not_absorb_next_page_text(self):
        from metapy import refs as meta_refs
        head = self._fn_unified("u0_fn", 1, "1 짧은 각주이다.")
        body = self._fn_unified("u1_fn", 2, "여기는 각주가 아니라 본문이다.")
        self.page.write_text(json.dumps({"page": 1, "w": 100, "h": 100, "in_ref": False,
                                         "unified": [head]}, ensure_ascii=False), encoding="utf-8")
        (self.udir / "page_1.json").write_text(json.dumps(
            {"page": 2, "w": 100, "h": 100, "in_ref": False, "unified": [body]}, ensure_ascii=False), encoding="utf-8")
        meta_refs.frefs_doc("paper", str(self.out_root))
        refs = json.loads((self.doc_dir / "metadata.json").read_text(encoding="utf-8"))["footnote_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["raw"], "짧은 각주이다.")

    def test_footnote_marker_allows_numeric_issue_at_content_start(self):
        from metapy import refs as meta_refs
        block = self._fn_unified("u0_fn", 1, "13 조용만의 회고이다.\n14 3호에 수록된 광고이다.")
        entries = meta_refs._fnote_collect([{"p": 0, "col": 0, "u": block,
                                             "t": block["main_text"]}])
        self.assertEqual([e["num"] for e in entries], ["13", "14"])
        self.assertEqual(entries[1]["text"], "3호에 수록된 광고이다.")

    def test_footnote_marker_ignores_inline_tags_after_number(self):
        from metapy import refs as meta_refs
        block = self._fn_unified("u0_fn", 1, "27 첫 각주이다.\n28 <sub>이는</sub> 다음 각주이다.\n29 마지막 각주이다.")
        entries = meta_refs._fnote_collect([{"p": 0, "col": 0, "u": block,
                                             "t": block["main_text"]}])
        self.assertEqual([e["num"] for e in entries], ["27", "28", "29"])
        self.assertEqual(entries[1]["text"], "<sub>이는</sub> 다음 각주이다.")

    def test_duplicate_number_in_same_unified_block_is_kept_once(self):
        from metapy import refs as meta_refs
        block = self._fn_unified("u0_fn", 1, "79 첫 번째 읽기이다.\n79 중복 읽기이다.")
        self.page.write_text(json.dumps({"page": 1, "w": 100, "h": 100, "in_ref": False,
                                         "unified": [block]}, ensure_ascii=False), encoding="utf-8")
        meta_refs.frefs_doc("paper", str(self.out_root))
        refs = json.loads((self.doc_dir / "metadata.json").read_text(encoding="utf-8"))["footnote_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["raw"], "첫 번째 읽기이다.")

    def test_reference_shows_raw_text_without_biblio_columns(self):
        meta = {"references": [{"page": 1, "uid": "u0_1", "num": 2,
                                "raw": "전체 참고문헌 원문", "title": "조선소설사",
                                "authors": ["김태준"], "year": "1930", "grammar": "quoted",
                                "source": "동아일보", "confidence": {"level": "high"}}]}
        panel = viewer._meta_cell(meta, 1)
        self.assertIn("本页文献 1 条(原文)", panel)
        self.assertIn('<tr class="mf high"', panel)
        self.assertIn('<span class="mftag">原文</span>', panel)
        self.assertIn("전체 참고문헌 원문", panel)
        self.assertNotIn("김태준", panel)
        self.assertNotIn("1930", panel)
        self.assertNotIn("조선소설사", panel)
        self.assertNotIn("동아일보", panel)
        self.assertNotIn("quoted", panel)

    def test_reference_hover_uses_matching_member_box(self):
        wrong = {"cat": "Text", "box": [10, 80, 90, 100], "text": "Hounslow, Water quality data."}
        right = {"cat": "Text", "box": [10, 10, 90, 40],
                 "text": "Filipek, Interaction of acid mine drainage."}
        unified = [{"uid": "u0_1", "page": 1, "type": "Text", "box": wrong["box"],
                    "cross_page": {"spans": False}, "members": {"mineru": [wrong, right]},
                    "readings": [{"ch": "mineru", "text": wrong["text"] + "\n" + right["text"],
                                  "dice": 1.0, "flags": []}],
                    "main_text": wrong["text"] + "\n" + right["text"],
                    "divergence": {"kind": "none", "detail": ""},
                    "confidence": {"level": "high", "basis": ""}, "review": False}]
        meta = {"references": [{"page": 1, "uid": "u0_1", "num": 14,
                                "raw": right["text"], "title": "Interaction of acid mine drainage",
                                "confidence": {"level": "high"}}]}
        out = self.root / "member_cite.html"
        viewer.build_compare_viewer(
            str(out), "member cite", [(100, 100)],
            {"mineru": {0: [wrong, right]}, "dots": None, "paddle": None},
            unified_pages={0: unified}, unified_driven=True, meta=meta)
        html = out.read_text(encoding="utf-8")
        self.assertIn('data-e="', html)
        self.assertIn('x="10" y="10" width="80" height="30"', html)
        self.assertNotIn('cite-target" data-u="u0_1" data-e=""', html)
        self.assertIn("mf.dataset.e", html)
        self.assertIn('rect.cite-target[data-e="', html)

    def test_overview_click_jumps_to_source_block(self):
        # 总览条目(含跨页关键词行)点击 → JS 跳转到原文块位置: scrollIntoView + 原图栏内定位 + 橙框闪烁
        block = {"cat": "Text", "box": [10, 10, 90, 40], "text": "原文"}
        def _u(p, uid, y):
            return {"uid": uid, "page": p + 1, "type": "Text", "box": [10, y, 90, y + 40],
                    "cross_page": {"spans": False}, "members": {"mineru": [block]},
                    "readings": [{"ch": "mineru", "text": "原文", "dice": 1.0, "flags": []}],
                    "main_text": "原文", "divergence": {"kind": "none", "detail": ""},
                    "confidence": {"level": "high", "basis": ""}, "review": False}
        unified_pages = {0: [_u(0, "u0_1", 10)], 1: [_u(1, "u1_1", 700)]}
        meta = {"keywords": [u"가나다"], "keywords_en": ["alpha"],
                "journal": {"value": u"한국지구시스템공학회지", "uid": "u1_1",
                            "confidence": {"level": "high", "source": "regex"}},
                "title": {"value": u"광산배수", "page": 2, "uid": "u1_1",
                          "confidence": {"level": "high", "source": "regex"}}}
        audit = {"issue": {"keywords": {"source": {"uid": "u1_1"},
                                        "source_en": {"uid": "u0_1"}}}}
        out = self.root / "jump_viewer.html"
        viewer.build_compare_viewer(
            str(out), "jump test", [(100, 100), (100, 100)],
            {"mineru": {0: [block], 1: [block]}, "dots": None, "paddle": None},
            unified_pages=unified_pages, unified_driven=True, meta=meta, audit=audit)
        html = out.read_text(encoding="utf-8")
        self.assertIn("function jumpToUid", html)
        self.assertIn("function flashAt", html)
        self.assertIn("function centerBlock", html)
        self.assertIn(".r.flash", html)
        self.assertIn("var UBOX=", html)
        self.assertIn("createElementNS", html)
        self.assertIn("window.scrollTo", html)   # 块中心对齐视口正中(平滑滚动)
        self.assertIn('class="mf mid" data-u="u1_1"', html)   # 关键词行(跨页)带证据 uid
        self.assertIn('class="mf mid" data-u="u0_1"', html)   # 关键词(英)行带证据 uid(同组不分头)
        self.assertIn(u'<b>期刊</b> 한국지구시스템공학회지', html)
        self.assertRegex(html, r'class="mf high" data-u="u1_1"><i class="md"></i><b>期刊</b>')  # 来源行带 uid(G3)
        self.assertRegex(html, r'class="mf high" data-u="u1_1"><i class="md"></i><b>标题</b> 광산배수')  # G1: 跨页题录行不再剥 uid

    def test_metadata_band_renders_inline_math(self):
        # cspan 句/文献题名/摘要此前只 HTML 转义, OCR 读出的行内公式($\approx/\frac/H_2O 式)
        # 以 LaTeX 源码裸露; _texesc=texlite+转义+白名单标签放行, 与正文块同一转换口径。
        sentence = u"광산배수(pH 7)는 $\\approx 660\\ mg\\ l^{-1}$ 이다"
        panel = viewer._meta_cell({"citation_spans": [{"page": 1, "uid": "u0_1",
                                                       "kind": "narrative", "sentence": sentence,
                                                       "offset": [0, 4], "confidence": "exact",
                                                       "linked_refs": []}]}, 1)
        self.assertIn(u"≈", panel)
        self.assertIn("<sup>-1</sup>", panel)
        ref_panel = viewer._meta_cell({"references": [{"page": 1, "uid": "u0_1", "num": 1,
                                                       "title": u"$\\frac{1}{2}$ 배수", "source": "",
                                                       "confidence": {"level": "high"}}]}, 1)
        self.assertIn('<i class="fr"><sup>1</sup>&frasl;<sub>2</sub></i>', ref_panel)
        plain = viewer._meta_cell({"citation_spans": [{"page": 1, "uid": "u0_1",
                                                       "kind": "narrative",
                                                       "sentence": u"일반 문장 <tag> 그대로",
                                                       "offset": [0, 4], "confidence": "exact",
                                                       "linked_refs": []}]}, 1)
        self.assertIn("&lt;tag&gt;", plain)   # 无公式特征时与纯转义等价
        self.assertNotIn("<sub>", plain)


class WsResolveTest(unittest.TestCase):
    """--ws 工作区解析(2026-11-24): --ws > KCI_WS_ROOT > .gui_prefs 当前 > 主库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.here = self.root / "pipeline"
        self.here.mkdir()
        self.custom = self.root / "beta"
        self.custom.mkdir()
        self.prefs = self.here / ".gui_prefs.json"
        self.prefs.write_text(json.dumps(
            {"workspace": "beta", "workspace_paths": {"beta": str(self.custom)}}),
            encoding="utf-8")

    def tearDown(self):
        os.environ.pop("KCI_WS_ROOT", None)
        self.tmp.cleanup()

    def resolve(self, explicit=None):
        return workspaces.resolve_ws_root(explicit=explicit, here=str(self.here),
                                          prefs_fp=str(self.prefs))

    def test_defaults_to_gui_current_workspace(self):
        root, name, src = self.resolve()
        self.assertEqual(root, str(self.custom))
        self.assertEqual(name, "beta")
        self.assertIn(".gui_prefs", src)

    def test_env_beats_prefs(self):
        env_ws = self.root / "envws"
        env_ws.mkdir()
        os.environ["KCI_WS_ROOT"] = str(env_ws)
        root, _name, src = self.resolve()
        self.assertEqual(root, str(env_ws))
        self.assertIn("KCI_WS_ROOT", src)

    def test_explicit_beats_env_and_main_keyword_resets(self):
        env_ws = self.root / "envws"
        env_ws.mkdir()
        os.environ["KCI_WS_ROOT"] = str(env_ws)
        root, name, _ = self.resolve(explicit="main")
        self.assertEqual(root, "")
        self.assertEqual(name, "main")
        root, _name, _ = self.resolve(explicit="beta")
        self.assertEqual(root, str(self.custom))

    def test_path_managed_and_unknown(self):
        root, _name, src = self.resolve(explicit=str(self.custom))
        self.assertEqual(root, str(self.custom))
        self.assertIn("路径", src)
        managed = self.here / "workspaces" / "alpha"
        managed.mkdir(parents=True)
        root, _name, src = self.resolve(explicit="alpha")
        self.assertEqual(root, str(managed))
        self.assertIn("受管", src)
        with self.assertRaises(ValueError):
            self.resolve(explicit="no-such-ws-xyz")

    def test_missing_prefs_falls_back_to_main(self):
        root, name, _src = workspaces.resolve_ws_root(
            here=str(self.here), prefs_fp=str(self.here / "nope.json"))
        self.assertEqual((root, name), ("", "main"))

    def test_stages_main_ws_flag_repoints_globals(self):
        saved = (stages.WS, stages.OUT, stages.UP)
        self.addCleanup(lambda: setattr(stages, "WS", saved[0])
                        or setattr(stages, "OUT", saved[1])
                        or setattr(stages, "UP", saved[2]))
        rc = stages._main(["--ws", str(self.custom), "list"])
        self.assertEqual(rc, 0)
        self.assertEqual(stages.OUT, str(self.custom / "out"))
        self.assertEqual(stages.UP, str(self.custom / "uploads"))


class GoldenDiffTest(unittest.TestCase):
    """golden.py 值级比对(2026-11-24): 出处扰动不算差异, 真值变化必须报。"""

    def test_provenance_churn_is_not_diff(self):
        exp = {"title": {"value": "T", "uid": "u1", "confidence": {"level": "mid"}},
               "authors": [{"name": "A", "uid": "u9", "box": [0, 0, 1, 1], "page": 3}],
               "references": [{"num": None, "raw": "X", "box": [0, 0, 1, 1]}],
               "generated": "2026-01-01 00:00"}
        act = {"title": {"value": "T", "uid": "u2", "confidence": {"level": "high"}},
               "authors": [{"name": "A"}],
               "references": [{"num": None, "raw": "X"}],
               "generated": "2026-02-02 00:00"}
        self.assertEqual(golden.diff_meta(exp, act), [])

    def test_real_value_change_is_diff(self):
        exp = {"keywords": ["a", "b"], "title": {"value": "T"}}
        act = {"keywords": ["a", "c"], "title": {"value": "T2"}}
        d = golden.diff_meta(exp, act)
        self.assertEqual({x[0] for x in d}, {"title", "keywords"})


class StageRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.out_root = self.root / "out"
        self.doc_dir = self.out_root / "paper"
        self.doc_dir.mkdir(parents=True)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"pdf")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lean_convergence_chain_excludes_fetch_ocr_and_llm_stages(self):
        self.assertEqual(stages.LEAN_IDS, list(stages.META_IDS) + ["viewer", "index"])
        for sid in ("render", "acquire", "adapt", "ner"):
            self.assertNotIn(sid, stages.LEAN_IDS)
        for sid in ("front", "issue", "viewer", "index"):
            self.assertIn(sid, stages.LEAN_IDS)

    def test_first_run_rechecks_dependencies_and_executes_full_chain(self):
        calls = []

        def fake_runner(sid):
            def run(ctx):
                calls.append(sid)
                if sid == "render":
                    target = self.doc_dir / "pages" / "page_0.jpg"
                elif sid == "acquire":
                    target = self.doc_dir / "raw" / "mineru_api" / "result.json"
                elif sid == "merge":
                    target = self.doc_dir / "merged" / "page_0.json"
                elif sid == "unify":
                    target = self.doc_dir / "unified" / "page_0.json"
                elif sid == "front":
                    target = self.doc_dir / "metadata.json"
                else:
                    return sid
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")
                return sid
            return run

        runners = {sid: fake_runner(sid) for sid in stages.ORDER}
        with mock.patch.dict(stages.RUNNERS, runners, clear=True), \
                mock.patch.object(stages, "_check_registry"), \
                mock.patch.object(stages, "_sizes", return_value=[]), \
                mock.patch.object(stages, "_find_pdf", return_value=str(self.pdf)):
            result = stages.run("paper", stages.ORDER, pdf=str(self.pdf),
                                out_root=str(self.out_root), log=lambda _s: None)

        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(calls, stages.ORDER)
        self.assertEqual(list(result["results"]), stages.ORDER)

    def test_missing_dependency_fails_instead_of_reporting_partial_success(self):
        (self.doc_dir / "pages").mkdir()
        (self.doc_dir / "pages" / "page_0.jpg").write_bytes(b"jpg")
        index = mock.Mock(return_value="index")
        with mock.patch.dict(stages.RUNNERS, {"viewer": mock.Mock(), "index": index}, clear=True), \
                mock.patch.object(stages, "_check_registry"), \
                mock.patch.object(stages, "_sizes", return_value=[(100, 100)]):
            result = stages.run("paper", ["viewer", "index"], pdf=str(self.pdf),
                                out_root=str(self.out_root), log=lambda _s: None)

        self.assertFalse(result["ok"])
        self.assertIn("merged", result["error"])
        self.assertFalse(result["results"]["viewer"]["ok"])
        self.assertTrue(result["results"]["index"]["skipped"])
        index.assert_not_called()

    def test_workbench_builds_document_table_and_log_on_startup(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)

        doc = {"pdf": str(self.pdf), "state": "未跑", "done_at": None, "miss": [],
               "ch": {ch: False for ch in gui.CH}}
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            # 无头环境(如 CI 未加 xvfb): tkinter 可导入但建不了窗口, 跳过而非判失败
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            with mock.patch.object(gui, "scan_docs", return_value={"paper": doc}), \
                    mock.patch.object(gui, "load_history", return_value={}), \
                    mock.patch.object(gui, "webapp_alive", return_value=False):
                app.refresh()
            self.assertEqual(app.tree.get_children(), ("paper",))
            self.assertTrue(app.logtxt.winfo_exists())
            self.assertTrue(app.btn_rerun.winfo_exists())
        finally:
            app.destroy()

    def test_refresh_defers_quality_checks_and_prioritizes_first_page(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        docs = {"doc%03d" % i: {"pdf": "doc.pdf", "state": "未完成", "done_at": None,
                                "miss": [], "ch": {ch: True for ch in gui.CH}}
                for i in range(40)}
        try:
            app.withdraw()
            with mock.patch.object(gui, "scan_docs", return_value=docs), \
                    mock.patch.object(gui, "load_history", return_value={}), \
                    mock.patch.object(gui, "webapp_alive", return_value=False), \
                    mock.patch.object(gui, "qc_of") as quality, \
                    mock.patch.object(app, "_start_qc_loader") as loader:
                app.refresh()
            quality.assert_not_called()
            loader.assert_called_once()
            self.assertEqual(len(app.tree.get_children()), 40)
            self.assertEqual(app.tree.item("doc000", "values")[1], "质检中")
            self.assertEqual(app._qc_pending[:30], ["doc%03d" % i for i in range(30)])
        finally:
            app.destroy()

    def test_retry_missing_uses_all_documents_not_only_selection(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        app.docs = {
            "doc1": {"pdf": "doc1.pdf", "miss": ["dots"]},
            "doc2": {"pdf": "doc2.pdf", "miss": []},
            "doc3": {"pdf": None, "miss": ["paddle"]},
            "doc4": {"pdf": "doc4.pdf", "miss": ["mineru"]},
        }
        try:
            app.withdraw()
            with mock.patch.object(app.runner, "submit", return_value=True) as submit, \
                    mock.patch.object(gui.messagebox, "askokcancel", return_value=True) as confirm:
                app._retry_missing()
            self.assertEqual([c.args[0] for c in submit.call_args_list], ["doc1", "doc4"])
            self.assertTrue(all("ner" not in c.args[2] for c in submit.call_args_list))
            self.assertIn("dots", confirm.call_args.args[1])
            self.assertIn("MinerU", confirm.call_args.args[1])
            self.assertIn("不执行 NER", confirm.call_args.args[1])
            app._sync_buttons()
            self.assertEqual(str(app.btn_retry.cget("state")), "normal")
            self.assertEqual(app.btn_retry.cget("text"), "重试全部缺失渠道（2篇）")
        finally:
            app.destroy()

    def test_search_filter_selection_and_conflict_banner(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        states = {"doc1": "完成", "doc2": "未完成", "doc3": "未跑", "doc4": "完成", "doc5": "完成"}
        docs = {d: {"pdf": d + ".pdf", "state": state, "done_at": None, "miss": [],
                    "ch": {ch: True for ch in gui.CH}} for d, state in states.items()}
        try:
            app.withdraw()
            app.runner.running["doc5"] = ("测试", time.time())
            with mock.patch.object(gui, "scan_docs", return_value=docs), \
                    mock.patch.object(gui, "load_history", return_value={}), \
                    mock.patch.object(gui, "webapp_alive", return_value=True), \
                    mock.patch.object(app, "_start_qc_loader"):
                app.refresh()
            app._qc_results["doc4"] = {"label": "✘ 作者", "level": "bad", "issues": ["作者"]}
            app.filter_var.set("运行中")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ("doc5",))
            app.filter_var.set("异常")
            app._apply_view()
            self.assertEqual(set(app.tree.get_children()), {"doc2", "doc4"})
            app.search_var.set("doc3")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ())
            app.filter_var.set("全部")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ("doc3",))
            self.assertEqual(app.count_lbl.cget("text"), "文档 1 / 5 篇")
            app.search_var.set("")
            app._apply_view()
            app.tree.selection_set(app.tree.get_children())
            app._sync_buttons()
            self.assertEqual(app.btn_rerun.cget("text"), "一键重跑（5） ▾")
            self.assertNotIn("doc1", app.sel_lbl.cget("text"))
            app._toggle_select_all()   # 2026-11-18: _clear_selection 已并入全选/清除二合一按钮
            self.assertFalse(app.tree.selection())
            app.docs["doc1"]["ner"] = True
            app.filter_var.set("未跑NER")
            app._apply_view()
            self.assertEqual(len(app.tree.get_children()), 4)
            self.assertNotIn("doc1", app.tree.get_children())
            self.assertIn("ner", app.tree["columns"])
            self.assertIn("language", app.tree["columns"])
            app.filter_var.set("全部")
            app._qc_results["doc1"] = {"language": "中文", "label": "✔", "level": "", "issues": []}
            app.lang_filter_var.set("中文")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ("doc1",))
            app.lang_filter_var.set("全部")
            app.filter_var.set("未通过")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ("doc4",))
            app._qc_results["doc1"] = {"language": "韩文", "label": "◇ 非正规论文",
                                        "level": "nonregular", "issues": ["摘要"]}
            app.filter_var.set("非正规")
            app._apply_view()
            self.assertEqual(app.tree.get_children(), ("doc1",))
            self.assertEqual(str(app.tree.tag_configure("qc_nonregular", "foreground")), "#2563eb")
            app.filter_var.set("异常")
            app._apply_view()
            self.assertNotIn("doc1", app.tree.get_children())
            app.filter_var.set("全部")
            self.assertIn("web", app.conflict_lbl.cget("text"))
            app.filter_var.set("未跑NER")
            app._apply_view()
            with mock.patch.object(app.tree, "identify_row",
                                   side_effect=lambda y: {1: "doc2", 2: "doc4"}.get(y, "")):
                app._remember_selection_anchor(mock.Mock(y=1))
                app._select_shift_range(mock.Mock(y=2))
            self.assertEqual(set(app.tree.selection()), {"doc2", "doc3", "doc4"})
            for i in range(100):
                app._log("line %d" % i)
            app._drain()
            app.logtxt.yview_moveto(0)
            app._sync_log_follow()
            self.assertFalse(app.log_follow_var.get())
            app.logtxt.yview_moveto(1)
            app._sync_log_follow()
            self.assertTrue(app.log_follow_var.get())
            app._clear_log_view()
            self.assertEqual(app.logtxt.get("1.0", "end-1c"), "")
        finally:
            app.destroy()

    def test_workbench_does_not_call_index_without_metadata_complete(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)

        uploads = self.root / "uploads"
        uploads.mkdir()
        (uploads / "paper.pdf").write_bytes(b"pdf")
        (self.doc_dir / "index.html").write_text("ok", encoding="utf-8")
        for ch in ("mineru", "dots", "paddle"):
            raw = self.doc_dir / "raw" / ch
            raw.mkdir(parents=True)
            (raw / "result.json").write_text("{}", encoding="utf-8")

        with mock.patch.object(gui, "UP", str(uploads)), \
                mock.patch.object(gui, "OUT", str(self.out_root)), \
                mock.patch.object(gui.archive, "archived_set", return_value=set()):
            self.assertEqual(gui.scan_docs()["paper"]["state"], "未完成")
            (self.doc_dir / "metadata.json").write_text("{}", encoding="utf-8")
            self.assertEqual(gui.scan_docs()["paper"]["state"], "完成")


class RunnerStopTest(unittest.TestCase):
    def test_stop_cancels_only_queued_documents(self):
        import gui
        gate = threading.Event()
        started = threading.Event()
        calls = []
        logs = []

        def fake_run(job, log, out_root=None):
            calls.append(job["doc"])
            started.set()
            gate.wait(3)
            return {"ok": True, "results": {}, "error": ""}

        with mock.patch.object(gui.stages, "run_one_child", side_effect=fake_run), \
                mock.patch.object(gui, "append_history"):
            runner = gui.Runner(logs.append, parallel=1)
            self.assertTrue(runner.submit("doc1", "doc1.pdf", ["index"]))
            self.assertTrue(started.wait(2))
            runner.submit("doc2", "doc2.pdf", ["index"])
            runner.submit("doc3", "doc3.pdf", ["index"])

            running, cancelled = runner.stop_after_current()
            self.assertEqual((running, cancelled), (1, 2))
            self.assertFalse(runner.submit("doc4", "doc4.pdf", ["index"]))
            self.assertTrue(runner.is_busy())
            gate.set()
            deadline = time.time() + 3
            while runner.is_busy() and time.time() < deadline:
                time.sleep(0.01)

            self.assertFalse(runner.is_busy())
            self.assertFalse(runner.is_stopping())
            self.assertEqual(calls, ["doc1"])
            self.assertNotIn("doc2", runner.queued)
            self.assertNotIn("doc3", runner.queued)

    def test_stage_timings_are_recorded_and_accumulated(self):
        import gui
        logs = []
        result = {"ok": True, "error": "", "results": {
            "acquire": {"ok": True, "elapsed": 12},
            "ner": {"ok": True, "elapsed": 34},
        }}
        with mock.patch.object(gui.stages, "run_one_child", return_value=result), \
                mock.patch.object(gui, "append_history") as history:
            runner = gui.Runner(logs.append, parallel=1)
            runner._run_one("doc1", "doc1.pdf", ["acquire", "ner"], (), "计时")
            rec = history.call_args.args[0]
            self.assertEqual(rec["ocr_s"], 12)
            self.assertEqual(rec["llm_s"], 34)
            self.assertEqual(rec["stage_elapsed"], {"acquire": 12, "ner": 34})
            self.assertEqual(runner.timing_totals(), (12, 34))
            self.assertIn("OCR 12s", logs[-1])
            self.assertIn("LLM 34s", logs[-1])

    def test_workbench_has_stop_button(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            self.assertTrue(app.btn_stop.winfo_exists())
            self.assertEqual(str(app.btn_stop.cget("state")), "disabled")
            self.assertIn("channels", app.tree["columns"])
            self.assertIn("timing", app.tree["columns"])
            self.assertTrue(app.tree_vscroll.winfo_exists())
            self.assertEqual(str(app.tree.tag_configure("state_complete", "foreground")), "#000000")
            app.deiconify()
            app.geometry("800x640")
            app.update()
            self.assertTrue(app.tree_vscroll.winfo_ismapped())
            self.assertLessEqual(app.tree_vscroll.winfo_x() + app.tree_vscroll.winfo_width(),
                                 app.tree_vscroll.master.winfo_width())
            self.assertTrue(app.par_box.winfo_ismapped())
            self.assertLessEqual(app.par_box.winfo_x() + app.par_box.winfo_width(),
                                 app.par_box.master.winfo_width())
        finally:
            app.destroy()


class GuiRestartTest(unittest.TestCase):
    """一键重启必须只留一个 GUI: 旧进程真正退出, 端口/名额交接给新进程。"""

    def _fake_app(self):
        import gui
        app = mock.Mock()
        app._restarting = False
        app._instance_sock = mock.Mock()
        app._runners = {}
        return app

    def test_restart_stops_tasks_frees_port_and_exits_old_process(self):
        import gui
        app = self._fake_app()
        runner = mock.Mock()
        app._runners = {"w": runner}
        kids = [mock.Mock(pid=111), mock.Mock(pid=222)]

        app._terminate_children = mock.Mock(return_value=[111, 222])
        app._free_own_slots = mock.Mock(return_value=2)
        old_sock = app._instance_sock
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(gui.os, "_exit") as exit_:
            # after 立即执行回调, 模拟 200ms 后硬退出
            app.after.side_effect = lambda delay, fn, *a: fn(*a)
            gui.App._restart(app)

        runner.stop_after_current.assert_called_once_with()
        app._terminate_children.assert_called_once_with()
        app._free_own_slots.assert_called_once_with([111, 222])
        old_sock.close.assert_called_once_with()
        self.assertIsNone(app._instance_sock)
        popen.assert_called_once()
        exit_.assert_called_once_with(0)

    def test_double_click_restart_spawns_only_one_process(self):
        import gui
        app = self._fake_app()
        app._terminate_children = mock.Mock(return_value=[])
        app._free_own_slots = mock.Mock(return_value=0)
        with mock.patch.object(gui.messagebox, "askyesno", return_value=True), \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch.object(gui.os, "_exit"):
            app.after.side_effect = lambda delay, fn, *a: None   # 不真退, 才能点第二次
            gui.App._restart(app)
            gui.App._restart(app)

        popen.assert_called_once()
        self.assertTrue(app._restarting)

    def test_free_own_slots_keeps_other_process_slots(self):
        import gui
        with tempfile.TemporaryDirectory() as tmp:
            import crossproc
            d = os.path.join(tmp, "ocr_api")
            os.makedirs(d)
            mine = os.path.join(d, "slot_0")
            other = os.path.join(d, "slot_1")
            with io.open(mine, "w", encoding="utf-8") as fh:
                fh.write("%d uuid 0" % os.getpid())
            with io.open(other, "w", encoding="utf-8") as fh:
                fh.write("999999 uuid 0")
            with mock.patch.object(crossproc, "LOCK_ROOT", tmp):
                n = gui.App._free_own_slots([44444])
                self.assertEqual(n, 1)
                self.assertFalse(os.path.exists(mine))
                self.assertTrue(os.path.exists(other))


class CachePreflightTest(unittest.TestCase):
    """缓存预检: 老文章不该为"读缓存"去抢 ocr_api 名额。"""

    def _out_with_cache(self, channels):
        d = tempfile.mkdtemp()
        for ch in channels:
            raw = os.path.join(d, "raw", ch + "_api")
            os.makedirs(raw)
            fp = os.path.join(raw, "x_content_list_v2.json" if ch == "mineru" else "page_0.json")
            with io.open(fp, "w", encoding="utf-8") as fh:
                fh.write("[]")
        return d

    def test_full_cache_skips_slot_and_api(self):
        import pipeline, crossproc
        out = self._out_with_cache(("mineru", "dots", "paddle"))
        logs = []
        slot = mock.Mock()
        with mock.patch.object(crossproc, "Slots", return_value=slot) as Slots, \
                mock.patch.object(pipeline, "fetch_mineru_api") as fmin, \
                mock.patch.object(pipeline, "fetch_dots_api") as fdots, \
                mock.patch.object(pipeline, "fetch_paddle_api") as fpaddle, \
                mock.patch.object(pipeline, "scan_inbox", return_value={}), \
                mock.patch.object(pipeline, "try_auto", return_value={}):
            chans, src = pipeline.fetch_channels("paper", "p.pdf", out,
                                                 {"mineru": None, "dots": None, "paddle": None},
                                                 echo=logs.append)
        Slots.assert_not_called()
        fmin.assert_not_called(); fdots.assert_not_called(); fpaddle.assert_not_called()
        self.assertTrue(all(chans.get(c) for c in ("mineru", "dots", "paddle")))
        self.assertTrue(all(src.get(c) == "API缓存" for c in ("mineru", "dots", "paddle")))

    def test_missing_channel_only_fetches_that_one(self):
        import pipeline, crossproc
        out = self._out_with_cache(("mineru", "dots"))   # paddle 缺
        logs = []
        slot = mock.MagicMock()
        slot.__enter__.return_value = slot
        slot.waited = 0
        with mock.patch.object(crossproc, "Slots", return_value=slot) as Slots, \
                mock.patch.object(pipeline, "fetch_mineru_api") as fmin, \
                mock.patch.object(pipeline, "fetch_dots_api") as fdots, \
                mock.patch.object(pipeline, "fetch_paddle_api", return_value=out + "/raw/paddle_api") as fpaddle, \
                mock.patch.object(pipeline, "scan_inbox", return_value={}), \
                mock.patch.object(pipeline, "try_auto", return_value={}), \
                mock.patch.object(pipeline, "CFG", {}):
            chans, src = pipeline.fetch_channels("paper", "p.pdf", out,
                                                 {"mineru": None, "dots": None, "paddle": None},
                                                 echo=logs.append)
        Slots.assert_called_once()
        fmin.assert_not_called(); fdots.assert_not_called()
        fpaddle.assert_called_once()
        self.assertTrue(chans.get("paddle"))


class QcLanguageTest(unittest.TestCase):
    def test_body_language_detection(self):
        cases = [
            ("韩文", "이것은 한국어로 작성된 본문입니다. " * 20),
            ("中文", "这是一段用于研究中国历史的中文正文。" * 20),
            ("英文", "This is an English body for historical research. " * 20),
            ("混合", "한국어 본문 中文正文 English body"),
        ]
        for expected, text in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                dd = pathlib.Path(tmp, "paper")
                udir = dd / "unified"
                udir.mkdir(parents=True)
                (udir / "page_0.json").write_text(json.dumps({
                    "in_ref": False,
                    "unified": [{"type": "Text", "main_text": text, "cross_page": {}}],
                }), encoding="utf-8")
                (udir / "_unified_summary.json").write_text("{}", encoding="utf-8")
                qc.clear_cache("paper")
                self.assertEqual(qc.language_of("paper", str(dd)), expected)
        qc.clear_cache("paper")

    def test_workbench_qc_label_does_not_mix_language(self):
        import gui
        self.assertEqual(gui.qc_display({"language": "中文", "label": "✘ 作者"}), "✘ 作者")
        self.assertEqual(gui.qc_display({"language": "未知", "label": "-"}), "-")


class WorkspaceTest(unittest.TestCase):
    def test_workspace_paths_and_safe_names(self):
        import workspaces
        with tempfile.TemporaryDirectory() as tmp:
            main = workspaces.get_workspace(tmp, "main")
            lab = workspaces.get_workspace(tmp, "rule-lab").ensure()
            self.assertEqual(main.out, os.path.join(tmp, "out"))
            self.assertNotEqual(main.out, lab.out)
            self.assertNotEqual(main.ner_root, lab.ner_root)
            self.assertIn("rule-lab", workspaces.list_workspaces(tmp))
            with self.assertRaises(ValueError):
                workspaces.get_workspace(tmp, "../bad")
            custom = workspaces.custom_workspace(os.path.join(tmp, "任意的位置")).ensure()
            self.assertEqual(custom.uploads, os.path.join(tmp, "任意的位置", "uploads"))
            self.assertEqual(custom.out, os.path.join(tmp, "任意的位置", "out"))
            self.assertIn("任意的位置", workspaces.list_workspaces(tmp, {"任意的位置": custom.root}))

    def test_scan_and_switch_are_isolated(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory() as tmp:
                ws = gui.workspaces.get_workspace(tmp, "rule-lab").ensure()
                pathlib.Path(ws.uploads, "paper.pdf").write_bytes(b"pdf")
                # CI 的 clone 里没有 out/ (被忽略), 本地有——两种环境必须走同一条路径。
                # 正式区 OUT 不存在时, 实验区的扫描不能去读它(2026-11-17 CI 实证:
                # scan_docs 判空看 out_dir、循环体却读全局 OUT, 本地因 out/ 存在而没暴露)。
                with mock.patch.object(gui, "OUT", os.path.join(tmp, "no-such-out")):
                    docs = gui.scan_docs(ws.uploads, ws.out, ws.ner_root, include_archived=False)
                self.assertEqual(docs["paper"]["state"], "未跑")
                self.assertEqual(docs["paper"]["pdf"], os.path.join(ws.uploads, "paper.pdf"))
                self.assertNotEqual(ws.out, gui.OUT)
                with mock.patch.object(gui.filedialog, "askdirectory", return_value=ws.root), \
                        mock.patch.object(gui, "PREFS_FP", os.path.join(tmp, "prefs.json")):
                    app._new_workspace()
                self.assertEqual(app.workspace.root, ws.root)
                self.assertEqual(app.runner.out_root, ws.out)
        finally:
            app.destroy()


    def test_archive_columns_fit_window(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            app.deiconify()
            win = gui.ArchiveWindow(app)
            win.geometry("900x540")
            win.update()
            win._fit_columns()
            base = win._archive_column_base
            self.assertLessEqual(sum(win.tree.column(c, "width") for c in base),
                                 win.tree.master.winfo_width())
            win.destroy()
        finally:
            app.destroy()

    def test_workspace_archive_retains_note_and_restores_files(self):
        import archive
        with tempfile.TemporaryDirectory() as tmp:
            import gui
            ws = gui.workspaces.custom_workspace(os.path.join(tmp, "lab")).ensure()
            pathlib.Path(ws.uploads, "paper.pdf").write_bytes(b"pdf")
            out = pathlib.Path(ws.out, "paper")
            out.mkdir()
            pathlib.Path(out, "metadata.json").write_text(json.dumps({"title": "测试"}), encoding="utf-8")
            result = archive.archive_docs(["paper"], out_root=ws.out, up_root=ws.uploads,
                                           arch_root=ws.archive, meta_note={"paper": "规则回测备注"})
            self.assertEqual(result["ok"], ["paper"])
            self.assertEqual(archive.item_of("paper", ws.archive)["note"], "规则回测备注")
            ok, _msg = archive.restore_doc("paper", out_root=ws.out, up_root=ws.uploads,
                                            arch_root=ws.archive)
            self.assertTrue(ok)
            self.assertTrue(pathlib.Path(ws.uploads, "paper.pdf").is_file())
            self.assertTrue(out.is_dir())

    def test_background_jobs_keep_submission_workspace(self):
        import gui
        calls = []
        gate = threading.Event()
        gate.set()
        logs = []

        def fake_run(job, log, out_root=None):
            calls.append((job["doc"], out_root))
            gate.wait(1)
            return {"ok": True, "results": {}, "error": ""}

        # ignore_cleanup_errors: 任务线程是 daemon, 收尾写盘可能比 with 退出晚一拍,
        # py3.12 上偶发 OSError: Directory not empty(2026-11-25 CI 复现)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                mock.patch.object(gui.stages, "run_one_child", side_effect=fake_run), \
                mock.patch.object(gui, "append_history"):
            runner = gui.Runner(logs.append, parallel=2, out_root=os.path.join(tmp, "main"),
                                history_fp=os.path.join(tmp, "main.jsonl"))
            runner.submit("one", "one.pdf", ["index"])
            runner.out_root = os.path.join(tmp, "lab")
            runner.history_fp = os.path.join(tmp, "lab.jsonl")
            runner.submit("two", "two.pdf", ["index"])
            deadline = time.time() + 5
            while len(calls) < 2 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(calls, [("one", os.path.join(tmp, "main")),
                                     ("two", os.path.join(tmp, "lab"))])
            # calls 只代表"开跑", 不代表"落盘完"; 等队列与在跑集合都清空再退出 with
            while (runner.queued or runner.running) and time.time() < deadline:
                time.sleep(0.05)


    def test_runners_and_logs_are_independent(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        gate = threading.Event()
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                one = gui.workspaces.custom_workspace(os.path.join(tmp, "one")).ensure()
                two = gui.workspaces.custom_workspace(os.path.join(tmp, "two")).ensure()
                r1, r2 = app._runner_for(one), app._runner_for(two)
                self.assertIsNot(r1, r2)
                def fake_run(job, log, out_root=None):
                    gate.wait(2)
                    return {"ok": True, "results": {}, "error": ""}
                with mock.patch.object(gui.stages, "run_one_child", side_effect=fake_run), \
                        mock.patch.object(gui, "append_history"):
                    self.assertTrue(r1.submit("same", "one.pdf", ["index"]))
                    self.assertTrue(r2.submit("same", "two.pdf", ["index"]))
                app._log("one-log", workspace_root=one.root)
                app._log("two-log", workspace_root=two.root)
                app._drain()
                app.workspace = two
                app.runner = r2
                app._restore_log_view()
                self.assertIn("two-log", app.logtxt.get("1.0", "end"))
                self.assertNotIn("one-log", app.logtxt.get("1.0", "end"))
        finally:
            gate.set()
            app.destroy()


class WorkbenchNoteTest(unittest.TestCase):
    def test_auto_note_marks_only_missing_evidence_fields(self):
        import gui
        self.assertEqual(gui.auto_note({"issues": ["出版年", "卷", "引文句/脚注"]}),
                         "原文未见年卷期/DOI证据；原文未见文内引文/脚注证据")
        self.assertEqual(gui.auto_note({"issues": ["作者"]}), "")

    def test_user_note_persists_and_takes_precedence(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
                    gui, "NOTES_FP", os.path.join(tmp, "notes.json")):
                app._notes = {}
                app.workspace.notes = os.path.join(tmp, "notes.json")
                app.docs = {"doc1": {"pdf": "doc1.pdf", "state": "完成", "done_at": None,
                                      "miss": [], "ch": {ch: True for ch in gui.CH}}}
                app._qc_results["doc1"] = {"label": "✘", "level": "bad", "issues": ["出版年"]}
                app.hist = {}
                app._save_note("doc1", "人工复核备注")
                self.assertEqual(gui.load_notes(), {"doc1": "人工复核备注"})
                app._apply_view()
                self.assertIn("note", app.tree["columns"])
                self.assertEqual(app.tree.item("doc1", "values")[8], "人工复核备注")
                app._save_note("doc1", "")
                self.assertEqual(app.tree.item("doc1", "values")[8], "")
        finally:
            app.destroy()


class ExtractionFallbackTest(unittest.TestCase):
    def test_cjk_author_line_requires_short_name_and_marker(self):
        from metapy import front
        self.assertEqual(front._cjk_author_names("李好 $ ^{*} $"), ["李好"])
        self.assertEqual(front._cjk_author_names("董昕·杨泽宇 $ ^{*} $"), ["董昕", "杨泽宇"])
        self.assertEqual(front._cjk_author_names("宋代驿壁诗的空间考察"), [])

    def test_invalid_author_metadata_labels_are_rejected(self):
        from metapy import front
        self.assertFalse(front._valid_author_name("■ 국문초록"))
        self.assertFalse(front._valid_author_name("저자: 금재원"))
        self.assertFalse(front._valid_author_name("출판연도: 2023"))
        self.assertTrue(front._valid_author_name("김진우"))

    def test_xpage_affiliation_uses_prejoin_tail(self):
        from metapy import front
        with tempfile.TemporaryDirectory() as tmp:
            udir = pathlib.Path(tmp, "unified")
            udir.mkdir()
            (udir / "_m3_xpage.json").write_text(json.dumps({"actions": [{
                "type": "join", "pages": [1, 2],
                "tail_clean": "• 부산대학교 예술대학 예술문화와영상매체협동과정 박사 수료. (사)하다 대표이사",
            }]}), encoding="utf-8")
            self.assertEqual(front._xpage_affils(tmp),
                             ["부산대학교 예술대학 예술문화와영상매체협동과정", "(사)하다"])

    def test_reference_fallback_accepts_cjk_and_classics_not_prose(self):
        from metapy import refs
        self.assertTrue(refs._cjk_ref_entry_like("김상욱. 2012, “중국 지방정부 부채”, 한국문화 32권."))
        self.assertTrue(refs._cjk_ref_entry_like("書經 禹貢篇."))
        self.assertTrue(refs._cjk_ref_entry_like("朝鮮總督府. 1932, 平壤府, 京城: 朝鮮總督府."))
        self.assertFalse(refs._cjk_ref_entry_like("이 연구는 지방정부의 채무 구조를 분석한다."))

    def test_publication_year_comes_only_from_explicit_label(self):
        from metapy import issue
        self.assertEqual(issue._publication_year([(0, "u0", "출판연도: 2023")]), 2023)
        self.assertIsNone(issue._publication_year([(0, "u0", "김진우의 2023년 연구")]))

    def test_chinese_keywords_support_punctuation_and_space_delimiters(self):
        from metapy import issue
        self.assertEqual(issue._kw_parse("关键词：驿壁诗，宋代、文学地理学", ["关键词：驿壁诗，宋代、文学地理学"], 0)[1],
                         ["驿壁诗", "宋代", "文学地理学"])
        self.assertEqual(issue._kw_parse("關鍵詞：基馬爾道路 民族資本主義 共產國際",
                                         ["關鍵詞：基馬爾道路 民族資本主義 共產國際"], 0)[1],
                         ["基馬爾道路", "民族資本主義", "共產國際"])

    def test_authoryear_split_supports_dot_before_year(self):
        from metapy import refs
        text = "Hughes-Warrington, Marnie. 2013, Revisionist Histories. Routledge.\nKershaw, Ian. 1993, Working Towards the Führer. CEH."
        self.assertEqual(len(refs._split_entries_block(text, "authoryear")), 2)

    def test_non_regular_article_requires_no_citation_structure(self):
        import qc
        with tempfile.TemporaryDirectory() as tmp:
            dd = pathlib.Path(tmp, "paper")
            (dd / "unified").mkdir(parents=True)
            body = "한국어 본문입니다. " * 100
            for p in range(2):
                (dd / "unified" / f"page_{p}.json").write_text(json.dumps({
                    "unified": [{"type": "Text", "main_text": body}]}), encoding="utf-8")
            (dd / "unified" / "_unified_summary.json").write_text("{}", encoding="utf-8")
            (dd / "merged.json").write_text(json.dumps({"page_data": [{
                "page": 1, "channels": {"mineru": [{"text": body}]}}]}), encoding="utf-8")
            meta = {"authors": [{"name": "홍길동"}], "references": [],
                    "footnote_refs": [], "citation_spans": []}
            self.assertTrue(qc._non_regular_article(str(dd), meta))
            (dd / "merged.json").write_text(json.dumps({"page_data": [{
                "page": 1, "channels": {"mineru": [{"text": "참고문헌"}]}}]}), encoding="utf-8")
            self.assertFalse(qc._non_regular_article(str(dd), meta))

    def test_series_fields_come_only_from_doi(self):
        from metapy import issue
        res = {}
        self.assertTrue(issue._apply_doi_series(
            res, [(0, "u", "https://doi.org/10.23425/defsec.2025.7.1.364")]))
        self.assertEqual((res["year"]["value"], res["volume"]["value"], res["issue"]["value"]),
                         (2025, 7, 1))
        dates_only = {}
        self.assertFalse(issue._apply_doi_series(
            dates_only, [(1, "u", "국방과 보안"), (0, "d", "게재확정일(2024년 12월 23일)")]))
        self.assertFalse(issue._apply_doi_series(res, [(1, "u", "다른 학술지")]))
        bjh = {}
        self.assertTrue(issue._apply_doi_series(bjh, [(0, "u", "https://doi.org/10.33957/BJH.2025.14.4")]))
        self.assertEqual((bjh["year"]["value"], bjh["volume"]["value"], bjh["issue"]["value"]),
                         (2025, 14, 4))
        self.assertNotIn("journal", bjh)

    def test_roman_anchored_korean_name_allows_spaces(self):
        from metapy import front
        got = front._parse_author_line("촉니 린포체(Tsaoknyi Rinpoche)*", None, {})
        self.assertEqual([a["name"] for a in got], ["촉니 린포체"])

    def test_book_review_exemption_requires_all_book_labels(self):
        import qc
        with tempfile.TemporaryDirectory() as tmp:
            udir = pathlib.Path(tmp, "paper", "unified")
            udir.mkdir(parents=True)
            blocks = [{"main_text": "저자: 금재원\n출판사: 진인진\n출판연도: 2023\n분량: 368쪽"}]
            (udir / "page_0.json").write_text(json.dumps({"unified": blocks}), encoding="utf-8")
            self.assertTrue(qc._book_review(str(udir.parent)))
            blocks[0]["main_text"] = "저자: 금재원\n출판연도: 2023"
            (udir / "page_0.json").write_text(json.dumps({"unified": blocks}), encoding="utf-8")
            self.assertFalse(qc._book_review(str(udir.parent)))


class ApiConfigTest(unittest.TestCase):
    def test_gui_config_saves_to_local_override(self):
        try:
            import gui
        except ImportError as exc:
            self.skipTest("tkinter unavailable: %s" % exc)
        import tkinter
        try:
            app = gui.App()
        except tkinter.TclError as exc:
            self.skipTest("no display for Tk: %s" % exc)
        try:
            app.withdraw()
            with tempfile.TemporaryDirectory() as tmp:
                base = pathlib.Path(tmp, "channels.json")
                local = pathlib.Path(tmp, "channels.local.json")
                base.write_text(json.dumps({"paddle_api": {"token": "", "timeout": 900}}), encoding="utf-8")
                import pipeline
                import webapp
                with mock.patch.object(pipeline, "LOCAL_CFG", str(local)), \
                        mock.patch.object(webapp, "CFG_FP", str(base)), \
                        mock.patch.object(gui.messagebox, "showinfo"):
                    window = gui.ApiConfigWindow(app)
                    window.vars["paddle_api"]["token"].set("secret")
                    window.vars["paddle_api"]["timeout"].set("60")
                    window._save()
                with mock.patch.object(pipeline._io, "CFG_FP", str(base)):
                    cfg = pipeline.load_cfg(use_env=False)
                self.assertEqual(cfg["paddle_api"]["token"], "secret")
                self.assertEqual(cfg["paddle_api"]["timeout"], 60)
        finally:
            app.destroy()


class DashboardTest(unittest.TestCase):
    def test_partial_channel_failure_is_visible_in_job_status(self):
        import webapp
        jobs = {}
        with tempfile.TemporaryDirectory() as tmp:
            def fake_main(argv):
                doc = os.path.splitext(os.path.basename(argv[0]))[0]
                pipeline.log("② 渠道[dots] 经云端 API 获取")
                pipeline.log("   dots 第6页 JSON 损坏(上游导出bug), 正则抢救 3 块")
                pipeline.log("⑥ 元数据 m3 完成")
                pipeline.log("  ner_lite| batch 0 网络错误: HTTP Error 401")
                pipeline.log("⑥ ner_lite: 提名 0 准入 0 记录 0")
                os.makedirs(os.path.join(webapp.OUT, doc), exist_ok=True)
                pathlib.Path(webapp.OUT, doc, "index.html").write_text("ok", encoding="utf-8")

            with mock.patch.object(webapp, "JOBS", jobs), \
                    mock.patch.object(webapp, "OUT", tmp), \
                    mock.patch.object(pipeline, "main", side_effect=fake_main):
                webapp._new_job("job1", "paper")
                webapp.run_pipeline("job1", os.path.join(tmp, "paper.pdf"))
                status = webapp._public_job("job1", jobs["job1"])

        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["channels"]["dots"], "warning")
        self.assertEqual(status["channels"]["ner_lite"], "error")
        self.assertEqual(status["channels"]["metadata"], "done")
        self.assertEqual(status["stages"]["acquire"], "warning")
        self.assertEqual(status["stages"]["metadata"], "done")
        self.assertEqual(status["stages"]["entities"], "error")
        self.assertEqual(len(status["warnings"]), 2)
        self.assertIn("elapsed", status)
        self.assertIn("updated_at", status)

    def test_same_document_has_only_one_active_job(self):
        import webapp
        jobs = {}
        with mock.patch.object(webapp, "JOBS", jobs):
            self.assertTrue(webapp._create_job_if_absent("j1", "paper"))
            self.assertFalse(webapp._create_job_if_absent("j2", "paper"))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(webapp._active_job_id("paper"), "j1")
            jobs["j1"]["state"] = "done"
            self.assertTrue(webapp._create_job_if_absent("j3", "paper"))
            self.assertEqual(len(jobs), 2)

    def test_dashboard_has_real_stages_without_fake_progress_bars(self):
        import webapp
        app = pathlib.Path(webapp.ROOT, "app.js").read_text(encoding="utf-8")
        self.assertIn('id="detail"', webapp.PAGE)
        self.assertIn('/dashboard.css', webapp.PAGE)
        self.assertIn('id="pipeline"', app)
        self.assertIn('class="latest-log"', app)
        self.assertNotIn('id="channelGrid"', app)
        self.assertNotIn('class="progress-track"', app)
        self.assertNotIn('channel-progress', app)
        self.assertNotIn('jobProgress', app)
        self.assertNotIn('id="log"', webapp.PAGE)
        self.assertNotIn('j.log.join', app)


if __name__ == "__main__":
    unittest.main()
