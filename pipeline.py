# -*- coding: utf-8 -*-
"""
三渠道解析对比 pipeline
用法:
  python pipeline.py <input.pdf> [--mineru <dir>] [--dots <dir>] [--paddle <dir或md>] [--out <dir>]
  未显式给出的渠道自动获取: 先扫 pipeline/inbox/ 按名称(<doc>__渠道)或内容签名识别,
  仍缺则调云端 API(产物落 out/<文档名>/raw/<渠道>_api, 优先复用); mineru 再兜底 CDP 驱动桌面版。
  三路并行获取(ThreadPoolExecutor), 仍无产物的渠道跳过并在 index.html 中标注"未提供"。

inbox 渠道识别优先级:
  1. 名称含 __mineru / __dots / __paddle(建议规范: <文档名>__渠道)
  2. 内容签名: 含 *_content_list_v2.json → mineru; 含 page_*.json(category键) → dots/paddle 目录; .md 含 bcebos → paddle

统一中间表示(第③步的核心约定):
  适配器返回 {页码int: [ {"cat":类别, "box":[x0,y0,x1,y1], "text":文本} ]}  (box=150dpi图像素,左上原点)
  或整篇 md 字符串(md 模式, 无块联动)。
渠道 bbox 坐标系各不相同, 用 channels.json 标定(src_w/src_h/x_off/y_off, 每次调用重读; 换源时叠图重校, 方法见 README)。

产物: out/<文档名>/ index.html + viewer_{mineru,dots,paddle,compare}.html + pages/ + raw/<渠道>/
      (merged/ 由 merge_out.py 生成)
"""
import os, re, io, json, html, argparse, shutil, subprocess, datetime, time

ROOT = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(ROOT, "inbox")
CFG = None  # 惰性: 首次访问经 ensure_cfg() 按三层合并读入；main() 的 --channels 可改 CFG_FP

DPI = 150
NS = {"Section-header":"sec","Text":"txt","Footnote":"ftn","Picture":"pic","Caption":"cap",
      "Page-header":"hd","Page-footer":"ft","Page-number":"pn","Table":"tbl","Formula":"fm"}
TMAP_MINERU = {"title":"Section-header","text":"Text","paragraph":"Text","list":"Text","image":"Picture","chart":"Picture",
               "image_caption":"Caption","page_header":"Page-header","page_footer":"Page-footer",
               "page_footnote":"Footnote","page_number":"Page-number","table":"Table","table_caption":"Caption","equation":"Formula"}

def log(*a):
    print("[pipeline %s]" % datetime.datetime.now().strftime("%H:%M:%S"), *a)

# ========== ① 页面渲染 ==========
def render_pages(pdf_path, pages_dir):
    import pypdfium2 as pdfium
    from PIL import Image
    os.makedirs(pages_dir, exist_ok=True)
    # 源文件 freshness 守卫: PDF 比已渲染页新(重导出/替换)则全量重渲
    marker = os.path.join(pages_dir, ".src_mtime")
    src_mt = str(os.path.getmtime(pdf_path))
    reused = os.path.exists(marker) and io.open(marker, encoding="ascii").read() == src_mt
    if not reused:
        for f in os.listdir(pages_dir):
            if f.startswith("page_"):
                os.remove(os.path.join(pages_dir, f))
        io.open(marker, "w", encoding="ascii").write(src_mt)
    log("① " + ("页图复用(PDF 未变化): " if reused else "渲染原页 -> ") + pages_dir)
    pdf = pdfium.PdfDocument(pdf_path)
    sizes = []
    for p in range(len(pdf)):
        fp = os.path.join(pages_dir, "page_%d.jpg" % p)
        if not os.path.exists(fp):
            img = pdf[p].render(scale=DPI / 72.0).to_pil().convert("RGB")
            img.save(fp, quality=72)
        sizes.append(Image.open(fp).size)
    return sizes

# ========== ③ 渠道适配器(归一化, 返回 {页:[块]} 或 md字符串) ==========
def flatten_content(c):
    """把嵌套 content 结构(paragraph_content/spans/lines/page_header_content 等任意 *_content)拍平为纯文本。
    MinerU table 块是 {"image_source":..,"table_caption":..,"html":"<table>.."} 结构, 优先取 html 键得到表格 HTML。"""
    if isinstance(c, str): return c
    if isinstance(c, dict):
        if "image_source" in c and "html" not in c:  # MinerU image/chart 块: 无正文, 题注/脚注在 *_caption/*_footnote 键下(原经 content 键丢失); table 块另有 html 键, 走 adapt_mineru 的 cap 提取
            parts = []
            for ck in ("image_caption", "chart_caption", "image_footnote", "chart_footnote"):
                v = c.get(ck)
                if v:
                    # 题注/脚注的 span 是分行(如韩英双语两行), 逐 span 拍平换行拼接, 免粘连
                    parts.append("\n".join(flatten_content(x) for x in v) if isinstance(v, list)
                                 else flatten_content(v))
            return "\n".join(p for p in parts if p)
        for k in ("content", "text", "title_content", "paragraph_content"):
            if k in c: return flatten_content(c[k])
        if isinstance(c.get("html"), str) and "<table" in c["html"]:
            return c["html"]
        if isinstance(c.get("list_items"), list):
            # MinerU list 块(参考文献列表等): {"list_type":..,"list_items":[{item_type,item_content}]}, 逐项拍平换行拼接
            return "\n".join(x for x in (flatten_content(it.get("item_content", it)) if isinstance(it, dict)
                                         else flatten_content(it) for it in c["list_items"]) if x)
        for k, v in c.items():  # MinerU 新版键名: page_header_content/page_number_content/...
            if k.endswith("_content"):
                return flatten_content(v)
        return json.dumps(c, ensure_ascii=False)
    if isinstance(c, list):
        # 同容器内的 span 是行内续接(paragraph_content/title_content 等), 不留换行;
        # 块级分隔(参考文献逐项/表格行间)由 dict 分支的 list_items 等显式给 "\n"
        return "".join(flatten_content(x) for x in c)
    return "" if c is None else str(c)

def cal(key, dw=1654.0, dh=2339.0):
    # 每次调用重读配置(三层合并): 改标定/token 不用重启服务, 也不需要重启进程
    try:
        cfg = load_cfg()
    except Exception:
        cfg = {}
    c = cfg.get(key) or {}
    sw, sh = c.get("src_w"), c.get("src_h")
    if sw is None or sh is None:  # identity: 显式 null = 引擎坐标原样当页图像素用(demo/CI 用, 免标定)
        sw, sh = dw, dh
    return (float(sw), float(sh), float(c.get("x_off", 0) or 0), float(c.get("y_off", 0) or 0))

def mineru_calib_key(src):
    """MinerU 标定键的显式来源判定(替代旧的"按目录 basename 猜"):
    优先读 raw 目录里的 _calib_key 标记(获取/归仓时落盘, 随 preserve_raw 孪生复制),
    退回目录名启发式(basename == "mineru_api" -> mineru_api), 最终默认 mineru(桌面版)。
    同一目录无论从 viewer 还是 merge_out 进, 得到的键一致。"""
    if src and os.path.isdir(src):
        mk = os.path.join(src, "_calib_key")
        if os.path.exists(mk):
            try:
                v = io.open(mk, encoding="utf-8").read().strip()
                if v in ("mineru", "mineru_api"):
                    return v
            except Exception:
                pass
    if os.path.basename(os.path.normpath(src or "")) == "mineru_api":
        return "mineru_api"
    return "mineru"

def write_calib_marker(dirpath, key):
    """把实际使用的标定键落进 raw 目录(_calib_key 文件), 供 viewer/merge_out 事后一致读取与审计。"""
    if dirpath and os.path.isdir(dirpath):
        io.open(os.path.join(dirpath, "_calib_key"), "w", encoding="utf-8").write(key + "\n")

_XTERM = set(u".”」』)）》…")

def _net_retry(desc, fn, tries=3, pause=4.0):
    """网络瞬断容错(三家 API 共用): 提交/下载等"单次调用"环节最多试 tries 次;
    轮询循环不用它, 用各自的 try/continue(稀疏日志, 错误不杀渠道, 总时长仍受 timeout 约束)。"""
    import time as _t
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            log("   %s 网络错误(%d/%d): %s" % (desc, i + 1, tries, e))
            if i + 1 < tries:
                _t.sleep(pause)
    raise last

def _tail_block(blocks):
    """页尾块: 最后一个含文本的非页眉页脚页码块"""
    ts = [b for b in blocks if (b.get("text") or "").strip()
          and b.get("cat") not in ("Page-header", "Page-footer", "Page-number")]
    return max(ts, key=lambda b: b["box"][3] if b.get("box") else 0) if ts else None

def mark_crosspage_merge(per_ch_all, sizes):
    """跨页并入检测(跨渠道证据版, 取代旧纯几何规则——旧规则只认"上页末块顶到页底", 漏检多):
    某渠道页首空块 E(非图, 顶部 28% 内), 若 ① 另渠道同页同区域(IoU≥0.3/中心互含)有非空块 R, 且满足
    ② 强证据: 本渠道上页末块已包含 R 的文本(bigram dice≥0.4, 引擎把 R 的文本吞进了上页末块), 或
    ③ 排版推断(从严): 本渠道自己的上页末块无终止标点且悬置在页底(>80%页高) —— 跨页句的下半句丢失;
       若上页末块已正常收尾, 空块只是内容缺失, 不冒认跨页。
    判中则 E.xmerge=1(记 xev=strong/layout), 本渠道上页末块标 xmerge_src=1。
    per_ch_all = {"mineru": per_page|None, ...}(全渠道, 互相作证)。"""
    chs = [c for c, pp in (per_ch_all or {}).items() if isinstance(pp, dict)]
    for p in range(1, len(sizes)):
        H = sizes[p][1]
        pblocks = {c: (per_ch_all.get(c) or {}).get(p) or [] for c in chs}
        qblocks = {c: (per_ch_all.get(c) or {}).get(p - 1) or [] for c in chs}
        for ch in chs:
            own_tail = _tail_block(qblocks[ch])
            for e in pblocks[ch]:
                if (e.get("text") or "").strip() or e.get("cat") == "Picture":
                    continue
                eb = e.get("box")
                if not eb or eb[1] > 0.28 * H:
                    continue
                for och in chs:
                    if och == ch:
                        continue
                    ref = None
                    for ob in pblocks[och]:
                        ot = ob.get("text") or ""
                        obb = ob.get("box")
                        if not ot.strip() or not obb:
                            continue
                        if _iou(eb, obb) >= 0.3 or _cin(obb, eb) or _cin(eb, obb):
                            ref = ob
                            break
                    if not ref:
                        continue
                    ev = None
                    if own_tail and _gdice(ref.get("text"), own_tail.get("text")) >= 0.4:
                        ev = "strong"  # 本渠道上页末块已含该文本(可能带页码前缀)
                    else:
                        # 排版推断(从严): 须"本渠道"上页末块自身悬置在页底(无终止标点) —— 跨页句的下半句
                        # 丢失才指向并入上页; 若上页末块已正常收尾, 本页空块只是内容缺失, 不冒认跨页
                        ot = (own_tail.get("text") or "").rstrip() if own_tail else ""
                        if (ot and ot[-1] not in _XTERM and own_tail.get("box")
                                and own_tail["box"][3] > 0.8 * sizes[p - 1][1]):
                            ev = "layout"
                    if ev:
                        e["xmerge"] = 1
                        e["xev"] = ev
                        if own_tail:
                            own_tail["xmerge_src"] = 1
                        break
    return per_ch_all

def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    aa = (a[2] - a[0]) * (a[3] - a[1]); bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter) if aa + bb - inter > 0 else 0.0

def backfill_empty_blocks(ch_data, CH):
    """merged 合并产物的跨渠道回填: 某渠道空块(非图)若与另一渠道同页非空块 IoU≥0.5,
    借其文本填入并记 text_from(下游可分辨回填来源)。只在合并层做, 渠道查看器保持原样。"""
    for ch in CH:
        for p, lst in (ch_data.get(ch) or {}).items():
            for b in lst:
                if (b.get("text") or "").strip() or b.get("cat") == "Picture":
                    continue
                bb = b.get("box") or [0, 0, 0, 0]
                best, bch, btxt = 0.0, None, ""
                for och in CH:
                    if och == ch:
                        continue
                    for ob in (ch_data.get(och) or {}).get(p) or []:
                        if not (ob.get("text") or "").strip():
                            continue
                        v = _iou(bb, ob.get("box") or [0, 0, 0, 0])
                        if v > best:
                            best, bch, btxt = v, och, ob.get("text")
                if best >= 0.5 and bch:
                    b["text"] = btxt
                    b["text_from"] = bch

