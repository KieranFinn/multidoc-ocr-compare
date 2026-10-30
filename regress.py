# -*- coding: utf-8 -*-
r"""五大基础问题回归检测(验收标准 v1.0 §5.1 的语言无关判据, 单位=参考文献条目):
  1 截断 T1 终止标点 / T2 开放连接符 / T3 编号断档 / T4 括号配平 / T5 悬置行
  2 作者丢失        条目以逗号/引号/书名号开头(无作者名)
  3 跨页拼接        T5 悬置 + 下页顶部续接候选; 附页码混入检测
  4 类型误判        脚注引用保留率(footnote 型文档); 文末区块被标成页眉页脚丢弃的检查
  5 韩文分词粘连    [가-힣]{7,} 密度(每千字) + 样例(疑粘连, 复合专名需人工辨别)
输出: out/_regress_report.html (按 文档×问题 定位到页, 供 viewer_compare 人工复核)
      控制台摘要。用法: python regress.py [--out out]
"""
import os, re, io, json, argparse, html, glob

ROOT = os.path.dirname(os.path.abspath(__file__))
REF_HEAD = re.compile(r"참고\s*문헌|인용\s*문헌|参考文献|References|REFERENCES|Bibliography|문헌", re.I)
NUM_MARK = re.compile(r"\[\s*(\d+)\s*\]")
TERMINAL = set(u".”」』)）》…")
OPEN_CONN = set(u",·,/\\")  # T2: 末字符为开放连接符(半角逗号/中点/斜杠/反斜杠)
BRACKETS = [(u"「", u"」"), (u"『", u"』"), (u"(", u")"), (u"“", u"”")]
SKIP_CAT = {"Page-header", "Page-footer", "Page-number", "Section-header"}  # 区内的 Section-header 是分节标签(如 "Ⅰ. 국내 문헌"), 不是条目
ABSTRACT_HEAD = re.compile(r"Abstract|요\s*약|초\s*록|Keywords?", re.I)

def split_entries(text):
    """块文本 -> 条目列表: 按 [n] 编号切(每块可能含多条); 无编号则整块一条(著者-出版年制)"""
    t = (text or "").strip()
    if not t:
        return []
    marks = list(NUM_MARK.finditer(t))
    if len(marks) >= 2:
        parts, last = [], 0
        for m in marks:
            if m.start() > last:
                parts.append(t[last:m.start()])
            last = m.start()
        parts.append(t[last:])
        return [p.strip() for p in parts if p.strip()]
    return [t]

def check_entry(e, box, ph):
    """返回命中的检测器集合(T1/T2/T4/T5/作者丢失/页码混入)"""
    flags = []
    tail = e.rstrip()
    if not tail:
        return flags
    last = tail[-1]
    if last not in TERMINAL:
        flags.append("T1")
    if last in OPEN_CONN or tail.endswith((u" 및", u" 와", u" 과")):
        flags.append("T2")
    for a, b in BRACKETS:
        if tail.count(a) != tail.count(b):
            flags.append("T4")
            break
    if box and ph and box[3] > 0.92 * ph and "T1" in flags:
        flags.append("T5")
    body = NUM_MARK.sub("", tail).lstrip()
    if body.startswith((u",", u",", u"“", u"「", u"『", u"'")):
        flags.append("NOAUTHOR")
    if re.search(r"(^|\n)\s*\d{1,3}\s*(?=\n)", tail):
        flags.append("PGNO")
    return flags

def hangul(s):
    return len(re.findall(u"[가-힣]", s or ""))

def glue_runs(s, minlen=7):
    return re.findall(u"[가-힣]{%d,}" % minlen, s or "")

def doc_entries(doc_dir):
    """从 merged.json 重建每渠道 页->块 列表(merged.json 块无 ch 分组但 channels 已分组)"""
    mj = json.load(io.open(os.path.join(doc_dir, "merged.json"), encoding="utf-8"))
    pages = []  # [(page_no, w, h, {ch: [blk]}), ...]
    for pg in mj["page_data"]:
        pages.append((pg["page"], pg["w"], pg["h"], pg["channels"]))
    return pages

