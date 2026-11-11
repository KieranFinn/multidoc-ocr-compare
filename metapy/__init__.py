# -*- coding: utf-8 -*-
"""metapy: M5 元数据字段层包(metadata.py 拆分, CLI 入口不变: python metadata.py <doc>... --stage ...)。"""
import os, argparse
from .common import *
from .m3 import m3_doc
from .refs import refs_doc, frefs_doc
from .front import front_doc
from .abs import abstract_doc
from .llmrep import llm_apply, report_meta, rebuild_viewer
from .dual import run as dual_run
from .cspan import cspan_doc
from .issue import issue_doc

__all__ = ["m3_doc", "refs_doc", "frefs_doc", "front_doc", "abstract_doc", "llm_apply",
           "report_meta", "rebuild_viewer", "dual_run",
           "cspan_doc", "issue_doc", "main"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+", help="文档名(out 下的目录名)")
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--stage", default="m3", choices=["m3", "refs", "frefs", "front", "abs", "llm", "dual", "report", "cspan", "issue"], help="增量阶段(m3=跨页拼接, refs=①文献条目, frefs=⑤全部编号脚注, front=②标题作者机构, abs=③摘要, llm=④LLM兜底, dual=双渠道对比(4d, 数据落 out_llm_trial/), report=④全量报告, cspan=行内引文句, issue=本篇期刊/年/卷/期/页+关键词[页眉投票正则]; 实体抽取用根目录 ner_lite.py)")
    ap.add_argument("--viewer", action="store_true", help="只按磁盘现有产物重建 1:3 对照查看器(不重跑 unify)")
    a = ap.parse_args()
    for doc in a.docs:
        if a.viewer:
            rebuild_viewer(doc, a.out)
        elif a.stage == "refs":
            refs_doc(doc, a.out)
        elif a.stage == "frefs":
            frefs_doc(doc, a.out)
        elif a.stage == "front":
            front_doc(doc, a.out)
        elif a.stage == "abs":
            abstract_doc(doc, a.out)
        elif a.stage == "llm":
            llm_apply(doc, a.out)
        elif a.stage == "dual":
            dual_run(doc, a.out)
        elif a.stage == "report":
            report_meta(a.docs, a.out)
            break
        elif a.stage == "cspan":
            cspan_doc(doc, a.out)
        elif a.stage == "issue":
            issue_doc(doc, a.out)
        else:
            m3_doc(doc, a.out)
