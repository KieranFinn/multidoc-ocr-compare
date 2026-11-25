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

__all__ = ["QC_REQUIRED", "QC_ONE_OF", "qc_of", "body_ok", "language_of", "clear_cache"]

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")
NER_ROOT = os.path.join(ROOT, "out_llm_trial")   # ner_lite 产物, 仅用于"机构"的口径对齐

QC_REQUIRED = [("title", "标题"), ("keywords", "关键词"), ("abstract", "摘要"),
               ("authors", "作者"), ("affiliations", "机构"), ("journal", "期刊"),
               ("year", "出版年"), ("volume", "卷"), ("issue", "期"),
               ("pages", "页码"), ("references", "参考文献")]
QC_ONE_OF = (("citation_spans", "引文句"), ("footnote_refs", "脚注"))   # 二存一即可

_BODY_CACHE = {}     # doc -> (summary_mtime, 正文是否达标, 正文主导语种)


def clear_cache(doc=None):
    if doc is None:
        _BODY_CACHE.clear()
    else:
        _BODY_CACHE.pop(doc, None)


def body_text_chars(blocks):
    """[公共判据·唯一实现] 正文总字符 = Text/Footnote 主读法长度和。
    qc(质检)/viewer(实体清单)/将来任何调用方共用本函数, 改判据只改这一处
    (2026-11-17 收敛: 此前 viewer.py:1391 手写同款公式, 靠注释人肉同步)。"""
    return sum(len((u.get("main_text") or "").strip()) for u in blocks
               if u.get("type") in ("Text", "Footnote"))


def affiliation_present(meta, org_entity_count=0):
    """[公共判据·唯一实现] 机构三源: affiliations ∪ authors[].affiliation ∪ org 实体。
    13563526 实证: 只查两处会漏第三处。调用方传入 org 实体计数(各自的数据源)。"""
    if meta.get("affiliations"):
        return True
    if any(isinstance(a, dict) and str(a.get("affiliation") or "").strip()
           for a in (meta.get("authors") or [])):
        return True
    return bool(org_entity_count)


def _body_stats(doc, dd):
    """一次扫描同时计算正文达标和主导语种，判定口径与分别扫描保持一致。
    2026-11-20 俄葡法适配: 西里尔字母单列(→俄语); 拉丁主导时用 langid 功能词
    打分细分 葡萄牙语/法语/英文(标签经 langid.LABELS 映射, GUI 语言列直接显示)。"""
    sp = os.path.join(dd, "unified", "_unified_summary.json")
    mt = os.path.getmtime(sp) if os.path.isfile(sp) else 0
    hit = _BODY_CACHE.get(doc)
    if hit and hit[0] == mt:
        return hit[1], hit[2]
    from metapy import langid
    counts = {u"韩文": 0, u"中文": 0, u"英文": 0, u"西里尔": 0}
    lat_parts = []
    total, udir = 0, os.path.join(dd, "unified")
    if os.path.isdir(udir):
        for f in os.listdir(udir):
            if not re.match(r"page_\d+\.json$", f):
                continue
            try:
                j = json.load(io.open(os.path.join(udir, f), encoding="utf-8"))
            except Exception:
                continue
            total += body_text_chars(j.get("unified") or [])
            for u in j.get("unified") or []:
                if j.get("in_ref") or u.get("type") != "Text" \
                        or (u.get("cross_page") or {}).get("role") == "head":
                    continue
                for ch in u.get("main_text") or "":
                    if u"\uac00" <= ch <= u"\ud7a3":
                        counts[u"韩文"] += 1
                    elif ("\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
                          or "\uf900" <= ch <= "\ufaff"):
                        counts[u"中文"] += 1
                    elif "\u0400" <= ch <= "\u04ff":
                        counts[u"西里尔"] += 1
                    elif "A" <= ch <= "Z" or "a" <= ch <= "z":
                        counts[u"英文"] += 1
                if len(lat_parts) < 8000:
                    lat_parts.append(u.get("main_text") or "")   # 整段攒样本供葡/法打分
    lang = "未知"
    n_chars = sum(counts.values())
    if n_chars >= 20:
        if counts[u"西里尔"] / float(n_chars) >= 0.15:
            lang = u"俄语"
        else:
            top = max((k for k in counts if k != u"西里尔"),
                      key=lambda k: counts[k])
            if counts[top] / float(n_chars) >= 0.60:
                if top == u"英文":
                    base = "".join(lat_parts)[:4000] or None
                    code = langid.classify(base) if base else None
                    lang = langid.LABELS.get(code, u"英文") if code else u"英文"
                else:
                    lang = top
            else:
                lang = "混合"
    ok = total >= 500
    _BODY_CACHE[doc] = (mt, ok, lang)
    return ok, lang


