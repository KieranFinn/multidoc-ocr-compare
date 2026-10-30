# -*- coding: utf-8 -*-
r"""out\ 全渠道分页合并器(按文档产出): 复用 pipeline 适配器(150dpi 页图像素坐标), 每个文档生成:
  out\<doc>\merged\page_<N>.json   该页跨渠道合并块(带渠道标签, 阅读序排序)
  out\<doc>\merged.json            该文档全量合并数据
  out\<doc>\merged.md              该文档人读版合并清单
用法: python merge_out.py [--out <dir>]
"""
import os, re, io, json, argparse, shutil, datetime
import pipeline

ROOT = os.path.dirname(os.path.abspath(__file__))
CH = ("mineru", "dots", "paddle")


def adapt_channel(doc_dir, ch, sizes):
    """返回 (per_page dict 或 None, md_text 或 None, src)"""
    src = os.path.join(doc_dir, "raw", ch)
    if not os.path.exists(src):
        alt = os.path.join(doc_dir, "raw", ch + "_api")
        if os.path.exists(alt):
            src = alt
        else:
            return None, None, None
    if os.path.isfile(src):
        if src.lower().endswith(".json"):
            return pipeline.adapt_paddle_json(src, sizes), None, src
        return None, io.open(src, encoding="utf-8", errors="ignore").read(), src  # md 模式
    if ch == "mineru":
        return pipeline.adapt_mineru(src, sizes, pipeline.mineru_calib_key(src)), None, src
    if ch == "dots":
        return pipeline.adapt_dots(src, sizes), None, src
    return pipeline.adapt_paddle(src, sizes), None, src


