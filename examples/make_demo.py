# -*- coding: utf-8 -*-
r"""生成离线 demo 输入(examples/demo/), 供 CI 无 token、无网络地跑通全链路。

生成内容(全部是小体积 JSON/PDF, 便于入库与 diff):
  samples/demo_kr.pdf            1 页、250x330pt 纯矢量 PDF(自写最小 PDF, 不依赖 PDF 库)
  inbox/demo_kr__mineru/         demo_kr_content_list_v2.json + layout.json(_ocr_enable=false)
  inbox/demo_kr__dots/page_0.json
  inbox/demo_kr__paddle/page_0.json
  channels.json                  标定 identity(src_w/src_h 为 null), token 为空, 三个 *_api.enabled=false

三家 bbox 直接定义在 150dpi 页图像素空间(521x688); dots/paddle 落盘时除以页宽高归一化。
所以 demo 数据自洽: 各家对同一句话的框几乎重合, 差异只体现在故意留的离群读法、
一家缺一个块、一个空块、一家独有页码上。

用法:
  python make_demo.py
  python pipeline.py examples/demo/samples/demo_kr.pdf --mineru examples/demo/inbox/demo_kr__mineru --dots examples/demo/inbox/demo_kr__dots --paddle examples/demo/inbox/demo_kr__paddle --channels examples/demo/channels.json --out examples/demo/out
"""
import os, io, json

HERE = os.path.dirname(os.path.abspath(__file__))      # 本脚本所在的 examples/
ROOT = os.path.dirname(HERE)                          # 管道代码与 out/ 所在的仓库根
DEMO = os.path.join(HERE, "demo")                     # 生成的输入与产物都留在 examples/demo/
PW, PH = 250, 330          # 页面尺寸 pt; 150dpi 渲染后为 521x688 px
PW_PX, PH_PX = 521, 688

BLOCKS = [
    # 坐标均为 150dpi 页图像素(页图 521x688); 版心 x=140..480
    dict(cat="Section-header", box=(140, 40, 480, 105), text="국문 초록",
         mineru="title", dots="section_header", paddle="title"),
    dict(cat="Text", box=(140, 125, 480, 230),
         text="본 논문은 세 가지 광학 문자 인식 엔진의 한국어 인식 결과를 비교한다.",
         mineru="text", dots="text", paddle="text"),
    dict(cat="Text", box=(140, 250, 480, 350),
         text="세 엔진 모두 문단 단위 분할이 유사하며, 문자 오류율은 1% 미만이었다.",
         mineru="text", dots="text", paddle="text"),
    dict(cat="Text", box=(140, 370, 480, 470),
         text="각 엔진의 병렬 처리 방식과 표 구조 복원 성능을 함께 제시한다.",
         mineru="text", dots="text", paddle="text",
         # 故意: dots 把「병렬」读成「병열」, 演示单家离群读法(dice 仍高, 不触发 FFFD)
         dots_override="각 엔진의 병열 처리 방식과 표 구조 복원 성능을 함께 제시한다."),
    dict(cat="Caption", box=(140, 495, 400, 525), text="표 1. 엔진별 처리 시간",
         mineru="table_caption", dots="caption", paddle="caption"),
    dict(cat="Table", box=(140, 540, 480, 650),
         text="<table><tr><th>엔진</th><th>페이지 수</th><th>소요 시간</th></tr>"
              "<tr><td>MinerU</td><td>12</td><td>128 s</td></tr>"
              "<tr><td>dots.ocr</td><td>12</td><td>96 s</td></tr>"
              "<tr><td>PaddleOCR-VL</td><td>12</td><td>104 s</td></tr></table>",
         mineru="table", dots="table", paddle="table", cap="표 1. 엔진별 처리 시간"),
    # 故意: Paddle 在这一块识别为空(上游常见空块), 演示空块 + 跨渠道回填
    dict(cat="Page-footer", box=(140, 655, 330, 678), text="한국문헌정보학회지",
         mineru="page_footer", dots="page_footer", paddle="footer", paddle_empty=True),
    # 故意: 只有 Paddle 出页码, MinerU/dots 都不出, 演示 coverage 差异
    dict(cat="Page-number", box=(430, 655, 470, 678), text="17",
         mineru="page_number", dots="page_number", paddle="number", mineru_skip=True, dots_skip=True),
]


def norm_box(b):
    x0, y0, x1, y1 = b["box"]
    return [round(x0 / float(PW_PX), 6), round(y0 / float(PH_PX), 6),
            round(x1 / float(PW_PX), 6), round(y1 / float(PH_PX), 6)]


