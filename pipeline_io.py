# -*- coding: utf-8 -*-
"""渠道获取、源文件归档、坐标标定与 OCR 结果适配。

pipeline.py 保留同名 re-export；log 在运行时转发到 pipeline.log，兼容工作台日志接管。
"""
import os, re, io, json, shutil, subprocess, datetime, time, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(ROOT, "inbox")
CFG = None
DPI = 150
TMAP_MINERU = {"title":"Section-header","text":"Text","paragraph":"Text","list":"Text","image":"Picture","chart":"Picture",
               "image_caption":"Caption","page_header":"Page-header","page_footer":"Page-footer",
               "page_footnote":"Footnote","page_number":"Page-number","table":"Table","table_caption":"Caption","equation":"Formula"}

def log(*a):
    main = sys.modules.get("pipeline")
    if main is not None and hasattr(main, "log"):
        return main.log(*a)
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
    退回目录名启发式(basename == "mineru_api" -> mineru_api), 最终默认 mineru(历史桌面版)。
    桌面驱动已移除, 默认分支现在只服务 inbox 投放的旧桌面产物; 新产物一律 mineru_api。
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


def adapt_mineru(path, sizes, cfg_key="mineru", extra_prefixes=None):
    """MinerU content_list_v2.json: bbox 在其渲染像素空间(与页图无关的独立坐标系, 且横纵非等比)。
    用 channels.json 的 cfg_key 段标定映射到 150dpi 页图像素: x*W/src_w+x_off, y*H/src_h+y_off;
    该段缺失时退回 cal() 默认 1241x1754、零偏移。
    目录可为多级: 递归找 *_content_list_v2.json, 优先取 _ocr_enable=false 的非OCR运行。
    extra_prefixes: img 相对路径前缀(按实际适配源目录给, 元组或字符串; None/空 → raw/mineru/ 归档位),
    与 merge_out 调用同一口径, 不再全局硬编码 raw/<ch>/。"""
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
    """块内 img 目前是引擎目录内相对路径; 查看器/合并器要的是相对 out/<doc>/ 的路径。
    前缀由调用方按"实际适配源目录 basename"给出(_api 缓存 vs inbox 归档不同), 不再全局硬编码 raw/<ch>/。
    prefixes 兼容元组(取[0])与裸字符串; None/空 → raw/mineru/(out 外来源走 preserve_raw 归档位)。"""
    if isinstance(prefixes, str):
        prefix = prefixes
    else:
        prefix = prefixes[0] if prefixes else None
    prefix = prefix or "raw/mineru/"
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
    src 本身已是 raw/<渠道>_api(API 缓存目录)时跳过孪生复制——全读者(merge_out 等, 含 _attic 归档的
    analyze/caption_audit)均有 _api 兜底, 不再产生双份; 只有 out 之外的真实外部来源(inbox 导出等)才归档复制。"""
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
    需 channels.json 配置 "paddle_api": {"token": "...", "model": "PaddleOCR-VL-1.6"}。返回产物目录或 None。"""
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
    model = meta.get("model", "PaddleOCR-VL-1.6")
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

    # 1) 申请上传 URL(参数同官方文档; is_ocr 默认 false, 即非OCR)
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
        with io.open(fp, encoding="utf-8") as fh:
            base = json.load(fh)
    except Exception as e:
        log("配置读取失败 %s: %s" % (fp, e))
        return {}
    if explicit:
        return base
    lp = LOCAL_CFG if fp == os.path.join(ROOT, "channels.json") else os.path.splitext(fp)[0] + ".local.json"
    if os.path.exists(lp):
        try:
            with io.open(lp, encoding="utf-8") as fh:
                loc = json.load(fh)
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
