r"""out\ 产物效果分析器: 逐文档×逐渠道汇总解析质量指标, 生成 _analysis_report.html + _analysis_summary.csv
用法: python analyze.py [--out <dir>]   (默认分析 pipeline/out)
指标均为客观统计: 块数/文本字符/韩文字符/U+FFFD/空页/类别分布/页数匹配, 不做主观质量打分。
"""
import os, re, io, json, argparse, datetime, csv
import pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
CH = ("mineru", "dots", "paddle")
CH_TITLE = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}


def _parse(fp):
    """容错解析 page_N.json(复用 pipeline 的抢救逻辑)"""
    try:
        raw = io.open(fp, encoding="utf-8").read()
    except OSError:
        return [], False
    try:
        j = json.loads(raw)
        if isinstance(j, str):
            j = json.loads(j)
        return (j if isinstance(j, list) else []), False
    except json.JSONDecodeError:
        try:
            outer = json.loads(raw)
            inner = outer if isinstance(outer, str) else raw
        except Exception:
            inner = raw
        out = []
        pat = re.compile(r'\{"bbox": \[([^\]]+)\],\s*"category":\s*"([^"]*)",\s*"text":\s*"((?:[^"\\]|\\.)*)"\}', re.S)
        for m in pat.finditer(inner):
            try:
                txt = m.group(3)
                if "\\" in txt:
                    txt = txt.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
                out.append({"bbox": [float(v) for v in m.group(1).split(",")], "category": m.group(2), "text": txt})
            except Exception:
                continue
        return out, True


def _page_files(d):
    fs = {}
    for x in os.listdir(d):
        m = re.match(r".*page_(\d+)\.json$", x)
        if m:
            fs[int(m.group(1))] = os.path.join(d, x)
    return fs


def _flatten(c):
    """拍平块内容: 直接复用 pipeline.flatten_content, 与查看器/合并器口径一致(含任意 *_content 兜底)"""
    return pipeline.flatten_content(c)


def _metrics(src):
    """返回 {pages, blocks, chars, hangul, fffd, empty_pages, cats, salvaged, md, per_page} 或 None;
    MinerU 分支另带 mineru=True。page_N.json 分支跳过无 bbox 的块(不计数),
    MinerU 分支统计全部块; chars 为拍平后纯文本长度(不含 JSON 标点)。"""
    if not src or not os.path.exists(src):
        return None
    if os.path.isfile(src):
        if src.lower().endswith(".md"):  # md 模式: 整篇文本无块级
            t = io.open(src, encoding="utf-8", errors="ignore").read()
            return {"pages": None, "blocks": 0, "chars": len(t), "hangul": len(re.findall(r"[\uac00-\ud7a3]", t)),
                    "fffd": t.count("\ufffd"), "empty_pages": [], "cats": {}, "salvaged": False, "md": True}
        return None
    fs = _page_files(src)
    if not fs:
        # MinerU 风格: 递归找 *_content_list_v2.json(按页一个大列表)
        cands = []
        for root, dirs, files in os.walk(src):
            cands += [os.path.join(root, x) for x in files if x.endswith("_content_list_v2.json")]
        if not cands:
            return None
        try:
            cl = json.loads(io.open(cands[0], encoding="utf-8").read())
        except Exception:
            return None
        if not isinstance(cl, list):
            return None
        blocks = chars = hangul = fffd = 0
        empty_pages, cats, per_page = [], {}, {}
        for pi, pg in enumerate(cl):
            pg = pg if isinstance(pg, list) else []
            per_page[pi] = len(pg)
            if not pg:
                empty_pages.append(pi + 1)
            for b in pg:
                if not isinstance(b, dict):
                    continue
                t = _flatten(b.get("content"))
                blocks += 1
                chars += len(t)
                hangul += len(re.findall(r"[\uac00-\ud7a3]", t))
                fffd += t.count("\ufffd")
                c = b.get("type", "text")
                cats[c] = cats.get(c, 0) + 1
        return {"pages": len(cl), "blocks": blocks, "chars": chars, "hangul": hangul, "fffd": fffd,
                "empty_pages": empty_pages, "cats": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
                "salvaged": False, "md": False, "per_page": per_page, "mineru": True}
    blocks = chars = hangul = fffd = 0
    empty_pages, cats, salvaged = [], {}, False
    per_page = {}
    for pi in sorted(fs):
        lst, salv = _parse(fs[pi])
        salvaged = salvaged or salv
        per_page[pi] = len(lst)
        if not lst:
            empty_pages.append(pi + 1)  # 1-based 页码
        for b in lst:
            if not isinstance(b, dict) or not b.get("bbox"):
                continue
            t = b.get("text") or ""
            if not isinstance(t, str):
                try:
                    t = json.dumps(t, ensure_ascii=False)
                except Exception:
                    t = str(t)
            blocks += 1
            chars += len(t)
            hangul += len(re.findall(r"[\uac00-\ud7a3]", t))
            fffd += t.count("\ufffd")
            c = b.get("category", "Text")
            cats[c] = cats.get(c, 0) + 1
    return {"pages": len(fs), "blocks": blocks, "chars": chars, "hangul": hangul, "fffd": fffd,
            "empty_pages": empty_pages, "cats": dict(sorted(cats.items(), key=lambda kv: -kv[1])), "salvaged": salvaged,
            "md": False, "per_page": per_page}