def analyze_doc(doc, doc_dir):
    pages = doc_entries(doc_dir)
    rep = {"doc": doc, "issues": [], "glue": {}, "t3": {}, "refpages": {},
           "footnote": {}, "pair": {}, "total_entries": {}}
    for ch in ("mineru", "dots", "paddle"):
        # 定位文末参考文献区(按 Section-header 命中 REF_HEAD 的最后一页起)
        ref_from = None
        for pno, w, h, chs in pages:
            for b in chs.get(ch) or []:
                if b["cat"] == "Section-header" and REF_HEAD.search(b.get("text") or ""):
                    ref_from = pno
        entries = []  # (pno, box, text)
        if ref_from is not None:
            for pno, w, h, chs in pages:
                if pno < ref_from:
                    continue
                stop = False
                for b in chs.get(ch) or []:
                    if b["cat"] == "Section-header" and ABSTRACT_HEAD.search(b.get("text") or ""):
                        stop = True  # 参考文献之后的摘要/关键词等新章节, 不属于文献区
                        break
                if stop:
                    break
                for b in chs.get(ch) or []:
                    if b["cat"] in SKIP_CAT:
                        continue
                    for e in split_entries(b.get("text") or ""):
                        entries.append((pno, b.get("box") or [0, 0, 0, 0], e))
        rep["refpages"][ch] = ref_from
        rep["total_entries"][ch] = len(entries)
        # T1/T2/T4/T5/作者丢失/页码混入
        for pno, box, e in entries:
            for f in check_entry(e, box, pages[pno - 1][2] if pno - 1 < len(pages) else None):
                rep["issues"].append({"ch": ch, "page": pno, "flag": f,
                                      "txt": e[-90:], "head": e[:24]})
        # T3 编号断档(只认条目首的 [n] 标签, n≤300; 法学的 [1933] 类年份括号不计)
        nums = []
        for pno, w, h, chs in pages:
            if ref_from is None or pno < ref_from:
                continue
            for b in chs.get(ch) or []:
                if b["cat"] in SKIP_CAT:
                    continue
                for ln in (b.get("text") or "").splitlines() or [b.get("text") or ""]:
                    m = re.match(r"\s*\[\s*(\d{1,3})\s*\]", ln)
                    if m and int(m.group(1)) <= 300:
                        nums.append(int(m.group(1)))
        if nums:
            seen, gaps, dups = set(), [], []
            for n in nums:
                if n in seen:
                    dups.append(n)
                seen.add(n)
            for n in range(min(seen), max(seen) + 1):
                if n not in seen:
                    gaps.append(n)
            if gaps or dups:
                rep["t3"][ch] = {"min": min(seen), "max": max(seen), "gap": gaps, "dup": sorted(set(dups))}
        # 跨页续接候选: T5 悬置条目的下一页顶部是否有条目
        for pno, box, e in entries:
            fl = check_entry(e, box, pages[pno - 1][2] if pno - 1 < len(pages) else None)
            if "T5" in fl and pno < len(pages):
                nxt = pages[pno]
                top = [b for b in (nxt[3].get(ch) or []) if b["cat"] not in SKIP_CAT
                       and (b.get("box") or [0, 999])[1] < 0.10 * nxt[2]]
                if top:
                    rep["issues"].append({"ch": ch, "page": pno, "flag": "XPAGE",
                                          "txt": e[-70:], "head": (top[0].get("text") or "")[:40]})
        # 脚注引用保留(footnote 型文档: 全文 Footnote 块计数)
        rep["footnote"][ch] = sum(1 for pno, w, h, chs in pages for b in (chs.get(ch) or []) if b["cat"] == "Footnote")
        # 跨页并入(MinerU 把下页段首并入上页末块留下的空块)与跨渠道回填计数
        for pno, w, h, chs in pages:
            for b in (chs.get(ch) or []):
                if b.get("xmerge"):
                    rep["issues"].append({"ch": ch, "page": pno, "flag": "XM",
                                          "txt": "", "head": "段首空块: 内容被并入上一页末块(页码混入风险)"})
                elif b.get("text_from"):
                    rep["issues"].append({"ch": ch, "page": pno, "flag": "BF",
                                          "txt": "", "head": "空块已回填(来源=%s)" % b["text_from"]})
        # 分词粘连密度
        all_txt = "\n".join((b.get("text") or "") for pno, w, h, chs in pages for b in (chs.get(ch) or []))
        hnum = hangul(all_txt)
        runs = glue_runs(all_txt)
        rep["glue"][ch] = {"hangul": hnum, "runs": len(runs), "per1k": round(len(runs) / max(hnum, 1) * 1000, 2),
                           "samples": sorted(set(runs), key=len, reverse=True)[:5]}
        # 条目键(跨渠道配对用)
        rep["pair"][ch] = set()
        for pno, box, e in entries:
            key = re.sub(r"\s+", "", NUM_MARK.sub("", e))[:14]
            if key:
                rep["pair"][ch].add(key)
    # 跨渠道条目印证
    allk = set().union(*rep["pair"].values()) if rep["pair"] else set()
    rep["pair_stats"] = {
        "shared23": sum(1 for k in allk if sum(k in rep["pair"][c] for c in rep["pair"]) >= 2),
        "single": sum(1 for k in allk if sum(k in rep["pair"][c] for c in rep["pair"]) == 1),
    }
    return rep