def language_of(doc, dd):
    """非参考文献页 Text 块的主导文字: 韩文/中文/英文/混合/未知。"""
    return _body_stats(doc, dd)[1]


def body_ok(doc, dd):
    """与 viewer.py:1358 完全一致: unified 全篇 Text/Footnote 型块的 main_text 总字符 >= 500。"""
    return _body_stats(doc, dd)[0]


def _book_review(dd):
    fp = os.path.join(dd, "unified", "page_0.json")
    if not os.path.isfile(fp):
        return False
    try:
        with io.open(fp, encoding="utf-8") as fh:
            blocks = json.load(fh).get("unified") or []
    except Exception:
        return False
    text = "\n".join(u.get("main_text") or "" for u in blocks)
    labels = (u"저자", u"출판사", u"출판연도", u"분량")
    return all(re.search(r"(?m)^\s*%s\s*[:：]" % re.escape(x), text) for x in labels)


# 免卷白名单已移除(2026-11-25 用户拍板): 该刊 67 篇已按 KCI 著录批量回填卷期,
# 4 个工作区 novol 篇数均为 0 —— 第三档已是死代码。缺卷期一律照常报缺, 不再结构豁免。


def _journal_value(m):
    v = ((m.get("journal") or {}).get("value") or "").strip()
    return v


_NONREG_CACHE = {}   # doc -> (key, result); key = summary/merged 的 max mtime(2026-11-18 补缓存)


def _non_regular_article(dd, m):
    """带缓存入口: 正文判据有 mtime 缓存, 此函数此前漏网——全库质检扫描时每个红篇
    重读全部 page_*.json + merged.json。失效键取 _unified_summary.json 与 merged.json
    的 mtime 较大者(两者随重建同生共死)。"""
    doc = os.path.basename(dd)
    key = 0.0
    for _f in ("unified", "merged.json"):
        _p = os.path.join(dd, _f)
        _p = os.path.join(_p, "_unified_summary.json") if _f == "unified" else _p
        if os.path.isfile(_p):
            key = max(key, os.path.getmtime(_p))
    hit = _NONREG_CACHE.get(doc)
    if hit and hit[0] == key:
        return hit[1]
    result = _non_regular_article_impl(dd, m)
    if len(_NONREG_CACHE) > 512:
        _NONREG_CACHE.clear()
    _NONREG_CACHE[doc] = (key, result)
    return result


def _non_regular_article_impl(dd, m):
    if not m.get("authors") or m.get("references") or m.get("footnote_refs") \
            or m.get("citation_spans") or not body_ok(os.path.basename(dd), dd):
        return False
    texts = []
    udir = os.path.join(dd, "unified")
    for name in ("page_0.json", "merged.json"):
        fp = os.path.join(dd, name) if name == "merged.json" else os.path.join(udir, name)
        if not os.path.isfile(fp):
            return False
    for name in os.listdir(udir):
        if not re.match(r"page_\d+\.json$", name):
            continue
        try:
            with io.open(os.path.join(udir, name), encoding="utf-8") as fh:
                j = json.load(fh)
        except Exception:
            return False
        for u in j.get("unified") or []:
            t = u.get("main_text") or ""
            texts.append(t)
            if u.get("type") == "Footnote" and re.match(r"^\s*\d{1,3}\s*[.)]", t):
                return False
    fp = os.path.join(dd, "merged.json")
    try:
        with io.open(fp, encoding="utf-8") as fh:
            mj = json.load(fh)
        for pg in mj.get("page_data") or []:
            for blocks in (pg.get("channels") or {}).values():
                texts.extend(b.get("text") or "" for b in blocks)
    except Exception:
        return False
    section = re.compile(u"요\\s*약|초\\s*록|Abstract|Keywords?|주제\\s*어|핵심\\s*어|"
                         u"참고\\s*문헌|References|Bibliography|"
                         u"Аннотация|Реферат|Ключевые\\s+слова|Список\\s+литературы|"
                         u"Resumo|Palavras[-–][Cc]have|Referências|Bibliografia|"
                         u"R[ée]sum[ée]|Mots[-–][Cc]l[ée]s|Références", re.I)
    return not any(section.search(t) for t in texts)