def _doc_pages(pages_dir):
    if not os.path.isdir(pages_dir):
        return 0
    return len([x for x in os.listdir(pages_dir) if re.match(r"page_\d+\.jpg$", x)])


def collect(out_root):
    docs = []
    for doc in sorted(os.listdir(out_root)):
        ddir = os.path.join(out_root, doc)
        if not os.path.isdir(ddir) or doc.startswith("_"):
            continue
        n_pages = _doc_pages(os.path.join(ddir, "pages"))
        row = {"doc": doc, "n_pages": n_pages, "ch": {}}
        for ch in CH:
            src = os.path.join(ddir, "raw", ch)
            m = _metrics(src)
            if m is None:  # 该渠道无产物
                alt = os.path.join(ddir, "raw", ch + "_api")
                m = _metrics(alt)
                if m is not None:
                    m["via"] = ch + "_api"
            row["ch"][ch] = m
        docs.append(row)
    return docs


def verdict(n_pages, m):
    """规则化状态判语"""
    if m is None:
        return ("无产物", "gray")
    if m.get("md"):
        return ("md模式(无块级)", "blue")
    flags = []
    if m["pages"] != n_pages:
        flags.append("页数不匹配(%d≠%d)" % (m["pages"], n_pages))
    if m["salvaged"]:
        flags.append("含损坏抢救页")
    if m["fffd"]:
        flags.append("FFFD×%d" % m["fffd"])
    if m["empty_pages"]:
        flags.append("空页:%s" % ",".join(map(str, m["empty_pages"][:4])) + ("..." if len(m["empty_pages"]) > 4 else ""))
    # 低块数页(>1页文档): 正文页正常 8+ 块, <=3 块基本是上游截断抢救结果(dots 导出 bug)
    if n_pages > 1 and m.get("per_page"):
        low = [str(p + 1) for p, c in m["per_page"].items() if c <= 3]
        if low:
            flags.append("低块数页(疑截断):%s" % ",".join(low[:5]) + ("..." if len(low) > 5 else ""))
    if m["hangul"] == 0 and m["chars"] > 0:
        flags.append("韩文字符为0(语言不符?)")
    if flags:
        return ("；".join(flags), "orange")
    return ("OK", "green")


BAR = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#dc2626", "#65a30d"]


def catbar(cats, total):
    if not cats or not total:
        return '<span style="color:#9ca3af">-</span>'
    parts = []
    for i, (c, n) in enumerate(list(cats.items())[:6]):
        w = max(2, round(120 * n / total))
        parts.append('<span style="display:inline-block;height:10px;width:%dpx;background:%s;border-radius:2px;vertical-align:middle" title="%s: %d"></span>' % (w, BAR[i % len(BAR)], c, n))
    top = max(cats.items(), key=lambda kv: kv[1])
    return "".join(parts) + ' <span style="color:#6b7280;font-size:11px">%s×%d</span>' % (top[0], top[1])


