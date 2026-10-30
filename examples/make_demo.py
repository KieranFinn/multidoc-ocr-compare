# -*- coding: utf-8 -*-
"""生成离线 demo: 合成 2 页 PDF + 三渠道 mock 产物, 全部落在 examples/demo/。

为什么需要: 真实链路要账号/额度/桌面版 App, 而评审、二次开发和 CI 只想验证
"输入 PDF -> 三渠道对齐 -> 查看器可打开" 这条主干能不能跑通。
合成页用 Pillow 画(白底 + 文字 + 灰块图), 三渠道 mock 只是同一批块的三种坐标写法:

    dots / paddle : 归一化 0-1 坐标(适配器按 max(bbox)<=1.5 自动识别, 免标定)
    mineru        : 像素坐标 + identity 标定(examples/demo/channels.json 里 src_w=页宽)

产物不含任何真实文档内容, 可安全入库/外发。

用法:
    python examples/make_demo.py
    python pipeline.py examples/demo/demo.pdf \
        --mineru examples/demo/mineru --dots examples/demo/dots --paddle examples/demo/paddle \
        --channels examples/demo/channels.json
    # 结果: out/demo/index.html (含三渠道查看器与 1:3 对照)
"""
import os
import io
import json
import shutil

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    raise SystemExit("需要 Pillow: pip install -r requirements.txt")

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")
PAGE_W, PAGE_H = 1241, 1754  # 150 dpi 的 A4: 8.27in*150 x 11.69in*150

# (页号, 类别, (x0,y0,x1,y1) 页图像素, 文本) —— 三个渠道都由这张表派生
BLOCKS = [
    (0, "Section-header", (120, 110, 1120, 190), "Multi-Engine OCR Comparison: A Demo Document"),
    (0, "Text", (120, 240, 1120, 400),
     "This page is synthetic. It exists so that the pipeline, the three channel "
     "adapters and the viewers can be exercised without any network access."),
    (0, "Text", (120, 430, 1120, 620),
     "Each engine reports blocks in its own coordinate space. The adapters rewrite "
     "every box into 150 dpi page pixels, which is what makes the side-by-side "
     "comparison meaningful."),
    (0, "Picture", (200, 700, 1040, 1120), ""),
    (0, "Caption", (200, 1140, 1040, 1210),
     "Figure 1. Placeholder figure block (image regions carry no text by design)."),
    (1, "Section-header", (120, 110, 1120, 190), "Second Page: Tables and Structure"),
    (1, "Text", (120, 240, 1120, 380),
     "Page two adds a table block so the viewers show more than one block type."),
    (1, "Table", (120, 420, 1120, 760), "Header A | Header B | Header C"),
    (1, "Text", (120, 800, 1120, 980),
     "Blocks keep their reading order because the adapters emit them in the order "
     "reported by the engine."),
]

MINERU_TYPE = {"Section-header": "title", "Text": "text", "Table": "table",
               "Picture": "image", "Caption": "image_caption"}
# 每渠道给一点确定性抖动(像素), 让对照查看器能看到"同一块在三家引擎里的落点差异"
JITTER = {"dots": 2.0, "paddle": -2.0, "mineru": 1.0}


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10 没有 size 参数, 退回位图字体
        return ImageFont.load_default()


def _wrap(draw, text, font, width):
    """按像素宽度折行; 超长单词退化为按字符切。"""
    lines, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if draw.textlength(cand, font=font) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = word
        while draw.textlength(cur, font=font) > width and len(cur) > 1:
            cut = len(cur)
            while cut > 1 and draw.textlength(cur[:cut], font=font) > width:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    return lines


