# -*- coding: utf-8 -*-
"""质检口径: 必抽项是否齐全(2026-11-12 用户定稿)。

只判"该有的有没有"——缺任一项即 bad, 缺几项就把名字写几项; 不看置信度、不分级、不设详情窗。
必抽: 标题/关键词/摘要/作者/机构/期刊/出版年/卷/期/页码/正文/参考文献;
引文句与脚注二存一即可; 科学数据集/基金/研究软件三类可选, 不参与判定。

本模块从 gui.py 抽出, 动机(2026-11-13): 归档需要在**搬走产物之前**把质检状态快照进索引,
但 archiving 是 CLI 也能跑的路径, 不能为此把 tkinter 拖进依赖链。
"""
import io
import json
import os
import re

__all__ = ["QC_REQUIRED", "QC_ONE_OF", "qc_of", "body_ok", "clear_cache"]

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
NER_ROOT = os.path.join(ROOT, "out_llm_trial")   # ner_lite 产物, 仅用于"机构"的口径对齐

QC_REQUIRED = [("title", "标题"), ("keywords", "关键词"), ("abstract", "摘要"),
               ("authors", "作者"), ("affiliations", "机构"), ("journal", "期刊"),
               ("year", "出版年"), ("volume", "卷"), ("issue", "期"),
               ("pages", "页码"), ("references", "参考文献")]
QC_ONE_OF = (("citation_spans", "引文句"), ("footnote_refs", "脚注"))   # 二存一即可

_BODY_CACHE = {}     # doc -> (summary_mtime, 正文是否达标); unified 不常变, 别每次刷新重读几百个 page_*.json


def clear_cache(doc=None):
    if doc is None:
        _BODY_CACHE.clear()
    else:
        _BODY_CACHE.pop(doc, None)


def body_ok(doc, dd):
    """与 viewer.py:1358 完全一致: unified 全篇 Text/Footnote 型块的 main_text 总字符 >= 500。"""
    sp = os.path.join(dd, "unified", "_unified_summary.json")
    mt = os.path.getmtime(sp) if os.path.isfile(sp) else 0
    hit = _BODY_CACHE.get(doc)
    if hit and hit[0] == mt:
        return hit[1]
    total, udir = 0, os.path.join(dd, "unified")
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            if not re.match(r"page_\d+\.json$", f):
                continue
            try:
                j = json.load(io.open(os.path.join(udir, f), encoding="utf-8"))
            except Exception:
                continue
            for u in j.get("unified") or []:
                if u.get("type") in ("Text", "Footnote"):
                    total += len((u.get("main_text") or "").strip())
    ok = total >= 500
    _BODY_CACHE[doc] = (mt, ok)
    return ok


def qc_of(doc, out_dir=None, ner_root=None):
    """质检: 必抽项是否齐全。返回 {issues: [中文缺项名], level: ""|"bad", label: "✔"|"✘ N 项 ..."}。

    out_dir / ner_root 可覆盖(归档预览时传 archive/<doc>/out 之类);
    缺省走工作区, 与 GUI 表格里那一列同一份实现。
    """
    out_dir = out_dir or OUT
    ner_root = ner_root or NER_ROOT
    dd = os.path.join(out_dir, doc)
    miss = []
    mfp = os.path.join(dd, "metadata.json")
    m = {}
    if os.path.isfile(mfp):
        try:
            m = json.load(io.open(mfp, encoding="utf-8")) or {}
        except Exception:
            return {"issues": ["metadata.json 解析失败"], "level": "bad", "label": "\u2718 产物损坏"}
    else:
        return {"issues": ["无 metadata.json"], "level": "bad", "label": "\u2718 无元数据"}

    def _empty(v):
        if isinstance(v, dict):          # 单值字段形如 {"value":..., "confidence":...}
            v = v.get("value")
        return v is None or v == "" or v == [] or v == {}

    for key, zh in QC_REQUIRED:
        if _empty(m.get(key)):
            miss.append(zh)
    # 「期」的结构性豁免(2026-11-13, 用户拍板): 只认**出版社自己给的证据**——DOI 期位留空
    # (年..卷 / 年.卷.文号), 由 issue 阶段写成 confidence.source="volume_only"。
    # 这类刊物按通卷编号(每期递增一个卷号), 期号在原文结构里就不存在, 报"缺期"是假红;
    # 证据不足(无 DOI、或 DOI 里期位有值)仍然照常报缺, 不把「期」从必抽清单里删掉。
    if "期" in miss:
        it = m.get("issue")
        if isinstance(it, dict) and (it.get("confidence") or {}).get("source") == "volume_only":
            miss.remove("期")
    # 关键词有两条通路(keywords 解析结果 / keywords_lines 原始行), 任一有即算有
    if "关键词" in miss and not _empty(m.get("keywords_lines")):
        miss.remove("关键词")
    # 机构与 viewer_compare.html 同口径(viewer.py:1027 的质检清单 + :816 的题录行 + 实体面板):
    # ①metadata.affiliations ②authors[].affiliation ③ner_lite 的 org 实体, 任一有即算有。
    # 13563526 实证: 前两处都空, 但实体面板显示 "机构 Gyeongkuk National University",
    # 质检却报"机构"缺失——就是漏了第③条。
    if "机构" in miss:
        in_authors = any(isinstance(a, dict) and str(a.get("affiliation") or "").strip()
                         for a in (m.get("authors") or []))
        in_ner = False
        nfp = os.path.join(ner_root, "fds_extract", "ner_lite", doc, "ner_lite.json")
        if os.path.isfile(nfp):
            try:
                recs = json.load(io.open(nfp, encoding="utf-8")).get("records") or []
                in_ner = any(r.get("kind") == "org" for r in recs if isinstance(r, dict))
            except Exception:
                in_ner = False
        if in_authors or in_ner:
            miss.remove("机构")
    # 正文: 与查看器同口径(见 body_ok)
    if not body_ok(doc, dd):
        miss.append("正文")
    # 引文句 / 脚注: 二存一
    if all(_empty(m.get(k)) for k, _zh in QC_ONE_OF):
        miss.append("引文句/脚注")

    if not miss:
        return {"issues": [], "level": "", "label": "\u2714"}
    head = "/".join(miss[:4]) + ("…" if len(miss) > 4 else "")
    return {"issues": miss, "level": "bad",
            "label": "\u2718 %d 项 %s" % (len(miss), head)}
