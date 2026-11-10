#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ner_lite.py — LLM 提名 + 程序定位的最小抽取器(不走 metapy.ner)。

契约(2026-11-10 定稿, 五轮 review 后):
  入口: 一句 prompt, 模型只回 [{kind, name}] —— 无 uid / relation / 复核;
  定位: 折叠匹配只是入场测试; 产出一律为原文切片(裸 in 可验);
        位置 = 全文枚举(跨块给 segments, 主块取重叠大者);
        右向字面外延(版本号/括号课题号)打 extended;
  出口: 产物 ner_lite.json + 审计 ner_lite_audit.json + _audit.html;
        批注闭合词典 GLOSSARY + lint(非法立即拒, 纪律对自己元数据执行)。

用法:
  python ner_lite.py selfcheck     # mock 自检, 不触网
  python ner_lite.py <doc> [...]   # 真跑(读 channels.json llm 段 + 环境变量 KCI_LLM_KEY)
"""
import datetime
import glob
import html
import io
import json
import os
import re
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # pipeline/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from metapy.common import _foldv                        # 折叠口径与管线一致
from metapy.llmrep import _json_objs

OUT = os.path.join(HERE, "out")
DST = os.path.join(os.path.dirname(HERE), "out_llm_trial", "fds_extract", "ner_lite")
# 无 unified 层的散装文档: paddle 原始 JSON(相对 pipeline/)
PADDLE_ONLY_DOCS = {"13564165": os.path.join(HERE, "..", "OCR", "13564165",
                                             "13564165.pdf_by_PaddleOCR-VL-1.5.json")}

def all_docs():
    """out/ 下有 unified/ 的文档 + 无 unified 层但给了 paddle JSON 的散装文档。

    惰性求值: 本模块要被 import(CI 的 import 冒烟 / 测试), 不能让"还没有 out/"成为导入错误。
    """
    have = ([d for d in os.listdir(OUT) if os.path.isdir(os.path.join(OUT, d, "unified"))]
            if os.path.isdir(OUT) else [])
    return sorted(have) + [d for d in PADDLE_ONLY_DOCS
                           if not os.path.isdir(os.path.join(OUT, d, "unified"))]
BATCH_CHARS = 8000
KINDS = ("funding", "software", "dataset")

PROMPT_SYS = u"你是文献实体提名器。只输出 JSON。"
PROMPT_BODY = (u"아래 텍스트에서 연구 지원(펀딩)의 기관·사업·과제명, 연구에 사용된 소프트웨어명, "
               u"과학 데이터셋·자료집명을 모두 찾아라.\n"
               u"규칙: 원문에 있는 명칭만; 원문을 그대로 복사(한 글자도 바꾸거나 고치지 말 것); "
               u"없으면 빈 배열; 설명·번호·코드블록 금지.\n"
               u"출력 형식: [{\"kind\":\"funding|software|dataset\",\"name\":\"...\"}]\n\n[텍스트]\n")

# 批注词典 v1(闭合, 追加制): lint 断言产物中的批注/枚举值全部来自本表
GLOSSARY = {
    "kind": {"scope": "record", "values": list(KINDS)},
    "multi_block": {"scope": "record", "meaning": u"名称定位到 >1 块"},
    "multi_occurrence": {"scope": "record", "meaning": u"某块内名称出现 >1 次"},
    "extended": {"scope": "record", "meaning": u"切片经右向字面外延(版本号/括号课题号)"},
    "cross_block": {"scope": "location", "meaning": u"该次出现跨块, segments ≥2"},
    "mention_in_biblio_footnote": {"scope": "record",
                                   "meaning": u"全部定位块为 Footnote 且 _fnote_biblio(著录脚注)"},
    "verdict": {"scope": "nomination", "values": ["admitted", "dropped"]},
    "drop_reason": {"scope": "nomination", "values": ["anchor_miss", "parse_fail"]},
    "etype": {"scope": "run", "values": ["network"]},
}

EXT_RE = re.compile(u"^\\s(?:\\(\\s*[\\w.\\-]+\\s*\\)|\\d+(?:\\.\\d+)+)")


# ---------- 块加载 ----------

def _load_paddle_json(path):
    blocks = []
    pages = json.load(io.open(path, encoding="utf-8"))
    for i, pg in enumerate(pages):
        for j, b in enumerate(pg.get("prunedResult", {}).get("parsing_res_list", [])):
            blocks.append({"page": i + 1, "uid": "p%d_%d" % (i, j),
                           "box": b.get("block_bbox"),
                           "text": b.get("block_content") or "",
                           "type": b.get("block_label")})
    return blocks


def load_blocks(doc):
    """正文块(ner.py 口径): unified 取 Text/Footnote、剔文献页/跨页头/栏外; 13564165 用 paddle JSON。"""
    if doc in PADDLE_ONLY_DOCS:
        raw = _load_paddle_json(PADDLE_ONLY_DOCS[doc])
        return [{"uid": b["uid"], "page": b["page"], "type": b["type"], "text": (b["text"] or "").strip()}
                for b in raw if b["type"] in ("text", "footnote", "paragraph_title")]
    blocks = []
    for p in sorted(glob.glob(os.path.join(OUT, doc, "unified", "page_*.json"))):
        j = json.load(io.open(p, encoding="utf-8"))
        if j.get("in_ref"):
            continue
        W = j.get("w") or 0
        for u in j.get("unified") or []:
            if u.get("type") not in ("Text", "Footnote"):
                continue
            if u.get("cross_page", {}).get("role"):
                continue
            t = (u.get("main_text") or "").strip()
            if not t:
                continue
            blocks.append({"uid": u.get("uid"), "page": u.get("page"),
                           "type": u.get("type"), "text": t, "box": u.get("box")})
    return blocks


# ---------- 折叠坐标 ----------

def fold_with_map(t):
    """返回 (folded_text, kept): kept[k] = folded 第 k 字符在原串中的下标(_foldv 逐字等长, 仅删空白)。"""
    kept = [j for j, ch in enumerate(t or "") if _foldv(ch) != ""]
    return "".join(_foldv((t or "")[j]) for j in kept), kept


class DocTable(object):
    """整篇折叠串 + 偏移表(folded 区间 -> 块/原始坐标)。"""

    def __init__(self, blocks):
        self.blocks = blocks
        self.ranges = []      # (fs, fe, block_index, kept)
        parts, pos = [], 0
        for i, b in enumerate(blocks):
            f, kept = fold_with_map(b["text"])
            self.ranges.append((pos, pos + len(f), i, kept))
            parts.append(f)
            pos += len(f)
        self.folded = "".join(parts)

    def occurrences(self, nf):
        """nf 在整篇折叠串中的全部 [a,b)。"""
        return [(m.start(), m.end()) for m in re.finditer(re.escape(nf), self.folded)]

    def locate(self, a, b):
        """折叠区间 -> [{'block','start','end','slice'}...](按重叠切到单块, 原始坐标)。"""
        segs = []
        for fs, fe, i, kept in self.ranges:
            la, lb = max(a, fs), min(b, fe)
            if la >= lb:
                continue
            blk = self.blocks[i]
            o1, o2 = kept[la - fs], kept[lb - fs - 1] + 1
            segs.append({"block": blk, "start": o1, "end": o2,
                         "slice": blk["text"][o1:o2]})
        return segs


def extend_slice(text, end):
    """右向字面外延: 只允许吃掉紧邻的(至多一个空白+版本号/括号课题号)。返回新 end。"""
    ext = False
    for _ in range(3):
        m = EXT_RE.match(text[end:])
        if not m:
            break
        end += m.end()
        ext = True
    return end, ext


# ---------- 著录脚注批注(口径搬自 _ner_current_backup._fnote_biblio) ----------

_FNOTE_BIBLIO = None


def is_biblio_footnote(t):
    global _FNOTE_BIBLIO
    if _FNOTE_BIBLIO is None:
        from metapy.refs import FN_CITE_X
        from metapy.common import YEAR_RE, YEAR_OK, QUOTE_TITLE
        _YEAR_RE, _YEAR_OK, _QUOTE, _FNX = YEAR_RE, YEAR_OK, QUOTE_TITLE, FN_CITE_X

        def _fnote_biblio(txt):
            lns = [ln.strip() for ln in (txt or "").split("\n") if ln.strip()]

            def _bib(ln):
                if not _FNX.match(ln):
                    return False
                has_y = any(int(m.group(1)) in _YEAR_OK for m in _YEAR_RE.finditer(ln)) \
                    or bool(re.search(r"op\.\s*cit", ln, re.I))
                return has_y and bool(_QUOTE.search(ln) or re.search(
                    r"pp?\.\s?\d|학\s*회|회\s*지|University|Press|Journal|출판사|vol\.|No\.", ln, re.I))

            n_bib = sum(1 for ln in lns if _bib(ln))
            if n_bib >= 2:
                return True
            return len(lns) <= 2 and n_bib == len(lns) and n_bib >= 1

        _FNOTE_BIBLIO = _fnote_biblio
    return _FNOTE_BIBLIO(t)


# ---------- LLM ----------

def parse_nominations(txt):
    """宽松解析: 优先顶层 JSON 数组, 退化到逐对象扫; 返回 (items, ok)。"""
    txt = (txt or "").strip()
    try:
        dec = json.JSONDecoder()
        i = txt.find("[")
        if i >= 0:
            arr, _ = dec.raw_decode(txt, i)
            if isinstance(arr, list):
                return [x for x in arr if isinstance(x, dict)], True
    except Exception:
        pass
    objs = _json_objs(txt)
    return objs, bool(objs)


def _ssl_ctx():
    """本机 TLS 链需 Windows 系统证书库(企业根/链不全, certifi 与 OpenSSL 默认路径均验不过)。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    for store in ("ROOT", "CA"):
        for cert, encoding, _trust in ssl.enum_certificates(store):
            if encoding == "x509_asn":
                try:
                    ctx.load_verify_locations(cadata=cert)
                except Exception:
                    pass
    return ctx


