# -*- coding: utf-8 -*-
"""失败档案 + 轮次台账 + 进度报告(2026-11-18)。

两种用法:
  python failures.py                          # 看当前红单聚类(不落盘)
  python failures.py --round [--tokens N] [--did "这轮做了什么"] [--json x.json]
      # 记一轮台账(_rounds.jsonl) + 跟上轮对比 + 重新生成进度报告(_rounds_report.html)

台账字段: 轮号/时间/耗时/这轮的token消耗/这轮怎么收敛的/总篇数/没通过数/正确率/
          剩余红单(按期刊分组, 组内篇目)。
token 消耗没有自动来源(运行者从界面抄), 不填就记"没记录"。
进度报告是给不干活的人看的: 不写术语, 每轮一节, 每节说清四件事——
什么时候跑的/花了多少/token 多少/这轮怎么收敛的/还剩哪些文档(带对照视图链接和实体摘要)。
"""
import io
import json
import os
import re
import sys
import time
import html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qc
from metapy import journals

OUT = "out"
UP = "uploads"
HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "_rounds.jsonl")
REPORT = os.path.join(HERE, "_rounds_report.html")

_PUBDATE_ANY = re.compile(u"(?:원고)?접수일|게재\\s*확정일")
_KW_HEAD = re.compile(u"(?:키\\s*워드|핵심\\s*어|주제\\s*어|중심\\s*어|주요\\s*어|Key\\s*words?)", re.I)
_KW_BRACKET = re.compile(r"(?m)^\s*\[[^\[\]]{4,80}/[^\[\]]{4,80}\]\s*$")

_FAM_NAME = {f["key"]: ((f["names"] or [f.get("doi_prefix") or f["key"]])[0])
             for f in journals.FAMILIES}


def _doc_text(doc, max_pages=3):
    ud = os.path.join(OUT, doc, "unified")
    if not os.path.isdir(ud):
        return "", ""
    pages = sorted(f for f in os.listdir(ud) if re.match(r"page_\d+\.json$", f))
    first, alltxt = [], []
    for i, f in enumerate(pages):
        try:
            j = json.load(io.open(os.path.join(ud, f), encoding="utf-8"))
        except Exception:
            continue
        t = "\n".join((u.get("main_text") or "") for u in j.get("unified") or [])
        alltxt.append(t)
        if i < max_pages:
            first.append(t)
    return "\n".join(first), "\n".join(alltxt)


def collect():
    rows = []
    for f in sorted(os.listdir(UP)):
        if not f.endswith(".pdf"):
            continue
        doc = f[:-4]
        dd = os.path.join(OUT, doc)
        if not os.path.isdir(dd):
            rows.append({"doc": doc, "level": "无产物", "issues": [], "note": "",
                         "family": None, "hints": {}})
            continue
        r = qc.qc_of(doc)
        if r["level"] != "bad":
            continue
        meta = {}
        mp = os.path.join(dd, "metadata.json")
        if os.path.isfile(mp):
            try:
                meta = json.load(io.open(mp, encoding="utf-8"))
            except Exception:
                pass
        first, alltxt = _doc_text(doc)
        hints = {
            "has_doi": bool(re.search(r"10\.\d{4,9}/", first)),
            "date_fn": bool(_PUBDATE_ANY.search(alltxt)),
            "kw_marker": bool(_KW_HEAD.search(first)),
            "kw_bracket": bool(_KW_BRACKET.search(first)),
        }
        fam = journals.family_of(meta, first)
        rows.append({"doc": doc, "level": r["level"], "issues": r["issues"],
                     "note": "", "family": fam and fam["key"], "hints": hints})
    return rows


def _load_notes():
    nfp = os.path.join(HERE, "gui_notes.json")
    if os.path.isfile(nfp):
        try:
            return json.load(io.open(nfp, encoding="utf-8"))
        except Exception:
            pass
    return {}


def _cluster(rows, notes):
    groups = {}
    for row in rows:
        key = (row["family"] or "?",
               "/".join(row["issues"]) if row["issues"] else row["level"])
        groups.setdefault(key, []).append(row)
    out = {}
    for (fam, miss), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        label = "%s|%s" % (fam, miss)
        docs = sorted(r["doc"] for r in rs)
        out[label] = {
            "display": "%s · 缺 %s" % (_FAM_NAME.get(fam, "其他刊"), miss),
            "docs": docs,
            "issues_by_doc": {r["doc"]: r["issues"] for r in rs},
            "all_noted": all(notes.get(d, "").strip() for d in docs),
            "hinted": sum(1 for r in rs if any(r["hints"].values())),
        }
    return out


def _print_clusters(clusters, total):
    print("== 失败聚类(刊例 × 缺项) ==  共 %d 篇" % total)
    for label, c in clusters.items():
        print("  %-26s x%-3d 可修线索: %-2d %s%s" % (
            c["display"][:26], len(c["docs"]), c["hinted"], c["docs"][:4],
            "  [全部已标原因]" if c["all_noted"] else "  [还有没写原因的!]"))