def qc_of(doc, out_dir=None, ner_root=None):
    """质检: 必抽项是否齐全。返回 issues/level/label，并附仅用于展示的 language。

    out_dir / ner_root 可覆盖(归档预览时传 archive/<doc>/out 之类);
    缺省走工作区, 与 GUI 表格里那一列同一份实现。
    """
    out_dir = out_dir or OUT
    ner_root = ner_root or NER_ROOT
    dd = os.path.join(out_dir, doc)
    lang = language_of(doc, dd)
    miss = []
    mfp = os.path.join(dd, "metadata.json")
    m = {}
    if os.path.isfile(mfp):
        try:
            m = json.load(io.open(mfp, encoding="utf-8")) or {}
        except Exception:
            return {"issues": ["metadata.json 解析失败"], "level": "bad",
                    "label": "\u2718 产物损坏", "language": lang}
    else:
        return {"issues": ["无 metadata.json"], "level": "bad",
                "label": "\u2718 无元数据", "language": lang}

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
    # 书评没有论文摘要/关键词: 只在首页明确印出书籍四要素时结构性豁免。
    if _book_review(dd):
        for zh in ("关键词", "摘要"):
            if zh in miss:
                miss.remove(zh)
    # 关键词有两条通路(keywords 解析结果 / keywords_lines 原始行), 任一有即算有
    if "关键词" in miss and not _empty(m.get("keywords_lines")):
        miss.remove("关键词")
    # 机构与 viewer_compare.html 同口径(viewer.py:1027 的质检清单 + :816 的题录行 + 实体面板):
    # ①metadata.affiliations ②authors[].affiliation ③ner_lite 的 org 实体, 任一有即算有。
    # 13563526 实证: 前两处都空, 但实体面板显示 "机构 Gyeongkuk National University",
    # 质检却报"机构"缺失——就是漏了第③条。
    if "机构" in miss:
        in_ner = False
        nfp = os.path.join(ner_root, "fds_extract", "ner_lite", doc, "ner_lite.json")
        if os.path.isfile(nfp):
            try:
                recs = json.load(io.open(nfp, encoding="utf-8")).get("records") or []
                in_ner = any(r.get("kind") == "org" for r in recs if isinstance(r, dict))
            except Exception:
                in_ner = False
        if affiliation_present(m, 1 if in_ner else 0):
            miss.remove("机构")
    # 正文: 与查看器同口径(见 body_ok)
    if not body_ok(doc, dd):
        miss.append("正文")
    # 引文句 / 脚注: 二存一
    if all(_empty(m.get(k)) for k, _zh in QC_ONE_OF):
        miss.append("引文句/脚注")

    if not miss:
        return {"issues": [], "level": "", "label": "\u2714", "language": lang}
    if _non_regular_article(dd, m):
        return {"issues": miss, "level": "nonregular", "label": "◇ 非正规论文", "language": lang}
    # 免卷第三档已移除(2026-11-25): 缺卷/期照常报 bad, 无结构豁免。
    head = "/".join(miss[:4]) + ("…" if len(miss) > 4 else "")
    return {"issues": miss, "level": "bad",
            "label": "\u2718 %d 项 %s" % (len(miss), head), "language": lang}