def build_report(docs, out_root):
    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows, details = [], []
    for d in docs:
        n = d["n_pages"]
        cells, stats = [], {}
        for ch in CH:
            m = d["ch"][ch]
            v, color = verdict(n, m)
            stats[ch] = (m, v, color)
            if m is None:
                cells.append('<td colspan="6" style="color:#9ca3af;text-align:center">无产物</td>')
                continue
            via = m.get("via", "")
            if m.get("md"):
                cells.append('<td colspan="6" style="text-align:center">md模式 字符%d 韩文%d FFFD %d %s</td>' % (m["chars"], m["hangul"], m["fffd"], v))
                continue
            empt = ",".join(map(str, m["empty_pages"][:3])) or "-"
            cells.append(
                '<td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td title="空页(1基):%s">%s</td>'
                % (m["pages"], m["blocks"], m["chars"], m["hangul"], m["fffd"], ",".join(map(str, m["empty_pages"])) or "无", empt))
        rows.append("<tr><td><b>%s</b> <span style='color:#9ca3af;font-size:11px'>%d页</span></td>%s</tr>"
                    % (d["doc"], n, "".join(cells)))
        # 明细
        page_rows = []
        lowpages = {}  # ch -> set(低块数页0基)
        for ch in CH:
            m = d["ch"][ch]
            if not m or m.get("md"):
                continue
            pp = m.get("per_page", {})
            lowpages[ch] = set(p for p, c in pp.items() if c <= 3) if n > 1 else set()
            tds = "".join('<td class="%s">%d</td>' % ("zero" if pp.get(p, 0) == 0 else "", pp.get(p, 0)) for p in range(n))
            v, color = verdict(n, m)
            page_rows.append('<tr><td>%s</td><td><span style="color:%s">%s</span></td>%s</tr>' % (CH_TITLE[ch], color, v, tds))
        # 共识判定: >=2 渠道同页低块数 -> 文档自身稀疏页; 单渠道 -> 引擎侧异常
        from collections import Counter
        cnt = Counter()
        for ch in CH:
            cnt.update(lowpages.get(ch, set()))
        consensus = sorted(p + 1 for p, c in cnt.items() if c >= 2)
        single = sorted(p + 1 for p, c in cnt.items() if c == 1)
        note = ""
        if consensus:
            note += '<p class="tip">跨渠道共识低块数页(≥2渠道): %s —— 系文档自身稀疏/特殊页, 非引擎故障。</p>' % ",".join(map(str, consensus))
        if single:
            note += '<p class="tip">单渠道低块数页: %s —— 引擎侧输出异常(截断/漏识别), 参考该渠道判定列。</p>' % ",".join(map(str, single))
        # 类别分布
        cat_rows = []
        for ch in CH:
            m = d["ch"][ch]
            if not m or m.get("md"):
                continue
            cat_rows.append('<tr><td>%s</td><td>%s</td><td style="font-size:11px;color:#6b7280">%s</td></tr>'
                            % (CH_TITLE[ch], catbar(m["cats"], m["blocks"]),
                               ", ".join("%s×%d" % kv for kv in list(m["cats"].items())[:8]) or "-"))
        cmp_link = ""
        if os.path.isfile(os.path.join(out_root, d["doc"], "viewer_compare.html")):
            cmp_link = ' <a href="%s/viewer_compare.html" style="font-size:12px;font-weight:normal">1:3 对照 →</a>' % d["doc"]
        details.append('''<section><h2>%s <span class="stat">%d 页</span>%s</h2>
%s
<table class="mtx"><tr><th>渠道</th><th>判定</th>%s</tr>%s</table>
<table class="cats"><tr><th style="width:110px">渠道</th><th style="width:200px">类别分布</th><th>明细</th></tr>%s</table>
</section>''' % (d["doc"], n, cmp_link, note, "".join("<th>p%d</th>" % (p + 1) for p in range(n)), "".join(page_rows), "".join(cat_rows)))

    html = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>out 产物效果分析报告</title>
