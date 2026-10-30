# -*- coding: utf-8 -*-
"""全量 caption 对比: 以 dots/Paddle 为参照, 找 MinerU 丢失图注(Caption)的位置。
判定: 某渠道存在 Caption 块(或图片块内嵌题注文本), 而 MinerU 在相近区域既无 Caption 块、
也无包含该题注前缀文本的块 -> MinerU caption 丢失实例。"""
import io, os, re, glob, json, collections
import pipeline
from PIL import Image

def norm(s):
    return re.sub(r"\s+", "", s or "")[:14]

def dice(a, b):
    """字符 bigram 相似度: 抗单个错字(찰영/촬영 类 OCR 差异)"""
    a, b = norm(a, )[:60], norm(b)[:60]
    if len(a) < 2 or len(b) < 2:
        return 0.0
    A = set(a[i:i+2] for i in range(len(a) - 1))
    B = set(b[i:i+2] for i in range(len(b) - 1))
    return 2.0 * len(A & B) / (len(A) + len(B))

def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    aa = (a[2]-a[0])*(a[3]-a[1]); bb = (b[2]-b[0])*(b[3]-b[1])
    return inter/(aa+bb-inter) if aa+bb-inter > 0 else 0.0

report = []
stat = collections.Counter()
for doc_dir in sorted(glob.glob("out/*")):
    doc = os.path.basename(doc_dir)
    if not os.path.isdir(doc_dir) or doc.startswith("_"):
        continue
    if not os.path.isdir(os.path.join(doc_dir, "raw", "mineru")):
        continue
    sizes = [Image.open(p).size for p in sorted(glob.glob(os.path.join(doc_dir, "pages", "page_*.jpg")))]
    mu = pipeline.adapt_mineru(os.path.join(doc_dir, "raw", "mineru"), sizes,
                               pipeline.mineru_calib_key(os.path.join(doc_dir, "raw", "mineru"))) or {}
    mu_by_page = {}
    mu_txt = set()
    for p, lst in mu.items():
        mu_by_page[p] = lst
        for b in lst:
            t = norm(b.get("text"))
            if t:
                mu_txt.add(t)
            ct = norm(b.get("cap"))  # 表格题注在 cap 字段(可能多行, 逐行归一)
            if ct:
                mu_txt.update(norm(x) for x in (b["cap"].splitlines() or [b["cap"]]))
    for ref_ch in ("dots", "paddle"):
        ref = (pipeline.adapt_dots(os.path.join(doc_dir, "raw", ref_ch), sizes)
               if ref_ch == "dots" else pipeline.adapt_paddle(os.path.join(doc_dir, "raw", ref_ch), sizes)) or {}
        for p, lst in ref.items():
            for b in lst:
                t = b.get("text") or ""
                key = norm(t)
                if not key:
                    continue
                is_cap = b.get("cat") == "Caption" or (
                    len(t) < 160 and re.match(r"^\s*(<(그림|표)\s*\d+|Fig\.?\s*\d|Table\s*\d)", t))
                if not is_cap:
                    continue
                stat["%s_caption_blocks" % ref_ch] += 1
                # MinerU 是否已有: 同页 IoU≥0.3 的含文本块, 或全库文本前缀命中, 或同页 dice≥0.5 的相似文本
                found = key in mu_txt
                if not found:
                    for mb in mu_by_page.get(p) or []:
                        mt = mb.get("text") or ""
                        if not mt.strip():
                            continue
                        mt_lines = mt.splitlines() or [mt]
                        if iou(b["box"], mb["box"]) >= 0.3 \
                                or any(dice(t, ln) >= 0.5 for ln in mt_lines) \
                                or (mb.get("cap") and any(dice(t, ln) >= 0.5
                                                         for ln in (mb["cap"].splitlines() or [mb["cap"]]))):
                            found = True
                            break
                if found:
                    stat["%s_hit_in_mineru" % ref_ch] += 1
                else:
                    stat["mineru_missing_caption"] += 1
                    report.append({"doc": doc, "page": p + 1, "ref": ref_ch, "cat": b["cat"],
                                   "box": [round(x, 1) for x in b["box"]], "txt": t[:110]})

print("统计:", dict(stat))
bydoc = collections.Counter((r["doc"], r["ref"]) for r in report)
print("\n丢失按 文档×参照渠道:")
for k, n in sorted(bydoc.items()):
    print("  %-30s %s: %d" % (k[0], k[1], n))
io.open("out/_caption_missing.json", "w", encoding="utf-8").write(
    json.dumps(report, ensure_ascii=False, indent=1))
print("\n明细 -> out/_caption_missing.json (%d 条)" % len(report))
for r in report[:12]:
    print("  %-30s p%-3d via=%-7s %s" % (r["doc"], r["page"], r["ref"], r["txt"][:60]))