def adapt_mineru(path, sizes, cfg_key="mineru"):
    """MinerU content_list_v2.json: bbox 在其渲染像素空间(与页图无关的独立坐标系, 且横纵非等比)。
    用 channels.json 的 cfg_key 段标定映射到 150dpi 页图像素: x*W/src_w+x_off, y*H/src_h+y_off;
    该段缺失时退回 cal() 默认 1241x1754、零偏移。
    目录可为多级: 递归找 *_content_list_v2.json, 优先取 _ocr_enable=false 的非OCR运行。"""
    cands = []
    for root, dirs, files in os.walk(path):
        hit = [os.path.join(root, x) for x in files if x.endswith("_content_list_v2.json")]
        if hit:
            ocr = True
            try:
                lj = json.load(io.open(os.path.join(root, "layout.json"), encoding="utf-8"))
                ocr = bool(lj.get("_ocr_enable", True))
            except Exception:
                pass
            cands.append((0 if not ocr else 1, hit[0], root))
    if not cands: return None
    cands.sort(key=lambda t: t[0])
    f0 = cands[0][1]
    log("   mineru 选中运行目录: %s (%s)" % (cands[0][2], "非OCR" if cands[0][0] == 0 else "OCR"))
    cl = json.loads(io.open(f0, encoding="utf-8").read())
    SW, SH, XO, YO = cal(cfg_key, 1241.0, 1754.0)
    try:
        _cfg = load_cfg()
        if "src_w" not in _cfg.get(cfg_key, {}):
            log("   ⚠ mineru 标定段[%s]缺 src_w/src_h, 退回 1241x1754 零偏移, 框将错位!" % cfg_key)
    except Exception:
        pass
    # 注: 曾试在代码内按文档暗像素剖面自标定, x 轴相关景观多峰不可靠(±40% 离群), 已删除;
    # 现统一走 cal(), 标定随页图宽高等比缩放, 跨页面几何(含非 A4/横排)已验证稳定。
    per_page = {}
    for pi, pg in enumerate(cl):
        if pi >= len(sizes): break
        W, H = sizes[pi]
        lst = []
        for b in pg:
            bb = b.get("bbox") or [0, 0, 0, 0]
            blk = {"cat": TMAP_MINERU.get(b.get("type"), "Text"),
                   "box": [round(bb[0]*W/SW + XO, 1), round(bb[1]*H/SH + YO, 1),
                           round(bb[2]*W/SW + XO, 1), round(bb[3]*H/SH + YO, 1)],
                   "text": flatten_content(b.get("content"))}
            c = b.get("content")
            if isinstance(c, dict) and isinstance(c.get("image_source"), dict):
                ip = c["image_source"].get("path")  # 记录引擎抽取图相对路径, 查看器据此渲染真实图片
                if ip:
                    full = os.path.normpath(os.path.join(cands[0][2], ip))
                    if os.path.exists(full):
                        blk["img"] = os.path.relpath(full, path).replace("\\", "/")
            if isinstance(c, dict) and b.get("type") == "table":
                # 表格块文本走 html; 题注/脚注(table_caption/table_footnote)单独提出, 免随 html 键丢失; span 逐行拼接(韩英双语题注是两行)
                caps = []
                for k in ("table_caption", "table_footnote"):
                    v = c.get(k)
                    if v:
                        caps.append("\n".join(flatten_content(x) for x in v) if isinstance(v, list)
                                    else flatten_content(v))
                caps = [x for x in caps if x]
                if caps:
                    blk["cap"] = "\n".join(caps)
            lst.append(blk)
        per_page[pi] = lst
    _tag_img(per_page, extra_prefixes)  # img 相对路径补"实际适配源目录"前缀, 调用方据此指到真正存在的文件
    return per_page

def _tag_img(per_page, prefixes):
    prefix = prefixes[0] or "raw/mineru/"  # None = out 外来源(inbox 导出), 走 preserve_raw 归档位
    """块内 img 目前是引擎目录内相对路径; 查看器/合并器要的是相对 out/<doc>/ 的路径。
    前缀由调用方按"实际适配源目录 basename"给出(_api 缓存 vs inbox 归档不同), 不再全局硬编码 raw/<ch>/。"""
    for lst in per_page.values():
        for b in lst:
            if b.get("img"):
                b["img"] = prefix + b["img"]

def _salvage_blocks(raw):
    """从损坏的 JSON 文本里抢救完整的块对象(正则容错, 跳过截断的尾部)"""
    out = []
    pat = re.compile(r'\{"bbox": \[([^\]]+)\],\s*"category":\s*"([^"]*)",\s*"text":\s*"((?:[^"\\]|\\.)*)"\}', re.S)
    for m in pat.finditer(raw):
        try:
            bb = [float(v) for v in m.group(1).split(",")]
            txt = m.group(3)
            if "\\" in txt:  # 只还原 JSON 转义, 禁止 unicode_escape(会把韩文打成乱码)
                txt = txt.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")
            out.append({"bbox": bb, "category": m.group(2), "text": txt})
        except Exception:
            continue
    return out

def _parse_page_json(raw):
    """容错解析 page_N.json 文本: 兼容双重编码(str包json)和内层损坏(dots 官方导出bug, 如整页被截断)。
    返回 (块列表, 是否经抢救)。"""
    try:
        j = json.loads(raw)
        if isinstance(j, str):
            j = json.loads(j)  # 双重编码
        return (j if isinstance(j, list) else []), False
    except json.JSONDecodeError:
        # 内层/外层损坏: 尽力抢救完整块(dots 官方导出 bug, 如 page_5)
        try:
            outer = json.loads(raw)
            inner = outer if isinstance(outer, str) else raw
        except Exception:
            inner = raw
        return _salvage_blocks(inner), True

def _load_page_json(fp):
    return _parse_page_json(io.open(fp, encoding="utf-8").read())[0]

def adapt_page_jsons(path, sizes, cfg_key):
    """page_N.json 风格块输出(dots.ocr / PaddleOCR-VL): bbox 在像素空间(标定) 或已归一化(0-1, 免标定)"""
    SW, SH, XO, YO = cal(cfg_key)
    files = {}
    for x in os.listdir(path):
        m = re.match(r".*page_(\d+)\.json$", x)
        if m: files[int(m.group(1))] = x
    if not files: return None
    per_page = {}
    for pi in sorted(files):
        if pi >= len(sizes): break
        W, H = sizes[pi]
        sx, sy = W / SW, H / SH
        j = _load_page_json(os.path.join(path, files[pi]))
        blocks = j if isinstance(j, list) else []
        box_list = [(b.get("bbox") or [0, 0, 0, 0]) for b in blocks]
        # 逐页判归一化: 全文档只要混入一页归一化坐标, 统一按页判, 避免像素页把该页框放大上千倍
        is_norm = bool(box_list) and max((max(v for v in bb) for bb in box_list), default=0) <= 1.5
        lst = []
        for bb, b in zip(box_list, blocks):
            if is_norm:  # 归一化坐标(如 Paddle API 转换产物): 直接×页图尺寸, 免标定
                box = [round(bb[0]*W, 1), round(bb[1]*H, 1), round(bb[2]*W, 1), round(bb[3]*H, 1)]
            else:
                box = [round(bb[0]*sx + XO, 1), round(bb[1]*sy + YO, 1),
                       round(bb[2]*sx + XO, 1), round(bb[3]*sy + YO, 1)]
            lst.append({"cat": b.get("category", "Text"), "box": box,
                        "text": flatten_content(b.get("text"))})
        per_page[pi] = lst
    return per_page

def adapt_dots(path, sizes):
    return adapt_page_jsons(path, sizes, "dots")

TMAP_PADDLE = {"text":"Text","paragraph":"Text","title":"Section-header","section-header":"Section-header",
               "header":"Page-header","page_header":"Page-header","footer":"Page-footer","page_footer":"Page-footer",
               "footnote":"Footnote","page_footnote":"Footnote","figure":"Picture","image":"Picture",
               "figure_caption":"Caption","image_caption":"Caption","table":"Table","table_caption":"Caption",
               "equation":"Formula","list":"Text","page_number":"Page-number"}

def adapt_paddle_json(path, sizes):
    """Paddle 平台 JSON 导出(单文件): 容错解析常见 schema(嵌套/平铺、x1y1/xywh、category/label/type)"""
    j = json.loads(io.open(path, encoding="utf-8").read())
    SW, SH, XO, YO = cal("paddle")
    blocks = []
    def walk(x):
        if isinstance(x, dict):
            bb = None
            for k in ("bbox", "box", "bounding_box"):
                if k in x and isinstance(x[k], (list, tuple)) and len(x[k]) >= 4:
                    try: bb = [float(v) for v in x[k][:4]]
                    except (TypeError, ValueError): bb = None
                    break
            txt = None
            for k in ("text", "content", "transcription"):
                if k in x: txt = flatten_content(x[k]); break
            if bb and txt is not None:
                pg = None
                for k in ("page", "page_idx", "page_no", "page_index"):
                    if k in x and isinstance(x[k], int): pg = x[k]; break
                raw = str(x.get("category") or x.get("label") or x.get("type") or "Text").lower()
                blocks.append((pg, TMAP_PADDLE.get(raw, "Text"), bb, txt))
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(j)
    if not blocks: return None
    per_page = {}
    n = len(sizes)
    for idx, (pg, cat, bb, txt) in enumerate(blocks):
        p = pg if (pg is not None and 0 <= pg < n) else min(idx * n // len(blocks), n - 1)
        W, H = sizes[p]
        sx, sy = W / SW, H / SH
        x1 = bb[2] if bb[2] > bb[0] else bb[0] + bb[2]  # 兼容 xywh
        y1 = bb[3] if bb[3] > bb[1] else bb[1] + bb[3]
        per_page.setdefault(p, []).append({"cat": cat,
            "box": [round(bb[0]*sx + XO, 1), round(bb[1]*sy + YO, 1), round(x1*sx + XO, 1), round(y1*sy + YO, 1)],
            "text": txt})
    return per_page

def adapt_paddle(src, sizes):
    """Paddle 渠道: 目录(page_*.json) / 单文件 .json → 块级; .md → 整篇 md 模式"""
    if os.path.isdir(src):
        return adapt_page_jsons(src, sizes, "paddle")
    if src.lower().endswith(".json"):
        return adapt_paddle_json(src, sizes)
    return io.open(src, encoding="utf-8").read()

# ========== ④ md -> html + viewer 生成 ==========
_TEX_SYM = {"cdot":"·","times":"×","div":"÷","Sigma":"Σ","sigma":"σ","lambda":"λ","Lambda":"Λ",
 "alpha":"α","beta":"β","gamma":"γ","delta":"δ","Delta":"Δ","theta":"θ","Theta":"Θ","rho":"ρ",
 "mu":"μ","pi":"π","omega":"ω","Omega":"Ω","phi":"φ","Phi":"Φ","chi":"χ","eta":"η","epsilon":"ε",
 "leq":"≤","geq":"≥","neq":"≠","approx":"≈","sim":"~","simeq":"≃","infty":"∞","partial":"∂","nabla":"∇",
 "subseteq":"⊆","subset":"⊂","supset":"⊃","supseteq":"⊇","in":"∈","ni":"∋","cup":"∪","cap":"∩",
 "pm":"±","mp":"∓","rightarrow":"→","leftarrow":"←","Rightarrow":"⇒","to":"→","mapsto":"↦",
 "circ":"∘","bullet":"•","ldots":"…","cdots":"⋯","quad":" ","qquad":" ","propto":"∝",
 "sum":"∑","prod":"∏","int":"∫","oint":"∮","sqrt":"√","sqsubseteq":"⊑","%":"%","#":"#"}
_TEX_ONE = re.compile(r"\\(mathrm|mathbf|mathit|mathsf|boldsymbol|operatorname|mathop|mathcal|mathfrak|hat|widehat|rm|bf|it|text)\s*(\{(?:[^{}]|\{[^{}]*\})*\})")
_TEX_FRAC = re.compile(r"\\[dt]?frac\s*(\{(?:[^{}]|\{[^{}]*\})*\})\s*(\{(?:[^{}]|\{[^{}]*\})*\})")
_TEX_BIG = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)\b\s*([()\[\]|.,])?")

def _tex_conv(x, _depth=0):
    r"""常见行内 LaTeX -> HTML: \mathrm{}等取内容(支持一层嵌套如 \mathrm{H_{2}O})、\frac→上下排、
    _{}/^_ → sub/sup、希腊字母/关系符。不追求完整 TeX 语法; 无法识别的命令保留原样。"""
    if _depth > 6:
        return x
    x = _TEX_BIG.sub(lambda m: m.group(1) or "", x)
    x = _TEX_ONE.sub(lambda m: _tex_conv(m.group(2)[1:-1], _depth + 1), x)
    x = _TEX_FRAC.sub(lambda m: '<i class="fr"><sup>%s</sup>&frasl;<sub>%s</sub></i>'
                      % (_tex_conv(m.group(1)[1:-1], _depth + 1), _tex_conv(m.group(2)[1:-1], _depth + 1)), x)
    # "M g S O _ { 4 }" 式逐字母空格压缩(连续 3+ 个单字母/数字才收, 降低误伤英文概率)
    x = re.sub(r"\b(?:[A-Za-z0-9] ){2,}[A-Za-z0-9]\b", lambda m: m.group(0).replace(" ", ""), x)
    x = re.sub(r"(?<=[\w\)\}\]])[ \t]*_[ \t]*\{[ \t]*([^{}]*?)[ \t]*\}", r"<sub>\1</sub>", x)
    x = re.sub(r"(?<=[A-Za-z])[ \t]*_[ \t]*([0-9])(?![0-9])", r"<sub>\1</sub>", x)
    x = re.sub(r"(?<=[\w\)\}\]])[ \t]*\^\s*\{[ \t]*([^{}]*?)[ \t]*\}", r"<sup>\1</sup>", x)
    x = re.sub(r"(?<=[\w\)\}\]])\^\s*([A-Za-z0-9+-])(?![A-Za-z0-9])", r"<sup>\1</sup>", x)
    x = re.sub(r"\\([A-Za-z]+|%|#)", lambda m: _TEX_SYM.get(m.group(1), m.group(0)), x)
    return re.sub(r"\\[,;! ]", " ", x)

def texlite(s):
    r"""查看器展示层的轻量公式渲染: $...$/\(...\) 定界式先取出转换, 再对全文残留命令
    (如 MinerU 无 $$ 定界、直接混排的 \mathrm{...})转换。data/merged 中的原始文本不变。
    无公式特征时原样返回。"""
    if not s or not any(ch in s for ch in "$\\_^"):
        return s
    s = re.sub(r"\\\((.+?)\\\)", lambda m: _tex_conv(m.group(1)), s)
    s = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), s)
    return _tex_conv(s)

def inline_md(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
    return s.replace("\n", "<br/>").replace("\ufffd", '<span class="bad">�</span>')

def md2html(md):
    def esc(s): return html.escape(s, quote=False)
    out, in_code, tbl = [], False, []
    def flush():
        nonlocal tbl
        if tbl:
            rows = [r for r in tbl if not re.match(r"^\s*\|?[\s:-]+\|", r)]
            if rows:
                t = ["<table>"]
                for i, r in enumerate(rows):
                    cells = [c.strip() for c in r.strip().strip("|").split("|")]
                    tag = "th" if i == 0 else "td"
                    t.append("<tr>" + "".join("<%s>%s</%s>" % (tag, esc(c), tag) for c in cells) + "</tr>")
                t.append("</table>"); out.append("".join(t))
            tbl = []
    for ln in md.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code; out.append("<pre>" if in_code else "</pre>"); continue
        if in_code: out.append(esc(ln) + "\n"); continue
        if ln.strip().startswith("|") and "|" in ln.strip()[1:]: tbl.append(ln); continue
        flush()
        s = ln.rstrip()
        if not s.strip(): continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m: out.append("<h%d>%s</h%d>" % (len(m.group(1)), esc(m.group(2)), len(m.group(1)))); continue
        if re.match(r"^---+\s*$", s): out.append("<hr/>"); continue
        m = re.match(r"^!\[(.*?)\]\((.*?)\)", s.strip())
        if m: out.append('<div class="fig">[图: %s]</div>' % esc(m.group(1))); continue
        s2 = re.sub(r"^\s*[-*]\s+", "", esc(s))
        s2 = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s2)
        out.append("<p>" + texlite(s2) + "</p>")
    flush()
    return "\n".join(out).replace("\ufffd", '<span class="bad">�</span>')