def _render_page(page_no):
    img = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for pg, cat, (x0, y0, x1, y1), text in BLOCKS:
        if pg != page_no:
            continue
        if cat == "Picture":
            d.rectangle([x0, y0, x1, y1], fill=(226, 232, 240), outline=(148, 163, 184), width=3)
            d.line([x0, y0, x1, y1], fill=(148, 163, 184), width=2)
            d.line([x0, y1, x1, y0], fill=(148, 163, 184), width=2)
            continue
        size = 34 if cat == "Section-header" else 24
        font = _font(size)
        lines = _wrap(d, text, font, x1 - x0)
        top = y0
        for ln in lines:
            if top + size > y1 + size:  # 超框就不再画, 保证 bbox 与可见内容大致一致
                break
            d.text((x0, top), ln, fill=(24, 24, 27), font=font)
            top += int(size * 1.45)
        if cat in ("Table", "Section-header"):  # 给这两类画一条下划线, 便于肉眼分辨
            d.line([x0, y1, x1, y1], fill=(120, 120, 120), width=2)
    return img


def _norm(box, dx=0.0, dy=0.0):
    return [round((box[0] + dx) / PAGE_W, 4), round((box[1] + dy) / PAGE_H, 4),
            round((box[2] + dx) / PAGE_W, 4), round((box[3] + dy) / PAGE_H, 4)]


def main():
    if os.path.isdir(DEMO):
        shutil.rmtree(DEMO)
    for sub in ("mineru", "dots", "paddle"):
        os.makedirs(os.path.join(DEMO, sub))

    # 1) 合成 PDF: 两页 150dpi A4
    pages = [_render_page(0), _render_page(1)]
    pdf = os.path.join(DEMO, "demo.pdf")
    pages[0].save(pdf, "PDF", resolution=150.0, save_all=True, append_images=pages[1:])

    # 2) dots / paddle: page_N.json, 归一化坐标
    for ch in ("dots", "paddle"):
        by_page = {0: [], 1: []}
        for i, (pg, cat, box, text) in enumerate(BLOCKS):
            dx = JITTER[ch] * ((i % 3) - 1)
            by_page[pg].append({"bbox": _norm(box, dx, dx * 0.5), "category": cat, "text": text})
        for pg, blocks in by_page.items():
            fp = os.path.join(DEMO, ch, "page_%d.json" % pg)
            with io.open(fp, "w", encoding="utf-8") as f:
                json.dump(blocks, f, ensure_ascii=False, indent=1)

    # 3) mineru: content_list_v2.json(页列表 -> 块列表), bbox 用页图像素(identity 标定)
    by_page = {0: [], 1: []}
    for i, (pg, cat, box, text) in enumerate(BLOCKS):
        d = JITTER["mineru"] * ((i % 3) - 1)
        blk = {"type": MINERU_TYPE.get(cat, "text"),
               "bbox": [box[0] + d, box[1] + d, box[2] + d, box[3] + d],
               "content": [{"type": "text", "content": text}]}
        by_page[pg].append(blk)
    with io.open(os.path.join(DEMO, "mineru", "demo_content_list_v2.json"), "w", encoding="utf-8") as f:
        json.dump([by_page[0], by_page[1]], f, ensure_ascii=False, indent=1)
    with io.open(os.path.join(DEMO, "mineru", "layout.json"), "w", encoding="utf-8") as f:
        json.dump({"_ocr_enable": False}, f)  # 适配器优先取非 OCR 运行目录

    # 4) demo 专用渠道配置: 只有标定段, mineru 用 identity(源空间 == 页图像素)
    ident = {"src_w": PAGE_W, "src_h": PAGE_H, "x_off": 0, "y_off": 0}
    cfg = {"mineru": dict(ident), "mineru_api": dict(ident), "dots": {}, "paddle": {},
           "_readme": "demo 专用: mock 产物与页图同空间, 故标定为 identity。真实文档请用仓库根的 channels.json。"}
    with io.open(os.path.join(DEMO, "channels.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)

    print("demo 已生成: %s" % DEMO)
    print("  demo.pdf / mineru/ / dots/ / paddle/ / channels.json")
    print("下一步:")
    print("  python pipeline.py examples/demo/demo.pdf --mineru examples/demo/mineru "
          "--dots examples/demo/dots --paddle examples/demo/paddle --channels examples/demo/channels.json")
    print("  然后打开 out/demo/index.html")


if __name__ == "__main__":
    main()