def merge_doc(doc, doc_dir):
    r"""合并单文档, 产物写 doc_dir\merged\ 下。返回统计信息 dict。"""
    sizes = []
    pages_dir = os.path.join(doc_dir, "pages")
    if os.path.isdir(pages_dir):
        from PIL import Image
        for x in sorted(os.listdir(pages_dir)):
            if re.match(r"page_\d+\.jpg$", x):
                sizes.append(Image.open(os.path.join(pages_dir, x)).size)
    n = len(sizes)
    ch_data, ch_md = {}, {}
    for ch in CH:
        pp, md_text, src = adapt_channel(doc_dir, ch, sizes)
        if pp is not None:
            ch_data[ch] = pp
        if md_text is not None:
            ch_md[ch] = md_text
    merged_dir = os.path.join(doc_dir, "merged")
    if os.path.isdir(merged_dir):
        shutil.rmtree(merged_dir)
    os.makedirs(merged_dir, exist_ok=True)
    pipeline.mark_crosspage_merge(ch_data, sizes)  # 跨页并入标记(跨渠道证据版, 需全渠道在场)
    pipeline.backfill_empty_blocks(ch_data, CH)  # 空块借其他渠道同区域文本回填, 记 text_from
    page_index = []
    for p in range(n):
        W, H = sizes[p]
        blocks = []
        for ch in CH:
            for b in (ch_data.get(ch) or {}).get(p) or []:
                blk = {"ch": ch, "cat": b.get("cat", "Text"), "box": b.get("box", [0, 0, 0, 0]),
                       "text": b.get("text") or ""}
                if b.get("img"):
                    blk["img"] = "raw/%s/%s" % (ch, b["img"])  # 相对 out/<doc>/ 的引擎抽取图, 供下游直接展示
                if b.get("cap"):
                    blk["cap"] = b["cap"]  # 表格题注/脚注(随 html 提出, 免丢失)
                if b.get("text_from"):
                    blk["text_from"] = b["text_from"]  # 跨渠道回填来源(本渠道此区域为空)
                if b.get("xmerge"):
                    blk["xmerge"] = True  # 引擎把下页段首并入上页末块的空块标记
                    if b.get("xev"):
                        blk["xev"] = b["xev"]  # strong=文本证据 / layout=排版推断
                blocks.append(blk)
        blocks.sort(key=lambda x: (round(x["box"][1] / 18.0), x["box"][0]))  # 阅读序: 行聚合(18px 带)再按 x
        io.open(os.path.join(merged_dir, "page_%d.json" % p), "w", encoding="utf-8").write(json.dumps({
            "doc": doc, "page": p + 1, "w": W, "h": H,
            "channels": {ch: [{"cat": b["cat"], "box": b["box"], "text": b["text"]}
                              for b in blocks if b["ch"] == ch] for ch in CH if ch in ch_data},
            "merged": blocks}, ensure_ascii=False))
        page_index.append(blocks)
    # 文档级 JSON
    payload = {"doc": doc, "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "space": "150dpi 页图像素, 左上原点", "pages": n,
               "channels": {ch: {"blocks": sum(len(v) for v in ch_data[ch].values())} for ch in ch_data},
               "md_channels": {ch: len(t) for ch, t in ch_md.items()},
               "page_data": []}
    for p in range(n):
        payload["page_data"].append({
            "page": p + 1, "w": sizes[p][0], "h": sizes[p][1],
            "channels": {ch: [{"cat": b["cat"], "box": b["box"], "text": b["text"],
                               **({"img": b["img"]} if b.get("img") else {}),
                               **({"cap": b["cap"]} if b.get("cap") else {}),
                               **({"text_from": b["text_from"]} if b.get("text_from") else {}),
                               **({"xmerge": True} if b.get("xmerge") else {})}
                              for b in page_index[p] if b["ch"] == ch] for ch in ch_data}})
    io.open(os.path.join(doc_dir, "merged.json"), "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False))
    # 文档级 Markdown: 渲染式文档(无块标签, 直接可读); 页眉页脚页码剔除, 分页降为源码注释;
    # 表格块嵌入清洗后的 HTML(任何 Markdown 阅读器可渲染); merged.json 仍保留全量带坐标数据
    SKIP_MD = {"Page-header", "Page-footer", "Page-number"}
    L = ["# %s — 全渠道渲染文档" % doc, "",
         "生成时间: %s ｜ 按渠道渲染为独立文档, 阅读序排列, 表格为内嵌 HTML" % payload["generated"], ""]
    for ch in CH:
        if ch in ch_data:
            L.append("\n\n---\n\n# %s（%d 块）" % (ch, payload["channels"][ch]["blocks"]))
        elif ch in ch_md:
            L.append("\n\n---\n\n# %s（md 模式, %d 字符, 无块级）" % (ch, len(ch_md[ch])))
            continue
        else:
            L.append("\n\n---\n\n# %s（无产物）" % ch)
            continue
        first_head = True
        for p in range(n):
            for b in page_index[p]:
                if b["ch"] != ch or b["cat"] in SKIP_MD:
                    continue
                t = b["text"].strip()
                if not t:
                    continue
                if b["cat"] == "Table" and t.startswith("<") and "<table" in t:
                    if b.get("cap"):
                        L.append("\n*%s*" % re.sub(r"\s+", " ", b["cap"]))
                    h = pipeline._TAB_BAD.sub("", t)
                    h = pipeline._TAB_ON.sub("", h)
                    L.append("\n" + h)
                elif b["cat"] == "Section-header":
                    L.append("\n\n%s %s" % ("# " if first_head else "## ", re.sub(r"\s+", " ", t)))
                    first_head = False
                elif b["cat"] == "Caption":
                    L.append("\n*%s*" % re.sub(r"\s+", " ", t))
                elif b["cat"] == "Footnote":
                    for ln in t.splitlines():
                        L.append("\n> %s" % ln)
                else:
                    L.append("\n" + t)
            L.append("\n<!-- p%d -->" % (p + 1))
    io.open(os.path.join(doc_dir, "merged.md"), "w", encoding="utf-8").write("\n".join(L))
    return {"doc": doc, "pages": n, "blocks": sum(len(x) for x in page_index)}


def clean_legacy(out_root):
    """删除旧版全局合并产物(已被按文档产物取代)"""
    removed = []
    for p in (os.path.join(out_root, "_merged"), os.path.join(out_root, "_merged_all.json"),
              os.path.join(out_root, "_merged_all.md")):
        if os.path.isdir(p):
            shutil.rmtree(p); removed.append(p)
        elif os.path.exists(p):
            os.remove(p); removed.append(p)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args()
    removed = clean_legacy(a.out)
    if removed:
        print("清理旧版全局产物:", *removed, sep="\n  ")
    docs = []
    for doc in sorted(os.listdir(a.out)):
        ddir = os.path.join(a.out, doc)
        if not os.path.isdir(ddir) or doc.startswith("_"):
            continue
        info = merge_doc(doc, ddir)
        print("合并: %-28s %d 页 %d 块" % (info["doc"], info["pages"], info["blocks"]))
    print("完成: 每文档 -> merged\\page_N.json + merged.json + merged.md")


if __name__ == "__main__":
    main()