def _read_ledger():
    if not os.path.isfile(LEDGER):
        return []
    out = []
    for ln in io.open(LEDGER, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _compare(now, prev, prev2):
    """返回人话对比结果: 每组现在是 消失/缩小/没动/连续没动/新出现。"""
    res = {"消失": [], "缩小": [], "没动": [], "连续没动": [], "新出现": []}
    for label, c in now.items():
        if label not in prev:
            res["新出现"].append((label, len(c["docs"])))
            continue
        pn = len(prev[label]["docs"])
        cn = len(c["docs"])
        if cn < pn:
            res["缩小"].append((label, pn, cn))
            continue
        stuck_prev2 = prev2 and label in prev2 and len(prev2[label]["docs"]) <= pn
        res["连续没动" if stuck_prev2 else "没动"].append((label, cn))
    for label, pc in prev.items():
        if label not in now:
            res["消失"].append((label, len(pc["docs"])))
    return res


def _entity_blurb(doc):
    try:
        import entview
        cells = entview.cells_for(doc, OUT, os.path.join(HERE, "out_llm_trial"))
        parts = []
        for k, zh in (("person", "人"), ("org", "机构"), ("dataset", "数据集"),
                      ("funding", "基金"), ("software", "软件")):
            if cells.get(k):
                parts.append("%s:%s" % (zh, cells[k]))
        return "　".join(parts)
    except Exception:
        return ""


def _write_report(rounds, current_clusters, notes):
    e = html.escape
    total = rounds[-1]["total"]
    red = rounds[-1]["red"]
    acc = rounds[-1]["acc"]
    conv = "已收敛：剩下的都是原文确实没印的（每组都标了原因），别再挖了。" if \
        all(c["all_noted"] for c in current_clusters.values()) and current_clusters else \
        "还没收敛：有的组没写原因，或还有组在动。"
    h = []
    h.append(u"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>抽取修复进度报告</title><style>
body{font-family:'Microsoft YaHei',sans-serif;max-width:1100px;margin:24px auto;color:#222;line-height:1.6}
h1{font-size:22px} h2{font-size:18px;border-bottom:2px solid #4a7ebb;padding-bottom:4px;margin-top:36px}
.now{background:#f0f6ff;border:1px solid #bcd3f5;border-radius:8px;padding:14px 18px;font-size:15px}
.round-meta{color:#666;font-size:13px;margin:4px 0 10px}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f7f7f7}
.tag{display:inline-block;border-radius:4px;padding:0 6px;font-size:12px;margin-right:4px}
.ok{background:#e2f5e2}.warn{background:#fdeec2}.bad{background:#fbdcdc}.new{background:#e6e0f8}
a{color:#2a6fd6} .muted{color:#888;font-size:12px}
details{margin:8px 0} summary{cursor:pointer;color:#2a6fd6}
</style></head><body>""")
    h.append(u"<h1>抽取修复进度报告</h1>")
    h.append(u'<p class="muted">每修一轮就记一笔。下面按时间顺序列出每一轮：什么时候跑的、跑了多久、'
             u'花了多少 token、这轮怎么收敛的、修完还剩哪些文档没通过。</p>')
    h.append(u'<div class="now">当前：共 <b>%d</b> 篇 · 没通过 <b>%d</b> 篇 · '
             u'正确率 <b>%.1f%%</b>（没通过 = 该抽的字段没抽全）<br>%s</div>' % (
                 total, red, acc, e(conv)))
    for rd in rounds:
        n = rd["round"]
        h.append(u"<h2>第 %d 轮</h2>" % n)
        tok = u"%s" % rd["tokens"] if rd.get("tokens") not in (None, "", 0) else u"没记录"
        h.append(u'<div class="round-meta">%s · 跑了 %.1f 秒 · 这轮消耗 token：%s</div>' % (
            e(rd["ts"]), rd.get("took_s") or 0.0, e(tok)))
        h.append(u"<p><b>这轮怎么收敛的：</b>%s</p>" % (e(rd.get("did") or "没写说明")))
        chg = rd.get("change") or {}
        line = []
        if rd.get("prev_red") is not None:
            line.append(u"没通过 %d → %d 篇（正确率 %.1f%% → %.1f%%）" % (
                rd["prev_red"], rd["red"], rd.get("prev_acc") or 0.0, rd["acc"]))
        for tag, zh, cls in (("消失", "修好了", "ok"), ("缩小", "缩小了", "ok"),
                             ("连续没动", "连续没动⚠", "bad"), ("没动", "没动", "warn"),
                             ("新出现", "新冒出来的⚠", "new")):
            items = chg.get(tag) or []
            if items:
                txt = u"、".join(u"%s(%d篇)" % (e(i[0].split("|")[0]), i[-1]) for i in items)
                line.append(u'<span class="tag %s">%s %d 组</span>%s' % (cls, zh, len(items), txt))
        if line:
            h.append(u"<p>%s</p>" % u"<br>".join(line))
        cl = rd.get("clusters") or {}
        if not cl:
            h.append(u"<p>这轮修完没有红单了。</p>")
            continue
        h.append(u"<p>这轮修完还剩 <b>%d</b> 篇没通过：</p>" % rd["red"])
        for label, c in cl.items():
            h.append(u"<details><summary>%s · %d 篇%s</summary><table>" % (
                e(c["display"]), len(c["docs"]),
                u" · 全部已标原因" if c.get("all_noted") else u" · <b style='color:red'>有没写原因的</b>"))
            h.append(u"<tr><th>文档号</th><th>没通过的字段</th><th>原因</th><th>实体摘要</th><th></th></tr>")
            for d in c["docs"]:
                miss = u"、".join((c.get("issues_by_doc") or {}).get(d) or []) or u"(无产物)"
                note = (notes.get(d, "") or "")[:60]
                ent = _entity_blurb(d)
                h.append(u"<tr><td>%s</td><td>%s</td><td class='muted'>%s</td><td class='muted'>%s</td>"
                         u"<td><a href='out/%s/viewer_compare.html'>对照视图</a></td></tr>" % (
                             e(d), e(miss), e(note), e(ent[:120]), e(d)))
            h.append(u"</table></details>")
    h.append(u'<p class="muted">报告生成于 %s · 由 failures.py --round 生成, 每跑一轮自动刷新</p>'
             % time.strftime("%Y-%m-%d %H:%M"))
    h.append(u"</body></html>")
    io.open(REPORT, "w", encoding="utf-8").write("".join(h))


def _round(tokens, did):
    journals.build_registry()   # 注册表每轮自动重扫语料(corpus 行); manual 行永驻
    t0 = time.time()
    notes = _load_notes()
    rows = collect()
    for r in rows:
        r["note"] = notes.get(r["doc"], "")
    clusters = _cluster(rows, notes)
    took = time.time() - t0

    history = _read_ledger()
    prev = history[-1] if history else None
    prev2 = history[-2] if len(history) >= 2 else None
    total = len([f for f in os.listdir(UP) if f.endswith(".pdf")])
    red = len(rows)
    acc = round(100.0 * (total - red) / total, 1) if total else 0.0

    rec = {
        "round": len(history) + 1,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "took_s": round(took, 1),
        "tokens": tokens or None,
        "did": did or "",
        "total": total, "red": red, "acc": acc,
        "clusters": clusters,
    }
    if prev:
        rec["prev_red"] = prev["red"]
        rec["prev_acc"] = prev["acc"]
        rec["change"] = _compare(clusters, prev.get("clusters") or {},
                                 (prev2 or {}).get("clusters"))
    with io.open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 控制台人话摘要
    print("== 第 %d 轮已记录 %s  耗时 %.1fs ==" % (rec["round"], rec["ts"], took))
    print("没通过 %d/%d 篇, 正确率 %.1f%%" % (red, total, acc))
    if prev:
        ch = rec["change"]
        print("跟上轮比: 修好 %d 组 / 缩小 %d 组 / 没动 %d 组 / 连续没动 %d 组 / 新出现 %d 组" % (
            len(ch["消失"]), len(ch["缩小"]), len(ch["没动"]), len(ch["连续没动"]), len(ch["新出现"])))
        for label, cnt in ch["连续没动"]:
            print("  ⚠ 这组连续两轮没动: %s(%d篇) —— 要么当初诊断错, 要么该上 LLM" % (label, cnt))
        for label, cnt in ch["新出现"]:
            print("  ⚠ 这组是新冒出来的: %s(%d篇) —— 警惕是不是改代码改出来的" % (label, cnt))
    else:
        print("(首轮记录, 无对比)")
    uncovered = [c["display"] for c in clusters.values() if not c["all_noted"]]
    if uncovered:
        print("这些组还有没写原因的文档, 先补备注:", uncovered)
    else:
        print("剩下的组全部标了原因(原文证据缺失型)。")

    _write_round_report(history + [rec], clusters, notes)
    print("报告 ->", os.path.basename(REPORT))
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        fp = sys.argv[i + 1] if i + 1 < len(sys.argv) else "_failures.json"
        io.open(fp, "w", encoding="utf-8").write(
            json.dumps(rows, ensure_ascii=False, indent=1))
        print("JSON ->", fp)


def _write_round_report(rounds, current_clusters, notes):
    try:
        _write_report(rounds, current_clusters, notes)
    except Exception as ex:
        print("HTML 报告生成失败(不影响台账):", ex)


def main():
    if "--round" in sys.argv:
        tokens = did = None
        for flag, var in (("--tokens", "tokens"), ("--did", "did")):
            if flag in sys.argv:
                i = sys.argv.index(flag)
                if i + 1 < len(sys.argv):
                    val = sys.argv[i + 1]
                    if var == "tokens":
                        try:
                            tokens = int(val)
                        except ValueError:
                            tokens = None
                    else:
                        did = val
        _round(tokens, did)
        return
    rows = collect()
    notes = _load_notes()
    for row in rows:
        row["note"] = notes.get(row["doc"], "")
    _print_clusters(_cluster(rows, notes), len(rows))
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        fp = sys.argv[i + 1] if i + 1 < len(sys.argv) else "_failures.json"
        io.open(fp, "w", encoding="utf-8").write(
            json.dumps(rows, ensure_ascii=False, indent=1))
        print("JSON ->", fp)


if __name__ == "__main__":
    main()