FLAG_CN = {"T1": "截断·无终止标点", "T2": "截断·开放连接符", "T3": "编号断档", "T4": "括号不配平",
           "T5": "悬置行(近页底)", "XPAGE": "跨页续接候选", "NOAUTHOR": "作者丢失(条目以标点/引号开头)",
           "PGNO": "页码混入候选", "XM": "跨页并入空块(引擎缺陷)", "BF": "空块跨渠道回填"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args()
    reps = []
    for doc in sorted(os.listdir(a.out)):
        dd = os.path.join(a.out, doc)
        if not os.path.isdir(dd) or doc.startswith("_") or not os.path.exists(os.path.join(dd, "merged.json")):
            continue
        reps.append(analyze_doc(doc, dd))
    # 控制台摘要
    print("== 回归摘要(13 篇, 条目级) ==")
    agg = {}
    for r in reps:
        for it in r["issues"]:
            agg[it["flag"]] = agg.get(it["flag"], 0) + 1
    for f in ("T1", "T2", "T4", "T5", "XPAGE", "NOAUTHOR", "PGNO", "XM", "BF"):
        print("  %-8s %s: %d 条" % (f, FLAG_CN.get(f, ""), agg.get(f, 0)))
    for r in reps:
        if r["t3"]:
            print("  T3 编号断档 [%s]: %s" % (r["doc"], r["t3"]))
    print("\n== 分词粘连密度(疑粘连 run 数/千韩文字, 复合专名需人工辨别) ==")
    for r in reps:
        g = r["glue"]
        print("  %-28s mineru=%-6s dots=%-6s paddle=%-6s | 最长样例: %s" % (
            r["doc"], g.get("mineru", {}).get("per1k", "-"), g.get("dots", {}).get("per1k", "-"),
            g.get("paddle", {}).get("per1k", "-"),
            (g.get("paddle", {}).get("samples") or [""])[0][:26]))
    print("\n== 脚注保留(Footnote 块数) ==")
    for r in reps:
        f = r["footnote"]
        if any(f.get(c) for c in f):
            print("  %-28s mineru=%s dots=%s paddle=%s" % (r["doc"], f.get("mineru"), f.get("dots"), f.get("paddle")))
    print("\n== 跨渠道条目印证 ==")
    ts = sum(r["pair_stats"]["shared23"] for r in reps)
    tg = sum(r["pair_stats"]["single"] for r in reps)
    print("  双渠道及以上命中 %d 条, 仅单渠道命中 %d 条(单渠道条目进复核队列)" % (ts, tg))
    # HTML 报告
    rows = []
    for r in reps:
        for it in r["issues"]:
            rows.append((r["doc"], it["flag"], it["ch"], it["page"], it.get("head", ""), it.get("txt", "")))
    def esc(s): return html.escape(str(s), quote=False)
    body = "\n".join(
        "<tr><td>%s</td><td><b>%s</b><br/><span class=f>%s</span></td><td>%s</td><td><a href='%s/viewer_compare.html#p%d'>p%d</a></td><td class=t>%s<br/><span class=f>%s</span></td></tr>"
        % (esc(d0), f0, esc(FLAG_CN.get(f0, f0)), esc(ch), esc(d0), pg, pg, esc(hd), esc(tx))
        for d0, f0, ch, pg, hd, tx in rows)
    page = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>五大问题回归报告</title>
<style>body{font-family:"Microsoft YaHei";max-width:1280px;margin:24px auto;background:#f4f5f7;padding:0 16px}
table{border-collapse:collapse;background:#fff;width:100%%;font-size:12px}td,th{border:1px solid #e5e7eb;padding:5px 8px;vertical-align:top}
th{background:#eef2ff;text-align:left}.f{color:#9ca3af;font-size:11px}.t{max-width:560px}a{color:#2563eb}
h1{font-size:19px}p{color:#6b7280;font-size:13px}</style></head><body>
<h1>五大基础问题回归报告(条目级, 按 文档×问题 定位)</h1>
<p>判据=验收标准 v1.0 §5.1 T1–T5(语言无关)。点页码进 1:3 对照查看器对应页; 悬停块可与原图框联动核对。T5/XPAGE=跨页, NOAUTHOR=作者丢失, PGNO=页码混入, T1/T2/T4=截断残留。</p>
<table><tr><th>文档</th><th>问题</th><th>渠道</th><th>页</th><th>条目(头/尾)</th></tr>%s</table>
</body></html>""" % body
    fp = os.path.join(a.out, "_regress_report.html")
    io.open(fp, "w", encoding="utf-8").write(page)
    print("\n报告 -> %s (%d 条 flagged)" % (fp, len(rows)))

if __name__ == "__main__":
    main()