def pdf_bytes():
    """最小可用单页 PDF: 250x330pt, 页底浅灰 + 每个块一道深灰矩形(完全不依赖字体/字形)。
    刻意不放任何文字算子: base-14 字体画 CJK 只会出黑块, 而省略 /Font 资源又依赖渲染器容错。
    矩形位置与 BLOCKS 的可视区域对应, 便于肉眼核对标定是否对得上。
    """
    sx, sy = PW / float(PW_PX), PH / float(PH_PX)   # 页图像素 -> pt

    def rule(x0, y0, x1, y1, gray):
        # PDF 原点在左下, 与页图像素的左上原点相反
        return b"%.2f g %.2f %.2f %.2f %.2f re f " % (
            gray, x0 * sx, PH - y1 * sy, (x1 - x0) * sx, (y1 - y0) * sy)

    parts = [b"1 g 0 0 %.2f %.2f re f " % (PW, PH)]                 # 白底
    parts.append(rule(0, 0, PW_PX, PH_PX, 0.93))                    # 整页浅灰
    for _b in BLOCKS:                                               # 每块一道中灰条
        parts.append(rule(_b["box"][0], _b["box"][1], _b["box"][2], _b["box"][3], 0.55))
    stream = b"".join(parts)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.1f %.1f] /Resources << >> "
         b"/Contents 4 0 R >>") % (PW, PH),
    ]
    objs.append(b"<< /Length %d >>" % len(stream) + b"\nstream\n" + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


MCONTENT = {"title": "title_content", "text": "text_content", "table": "table_content",
            "table_caption": "table_caption_content", "page_footer": "page_footer_content",
            "page_number": "page_number_content"}


def mineru_content_list():
    """content_list_v2 形态: 一行 = 一页, 页内是块列表; 块正文统一挂在 content 下(新版 *_content 键名)。
    标定是 identity, 所以 bbox 就是 150dpi 页图像素。"""
    page = []
    for b in BLOCKS:
        if b.get("mineru_skip"):
            continue  # 故意: MinerU 这份不出页码, 演示 coverage 差异
        x0, y0, x1, y1 = b["box"]
        it = {"type": b["mineru"], "bbox": [x0, y0, x1, y1], "page": 0}
        if b["cat"] == "Table":
            # 表格: html 在 content.html, 题注在 content.table_caption(适配器单独提出, 免随 html 丢失)
            it["content"] = {"html": b["text"], "table_caption": [{"content": b.get("cap") or ""}]}
        else:
            it["content"] = {MCONTENT[b["mineru"]]: b["text"]}
        page.append(it)
    return [page]


def layout_json():
    return {"_ocr_enable": False, "page_info": {"page_no": 0, "width": PW_PX, "height": PH_PX}}


def dots_page():
    """dots.ocr 导出: 顶层 list, 每项 category + bbox(0-1) + text。
    故意: 某段把「병렬」读成「병열」(离群读法, dice 仍高); 不出页码。"""
    out = []
    for b in BLOCKS:
        if b.get("dots_skip"):
            continue
        it = {"category": b["dots"], "bbox": norm_box(b),
              "text": b.get("dots_override") or b["text"]}
        if b["cat"] == "Table":
            it["html"] = b["text"]
            it["text"] = b.get("cap") or ""
        out.append(it)
    return out


def paddle_page():
    """Paddle 异步 jobs 形态: {parsing_res_list:[{block_label, block_content, block_bbox}]}。
    故意: 页脚 block_content 为空(上游常见空块), 演示空块与跨渠道回填。"""
    res = []
    for b in BLOCKS:
        txt = "" if b.get("paddle_empty") else b["text"]
        res.append({"block_label": b["paddle"], "block_content": txt,
                    "block_bbox": norm_box(b)})
    return {"parsing_res_list": res, "page_index": 0, "page_size": [PW_PX, PH_PX]}


def channels_json():
    return {
        "keep_api_zip": False,
        "_note": "离线 demo 配置: 标定 identity(引擎坐标即 150dpi 页图像素), 无 token, 三个 *_api.enabled=false。由 make_demo.py 生成, 请勿手改。",
        "mineru": {"src_w": None, "src_h": None, "x_off": 0, "y_off": 0,
                   "_note": "null = identity(坐标已等于页图像素)"},
        "mineru_api": {"token": "", "src_w": None, "src_h": None, "x_off": 0, "y_off": 0,
                       "model_version": "vlm", "language": "korean", "is_ocr": False,
                       "timeout": 1800, "enabled": False},
        "dots": {"src_w": None, "src_h": None, "x_off": 0, "y_off": 0},
        "paddle": {},
        "dots_api": {"base": "https://dotsocr.xiaohongshu.com", "model": "dots.mocr",
                     "prompt": "prompt_layout_all_en", "timeout": 1800, "enabled": False},
        "paddle_api": {"token": "", "model": "PaddleOCR-VL", "timeout": 900, "enabled": False},
        "desktop": {"exe": "", "landing": "", "port": 9223, "kill_existing": False},
    }


def write(rel, data, as_json=False):
    fp = os.path.join(DEMO, rel)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    if isinstance(data, (bytes, bytearray)):
        io.open(fp, "wb").write(data)
    elif as_json:
        io.open(fp, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    else:
        io.open(fp, "w", encoding="utf-8", newline="\n").write(data)
    return fp


def main():
    made = [
        write("samples/demo_kr.pdf", pdf_bytes()),
        write("inbox/demo_kr__mineru/demo_kr_content_list_v2.json", mineru_content_list(), True),
        write("inbox/demo_kr__mineru/layout.json", layout_json(), True),
        write("inbox/demo_kr__dots/page_0.json", dots_page(), True),
        write("inbox/demo_kr__paddle/page_0.json", paddle_page(), True),
        write("channels.json", channels_json(), True),
    ]
    print("已生成 %d 个 demo 文件 -> %s" % (len(made), DEMO))
    for fp in made:
        print("  %s" % os.path.relpath(fp, ROOT).replace(os.sep, "/"))
    print("跑一遍(无网络、无 token):")
    print("  python pipeline.py examples/demo/samples/demo_kr.pdf "
          "--mineru examples/demo/inbox/demo_kr__mineru "
          "--dots examples/demo/inbox/demo_kr__dots "
          "--paddle examples/demo/inbox/demo_kr__paddle "
          "--channels examples/demo/channels.json --out examples/demo/out")


if __name__ == "__main__":
    main()