<style>
body{font-family:"Microsoft YaHei";margin:0;background:#f4f5f7;color:#222;max-width:1560px;margin:0 auto;padding:20px 22px}
h1{font-size:20px}h2{font-size:16px;color:#374151;border-left:4px solid #2563eb;padding-left:8px}
.stat{color:#6b7280;font-weight:normal;font-size:12px;margin-left:10px}
table{border-collapse:collapse;background:#fff;width:100%%;margin:12px 0}
td,th{border:1px solid #e5e7eb;padding:6px 10px;font-size:13px;text-align:left}
th{background:#eef2ff}
td.zero{background:#fee2e2;color:#dc2626;font-weight:bold}
.mtx td,.mtx th{text-align:center;font-size:12px;padding:4px 6px}
.cats td{vertical-align:middle}
.tip{color:#6b7280;font-size:12px}
</style></head><body>
<h1>out&#92; 产物效果分析报告</h1>
<p class="tip">生成时间: %s ｜ 文档数: %d ｜ 指标口径: 块数/文本字符/韩文字符/U+FFFD/空页(1基)/页数匹配; 数据来源 raw&#92;&lt;渠道&gt;&#92;page_*.json(md 模式渠道按整篇文本计)。只统计客观指标, 不代表语义质量。</p>
<table><tr><th rowspan="2">文档</th><th colspan="6">%s</th><th colspan="6">%s</th><th colspan="6">%s</th></tr>
<tr>%s</tr>
%s</table>
%s
</body></html>""" % (gen, len(docs), CH_TITLE["mineru"], CH_TITLE["dots"], CH_TITLE["paddle"],
                     '<th>页</th><th>块</th><th>字符</th><th>韩文</th><th>FFFD</th><th>空页</th>' * 3,
                     "".join(rows), "".join(details))
    fp = os.path.join(out_root, "_analysis_report.html")
    io.open(fp, "w", encoding="utf-8").write(html)
    return fp


def build_csv(docs, out_root):
    fp = os.path.join(out_root, "_analysis_summary.csv")
    with io.open(fp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文档", "文档页数", "渠道", "来源", "渠道页数", "块数", "文本字符", "韩文字符", "U+FFFD", "空页(1基)", "损坏抢救", "判定"])
        for d in docs:
            for ch in CH:
                m = d["ch"][ch]
                v, _ = verdict(d["n_pages"], m)
                if m is None:
                    w.writerow([d["doc"], d["n_pages"], CH_TITLE[ch], "-", "", "", "", "", "", "", "", v])
                elif m.get("md"):
                    w.writerow([d["doc"], d["n_pages"], CH_TITLE[ch], "md", "", 0, m["chars"], m["hangul"], m["fffd"], "", "", v])
                else:
                    w.writerow([d["doc"], d["n_pages"], CH_TITLE[ch], m.get("via", "raw/" + ch), m["pages"], m["blocks"],
                                m["chars"], m["hangul"], m["fffd"], ",".join(map(str, m["empty_pages"])), "是" if m["salvaged"] else "", v])
    return fp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args()
    docs = collect(a.out)
    if not docs:
        print("out 目录下没有文档产物:", a.out)
        return
    rp = build_report(docs, a.out)
    cp = build_csv(docs, a.out)
    print("报告 ->", rp)
    print("CSV  ->", cp)
    # 控制台速览: 异常清单
    print("\n== 异常速览 ==")
    any_flag = False
    for d in docs:
        for ch in CH:
            m = d["ch"][ch]
            v, color = verdict(d["n_pages"], m)
            if color != "green":
                any_flag = True
                print("  [%s] %s: %s" % (d["doc"], CH_TITLE[ch], v))
    if not any_flag:
        print("  (全部 OK)")


if __name__ == "__main__":
    main()