VIEWER_TMPL = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>@@TITLE@@</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f4f5f7;color:#222}
header{position:sticky;top:0;background:#1f2937;color:#fff;padding:10px 18px;z-index:9}
header b{font-size:15px}nav{display:inline-block;margin-left:16px}
nav a{color:#9ca3af;margin:0 4px;text-decoration:none;font-size:14px}nav a:hover{color:#fff}
.back{color:#fbbf24;margin-right:12px;text-decoration:none}
.wrap{max-width:1560px;margin:0 auto;padding:14px}
section{margin:20px 0}h2{font-size:15px;color:#374151;border-left:4px solid #2563eb;padding-left:8px}
.stat{color:#6b7280;font-weight:normal;font-size:12px;margin-left:10px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pane{background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.ptitle{background:#eef2ff;color:#3730a3;font-size:12px;padding:5px 10px;border-bottom:1px solid #e5e7eb}
.imgw{position:relative}
.imgw img{width:100%;display:block}
.imgw svg{position:absolute;left:0;top:0;width:100%;height:100%}
.r{fill-opacity:.14;stroke-width:2.4;stroke-opacity:.9;cursor:pointer;vector-effect:non-scaling-stroke}
.r.txt{fill:#6b7280;stroke:#6b7280}.r.sec{fill:#2563eb;stroke:#2563eb}
.r.ftn{fill:#d97706;stroke:#d97706}.r.pic{fill:#7c3aed;stroke:#7c3aed}
.r.cap{fill:#0891b2;stroke:#0891b2}.r.hd,.r.ft,.r.pn{fill:none;stroke:#dc2626;stroke-dasharray:5 3}
.r.tbl{fill:#059669;stroke:#059669}.r.fm{fill:#db2777;stroke:#db2777}
.r.on{fill-opacity:.38;stroke-opacity:1;stroke-width:3.2}
.txt{padding:8px 10px;max-height:980px;overflow:auto;font-size:14px;line-height:1.8}
.blk table{border-collapse:collapse;margin:4px 0;font-size:12px}.blk td,.blk th{border:1px solid #d1d5db;padding:2px 6px}
.blk{border-left:4px solid #e5e7eb;padding:4px 8px;margin:4px 0;border-radius:0 6px 6px 0;cursor:pointer;background:#fafafa}
.blk .tag{display:inline-block;font-size:10px;color:#9ca3af;border:1px solid #e5e7eb;border-radius:4px;padding:0 5px;margin-right:6px;background:#fff}
.blk.on{background:#fef9c3;box-shadow:0 0 0 2px #f59e0b inset}
.blk.emp{background:#fef2f2;border-left-color:#dc2626}
.bad{background:#fee2e2;color:#dc2626;padding:0 3px;border-radius:3px;font-weight:bold}
a.bad,a.empf{cursor:pointer;text-decoration:none}
.empf{background:#f3e8ff;color:#7c3aed;padding:0 5px;border-radius:3px;font-weight:bold;font-size:12px}
i.fr{font-style:normal;white-space:nowrap}
.tcap{font-size:12px;color:#374151;padding:3px 0 5px;font-weight:bold}
.pfig img{max-width:100%;border:1px solid #e5e7eb;border-radius:4px;display:block}
svg.picr{width:100%;height:auto;display:block;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.r.emp{stroke:#dc2626;stroke-dasharray:4 3}
.lg{display:inline-block;margin-left:14px;font-size:12px}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 3px;vertical-align:-1px}
.fullmd{padding:14px 18px;font-size:14px;line-height:1.9;max-width:1100px;margin:0 auto;background:#fff;min-height:600px}
</style></head><body>
<header><a class="back" href="index.html">‹ 三渠道总览</a><b>@@TITLE@@</b><nav>跳页：@@NAV@@</nav>
<span class="lg"><i style="background:#2563eb"></i>标题 <i style="background:#6b7280"></i>正文 <i style="background:#d97706"></i>脚注 <i style="background:#7c3aed"></i>图片 <i style="background:#0891b2"></i>图注 <i style="background:#059669"></i>表格 <i style="background:#fff;border:1px dashed #dc2626"></i>页眉页脚页码 <i style="background:#fee2e2;border:1px solid #dc2626"></i>空块</span>
</header>
@@BODY@@
<script>
var META=@@DATA@@, pinned=null;
function act(id,on){var e=document.querySelectorAll('[data-b="'+id+'"]');for(var k=0;k<e.length;k++){if(on)e[k].classList.add('on');else e[k].classList.remove('on');}}
function stat(id){var p=id.split('_')[0];var el=document.getElementById('st_p'+p);if(el&&META[id])el.textContent=' 块 '+id.split('_')[1]+' · '+META[id].cat+' · '+META[id].txt.replace(/\\s+/g,' ').slice(0,40);}
document.addEventListener('mouseover',function(e){var t=e.target.closest?e.target.closest('[data-b]'):null;if(!t||t.dataset.b===pinned)return;act(t.dataset.b,true);stat(t.dataset.b);});
document.addEventListener('mouseout',function(e){var t=e.target.closest?e.target.closest('[data-b]'):null;if(!t||t.dataset.b===pinned)return;act(t.dataset.b,false);});
document.addEventListener('click',function(e){var t=e.target.closest?e.target.closest('[data-b]'):null;if(pinned){act(pinned,false);if(!t){pinned=null;return;}}if(t){pinned=t.dataset.b;act(pinned,true);stat(pinned);}});
document.addEventListener('click',function(e){var j=e.target.closest?e.target.closest('.jump'):null;if(!j)return;e.preventDefault();
 var el=document.getElementById(j.dataset.t);if(!el)return;el.scrollIntoView({block:'center'});
 var id=el.dataset.b;act(id,true);setTimeout(function(){if(pinned!==id)act(id,false);},1600);});
</script>
</body></html>"""

_TAB_BAD = re.compile(r"</?(script|style|iframe|object|embed|form|button|a|link|meta)\b[^>]*>", re.I)
_TAB_ON = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)

_BLK_TAG_OK = re.compile(r"&lt;(/?(?:sub|sup|b|i)(?:\s+class=\"fr\")?\s*/?)&gt;")

def _blk_html(t):
    """正文块展示层: texlite 转行内公式 -> 全转义消毒 -> 白名单标签(sub/sup/b/i/br)放行 -> md 强调/换行。
    源文本自带的 <sub>/<sup>(MinerU/Paddle 已有 HTML)借此按原意渲染, 其余标签一律转义显示。"""
    e = html.escape(texlite(t), quote=False)
    e = _BLK_TAG_OK.sub(r"<\1>", e)
    e = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e)
    e = re.sub(r"\*(.+?)\*", r"<i>\1</i>", e)
    return e.replace("\n", "<br/>").replace("\ufffd", '<span class="bad">\ufffd</span>')

_SYM_FOLD = {
    u'“': u'"', u'”': u'"', u'‘': u"'", u'’': u"'",      # 弯引号->直引号(仅比较口径)
    u'․': u'.', u'·': u'.', u'⋅': u'.',                     # 韩文一点杠/中点->句点
    u'‐': u'-', u'‑': u'-', u'–': u'-', u'—': u'-', u'―': u'-', u'−': u'-',  # 破折/连接号族+数学minus
    u'…': u'...', u'⋯': u'...',                             # 省略号族
    u'，': u',', u'。': u'.', u'．': u'.', u'：': u':', u'；': u';', u'！': u'!', u'？': u'?',
    u'（': u'(', u'）': u')', u'［': u'[', u'］': u']',
}
def _fold_sym(s):
    """符号变体折叠: 只用于跨渠道"比较口径", 绝不改原始文本(忠实度评估须用原文, 两条口径物理分离)"""
    return "".join(_SYM_FOLD.get(c, c) for c in (s or ""))

def _gdice(a, b):
    """跨渠道文本相似度: 符号折叠(比较口径) -> 去空白 -> 字符 bigram dice(抗单字 OCR 差异与空格分歧)。
    隐患约定见 REVIEW §9.20: 不含上下标/NFKC; 上下标保留结构待 M2, 避免 x² 与 x2 被抹平。"""
    a = re.sub(r"\s+", "", _fold_sym(a))[:80]; b = re.sub(r"\s+", "", _fold_sym(b))[:80]
    if len(a) < 2 or len(b) < 2: return 0.0
    A = set(a[i:i+2] for i in range(len(a)-1)); B = set(b[i:i+2] for i in range(len(b)-1))
    return 2.0*len(A & B)/(len(A)+len(B))

def _cin(a, b):
    cx, cy = (a[0]+a[2])/2, (a[1]+a[3])/2
    return b[0]-5 <= cx <= b[2]+5 and b[1]-5 <= cy <= b[3]+5

_CMP_DOT = {"mineru": "m", "dots": "d", "paddle": "p"}
_CMP_CN = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}

def _cmp_preview(t, limit=240):
    """遮罩内的块文本预览: 表格块渲染清洗后的真表格(深色遮罩样式), 纯文本走 _blk_html(公式/sub/sup)。"""
    t = (t or "").strip()
    if "<table" in t:
        h = _TAB_BAD.sub("", t)
        h = _TAB_ON.sub("", h)
        if "$" in h or "\\" in h:
            h = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), h)
        return '<div class="pv pvt">%s</div>' % h
    return '<div class="pv">%s</div>' % _blk_html(re.sub(r"\s+", " ", t)[:limit])

def _cmp_group_masks(g, chs):
    """小眼睛遮罩内容: 返回 (panes[4], 是否值得标眼, 锚框)。
    panes[0]=原图栏(三渠道总览表), panes[1:]=各渠道栏(该渠道视角的差异分析)。g=[(ch,i,blk),...]"""
    texts = {c: (b.get("text") or "") for c, i, b in g}
    boxes = {c: b.get("box") or [0, 0, 0, 0] for c, i, b in g}
    blks = {c: b for c, i, b in g}
    filled = {c: t for c, t in texts.items() if t.strip()}
    main_c = next((c for c in chs if c in filled), None)
    main_t = texts.get(main_c, "")
    flags = []
    have = [c for c, i, b in g]
    if len(have) < 3:
        flags.append("渠道缺失:" + "/".join(c for c in chs if c not in have))
    vs = list(filled.values())
    n_diff = sum(1 for x in range(len(vs)) for y in range(x+1, len(vs)) if _gdice(vs[x], vs[y]) < 0.9)
    if n_diff: flags.append("文本分歧")
    if any("\ufffd" in t for t in filled.values()): flags.append("FFFD")
    if any(b.get("xmerge") for c, i, b in g):
        flags.append("跨页并入(" + {"strong": "文本证据", "layout": "排版推断"}.get(
            next((b.get("xev") for c, i, b in g if b.get("xmerge")), ""), "") + ")")
    if any(not (b.get("text") or "").strip() and b.get("cat") != "Picture" for c, i, b in g): flags.append("空块")
    anchor = boxes.get(main_c) or boxes[have[0]]
    rows = []
    for c in chs:
        if c in filled:
            b = blks[c]
            mark = []
            if "\ufffd" in texts[c]: mark.append("FFFD")
            if b.get("xmerge"): mark.append("跨页并入(" + {"strong": "文本证据", "layout": "排版推断"}.get(b.get("xev"), "") + ")")
            if not (b.get("text") or "").strip() and b.get("cat") != "Picture": mark.append("空块")
            rows.append("<tr><td><i class='dot %s'></i>%s</td><td>✓ %s</td><td>%d</td><td>%d</td><td>%.2f</td><td>%s</td></tr>" % (
                _CMP_DOT[c], _CMP_CN[c], b.get("cat", "Text"), len(texts[c]),
                len(re.findall(r"[가-힣]", texts[c])),
                _iou(boxes[c], anchor), " ".join(mark) or "-"))
        else:
            rows.append("<tr class='miss'><td><i class='dot %s'></i>%s</td><td colspan=5>✗ 未识别此区域</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c]))
    pane0 = ("<h5>三渠道总览（本区域）</h5><table><tr><th>渠道</th><th>检出</th><th>字符</th><th>韩文</th>"
             "<th>框IoU</th><th>标记</th></tr>%s</table><p class='fl'>%s</p>"
             % ("".join(rows), " · ".join(flags) or "三家一致"))
    panes = [pane0]
    for c in chs:
        head = "<h5>%s 视角</h5>" % _CMP_CN[c]
        if c not in blks:
            panes.append(head + "<p class='miss'>✗ 本渠道未识别到此区域（另两家有）</p>"); continue
        b, t = blks[c], texts[c]
        if not t.strip():
            body = "<p class='miss'>本渠道此区域识别为空</p>"
            if b.get("xmerge"):
                body += "<p>段首已被引擎并入上一页末块（跨页并入，页码混入风险）</p>"
            panes.append(head + body); continue
        if c == main_c:
            st = "主读法来源" + ("（三家文本一致）" if not n_diff else "")
        else:
            d = _gdice(t, main_t)
            st = "与主读法 dice=%.2f → %s" % (d, "实质一致" if d >= 0.9 else "存在分歧")
        body = "<p>%s</p>%s" % (st, _cmp_preview(t))
        if c != main_c and n_diff:
            body += "<p class='pm'>主读法(%s) 对照:</p>%s" % (_CMP_CN[main_c], _cmp_preview(main_t))
        if b.get("cap"):
            body += "<p class='pm'>题注: %s</p>" % html.escape(b["cap"][:120])
        panes.append(head + body)
    return panes, bool(flags), anchor

def _tag(b):
    """块标签: 类型 + 跨页合并标记(MinerU 把下页段首并入上页末块的缺陷)"""
    t = b["cat"]
    if b.get("xmerge"):
        t += " · 跨页并入"
    elif b.get("xmerge_src"):
        t += " · 含下页段首"
    return t

def _blk_text(b, img_prefix=None, page_img=None):
    """块文本渲染: Table 块嵌入清洗后的 HTML 表格(白名单去危险标签/事件属性);
    Picture 块渲染真实图片(引擎抽取图优先, 否则从原页图按 bbox 裁区), 题注随文本附在后面;
    其余空文本块给占位说明(识别为空)。img_prefix: raw/<渠道>/ 归档目录前缀; page_img: (原页图URL, (W,H))"""
    t = (b.get("text") or "").strip()
    if b.get("cat") == "Table" and t.startswith("<") and "<table" in t:
        html_ = _TAB_BAD.sub("", t)
        html_ = _TAB_ON.sub("", html_)
        if "$" in html_ or "\\" in html_:  # 表格单元格内的行内公式
            html_ = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), html_)
        if b.get("cap"):
            html_ = '<div class="tcap">%s</div>' % _blk_html(b["cap"]) + html_
        return html_  # 本地单文件查看器, 去危险标签与 on* 属性后原样嵌入
    fig = ""
    if b.get("cat") == "Picture":
        if b.get("img") and img_prefix:
            fig = '<div class="pfig"><img src="%s%s" loading="lazy"/></div>' % (img_prefix, b["img"])
        elif page_img:
            url, (PW, PH) = page_img
            x0, y0, x1, y1 = b.get("box") or [0, 0, 0, 0]
            fig = ('<svg class="picr" viewBox="%s %s %s %s" preserveAspectRatio="none">'
                   '<image width="%s" height="%s" href="%s"/></svg>'
                   % (round(x0, 1), round(y0, 1), round(max(2.0, x1 - x0), 1), round(max(2.0, y1 - y0), 1), PW, PH, url))
    if t:
        body = _blk_html(t)
        return fig + body if fig else body
    if fig:
        return fig
    if b.get("xmerge"):
        return '<span style="color:#b45309;font-style:italic">（识别为空——段首已被引擎并入上一页末块，悬停上一页末块可见原文）</span>'
    ph = "（图片区域）" if b.get("cat") == "Picture" else "（识别为空）"
    return '<span style="color:#9ca3af;font-style:italic">%s</span>' % ph


def build_viewer(out_fp, title, sizes, per_page=None, md_text=None, calib_note=None, img_prefix=None):
    n = len(sizes)
    nav = "".join('<a href="#p%d">%d</a>' % (i, i+1) for i in range(n))
    secs, data = [], {}
    if md_text is None:
        for p in range(n):
            W, H = sizes[p]
            blocks = (per_page or {}).get(p) or []
            if blocks:
                rb, rects = [], []
                nemp, first_emp, first_ffd = 0, None, None
                for i, b in enumerate(blocks):
                    bid = "%d_%d" % (p, i)
                    raw = b.get("text") or ""
                    data[bid] = {"cat": b["cat"], "txt": raw[:60]}
                    is_emp = not raw.strip() and b.get("cat") != "Picture"
                    if is_emp:
                        nemp += 1; first_emp = first_emp or bid
                    if "\ufffd" in raw:
                        first_ffd = first_ffd or bid
                    anchor = ""
                    if bid == first_emp: anchor = ' id="emp_%s"' % bid
                    elif bid == first_ffd: anchor = ' id="ffd_%s"' % bid
                    cls = "blk emp" if is_emp else "blk"
                    rb.append('<div class="%s" data-b="%s"%s><span class="tag">%s</span>%s</div>' % (cls, bid, anchor, _tag(b), _blk_text(b, img_prefix, ("pages/page_%d.jpg" % p, (W, H)))))
                    box = b["box"]
                    rcls = NS.get(b["cat"], "txt") + (" emp" if is_emp else "")
                    rects.append('<rect data-b="%s" class="r %s" x="%s" y="%s" width="%s" height="%s"/>' % (
                        bid, rcls, box[0], box[1], max(2, box[2]-box[0]), max(2, box[3]-box[1])))
                fffd = sum((b.get("text") or "").count("\ufffd") for b in blocks)
                flag = ""
                if fffd:
                    flag += ' <a class="bad jump" data-t="ffd_%s" title="跳到本页首个含错误字符的块">FFFD×%d</a>' % (first_ffd, fffd)
                if nemp:
                    flag += ' <a class="empf jump" data-t="emp_%s" title="跳到本页首个空块">空块×%d</a>' % (first_emp, nemp)
                body = '''<div class="grid">
  <div class="pane"><div class="ptitle">原文件（悬停/点击区块联动）</div>
   <div class="imgw"><img src="pages/page_%d.jpg"/><svg viewBox="0 0 %d %d" preserveAspectRatio="none">%s</svg></div></div>
  <div class="pane"><div class="ptitle">解析输出（块级）</div><div class="txt">%s</div></div>
 </div>''' % (p, W, H, "".join(rects), "\n".join(rb))
            else:
                flag = ""
                body = '''<div class="grid">
  <div class="pane"><div class="ptitle">原文件</div><img src="pages/page_%d.jpg"/></div>
  <div class="pane"><div class="ptitle">解析输出</div><div class="txt" style="color:#9ca3af">该渠道此页无结构化输出</div></div>
 </div>''' % p
            secs.append('<section id="p%d"><h2>第 %d 页%s <span class="stat" id="st_p%d"></span></h2>%s</section>' % (p, p+1, flag, p, body))
        body_html = '<div class="wrap">%s</div>' % "\n".join(secs)
    else:
        body_html = '<div class="fullmd">%s</div>' % md2html(md_text)
    v = VIEWER_TMPL.replace("@@TITLE@@", title).replace("@@NAV@@", nav)
    v = v.replace("@@BODY@@", body_html).replace("@@DATA@@", json.dumps(data, ensure_ascii=False))
    if calib_note:
        v = v.replace("<head>", "<head>\n<!-- calib: %s -->" % calib_note, 1)
    io.open(out_fp, "w", encoding="utf-8").write(v)
    return len(data)

COMPARE_TMPL = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>@@TITLE@@</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f4f5f7;color:#222}
header{position:sticky;top:0;background:#1f2937;color:#fff;padding:10px 18px;z-index:9}
header b{font-size:15px}nav{display:inline-block;margin-left:16px}
nav a{color:#9ca3af;margin:0 4px;text-decoration:none;font-size:14px}nav a:hover{color:#fff}
.back{color:#fbbf24;margin-right:12px;text-decoration:none}
.wrap{max-width:1960px;margin:0 auto;padding:14px}
section{margin:20px 0}h2{font-size:15px;color:#374151;border-left:4px solid #2563eb;padding-left:8px}
.stat{color:#6b7280;font-weight:normal;font-size:12px;margin-left:10px}
.grid{display:grid;grid-template-columns:1.15fr 1fr 1fr 1fr;gap:10px}
.pane{background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.ptitle{font-size:12px;padding:5px 10px;border-bottom:1px solid #e5e7eb;color:#fff;font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ptitle.src{background:#374151}.ptitle.mineru{background:#2563eb}.ptitle.dots{background:#d97706}.ptitle.paddle{background:#059669}
.imgw{position:relative}
.imgw img{width:100%;display:block}
.imgw svg{position:absolute;left:0;top:0;width:100%;height:100%}
.r{fill-opacity:.14;stroke-width:2.4;stroke-opacity:.9;cursor:pointer;vector-effect:non-scaling-stroke;opacity:0;transition:opacity .12s}
.r.txt{fill:#6b7280;stroke:#6b7280}.r.sec{fill:#2563eb;stroke:#2563eb}
.r.ftn{fill:#d97706;stroke:#d97706}.r.pic{fill:#7c3aed;stroke:#7c3aed}
.r.cap{fill:#0891b2;stroke:#0891b2}.r.hd,.r.ft,.r.pn{fill:none;stroke:#dc2626;stroke-dasharray:5 3}
.r.tbl{fill:#059669;stroke:#059669}.r.fm{fill:#db2777;stroke:#db2777}
.r.on{opacity:1;fill-opacity:.38;stroke-opacity:1;stroke-width:3.2}
.txt{padding:8px 10px;max-height:640px;overflow:auto;font-size:13px;line-height:1.75}
.blk table{border-collapse:collapse;margin:4px 0;font-size:12px}.blk td,.blk th{border:1px solid #d1d5db;padding:2px 6px}
.blk{border-left:4px solid #e5e7eb;padding:4px 8px;margin:4px 0;border-radius:0 6px 6px 0;cursor:pointer;background:#fafafa}
.blk .tag{display:inline-block;font-size:10px;color:#9ca3af;border:1px solid #e5e7eb;border-radius:4px;padding:0 5px;margin-right:6px;background:#fff}
.blk.on{background:#fef9c3;box-shadow:0 0 0 2px #f59e0b inset}
.blk.mineru.on{background:#dbeafe;box-shadow:0 0 0 2px #2563eb inset}
.blk.dots.on{background:#fef3c7;box-shadow:0 0 0 2px #d97706 inset}
.blk.paddle.on{background:#d1fae5;box-shadow:0 0 0 2px #059669 inset}
.blk.emp{background:#fef2f2;border-left-color:#dc2626}
a.bad,a.empf{cursor:pointer;text-decoration:none}
.empf{background:#f3e8ff;color:#7c3aed;padding:0 5px;border-radius:3px;font-weight:bold;font-size:12px}
i.fr{font-style:normal;white-space:nowrap}
.tcap{font-size:12px;color:#374151;padding:3px 0 5px;font-weight:bold}
.pfig img{max-width:100%;border:1px solid #e5e7eb;border-radius:4px;display:block}
svg.picr{width:100%;height:auto;display:block;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.r.emp{stroke:#dc2626;stroke-dasharray:4 3}
.none{color:#9ca3af;font-size:13px;padding:14px 10px}
.bad{background:#fee2e2;color:#dc2626;padding:0 3px;border-radius:3px;font-weight:bold}
.pane{position:relative}
.eye{position:absolute;z-index:5;width:20px;height:20px;line-height:19px;text-align:center;background:rgba(31,41,55,.88);border:1.5px solid #fbbf24;border-radius:50%;cursor:pointer;font-size:11px;transform:translate(-50%,-50%);user-select:none}
.eye:hover,.eye.pin{background:#fbbf24}
.eye.lm{background:rgba(245,158,11,.92);border-color:#fef3c7}.eye.lm:hover,.eye.lm.pin{background:#f59e0b}
.eye.ll{background:rgba(220,38,38,.92);border-color:#fecaca}.eye.ll:hover,.eye.ll.pin{background:#ef4444}
.eye.lc{background:rgba(124,58,237,.92);border-color:#ddd6fe}.eye.lc:hover,.eye.lc.pin{background:#8b5cf6}
.eye.lo{background:rgba(37,99,235,.92);border-color:#bfdbfe}.eye.lo:hover,.eye.lo.pin{background:#3b82f6}
.r.um{fill:none;stroke:#111827;stroke-width:3;stroke-dasharray:10 5;opacity:.85;pointer-events:none}
.r.umb{fill:none;stroke:#fff;stroke-width:6;opacity:.9;pointer-events:none}
.pmask{position:absolute;left:0;right:0;top:27px;bottom:0;background:#111827;color:#e5e7eb;display:none;z-index:6;padding:10px 12px;font-size:12px;line-height:1.6;overflow:auto;text-align:left;pointer-events:none}
.pmask h5{margin:0 0 6px;font-size:12px;color:#fff;border-bottom:1px solid #4b5563;padding-bottom:4px}
.pmask table{border-collapse:collapse;width:100%;margin:4px 0}
.pmask th,.pmask td{border:1px solid #4b5563;padding:2px 6px;text-align:left;font-weight:normal;font-size:11px;white-space:nowrap}
.pmask th{background:rgba(255,255,255,.08);color:#9ca3af}
.pmask .pv{background:rgba(255,255,255,.06);border-radius:6px;padding:6px 8px;margin:4px 0;color:#d1d5db;word-break:break-word;font-size:12px}
.pmask .pv.pvt{padding:4px 2px}
.pmask .pv table{border-collapse:collapse;margin:2px 0;width:100%}
.pmask .pv td,.pmask .pv th{border:1px solid #6b7280;padding:2px 5px;font-size:11px;color:#e5e7eb;font-weight:normal;text-align:left;white-space:normal}
.pmask .pm{color:#fbbf24;margin:6px 0 2px}
.pmask table.umt{margin:2px 0 6px}
.pmask table.umt td{border:1px solid #374151;font-size:11px;white-space:normal;word-break:break-word}
.pmask table.umt td:first-child{white-space:nowrap;color:#9ca3af;width:1%}
.pv.dfx{font-family:Consolas,monospace;font-size:12px;line-height:1.7}
.df.del{color:#f87171;text-decoration:line-through}
.df.add{color:#34d399;font-weight:bold}
.pmask .fl{color:#fbbf24;font-weight:bold;margin:6px 0 0}
.pmask .miss{color:#f87171}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.dot.m{background:#60a5fa}.dot.d{background:#fbbf24}.dot.p{background:#34d399}
.lg{display:inline-block;margin-left:14px;font-size:12px}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 3px;vertical-align:-1px}
</style></head><body>
<header><a class="back" href="index.html">‹ 三渠道总览</a><b>@@TITLE@@</b><nav>跳页：@@NAV@@</nav>
<span class="lg"><i style="background:#2563eb"></i>标题 <i style="background:#6b7280"></i>正文 <i style="background:#d97706"></i>脚注 <i style="background:#7c3aed"></i>图片 <i style="background:#0891b2"></i>图注 <i style="background:#059669"></i>表格 <i style="background:#fff;border:1px dashed #dc2626"></i>页眉页脚页码 <i style="background:#fee2e2;border:1px solid #dc2626"></i>空块</span>
</header>
@@BODY@@
<script>
var META=@@DATA@@, pinned=null;
var GROUPS=@@GROUPS@@, gpin=null, ghide=null;
function act(id,on){var e=document.querySelectorAll('[data-b="'+id+'"]');for(var k=0;k<e.length;k++){if(on)e[k].classList.add('on');else e[k].classList.remove('on');}}
function stat(id){var p=id.split('_');var el=document.getElementById('st_p'+p[1]);if(el&&META[id])el.textContent=' '+p[0]+' · 块 '+p[2]+' · '+META[id].cat+' · '+META[id].txt.replace(/\\s+/g,' ').slice(0,40);}
function fillMasks(gid,sec){var g=GROUPS[gid];if(!g)return;var ms=sec.querySelectorAll('.pmask');
 for(var k=0;k<ms.length&&k<g.panes.length;k++){ms[k].innerHTML=g.panes[k];ms[k].style.display='block';}}
function hideAllMasks(){var ms=document.querySelectorAll('.pmask');for(var k=0;k<ms.length;k++)ms[k].style.display='none';
 document.querySelectorAll('.eye.pin').forEach(function(x){x.classList.remove('pin');});}
function showPinned(){if(!gpin)return;hideAllMasks();var eye=document.querySelector('.eye[data-g="'+gpin+'"]');
 if(eye){fillMasks(gpin,eye.closest('section'));eye.classList.add('pin');}}
function scheduleHide(){if(ghide)clearTimeout(ghide);
 ghide=setTimeout(function(){if(gpin)showPinned();else hideAllMasks();},250);}
document.addEventListener('mouseover',function(e){
 var eye=e.target.closest?e.target.closest('.eye'):null;
 if(eye){clearTimeout(ghide);fillMasks(eye.dataset.g,eye.closest('section'));return;}
 scheduleHide();
 var t=e.target.closest?e.target.closest('[data-b]'):null;if(!t||t.dataset.b===pinned)return;act(t.dataset.b,true);stat(t.dataset.b);});
document.addEventListener('mouseout',function(e){
 var t=e.target.closest?e.target.closest('[data-b]'):null;if(!t||t.dataset.b===pinned)return;act(t.dataset.b,false);});
document.addEventListener('click',function(e){
 var eye=e.target.closest?e.target.closest('.eye'):null;
 if(eye){gpin=(gpin===eye.dataset.g)?null:eye.dataset.g;
  if(gpin)showPinned();else hideAllMasks();return;}
 if(gpin){gpin=null;hideAllMasks();}
 var t=e.target.closest?e.target.closest('[data-b]'):null;if(pinned){act(pinned,false);if(!t){pinned=null;return;}}if(t){pinned=t.dataset.b;act(pinned,true);stat(pinned);}});
document.addEventListener('click',function(e){var j=e.target.closest?e.target.closest('.jump'):null;if(!j)return;e.preventDefault();
 var el=document.getElementById(j.dataset.t);if(!el)return;el.scrollIntoView({block:'center'});
 var id=el.dataset.b;act(id,true);setTimeout(function(){if(pinned!==id)act(id,false);},1600);});
</script>
</body></html>"""

def _dice_verdict(d):
    """dice 值配色结论(深色遮罩底色上的可读色)。"""
    if d >= 0.95:
        return "<span style='color:#34d399'>%.2f 一致</span>" % d
    if d >= 0.8:
        return "<span style='color:#fbbf24'>%.2f 小分歧</span>" % d
    return "<span style='color:#f87171'>%.2f 离群</span>" % d


def _token_diff(main, ch):
    """数字/西文词对比(语言无关裁决, 不懂韩文也能判读): 返回差异描述或 None。
    剥离 HTML 标签(sub/sup 等)后取数字/西文词, 集合差集去重(计数差一般不影裁决)。"""
    tok = lambda s: set(re.findall(r"[0-9]+(?:[.,][0-9]+)*|[A-Za-z]+",
                                   _fold_sym(re.sub(r"<[^>]+>", " ", s or ""))))
    a, b = tok(main), tok(ch)
    if a == b:
        return None
    parts = []
    if a - b:
        parts.append("仅主读法有: " + " ".join(sorted(a - b)[:12]))
    if b - a:
        parts.append("仅本渠道有: " + " ".join(sorted(b - a)[:12]))
    return " ｜ ".join(parts)


def _char_diff(main, ch):
    """主读法 vs 本渠道读法的逐字 diff(比较口径: 符号折叠+剥空白, 故剩余差异=真字符差异)。
    返回 HTML: 一致部分原样, 主有本无标红删除线(漏), 本渠道多出/不同标绿(多/异)。
    完全一致返回 None。"""
    import difflib
    a = re.sub(r"\s+", "", _fold_sym(main))
    b = re.sub(r"\s+", "", _fold_sym(ch))
    if not b or a == b:
        return None
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(a[i1:i2]))
        elif op == "replace":
            out.append('<span class="df del">%s</span><span class="df add">%s</span>'
                       % (html.escape(a[i1:i2]), html.escape(b[j1:j2])))
        elif op == "delete":
            out.append('<span class="df del">%s</span>' % html.escape(a[i1:i2]))
        else:
            out.append('<span class="df add">%s</span>' % html.escape(b[j1:j2]))
    s = "".join(out)
    return s if len(s) <= 6000 else None  # 过长(表格html等)不回溯, 退回预览


_UNI_CN = {"none": "一致", "read": "读法分歧", "split": "拆分分歧", "merge": "合并分歧",
           "coverage": "覆盖缺失", "type": "类型分歧", "xpage": "跨页涉入"}

def _unified_group_panes(u, chs):
    """unified 对象的差异遮罩: ([原图栏总览, 各渠道栏...], 眼色级别)。
    与 _cmp_group_masks 同构(4 pane 对齐 .pmask), 前端显隐/钉住逻辑零改动。"""
    conf, div = u["confidence"], u["divergence"]
    lvl = "lc" if div["kind"] == "coverage" else {"mid": "lm", "low": "ll"}.get(conf["level"], "lm")
    rd = {r["ch"]: r for r in u["readings"]}
    rows = []
    for c in chs:
        if c in u["members"]:
            us = u["members"][c]
            nch = sum(len(re.sub(r"\s+", "", m.get("text") or "")) for m in us)  # 非空白字符数(空格风格不干扰漏字判断)
            mark = " ".join(rd[c]["flags"]) or "-"
            rows.append("<tr><td><i class='dot %s'></i>%s</td><td>✓ %s×%d</td><td>%d</td><td>%s</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c], us[0]["cat"], len(us), nch, mark))
        else:
            rows.append("<tr class='miss'><td><i class='dot %s'></i>%s</td><td colspan=3>✗ 未识别此区域</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c]))
    pane0 = ("<h5>统一对象 %s ｜ %s · %s · %s</h5><table><tr><th>渠道</th><th>在场</th><th>字符</th><th>标记</th></tr>%s</table>"
             "<p class='fl'>分歧 <b>%s</b>: %s</p><p class='fl'>置信依据: %s</p>%s"
             % (u["uid"], u["type"], conf["level"], _UNI_CN.get(div["kind"], div["kind"]), "".join(rows),
                div["kind"], html.escape(div.get("detail", "")), html.escape(conf.get("basis", "")),
                ('<p class="pm">主读法:</p>%s' % _cmp_preview(u.get("main_text") or "")) if u.get("main_text") else ""))
    panes = [pane0]
    for c in chs:
        head = "<h5>%s 视角</h5>" % _CMP_CN[c]
        if c not in u["members"]:
            panes.append(head + "<p class='miss'>✗ 本渠道未识别到此区域（另两家有）→ coverage 分歧，"
                                "待人工确认是漏检还是别家误检</p>")
            continue
        r = rd.get(c) or {}
        filled = (r.get("text") or "").strip()
        show_pv = (not filled) or (r.get("dice", 1.0) < 0.95) or bool(r.get("flags"))
        if not filled:
            verdict = "空文本块"
        else:
            verdict = "dice " + _dice_verdict(r.get("dice", 0.0))
        # 成员块列表(split/merge 时尤其关键: 各家切成几块、每块什么是人工要看的证据)
        mrows = []
        for m in u["members"][c]:
            mt = (m.get("text") or "").strip()
            mrows.append("<tr><td>%s</td><td class=t>%s</td></tr>" % (
                m.get("cat", "?"),
                html.escape((re.sub(r"\s+", " ", mt)[:60]) if mt else ("（空）" if m.get("cat") != "Picture" else "（图区）"))))
        body = "<p>%s ｜ %s</p><table class=umt>%s</table>" % (
            verdict,
            ("flags: " + " ".join(r.get("flags") or [])) if r.get("flags") else "无标记",
            "".join(mrows))
        # 自动修复读法(渠道内缺陷已机械修复: 溢出裁剪/组内去重; 原文留档成员表)
        if r.get("text_fixed"):
            body += ("<p class='pm'>✅ 自动修复读法（溢出裁剪/组内去重，确认后可代替原文使用；原样见成员表）:</p>%s"
                     % _cmp_preview(r["text_fixed"]))
        # 数字/西文词对比(语言无关): 不懂韩文也能裁决的差异
        td = _token_diff(u.get("main_text") or "", r.get("text") or "") if filled else None
        if td:
            body += "<p class='pm'>数字/西文词对比（不懂韩文也能裁决）: %s</p>" % html.escape(td)
        # 逐字 diff(用户核心诉求: 渠道间字符差异/错漏的直接视图)优先于文本预览
        dfx = _char_diff(u.get("main_text") or "", r.get("text") or "") if filled else None
        if dfx:
            body += ("<p class='pm'>与主读法逐字 diff（口径: 符号折叠+去空白；"
                     "<span class='df del'>红删=主有本无(漏)</span>／<span class='df add'>绿增=本渠道多/异</span>）:</p>"
                     "<div class='pv dfx'>%s</div>") % dfx
        elif show_pv:
            body += _cmp_preview(r.get("text") or "")
        caps = [m.get("cap") for m in u["members"][c] if m.get("cap")]
        if caps:
            body += "<p class='pm'>题注: %s</p>" % html.escape("\n".join(caps)[:120])
        panes.append(head + body)
    return panes, lvl


def build_compare_viewer(out_fp, title, sizes, per_ch, src_dirs=None, unified_pages=None, unified_driven=False):
    """1:3 对照查看器: 每页 左原图(叠全部块框) + 右三栏各渠道块列表。
    per_ch = {"mineru": per_page|md字符串|None, ...}; 块 id 带渠道前缀, 同渠道内 rect<->文本联动, 无跨渠道联动。
    src_dirs = {ch: raw 目录 basename} 供引擎抽取图按实际来源目录引用(不再硬编码 raw/<ch>/, API 文档源是 <ch>_api)。
    unified_pages = {页号0起: [unified对象...]} 存在时, 👁差异标注与遮罩改由 unify 归一化层驱动:
    review=true 的对象出眼(眼色按置信分级: mid=琥珀 low=红 coverage=紫), 遮罩=统一对象视角(分歧类型/置信依据/各家读法dice),
    原图栏叠加共识几何虚线框。
    unified_driven=False(老产物/--no-unify)时走旧的临时聚组逻辑; 为 True 时旧的差异眼一律不出——
    两套判据叠加会把归一化层判定无分歧的块又点亮一批, 复检队列被人为放大。"""
    n = len(sizes)
    nav = "".join('<a href="#p%d">%d</a>' % (i, i + 1) for i in range(n))
    secs, data, groups_json = [], {}, {}
    chs = ("mineru", "dots", "paddle")
    for p in range(n):
        W, H = sizes[p]
        rects, col_html = [], {}
        nemp, first_emp, first_ffd, nffd = 0, None, None, 0
        by_ch = {}
        for ch in chs:
            src = per_ch.get(ch)
            blocks = (src or {}).get(p) if isinstance(src, dict) else None
            by_ch[ch] = list(enumerate(blocks or []))
            if blocks:
                rb = []
                for i, b in enumerate(blocks):
                    bid = "%s_%d_%d" % (ch, p, i)
                    raw = b.get("text") or ""
                    data[bid] = {"cat": b["cat"], "txt": raw[:60]}
                    is_emp = not raw.strip() and b.get("cat") != "Picture"
                    if is_emp:
                        nemp += 1; first_emp = first_emp or bid
                    if "\ufffd" in raw:
                        nffd += 1; first_ffd = first_ffd or bid
                    anchor = ""
                    if bid == first_emp: anchor = ' id="emp_%s"' % bid
                    elif bid == first_ffd: anchor = ' id="ffd_%s"' % bid
                    cls = "blk %s%s" % (ch, " emp" if is_emp else "")
                    rb.append('<div class="%s" data-b="%s"%s><span class="tag">%s</span>%s</div>'
                              % (cls, bid, anchor, _tag(b), _blk_text(b, "raw/%s/" % (src_dirs or {}).get(ch, ch),
                                                                 ("pages/page_%d.jpg" % p, (W, H)))))
                    box = b["box"]
                    rcls = NS.get(b["cat"], "txt") + (" emp" if is_emp else "")
                    rects.append('<rect data-b="%s" class="r %s" x="%s" y="%s" width="%s" height="%s"/>' % (
                        bid, rcls, box[0], box[1], max(2, box[2] - box[0]), max(2, box[3] - box[1])))
                col_html[ch] = '<div class="txt">%s</div>' % "\n".join(rb)
            elif isinstance(src, str):
                col_html[ch] = '<div class="none">md 模式无块级</div>'
            elif src is None:
                col_html[ch] = '<div class="none">无产物</div>'
            else:
                col_html[ch] = '<div class="none">该渠道此页无结构化输出</div>'
        eyes = []
        if unified_driven:
            # —— unified 模式: 眼/遮罩由归一化层驱动(review=true 才出眼), 原图叠加共识几何框 ——
            for ui, u in enumerate(unified_pages.get(p) or []):
                sev = min((r["dice"] for r in u["readings"] if (r["text"] or "").strip()), default=1.0)
                hint = ""
                if not u.get("review"):
                    if sev >= 0.5:
                        continue
                    hint = " · 某家读法严重异常(dice=%.2f)" % sev  # 信息提示眼: high 语义不变, 但异常读法要让人能看见
                panes, lvl = _unified_group_panes(u, chs)
                if hint:
                    lvl = "lo"
                gid = u["uid"]  # 与 unified\page_N.json 的 uid 一致(列表已按阅读序重排, 勿用 enumerate 序号)
                groups_json[gid] = {"panes": panes}
                bx = u["box"]
                rw, rh = max(2, bx[2] - bx[0]), max(2, bx[3] - bx[1])
                rects.append('<rect class="r umb" x="%s" y="%s" width="%s" height="%s"/>'
                             % (bx[0], bx[1], rw, rh))  # 白色衬底让共识框在深色块区/白底都可见
                rects.append('<rect class="r um" x="%s" y="%s" width="%s" height="%s"/>' % (bx[0], bx[1], rw, rh))
                eyes.append('<div class="eye %s" data-g="%s" style="left:%.2f%%;top:%.2f%%" title="%s · %s">👁</div>'
                            % (lvl, gid, (bx[0] + bx[2]) / 2.0 / W * 100, (bx[1] + bx[3]) / 2.0 / H * 100,
                               u["confidence"]["level"], u["divergence"]["kind"] + hint))
        else:
            # —— 跨渠道分组: 有差异/问题的区域在原文栏标小眼睛, 悬停时四栏浮遮罩(差异分析) ——
            groups = []
            for ch in chs:
                for i, b in by_ch[ch]:
                    if not b.get("box"):
                        continue
                    for g in groups:
                        c0, i0, b0 = g[0]
                        if b0.get("box") and (_iou(b["box"], b0["box"]) >= 0.3
                                              or _gdice(b.get("text"), b0.get("text")) >= 0.5
                                              or _cin(b["box"], b0["box"])):
                            g.append((ch, i, b))
                            break
                    else:
                        groups.append([(ch, i, b)])
            for gi, g in enumerate(groups):
                # 结构性组(页眉/页脚/页码)不算差异标注, 不出眼睛(避免一页一堆噪音按钮)
                if all(b.get("cat") in ("Page-header", "Page-footer", "Page-number") for c, i, b in g):
                    continue
                panes, interesting, anchor = _cmp_group_masks(g, chs)
                if not interesting:
                    continue
                gid = "G%d_%d" % (p, gi)
                groups_json[gid] = {"panes": panes}
                eyes.append('<div class="eye" data-g="%s" style="left:%.2f%%;top:%.2f%%" title="悬停/点击：四栏差异分析遮罩">👁</div>'
                            % (gid, (anchor[0] + anchor[2]) / 2.0 / W * 100, (anchor[1] + anchor[3]) / 2.0 / H * 100))
        img_cell = ('<div class="pane"><div class="ptitle src">原文件（区块联动 · 👁=%s）</div>'
                    '<div class="imgw"><img src="pages/page_%d.jpg"/><svg viewBox="0 0 %d %d" preserveAspectRatio="none">%s</svg>%s</div>'
                    '<div class="pmask"></div></div>'
                    % ("unified 复检项(虚线=共识框)" if unified_pages is not None else "差异分析",
                       p, W, H, "".join(rects), "".join(eyes)))
        ch_cells = "".join('<div class="pane"><div class="ptitle %s">%s</div>%s<div class="pmask"></div></div>'
                           % (ch, {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}[ch], col_html[ch]) for ch in chs)
        flag = ""
        if nffd:
            flag += ' <a class="bad jump" data-t="ffd_%s" title="跳到本页首个含错误字符的块">FFFD×%d</a>' % (first_ffd, nffd)
        if nemp:
            flag += ' <a class="empf jump" data-t="emp_%s" title="跳到本页首个空块">空块×%d</a>' % (first_emp, nemp)
        secs.append('<section id="p%d"><h2>第 %d 页%s <span class="stat" id="st_p%d"></span></h2><div class="grid">%s%s</div></section>' % (p, p + 1, flag, p, img_cell, ch_cells))
    v = COMPARE_TMPL.replace("@@TITLE@@", title).replace("@@NAV@@", nav)
    v = v.replace("@@BODY@@", '<div class="wrap">%s</div>' % "\n".join(secs))
    v = v.replace("@@DATA@@", json.dumps(data, ensure_ascii=False))
    v = v.replace("@@GROUPS@@", json.dumps(groups_json, ensure_ascii=False))
    io.open(out_fp, "w", encoding="utf-8").write(v)
    return len(data)

# ========== ② 渠道获取(自动命令 / inbox 投放) ==========
def scan_inbox(doc=None):
    """严格按文档名归配: 渠道产物只属于同名文档, 禁止跨文档兜底(曾因此把旧文档块画到新文档上)"""
    found = {}
    if not os.path.isdir(INBOX): return found
    docl = (doc or "").lower()
    best = {}  # ch -> (isdir, path); 目录优先
    for name in sorted(os.listdir(INBOX)):
        p = os.path.join(INBOX, name)
        nl = name.lower()
        if docl and not nl.startswith(docl): continue  # 非本文档产物一律不收
        isd = 1 if os.path.isdir(p) else 0
        ch = None
        if "__mineru" in nl: ch = "mineru"
        elif "__dots" in nl: ch = "dots"
        elif "__paddle" in nl: ch = "paddle"
        elif os.path.isdir(p):
            fs = os.listdir(p)
            if any(f.endswith("_content_list_v2.json") for f in fs): ch = "mineru"
            elif any(re.match(r".*page_\d+\.json$", f) for f in fs): ch = "dots"
        elif nl.endswith(".md"):
            t = io.open(p, encoding="utf-8", errors="ignore").read(4000)
            if "bcebos" in t or "paddleocr" in nl: ch = "paddle"
        if ch and (ch not in best or isd > best[ch][0]):
            best[ch] = (isd, p)
    return {ch: v[1] for ch, v in best.items()}

def try_auto(pdf, work):
    outs = {}
    for ch, meta in CFG.get("auto", {}).items():
        cmd_t = meta.get("cmd")
        if not cmd_t: continue
        exe = cmd_t.split()[0]
        if shutil.which(exe) is None:
            log("渠道[%s] 命令 %s 不在 PATH，跳过（可改为 inbox 投放）" % (ch, exe)); continue
        odir = os.path.join(work, "auto_" + ch)
        cmd = cmd_t.format(pdf=pdf, out=odir)
        log("自动执行渠道[%s]: %s" % (ch, cmd))
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=meta.get("timeout", 1800))
        if r.returncode == 0: outs[ch] = odir
        else: log("渠道[%s] 失败: %s" % (ch, r.stderr[-300:]))
    return outs

def _out_rel(src, out):
    """src 在 out 内(_api 缓存目录) -> "raw/<basename>/"; src 在 out 外(inbox 等) -> 用 preserve_raw 的归档位 "raw/<ch>/"。
    与 merge_out/viewer 同一判定, 三处不再各写一份。"""
    if os.path.abspath(src).startswith(os.path.abspath(out) + os.sep):
        return "raw/%s/" % os.path.basename(os.path.normpath(src))
    return None

def preserve_raw(src, rawd):
    """把渠道源目录/文件原样复制到 out/<doc>/raw/<渠道>/(不解析, 仅归档)。
    src 本身已是 raw/<渠道>_api(API 缓存目录)时跳过孪生复制——全读者(merge_out/analyze/caption_audit)
    均有 _api 兜底, 不再产生双份; 只有 out 之外的真实外部来源(inbox 导出等)才归档复制。"""
    if os.path.abspath(src) == os.path.abspath(rawd + "_api"):
        return sum(len(fs) for _, _, fs in os.walk(src))
    os.makedirs(rawd, exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, rawd, dirs_exist_ok=True)
    else:
        shutil.copy2(src, rawd)
    return sum(len(fs) for _, _, fs in os.walk(rawd))

def fetch_paddle_api(pdf_path, out_dir):
    """PaddleOCR 云端 API: 提交->轮询->转换。产物: page_N.json(归一化块)+page_N.md+api_result.jsonl。
    需 channels.json 配置 "paddle_api": {"token": "...", "model": "PaddleOCR-VL"}。返回产物目录或 None。"""
    try:
        cfg = load_cfg()
    except Exception:
        return None
    meta = cfg.get("paddle_api") or {}
    token = meta.get("token")
    if not token:
        return None
    import ssl, urllib.request
    ctx = ssl._create_unverified_context()  # 服务端证书链缺AKI, 严格校验不过(浏览器宽松可通过)
    base = meta.get("base", "https://paddleocr.aistudio-app.com")
    model = meta.get("model", "PaddleOCR-VL")
    max_wait = int(meta.get("timeout", 900))

    def http(req, timeout=60):
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())

    boundary = "----kciapi1"
    body = io.BytesIO()
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n" % (boundary, os.path.basename(pdf_path))).encode())
    body.write(io.open(pdf_path, "rb").read()); body.write(b"\r\n")
    for k, v in [("model", model), ("optionalPayload", json.dumps({"useDocOrientationClassify": False, "useDocUnwarping": False, "useChartRecognition": False}))]:
        body.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    body.write(("--%s--\r\n" % boundary).encode())
    resp = _net_retry("paddle 提交", lambda: http(urllib.request.Request(base + "/api/v2/ocr/jobs", data=body.getvalue(), headers={
        "Authorization": "Bearer " + token, "Content-Type": "multipart/form-data; boundary=" + boundary}), 120))
    if resp.get("code") != 0:
        raise RuntimeError("提交失败: %s" % resp.get("msg"))
    job = resp["data"]["jobId"]
    log("   paddle job %s 已提交, 轮询中..." % job)
    import time as _t
    run_cap = int(meta.get("run_timeout", 300))     # 仅"解析中(running)"累计上限
    queue_cap = int(meta.get("queue_timeout", 7200))  # 排队总兜底( generous, 正常远不会触到)
    run_spent = 0.0
    last_state = None
    last_net = 0.0
    t0 = _t.time()
    while True:
        _t.sleep(5)
        try:
            st = http(urllib.request.Request(base + "/api/v2/ocr/jobs/" + job, headers={"Authorization": "Bearer " + token}), 30)["data"]
        except Exception as e:
            # 网络抖动不杀渠道: 稀疏日志后继续轮询, 解析计时只认 running 状态
            if _t.time() - last_net > 60:
                last_net = _t.time()
                log("   paddle 状态查询网络错误(重试中): %s" % e)
            continue
        state = st.get("state")
        if state != last_state:
            log("   paddle 状态 -> %s" % state)
            last_state = state
        if state == "running":
            run_spent += 5
            prog = st.get("extractProgress") or {}
            log("   paddle running %s/%s页 (累计 %ds/%ds)" % (prog.get("extractedPages"), prog.get("totalPages"), run_spent, run_cap))
            if run_spent > run_cap:
                raise RuntimeError("paddle 解析超时(running>%ds, 排队时间不计)" % run_cap)
        if state == "done":
            url = st["resultUrl"]["jsonUrl"]
            break
        if state == "failed":
            raise RuntimeError("解析失败: %s" % st.get("errorMsg"))
        if _t.time() - t0 > queue_cap:
            raise RuntimeError("排队总超时(>%ds): 服务端一直没开始解析" % queue_cap)

    raw = _net_retry("paddle 结果下载", lambda: urllib.request.urlopen(urllib.request.Request(url), timeout=180, context=ctx).read())
    os.makedirs(out_dir, exist_ok=True)
    io.open(os.path.join(out_dir, "api_result.jsonl"), "wb").write(raw)
    CAT = {"doc_title": "Section-header", "paragraph_title": "Section-header", "text": "Text", "abstract": "Text",
           "footnote": "Footnote", "header": "Page-header", "number": "Page-number", "table": "Table",
           "figure_title": "Caption", "image": "Picture", "chart": "Picture", "formula": "Formula", "vision_footnote": "Caption"}
    n = 0
    for line in raw.decode("utf-8").strip().split("\n"):
        if not line.strip(): continue
        for lp in (json.loads(line)["result"].get("layoutParsingResults") or []):
            pr = lp.get("prunedResult") or {}
            w, h = float(pr.get("width") or 1), float(pr.get("height") or 1)
            blocks = []
            for b in (pr.get("parsing_res_list") or []):
                bb = b.get("block_bbox") or [0, 0, 0, 0]
                blocks.append({"category": CAT.get(b.get("block_label"), "Text"),
                               "bbox": [round(bb[0]/w, 4), round(bb[1]/h, 4), round(bb[2]/w, 4), round(bb[3]/h, 4)],
                               "text": b.get("block_content", "")})
            io.open(os.path.join(out_dir, "page_%d.json" % n), "w", encoding="utf-8").write(json.dumps(blocks, ensure_ascii=False))
            io.open(os.path.join(out_dir, "page_%d.md" % n), "w", encoding="utf-8").write((lp.get("markdown") or {}).get("text", ""))
            n += 1
    log("   paddle API 完成: %d 页" % n)
    return out_dir

def fetch_dots_api(pdf_path, out_dir):
    """dots.ocr 云端实例(小红书 Gradio5): /config 拉组件态 -> /upload -> fn1 预热 -> fn0 解析prompt -> fn6 推理 -> 同会话下载结果 zip。
    产物: page_N.json(按 zip 内该页 jpg 尺寸归一化, 免标定) + page_N.md/jpg(api_result.zip 仅 keep_api_zip=true 时保留)。
    需 channels.json 配置 "dots_api": {"base","model","prompt",...}。返回产物目录或 None。"""
    try:
        cfg = load_cfg()
    except Exception:
        return None
    meta = cfg.get("dots_api") or {}
    if not meta.get("enabled", True):
        return None
    import ssl, time as _t, http.cookiejar, urllib.request
    ctx = ssl._create_unverified_context()  # 与 paddle_api 同款: 证书链不完整, 浏览器宽松可通过
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPSHandler(context=ctx))
    base = meta.get("base", "https://dotsocr.xiaohongshu.com").rstrip("/")
    max_wait = int(meta.get("timeout", 1800))

    def http_raw(url, data=None, headers=None, timeout=max_wait):
        req = urllib.request.Request(base + url, data=data, headers=headers or {})
        with opener.open(req, timeout=timeout) as r:
            return r.read()

    def http(url, data=None, headers=None, timeout=max_wait):
        return json.loads(http_raw(url, data, headers, timeout).decode())

    # /config 同时是上传会话的 bootstrap(自定义 JS 会先请求它拿 cookie) 和组件初始态来源
    gcfg = _net_retry("dots /config", lambda: http("/config"), tries=5, pause=3.0)
    comps = {c["id"]: c for c in gcfg.get("components", [])}
    state0 = comps.get(1, {}).get("props", {}).get("value")  # gr.State 按对象传, 传字符串会被服务端当异常
    prompt_text0 = comps.get(11, {}).get("props", {}).get("value") or ""
    prompt_key = meta.get("prompt") or comps.get(10, {}).get("props", {}).get("value") or "prompt_layout_all_en"
    model = meta.get("model") or comps.get(7, {}).get("props", {}).get("value") or "dots.mocr"
    min_px = int(meta.get("min_pixels", comps.get(18, {}).get("props", {}).get("value") or 3136))
    max_px = int(meta.get("max_pixels", comps.get(19, {}).get("props", {}).get("value") or 11289600))
    fitz = bool(comps.get(16, {}).get("props", {}).get("value", True))
    if state0 is None:
        raise RuntimeError("dots /config 缺少 state 组件")

    # 1) 上传( multipart 字段名 files )
    boundary = "----kcidots%d" % int(_t.time() * 1000)
    body = io.BytesIO()
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n" % (boundary, os.path.basename(pdf_path))).encode())
    body.write(io.open(pdf_path, "rb").read()); body.write(b"\r\n--%s--\r\n" % boundary.encode())
    upath = _net_retry("dots 上传", lambda: http("/gradio_api/upload", body.getvalue(), {"Content-Type": "multipart/form-data; boundary=" + boundary}), tries=3, pause=3.0)[0]
    fv = {"path": upath, "orig_name": os.path.basename(pdf_path), "meta": {"_type": "gradio.FileData"}}  # 官方 client 的序列化形态
    log("   dots 已上传: %s" % upath)

    sess = "kci%d" % int(_t.time())
    deps = gcfg.get("dependencies", [])
    IDX = {d.get("api_name"): i for i, d in enumerate(deps)}

    def call(api_name, data):
        """queue/join -> SSE /queue/data, 返回 output.data; 失败抛异常(show_error=false 的实例 error 恒 null)"""
        fn_index = IDX.get(api_name)
        if fn_index is None:
            raise RuntimeError("dots /config 缺少依赖 %s" % api_name)
        _net_retry("dots queue/join", lambda: http("/gradio_api/queue/join", json.dumps({"fn_index": fn_index, "data": data, "session_hash": sess}).encode(),
             {"Content-Type": "application/json"}, timeout=60), tries=5, pause=3.0)
        st = _t.time()
        last_ping = 0.0
        while True:
            if _t.time() - st > max_wait:
                raise RuntimeError("dots %s 轮询超时" % api_name)
            try:
                resp = opener.open(urllib.request.Request(base + "/gradio_api/queue/data?session_hash=%s" % sess),
                                   timeout=max_wait)
                while True:
                    ln = resp.readline()  # SSE 逐行读; 心跳/评论行也会返回, 用于驱动等待日志
                    if not ln:
                        break  # 流结束仍未拿到 completed -> 外层重连
                    if _t.time() - last_ping > 30:
                        last_ping = _t.time()
                        log("   dots %s 等待中... 已 %.0fs" % (api_name, _t.time() - st))
                    if not ln.startswith(b"data:"):
                        continue
                    try:
                        e = json.loads(ln[5:].strip().decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if e.get("msg") == "process_completed":
                        if not e.get("success"):
                            raise RuntimeError("dots %s 执行失败" % api_name)
                        return (e.get("output") or {}).get("data") or []
            except RuntimeError:
                raise
            except Exception:
                _t.sleep(3); continue  # 偶发断流重连(服务端按 session_hash 续传)

    # 2) fn1 预热(装载 pdf 到会话) 3) fn0 按所选 prompt 取模型提示词文本
    st1 = call("load_file_for_preview", [fv, state0])
    state_mid = st1[2] if len(st1) > 2 else None
    d0 = call("update_prompt_and_interactive", [prompt_key, state_mid if state_mid is not None else state0])
    prompt_text = (d0[0] if d0 and isinstance(d0[0], str) else None) or prompt_text0
    log("   dots 预热完成, 推理中(model=%s, prompt=%s)..." % (model, prompt_key))

    # 4) fn6 推理
    out6 = call("_run_inference_and_unlock", [state_mid, None, fv, prompt_key, model, min_px, max_px, fitz, prompt_text])
    zip_url = None
    for o in out6:
        v = o.get("value") if isinstance(o, dict) else None
        if isinstance(v, dict) and str(v.get("path", "")).endswith(".zip"):
            zip_url = v.get("url") or ("/gradio_api/file=" + v["path"]); break
    if not zip_url:
        raise RuntimeError("dots 输出里没有结果 zip")
    raw = _net_retry("dots 结果下载", lambda: opener.open(urllib.request.Request(zip_url if zip_url.startswith("http") else base + zip_url),
                      timeout=300).read())  # 同会话 cookie 才能下载
    os.makedirs(out_dir, exist_ok=True)
    if cfg.get("keep_api_zip"):  # 默认 false 不落盘: 适配器只读解压产物, zip 无读取方(2026-10-30 全量清理验证)
        io.open(os.path.join(out_dir, "api_result.zip"), "wb").write(raw)

    # 5) 解压 + 按页归一化(bbox / 该页 jpg 实际尺寸, 同 paddle 方案免标定)
    import zipfile
    from PIL import Image
    CAT = {"List-item": "Text", "Title": "Section-header", "Figure": "Picture", "Image": "Picture"}
    names = {}
    zf = zipfile.ZipFile(io.BytesIO(raw))
    for n in zf.namelist():
        m = re.match(r".*page_(\d+)\.(json|jpg|md)$", os.path.basename(n))
        if m:
            names.setdefault(int(m.group(1)), {})[m.group(2)] = n
    if not names:
        raise RuntimeError("dots zip 里没有 page_*.json")
    for pi in sorted(names):
        ent = names[pi]
        if "json" not in ent or "jpg" not in ent:
            continue
        raw_txt = zf.read(ent["json"]).decode("utf-8")
        blocks, salvaged = _parse_page_json(raw_txt)
        if salvaged:
            log("   dots 第%d页 JSON 损坏(上游导出bug), 正则抢救 %d 块; 内容仍可能不全, 可删 raw\\dots_api 重跑碰运气" % (pi + 1, len(blocks)))
        elif not blocks and raw_txt.strip() not in ("", "[]"):
            log("   dots 第%d页解析出 0 块(原始 %d 字节), 疑似上游漏页" % (pi + 1, len(raw_txt)))
        W, H = Image.open(io.BytesIO(zf.read(ent["jpg"]))).size
        nb = [{"category": CAT.get(b.get("category"), b.get("category", "Text")),
               "bbox": [round(b["bbox"][0]/W, 4), round(b["bbox"][1]/H, 4),
                        round(b["bbox"][2]/W, 4), round(b["bbox"][3]/H, 4)],
               "text": flatten_content(b.get("text"))} for b in blocks if isinstance(b, dict) and b.get("bbox")]
        io.open(os.path.join(out_dir, "page_%d.json" % pi), "w", encoding="utf-8").write(json.dumps(nb, ensure_ascii=False))
        if "md" in ent:
            io.open(os.path.join(out_dir, "page_%d.md" % pi), "wb").write(zf.read(ent["md"]))
        io.open(os.path.join(out_dir, "page_%d.jpg" % pi), "wb").write(zf.read(ent["jpg"]))
    log("   dots API 完成: %d 页" % len(names))
    return out_dir

def fetch_mineru_api(pdf_path, out_dir):
    """MinerU 云端 API(file-urls/batch 签名上传): 申请上传URL -> http.client 裸PUT(OSS验签不容额外头) -> 轮询batch -> 下载zip解压。
    产物: 标准 MinerU 输出目录(content_list_v2.json 等; api_result.zip 仅 keep_api_zip=true 时保留)。
    需 channels.json 配置 "mineru_api": {"token": "sk-...", ...}。返回产物目录或 None。"""
    try:
        cfg = load_cfg()
    except Exception:
        return None
    meta = cfg.get("mineru_api") or {}
    token = meta.get("token")
    if not token:
        return None
    import ssl, time as _t, http.client, urllib.request
    ctx = ssl._create_unverified_context()
    base = meta.get("base", "https://mineru.net").rstrip("/")
    max_wait = int(meta.get("timeout", 1800))
    AH = {"Authorization": "Bearer " + token}

    def http_json(method, url, payload=None, timeout=120):
        data = json.dumps(payload).encode() if payload is not None else None
        h = dict(AH, **{"Content-Type": "application/json"}) if data else AH
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())

    # 1) 申请上传 URL(参数同官方文档; is_ocr 默认 false, 与桌面版非OCR对齐)
    finfo = {"name": os.path.basename(pdf_path), "is_ocr": bool(meta.get("is_ocr", False))}
    payload = {"files": [finfo], "model_version": meta.get("model_version", "pipeline"),
               "language": meta.get("language", "korean")}
    for k in ("enable_formula", "enable_table"):
        if k in meta:
            payload[k] = bool(meta[k])
    j = _net_retry("mineru 申请上传URL", lambda: http_json("POST", base + "/api/v4/file-urls/batch", payload))
    if j.get("code") != 0:
        raise RuntimeError("申请上传URL失败: %s" % j.get("msg"))
    batch_id, furl = j["data"]["batch_id"], j["data"]["file_urls"][0]

    # 2) 裸 PUT 上传: 签名按空头部计算, urllib 自动补的 Content-Type 等都会导致 OSS SignatureDoesNotMatch
    from urllib.parse import urlparse
    pu = urlparse(furl)
    conn = http.client.HTTPSConnection(pu.netloc, context=ctx, timeout=600)
    conn.request("PUT", pu.path + "?" + pu.query, body=io.open(pdf_path, "rb").read(), headers={})
    resp = conn.getresponse(); resp.read(); conn.close()
    if resp.status not in (200, 201):
        raise RuntimeError("OSS 上传失败 HTTP %d" % resp.status)
    log("   mineru 已上传, batch %s 解析中..." % batch_id)

    # 3) 轮询批量结果(网络抖动不杀渠道: 稀疏日志后重试; 仅"解析中(running)"计时, 排队/等待不计时)
    zip_url = None
    t0 = _t.time()  # 排队总兜底( generous )
    run_cap = int(meta.get("run_timeout", 300))      # 仅 running 状态的累计上限
    queue_cap = int(meta.get("queue_timeout", 7200))
    run_spent = 0.0
    wf0 = None
    last_net = 0.0
    while _t.time() - t0 < queue_cap:
        _t.sleep(6)
        try:
            d = http_json("GET", base + "/api/v4/extract-results/batch/" + batch_id, timeout=60).get("data") or {}
        except Exception as e:
            if _t.time() - last_net > 60:
                last_net = _t.time()
                log("   mineru 状态查询网络错误(重试中): %s" % e)
            continue
        ers = d.get("extract_result") or []
        er = ers[0] if ers else {}
        state = er.get("state")
        prog = er.get("extract_progress") or {}
        log("   mineru %s %s/%s页" % (state, prog.get("extracted_pages"), prog.get("total_pages")))
        if state == "running":
            run_spent += 6
            if run_spent > run_cap:
                raise RuntimeError("mineru 解析超时(running>%ds, 排队/等待时间不计)" % run_cap)
        if state == "waiting-file":
            if wf0 is None:
                wf0 = _t.time()
            elif _t.time() - wf0 > 180:
                raise RuntimeError("mineru 未检测到上传(等待超180s), 该批次作废请重试")  # 平台偶发不扫描, 快速失败省额度
        else:
            wf0 = None
        if state == "done":
            zip_url = er.get("full_zip_url"); break
        if state == "failed":
            raise RuntimeError("mineru 解析失败: %s" % er.get("err_msg"))
    if not zip_url:
        raise RuntimeError("mineru 排队总超时(>%ds)" % queue_cap)

    # 4) 下载 + 解压(标准 MinerU 输出, adapt_mineru 直接可用)
    raw = _net_retry("mineru 结果下载", lambda: urllib.request.urlopen(urllib.request.Request(zip_url), timeout=600, context=ctx).read())
    os.makedirs(out_dir, exist_ok=True)
    if cfg.get("keep_api_zip"):  # 默认 false 不落盘: 适配器只读解压产物, zip 无读取方(2026-10-30 全量清理验证)
        io.open(os.path.join(out_dir, "api_result.zip"), "wb").write(raw)
    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(raw))
    # 解压前清空旧内容: 不清理时, 服务端 batch 复用会叠加旧 uuid run 子目录,
    # 可能冒出同名异内容的 content_list_v2.json 让 adapt_mineru 选错 run(2026-10-30 13563525 实况)
    for old in os.listdir(out_dir):
        p = os.path.join(out_dir, old)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    for n in zf.namelist():
        if ".." in n:
            continue
        zf.extract(n, out_dir)
    log("   mineru API 完成 -> %s" % out_dir)
    return out_dir

# ========== ⑤ 编排主流程 + 报告 ==========
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--mineru"); ap.add_argument("--dots"); ap.add_argument("--paddle")
    ap.add_argument("--out")
    ap.add_argument("--channels", help="备用配置路径: 覆盖模块级 CFG(离线 demo/CI 用, 不叠加 local 与环境变量)")
    ap.add_argument("--no-unify", action="store_true", help="不跑归一化层(unify.py), 只出单渠道+1:3 对照")
    ap.add_argument("--no-merge", action="store_true", help="不出跨渠道合并产物 merged\page_N.json / merged.json / merged.md")
    a = ap.parse_args(argv)
    global CFG, CFG_FP
    CFG = ensure_cfg()   # 惰性配置入位(模块导入时已不再读盘)
    if a.channels:  # 显式 --channels: 全配置读取改走该文件(demo/CI 用), 不叠加 channels.local.json 与环境变量
        CFG_FP = os.path.abspath(a.channels)
        CFG = load_cfg(a.channels, use_env=False, use_local=False)
    doc = re.sub(r"\.pdf$", "", os.path.basename(a.pdf), flags=re.I)
    doc = re.sub(r"[\s]*[（(]\d+[)）]$", "", doc).strip()  # 归一化" (1)"类复制后缀, 防 inbox 匹配失败
    out = a.out or os.path.join(ROOT, "out", doc)
    _start_ms = int(time.time() * 1000)
    t0 = _start_ms / 1000.0  # 供工作台判定"这次真的跑出了新产物"(留 ~2s 时钟粒度余量)
    pages_dir = os.path.join(out, "pages")
    os.makedirs(out, exist_ok=True)

    sizes = render_pages(a.pdf, pages_dir)

    chans = {"mineru": a.mineru, "dots": a.dots, "paddle": a.paddle}
    SRC_KIND = {k: "命令行参数" for k, v in chans.items() if v}  # 渠道来源标注, 末尾汇总一目了然
    found = scan_inbox(doc)
    for k, v in chans.items():
        if not v and k in found:
            chans[k] = found[k]; SRC_KIND[k] = "inbox"
            log("② 渠道[%s] 从 inbox 识别: %s" % (k, found[k]))
    for k, v in try_auto(a.pdf, out).items():
        if v and not chans.get(k):
            chans[k] = v; SRC_KIND[k] = "auto命令"

    # 三路获取并行跑(ThreadPoolExecutor), 主线程收结果: CDP 驱动本地 App 与云端 API 互不阻塞
    from concurrent.futures import ThreadPoolExecutor
    def _mineru_cdp():
        try:
            import mineru_drive
            mu_dir = mineru_drive.drive(a.pdf)   # CDP 驱动桌面版: 启动->上传->等解析
            if mu_dir and os.path.isdir(mu_dir):
                log("② 渠道[mineru] 经 CDP 驱动 App 获取 -> %s" % mu_dir)
                SRC_KIND["mineru"] = "CDP"
                return mu_dir
        except Exception as e:
            log("② mineru CDP 驱动失败(可手动跑 App 走归仓): %s" % e)
        return None
    def _resolve_mineru():
        if not (CFG.get("mineru_api") or {}).get("enabled", True):  # 配置关掉 API 时直落 CDP, 不再先打一次必然失败的请求
            SRC_KIND["mineru"] = "API禁用(转CDP)"
            return _mineru_cdp()
        cached = os.path.join(out, "raw", "mineru_api")
        if any(x.endswith("_content_list_v2.json") for _, _, fs in os.walk(cached) for x in fs):
            log("② 渠道[mineru] 复用缓存: %s" % cached)   # 强制重跑请删除 raw\mineru_api
            SRC_KIND["mineru"] = "API缓存"
            return cached
        try:
            api_dir = fetch_mineru_api(a.pdf, cached)
            if api_dir:
                log("② 渠道[mineru] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["mineru"] = "API新获取"
                return api_dir
        except Exception as e:
            log("② mineru 云端 API 失败, 转 CDP 驱动: %s" % e)
        return _mineru_cdp()
    def _resolve_dots():
        if not (CFG.get("dots_api") or {}).get("enabled", True):
            return None
        cached = os.path.join(out, "raw", "dots_api")
        if os.path.exists(os.path.join(cached, "page_0.json")):
            log("② 渠道[dots] 复用缓存: %s" % cached)   # 复用已有 API 结果; 强制重跑请删除 raw\dots_api
            SRC_KIND["dots"] = "API缓存"
            return cached
        try:
            api_dir = fetch_dots_api(a.pdf, cached)
            if api_dir:
                log("② 渠道[dots] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["dots"] = "API新获取"
                return api_dir
        except Exception as e:
            log("② dots 云端 API 失败: %s" % e)
        return None
    def _resolve_paddle():
        if not (CFG.get("paddle_api") or {}).get("enabled", True):
            return None
        cached = os.path.join(out, "raw", "paddle_api")
        if os.path.exists(os.path.join(cached, "page_0.json")):
            log("② 渠道[paddle] 复用缓存: %s" % cached)  # 复用已有 API 结果, 省配额; 强制重跑请删除 raw\paddle_api
            SRC_KIND["paddle"] = "API缓存"
            return cached
        try:
            api_dir = fetch_paddle_api(a.pdf, cached)
            if api_dir:
                log("② 渠道[paddle] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["paddle"] = "API新获取"
                return api_dir
        except Exception as e:
            log("② paddle 云端 API 失败: %s" % e)
        return None

    _t_fetch = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {}
        if not chans.get("mineru"):
            futs["mineru"] = ex.submit(_resolve_mineru)
        if not chans.get("dots"):
            futs["dots"] = ex.submit(_resolve_dots)
        if not chans.get("paddle"):
            futs["paddle"] = ex.submit(_resolve_paddle)
        for k, f in futs.items():
            v = f.result()
            if v:
                chans[k] = v
    log("② 三路并行获取结束, 共 %.1fs (墙钟)" % (time.time() - _t_fetch))
    log("② 渠道来源汇总: " + " ｜ ".join("%s=%s" % (k, SRC_KIND.get(k, "无")) for k in ("mineru", "dots", "paddle")))

    jobs = [
        ("mineru", "MinerU（本地·非OCR）",  lambda s: adapt_mineru(s, sizes, mineru_calib_key(s),
                                                  (_out_rel(s, out),))),
        ("dots",   "dots.ocr（小红书）",    lambda s: adapt_dots(s, sizes)),
        ("paddle", "PaddleOCR-VL-1.6（在线）", lambda s: adapt_paddle(s, sizes)),
    ]
    report = []
    per_ch = {}
    # 第一遍: 归仓 + 适配(不建查看器)
    for key, title, fn in jobs:
        src = chans.get(key)
        if not src or not os.path.exists(src):
            log("渠道[%s] 无输入，跳过" % key)
            per_ch[key] = None
            report.append((title, "未提供", 0, 0, 0, "-", 0)); continue
        log("③ 处理渠道[%s]: %s" % (key, src))
        try:
            nraw = preserve_raw(src, os.path.join(out, "raw", key))
            if key == "mineru":
                # 标定键按"实际来源类型"显式确定并落盘标记(写入 out 内的源目录与孪生目录), 不再靠调用路径猜
                mu_key = "mineru_api" if str(SRC_KIND.get(key, "")).startswith("API") else "mineru"
                if os.path.abspath(src).startswith(os.path.abspath(out) + os.sep):
                    write_calib_marker(src, mu_key)  # API 缓存目录(孪生已停建, 标记落源目录)
                else:
                    write_calib_marker(os.path.join(out, "raw", key), mu_key)  # 桌面版等外部来源: 标记落归档目录
            res = fn(src)
            per_ch[key] = res
            if res is None:
                report.append((title, "适配失败", 0, 0, 0, "-", nraw)); continue
            if isinstance(res, str):  # md 模式
                hang = len(re.findall(r"[\uac00-\ud7a3]", res))
                report.append((title, "OK(md无块级)", 0, hang, res.count("\ufffd"), "viewer_%s.html" % key, nraw))
            elif len(res) != len(sizes):  # 页数守卫: 防串文档的二次保险
                log("渠道[%s] 页数不匹配(渠道%d页 vs 文档%d页), 疑似其他文档产物, 跳过" % (key, len(res), len(sizes)))
                per_ch[key] = None
                report.append((title, "页数不匹配(%d≠%d)" % (len(res), len(sizes)), 0, 0, 0, "-", nraw)); continue
            else:
                alltext = "".join(b.get("text", "") for lst in res.values() for b in lst)
                hang = len(re.findall(r"[\uac00-\ud7a3]", alltext))
                fffd = alltext.count("\ufffd")
                nblk = sum(len(v) for v in res.values())
                report.append((title, "OK", nblk, hang, fffd, "viewer_%s.html" % key, nraw))
        except Exception as e:
            log("渠道[%s] 处理失败: %s" % (key, e))
            per_ch[key] = None
            report.append((title, "失败: %s" % e, 0, 0, 0, "-", 0))
    # 跨页并入检测依赖跨渠道证据(页首空块 + 另家同区域文本 + 上页页尾悬置/文本被吞), 须全渠道适配完统一标记
    mark_crosspage_merge(per_ch, sizes)
    # 第二遍: 建单渠道查看器(此时 xmerge 标记已就位)
    for key, title, fn in jobs:
        vw = os.path.join(out, "viewer_%s.html" % key)
        src = chans.get(key)
        res = per_ch.get(key)
        if res is None:
            if os.path.exists(vw): os.remove(vw)  # 清掉旧文档的残留查看器
            continue
        if isinstance(res, str):
            build_viewer(vw, "%s ｜ %s" % (doc, title), sizes, md_text=res)
        else:
            build_viewer(vw, "%s ｜ %s" % (doc, title), sizes, per_page=res,
                          calib_note=("%s -> %s" % (key, mineru_calib_key(src))) if key == "mineru" else None,
                          # img 前缀: out 内源(_api 缓存)=basename(src); out 外来源(inbox 等)=归档目录 raw/<ch>(preserve_raw 归仓位)
                          img_prefix=_out_rel(src, out) or ("raw/%s/" % key))

    cmp_fp = os.path.join(out, "viewer_compare.html")
    if any(v is not None for v in per_ch.values()):
        udir = os.path.join(out, "unified")
        # 跨渠道合并产物: 复用 merge_out 的 merge_doc(与独立 CLI 同一实现, 不再要求手工另跑)
        if not a.no_merge:
            try:
                import merge_out
                info = merge_out.merge_doc(doc, out, sizes=sizes, in_memory=per_ch)
                log("④ 合并产物: merged\\page_N.json x%d + merged.json + merged.md (%d 块)" % (info["pages"], info["blocks"]))
            except Exception as e:
                log("④ 合并产物失败(不阻断查看器): %s" % e)
        # 归一化层: 必须"本次"重算。旧版有 unified 目录就直接读 = 随时序错配的陈旧产物(改代码/换文档后 👁 永远看旧结果)
        if not a.no_unify:
            try:
                import unify
                ustat = unify.unify_doc(doc, out)
                log("④ 归一化层: %s" % (ustat if isinstance(ustat, str) else json.dumps(ustat, ensure_ascii=False)[:400]))
            except Exception as e:
                log("④ 归一化层失败(👁 退回临时聚组): %s" % e)
        if os.path.isdir(udir):
            # 本轮没有产出 unified(失败/--no-unify), 但目录里还有上一轮的页文件: 再读进来就是陈旧数据。
            # 这里显式清场, 只认本次写出的一 _unified_summary.json 的时间戳。
            try:
                if not os.path.exists(os.path.join(udir, "_unified_summary.json")) or \
                        os.path.getmtime(os.path.join(udir, "_unified_summary.json")) < t0:
                    shutil.rmtree(udir)
                    log("④ 归一化层无本次产物, 已清掉陈旧 unified/ 目录")
            except Exception as e:
                log("④ 清理陈旧 unified/ 失败: %s" % e)
        upages = None
        unified_driven = False
        if os.path.isdir(udir):  # 本轮 unify 成功才有 unified 产物 -> 👁 改由 unified 层驱动(仅 review=true 出眼)
            upages = {}
            for f in os.listdir(udir):
                m = re.match(r"page_(\d+)\.json$", f)
                if m:
                    upages[int(m.group(1))] = json.load(
                        io.open(os.path.join(udir, f), encoding="utf-8")).get("unified", [])
            unified_driven = True
        nc = build_compare_viewer(cmp_fp, "%s ｜ 1:3 渠道对照" % doc, sizes, per_ch,
                                  {k: (os.path.basename(os.path.normpath(v))
                                       if os.path.abspath(v).startswith(os.path.abspath(out) + os.sep) else k)
                                     for k, v in chans.items() if v},
                                  unified_pages=upages, unified_driven=unified_driven)
        log("④ 1:3 对照查看器: %d 块 -> %s" % (nc, cmp_fp))
    elif os.path.exists(cmp_fp):
        os.remove(cmp_fp)  # 本轮全失败则清掉旧对照, 防残留

    rows = "\n".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % r
        for r in report)
    cmp_cell = '<td><a href="viewer_compare.html">1:3 对照</a></td>' if os.path.exists(cmp_fp) else "<td>-</td>"
    rows = rows.replace("</tr>", cmp_cell + "</tr>")
    idx = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>%s 三渠道对比</title>
<style>body{font-family:"Microsoft YaHei";max-width:1080px;margin:40px auto;background:#f4f5f7;padding:0 16px}
h1{font-size:20px}table{border-collapse:collapse;background:#fff;width:100%%}td,th{border:1px solid #e5e7eb;padding:8px 12px;font-size:14px}
th{background:#eef2ff}a{color:#2563eb}</style></head><body>
<h1>%s — 三渠道解析对比</h1><p>生成时间: %s ｜ 页数: %d ｜ 原文件: %s ｜ 各渠道源文件(md/json)保留在 raw\\ 子目录</p>
<table><tr><th>渠道</th><th>状态</th><th>块数</th><th>韩文字符</th><th>U+FFFD</th><th>查看器</th><th>保留源文件数</th>%s</tr>%s</table>
<p>查看器内悬停/点击区块，左右两栏同步高亮；md 模式渠道无块级联动。<a href="viewer_compare.html">→ 打开 1:3 对照查看器</a></p>
</body></html>""" % (doc, doc, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), len(sizes), os.path.basename(a.pdf),
                     "<th>三合一</th>" if os.path.exists(cmp_fp) else "", rows)
    io.open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(idx)
    log("⑤ 完成 ->", out)
    log("   _elapsed_ms=%d" % (int(time.time() * 1000) - _start_ms))  # 工作台据此判定成功(不看旧 index 的 mtime)
    for r in report: log("   %s | %s | 块 %s | 韩文 %s | FFFD %s | 源文件 %s" % (r[0], r[1], r[2], r[3], r[4], r[6]))



# ---------------- 配置分层: channels.json(入库, 无密钥) -> channels.local.json(不入库) -> 环境变量 ----------------
LOCAL_CFG = os.path.join(ROOT, "channels.local.json")   # 本机覆盖层(不入库); 兼容旧名 CFG_LOCAL
CFG_LOCAL = LOCAL_CFG
CFG_FP = os.path.join(ROOT, "channels.json")   # main(--channels) 可改指向
ENV_KEYS = {("mineru_api", "token"): "MINERU_API_TOKEN", ("paddle_api", "token"): "PADDLE_API_TOKEN"}
_ENV_CACHE = {}


def _apply_env(cfg):
    """③ 环境变量层: cfg[sec][k] = os.environ[ENV_KEYS[(sec,k)]], 只在变量非空时覆盖。"""
    import os as _os
    for (sec, k), env in ENV_KEYS.items():
        v = _os.environ.get(env)
        if v:
            cfg.setdefault(sec, {})[k] = v
    return cfg


def load_cfg(fp=None, use_env=True, use_local=True):
    """三层合并读取: channels.json -> channels.local.json(逐段浅合并, 覆盖同名键) -> 环境变量。
    每次调用都重读, 改标定/token 即生效, 不重启进程。
    fp 显式给出(命令行 --channels / webapp 指定)时视为最终配置: 不再叠加 local 与环境变量,
    避免"离线 demo 意外读到本机真实 token"。"""
    explicit = (fp is not None) or (not use_local)
    fp = fp or globals().get("CFG_FP") or os.path.join(ROOT, "channels.json")
    base = {}
    try:
        base = json.load(io.open(fp, encoding="utf-8"))
    except Exception as e:
        log("配置读取失败 %s: %s" % (fp, e))
        return {}
    if explicit:
        return base
    lp = LOCAL_CFG if fp == os.path.join(ROOT, "channels.json") else os.path.splitext(fp)[0] + ".local.json"
    if os.path.exists(lp):
        try:
            loc = json.load(io.open(lp, encoding="utf-8"))
            for sec, v in loc.items():
                if isinstance(v, dict) and isinstance(base.get(sec), dict):
                    base[sec].update(v)
                else:
                    base[sec] = v
        except Exception as e:
            log("本地覆盖配置读取失败 %s: %s" % (lp, e))
    return _apply_env(base) if use_env else base


def ensure_cfg():
    """模块级 CFG 的惰性初始化/刷新: 旧的"导入时读一次"会让改 channels.json 后的进程一直用旧值。"""
    global CFG
    if CFG is None:
        CFG = load_cfg()
    return CFG

if __name__ == "__main__":
    main()
