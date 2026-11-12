# -*- coding: utf-8 -*-
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

import merge_out
import pipeline
import unify
import viewer


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
        self.assertIn(".refs .mf[data-u]", html)
        self.assertIn("citeHover(mf,true)", html)
        self.assertIn("citeHover(mf,false)", html)
        self.assertIn(".r.cite-target.cite-on", html)
        self.assertIn("r.classList.add('flash')", html)   # 点击定位统一走 .flash 通道(旧 cite-on 点击分支已收敛删除)
        self.assertIn('id="citepop"', html)
        self.assertIn('id="citesvg"', html)
        self.assertIn("cs.setAttribute('viewBox'", html)
        self.assertIn('class="cpbox"', html)
        self.assertIn(".pane:not(.meta) > .pmask", html)

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