def call_llm(cfg, prompt):
    import urllib.request
    body = json.dumps({"model": cfg.get("model"),
                       "temperature": float(cfg.get("temperature") or 0),
                       "messages": [{"role": "system", "content": PROMPT_SYS},
                                    {"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(cfg["endpoint"], data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + os.environ.get("KCI_LLM_KEY", "")})
    with urllib.request.urlopen(req, timeout=180, context=_ssl_ctx()) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"]


# ---------- 主流程 ----------

def run_doc(doc, model, cfg=None, echo=print, blocks=None):
    blocks = blocks if blocks is not None else load_blocks(doc)
    tbl = DocTable(blocks)
    # 分批(按原始字符预算, 块不拆)
    batches, cur, cur_n = [], [], 0
    for i, b in enumerate(blocks):
        if cur and cur_n + len(b["text"]) > BATCH_CHARS:
            batches.append(cur)
            cur, cur_n = [], 0
        cur.append(i)
        cur_n += len(b["text"])
    if cur:
        batches.append(cur)

    nominations = []
    n_err_net = 0
    for bi, idxs in enumerate(batches):
        btxt = "\n\n".join(blocks[i]["text"] for i in idxs)
        bf_start = tbl.ranges[idxs[0]][0]
        bf_end = tbl.ranges[idxs[-1]][1]
        batch_folded = tbl.folded[bf_start:bf_end]
        try:
            resp = model(PROMPT_BODY + btxt, cfg)
        except Exception as e:
            n_err_net += 1
            echo(u"  batch %d 网络错误: %s" % (bi, str(e)[:100]))
            continue
        items, ok = parse_nominations(resp)
        if not ok:
            nominations.append({"batch": bi, "raw": (resp or "")[:200],
                                "verdict": "dropped", "drop_reason": "parse_fail"})
            continue
        for it in items:
            k = str(it.get("kind") or "").strip()
            n = str(it.get("name") or "").strip()
            if k not in KINDS or not n:
                nominations.append({"batch": bi, "raw": json.dumps(it, ensure_ascii=False)[:200],
                                    "verdict": "dropped", "drop_reason": "parse_fail"})
                continue
            nf = _foldv(n)
            hit = nf != "" and nf in batch_folded
            nominations.append({"batch": bi, "kind": k, "name": n,
                                "verdict": "admitted" if hit else "dropped",
                                "drop_reason": None if hit else "anchor_miss"})

    # 记录: 按 (kind, name) 归并; 位置 = 全篇枚举; 去重限记录内(不同名称命中同一 span 合法)
    records = {}
    for nom in nominations:
        if nom["verdict"] != "admitted":
            continue
        key = (nom["kind"], nom["name"])
        rec = records.setdefault(key, {"kind": nom["kind"], "name_model": nom["name"],
                                       "locations": [], "annotations": [], "_seen": set()})
        nf = _foldv(nom["name"])
        for (a, b) in tbl.occurrences(nf):
            segs = tbl.locate(a, b)
            if not segs:
                continue
            new_segs = []
            for s in segs:  # 右向字面外延(仅单块段; 跨块段保持原样)
                blk = s["block"]
                ne, ext = extend_slice(blk["text"], s["end"])
                if ext:
                    s = dict(s, end=ne, slice=blk["text"][s["start"]:ne])
                s["extended"] = ext
                new_segs.append(s)
            segs = new_segs
            lid = tuple((s["block"]["uid"], s["start"], s["end"]) for s in segs)
            if lid in rec["_seen"]:
                continue
            rec["_seen"].add(lid)
            loc = {"segments": [{"uid": s["block"]["uid"], "page": s["block"]["page"],
                                 "box": s["block"].get("box"), "start": s["start"],
                                 "end": s["end"], "slice": s["slice"],
                                 "extended": s["extended"]} for s in segs]}
            if len(segs) > 1:
                loc["annotations"] = ["cross_block"]
            rec["locations"].append(loc)

    for rec in records.values():
        uids = {s["uid"] for loc in rec["locations"] for s in loc["segments"]}
        if len(uids) > 1:
            rec["annotations"].append("multi_block")
        for loc in rec["locations"]:
            for s in loc["segments"]:
                blk = next(b for b in blocks if b["uid"] == s["uid"])
                nf = _foldv(rec["name_model"])
                if len(fold_with_map(blk["text"])[0].split(nf)) - 1 > 1:
                    rec["annotations"].append("multi_occurrence")
                    break
            else:
                continue
            break
        bt = [b for b in blocks if any(s["uid"] == b["uid"] for loc in rec["locations"]
                                       for s in loc["segments"])]
        if bt and all(b["type"] == "Footnote" and is_biblio_footnote(b["text"]) for b in bt):
            rec["annotations"].append("mention_in_biblio_footnote")
        if any(s["extended"] for loc in rec["locations"] for s in loc["segments"]):
            rec["annotations"].append("extended")
        rec["annotations"] = sorted(set(rec["annotations"]))
        # 不变量自检: 切片必须裸 in 块原文
        for loc in rec["locations"]:
            for s in loc["segments"]:
                blk = next(b for b in blocks if b["uid"] == s["uid"])
                assert s["slice"] in blk["text"], (doc, s)

    for r in records.values():
        r.pop("_seen", None)
    recs = sorted(records.values(), key=lambda r: (r["kind"], r["name_model"]))
    n_admit = sum(1 for n in nominations if n["verdict"] == "admitted")
    audit = {"doc": doc, "model": getattr(model, "model_name", None),
             "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "counts": {"blocks": len(blocks), "batches": len(batches),
                        "nominations": len(nominations), "admitted": n_admit,
                        "dropped_anchor_miss": sum(1 for n in nominations if n.get("drop_reason") == "anchor_miss"),
                        "dropped_parse_fail": sum(1 for n in nominations if n.get("drop_reason") == "parse_fail"),
                        "errors_network": n_err_net,
                        "records": len(recs),
                        "locations": sum(len(r["locations"]) for r in recs),
                        "multi_block": sum(1 for r in recs if "multi_block" in r["annotations"]),
                        "cross_block": sum(1 for r in recs for loc in r["locations"]
                                           if "cross_block" in loc.get("annotations", [])),
                        "extended": sum(1 for r in recs if "extended" in r["annotations"]),
                        "biblio_footnote": sum(1 for r in recs if "mention_in_biblio_footnote" in r["annotations"])},
             "nominations": nominations,
             "glossary_version": "v1"}
    lint_audit(audit, recs)
    return {"doc": doc, "records": recs}, audit


def lint_audit(audit, recs):
    for n in audit["nominations"]:
        assert n["verdict"] in GLOSSARY["verdict"]["values"]
        if n.get("drop_reason"):
            assert n["drop_reason"] in GLOSSARY["drop_reason"]["values"]
    for r in recs:
        assert r["kind"] in GLOSSARY["kind"]["values"]
        for a in r["annotations"]:
            assert a in GLOSSARY, "annotation not in glossary: %s" % a
        for loc in r["locations"]:
            for a in loc.get("annotations", []):
                assert a in GLOSSARY, "annotation not in glossary: %s" % a


# ---------- 输出 ----------

def write_outputs(doc, product, audit):
    d = os.path.join(DST, doc)
    if not os.path.isdir(d):
        os.makedirs(d)
    io.open(os.path.join(d, "ner_lite.json"), "w", encoding="utf-8").write(
        json.dumps(product, ensure_ascii=False, indent=1))
    io.open(os.path.join(d, "ner_lite_audit.json"), "w", encoding="utf-8").write(
        json.dumps(audit, ensure_ascii=False, indent=1))
    io.open(os.path.join(d, "_audit.html"), "w", encoding="utf-8").write(render_html(audit))
    return d


def render_html(audit):
    c = audit["counts"]
    rows_n = "".join(
        "<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            i, html.escape(str(n.get("kind") or "-")),
            html.escape(str(n.get("name") or n.get("raw") or "-"))[:80],
            n["verdict"], n.get("drop_reason") or "-",
            html.escape(str(n.get("batch") or "-")))
        for i, n in enumerate(audit["nominations"]))
    return (u"""<!doctype html><meta charset="utf-8"><title>ner_lite audit %s</title>
<style>body{font-family:sans-serif;font-size:13px}table{border-collapse:collapse;margin:8px 0}
td,th{border:1px solid #999;padding:2px 6px}th{background:#eee}
.admit{color:#060}td.drop{color:#a00}</style>
<h3>ner_lite 审计 · %s · %s</h3><div>计数: %s</div>
<h4>提名(%d)</h4><table><tr><th>#</th><th>kind</th><th>name/raw</th><th>verdict</th><th>reason</th><th>batch</th></tr>%s</table>
<p>产物与逐条位置见 ner_lite.json; 本页=裁决留痕(含被丢弃提名与模型原话)。</p>""" % (
        audit["doc"], audit["doc"], audit.get("model") or "-",
        html.escape(json.dumps(c, ensure_ascii=False)),
        len(audit["nominations"]), rows_n))


# ---------- mock 自检 ----------

class MockModel(object):
    model_name = "mock"

    def __init__(self, responses):
        self.responses = list(responses)
        self.i = 0

    def __call__(self, prompt, cfg=None):
        r = self.responses[self.i % len(self.responses)]
        self.i += 1
        if isinstance(r, Exception):
            raise r
        return r


def selfcheck():
    blocks = [
        {"uid": "u0", "page": 1, "type": "Text", "text": u"자료 분석은 SPSS 21.0 프로그램을 사용하였다."},
        {"uid": "u1", "page": 1, "type": "Text", "text": u"검증을 위해 SPSS 21 .0 을 다시 돌렸다."},   # OCR 空格噪声
        {"uid": "u2", "page": 2, "type": "Text", "text": u"본 연구는 성신여자대학교"},
        {"uid": "u3", "page": 2, "type": "Text", "text": u"교내연구비(2012-2-11-016)에 의해 지원되었다."},
        {"uid": "u4", "page": 3, "type": "Footnote", "text": u"30) Arbuckle, AMOS manuel, SPSS Inc., 1997, p.201."},
        {"uid": "u5", "page": 3, "type": "Text", "text": u"이 논문은 서울교육대학교 교내 연구비로 연구되었다."},
        {"uid": "u6", "page": 4, "type": "Text", "text": u"보조 분석에 SPSS 를, 또 SPSS 를 사용하였다."},  # 块内两次
    ]
    noms = [{"kind": "software", "name": "SPSS 21.0"},
            {"kind": "software", "name": "QuantumX"},            # 幻觉 → anchor_miss
            {"kind": "software", "name": "SPSS"},                # 截断 → 外延; u6 两次 → multi_occurrence
            {"kind": "funding", "name": u"성신여자대학교 교내연구비(2012-2-11-016)"},  # 跨缝
            {"kind": "funding", "name": u"서울교육대학교 교내 연구비"},
            {"kind": "software", "name": "SPSS 21.0"},           # 重复提名 → 去重
            {"kind": "software", "name": "AMOS manuel"}]         # 仅著录脚注 → 批注
    model = MockModel([json.dumps(noms, ensure_ascii=False)])
    product, audit = run_doc("mockdoc", model, echo=lambda s: None, blocks=blocks)

    recs = {(r["kind"], r["name_model"]): r for r in product["records"]}
    # 1) 幻觉被拒
    assert audit["counts"]["dropped_anchor_miss"] == 1, audit["counts"]
    # 2) SPSS 21.0: 两块定位 + 重复提名去重
    r = recs[("software", "SPSS 21.0")]
    uids = {s["uid"] for loc in r["locations"] for s in loc["segments"]}
    assert uids == {"u0", "u1"}, uids
    assert "multi_block" in r["annotations"]
    # 3) 截断 SPSS: 字面外延(u0 吃到 " 21.0"; u1 的 " 21 .0" 不吃半残版本)
    r2 = recs[("software", "SPSS")]
    assert "extended" in r2["annotations"], r2["annotations"]
    ext_slices = [s["slice"] for loc in r2["locations"] for s in loc["segments"] if s["extended"]]
    assert any(v == u"SPSS 21.0" for v in ext_slices), ext_slices
    # 4) 跨缝资助: segments ≥2 + cross_block
    r3 = recs[("funding", u"성신여자대학교 교내연구비(2012-2-11-016)")]
    assert any("cross_block" in loc.get("annotations", []) for loc in r3["locations"])
    assert all(len(loc["segments"]) >= 2 for loc in r3["locations"]
               if "cross_block" in loc.get("annotations", []))
    # 5) 块内多次出现: SPSS 裸名同时出现在 u0/u1/u6, u6 里出现两次
    #    (multi_occurrence 只在"该名称实际定位到的块"里数次数, 所以必须挑那条定位到 u6 的记录)
    assert "multi_occurrence" in r2["annotations"], r2["annotations"]
    u6_ids = [(s["start"], s["end"]) for loc in r2["locations"] for s in loc["segments"] if s["uid"] == "u6"]
    assert len(u6_ids) == 2, u6_ids      # 同块两次, 是全篇枚举出来的两个不同 span
    # 6) 著录脚注批注: 全部定位在著录脚注
    r4 = recs[("software", "AMOS manuel")]
    assert "mention_in_biblio_footnote" in r4["annotations"], r4["annotations"]
    # 7) 不变量: 每个单块切片裸 in 原文
    for rec in product["records"]:
        for loc in rec["locations"]:
            for s in loc["segments"]:
                blk = next(b for b in blocks if b["uid"] == s["uid"])
                assert s["slice"] in blk["text"]
    # 8) 单测: parse_fail / 外延 / 折叠映射
    assert parse_nominations("garbage")[1] is False
    assert parse_nominations('[{"kind":"software","name":"X"}]')[0][0]["name"] == "X"
    assert extend_slice(u"SPSS 26.0을 사용", 4) == (9, True)
    assert extend_slice(u"SPSS 로 분석", 4) == (4, False)
    f, kept = fold_with_map(u"AB CD")
    assert f == "ABCD" and kept == [0, 1, 3, 4]
    assert is_biblio_footnote(u"30) Arbuckle, AMOS manuel, SPSS Inc., 1997, p.201.")
    assert not is_biblio_footnote(u"* 이 논문은 한국연구재단의 지원을 받았다(NRF-2023).")
    print("selfcheck OK:", json.dumps(audit["counts"], ensure_ascii=False))
    return 0


def main(argv):
    if argv and argv[0] == "selfcheck":
        return selfcheck()
    docs = all_docs() if (argv and argv[0] == "all") else argv
    if not docs:
        print("usage: ner_lite.py selfcheck|<doc>...|all")
        return 1
    cfg = json.load(io.open(os.path.join(ROOT, "channels.json"), encoding="utf-8")).get("llm") or {}
    model = lambda prompt, _cfg=None: call_llm(cfg, prompt)
    model.model_name = cfg.get("model")
    for doc in docs:
        product, audit = run_doc(doc, model)
        d = write_outputs(doc, product, audit)
        print("%s -> %s  %s" % (doc, d, json.dumps(audit["counts"], ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
