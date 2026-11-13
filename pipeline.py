# -*- coding: utf-8 -*-
"""三渠道 OCR 主链路编排。

pipeline_io.py 负责获取/归档/标定/适配，viewer.py 负责查看器与元数据面板。
本模块保留原有函数与常量 re-export，兼容 merge_out/unify/metapy/_attic 的旧 import。
"""
import os, re, io, json, argparse, datetime, time
import pipeline_io as _io
from docid import doc_of
import viewer as _viewer

ROOT = _io.ROOT
INBOX = _io.INBOX
DPI = _io.DPI
CFG = None
LOCAL_CFG = _io.LOCAL_CFG
CFG_LOCAL = _io.CFG_LOCAL
CFG_FP = _io.CFG_FP
ENV_KEYS = _io.ENV_KEYS
_ENV_CACHE = _io._ENV_CACHE

TMAP_MINERU = _io.TMAP_MINERU
TMAP_PADDLE = _io.TMAP_PADDLE
_XTERM = _io._XTERM
NS = _viewer.NS
_TEX_SYM = _viewer._TEX_SYM
_TEX_ONE = _viewer._TEX_ONE
_TEX_FRAC = _viewer._TEX_FRAC
_TEX_BIG = _viewer._TEX_BIG
VIEWER_TMPL = _viewer.VIEWER_TMPL
_TAB_BAD = _viewer._TAB_BAD
_TAB_ON = _viewer._TAB_ON
_BLK_TAG_OK = _viewer._BLK_TAG_OK
_SYM_FOLD = _io._SYM_FOLD
_CMP_DOT = _viewer._CMP_DOT
_CMP_CN = _viewer._CMP_CN
COMPARE_TMPL = _viewer.COMPARE_TMPL
_UNI_CN = _viewer._UNI_CN


def log(*a):
    print("[pipeline %s]" % datetime.datetime.now().strftime("%H:%M:%S"), *a)


render_pages = _io.render_pages
flatten_content = _io.flatten_content
cal = _io.cal
mineru_calib_key = _io.mineru_calib_key
write_calib_marker = _io.write_calib_marker
_net_retry = _io._net_retry
_tail_block = _io._tail_block
mark_crosspage_merge = _io.mark_crosspage_merge
_iou = _io._iou
backfill_empty_blocks = _io.backfill_empty_blocks
adapt_mineru = _io.adapt_mineru
_tag_img = _io._tag_img
_salvage_blocks = _io._salvage_blocks
_parse_page_json = _io._parse_page_json
_load_page_json = _io._load_page_json
adapt_page_jsons = _io.adapt_page_jsons
adapt_dots = _io.adapt_dots
adapt_paddle_json = _io.adapt_paddle_json
adapt_paddle = _io.adapt_paddle
_fold_sym = _io._fold_sym
_gdice = _io._gdice
_cin = _io._cin
scan_inbox = _io.scan_inbox
try_auto = _io.try_auto
_out_rel = _io._out_rel
preserve_raw = _io.preserve_raw
fetch_paddle_api = _io.fetch_paddle_api
fetch_dots_api = _io.fetch_dots_api
fetch_mineru_api = _io.fetch_mineru_api
_apply_env = _io._apply_env

_tex_conv = _viewer._tex_conv
texlite = _viewer.texlite
md2html = _viewer.md2html
_blk_html = _viewer._blk_html
_cmp_preview = _viewer._cmp_preview
_cmp_group_masks = _viewer._cmp_group_masks
_tag = _viewer._tag
_blk_text = _viewer._blk_text
build_viewer = _viewer.build_viewer
_dice_verdict = _viewer._dice_verdict
_token_diff = _viewer._token_diff
_char_diff = _viewer._char_diff
_unified_group_panes = _viewer._unified_group_panes
_meta_cell = _viewer._meta_cell
_meta_overview = _viewer._meta_overview
build_compare_viewer = _viewer.build_compare_viewer


def load_cfg(fp=None, use_env=True, use_local=True):
    return _io.load_cfg(fp, use_env=use_env, use_local=use_local)


def ensure_cfg():
    global CFG
    CFG = _io.ensure_cfg()
    return CFG


def _load_fresh_unified(udir, t0):
    """只加载本轮 fresh unified；旧产物保留在磁盘，但不用于本次查看器。"""
    if not os.path.isdir(udir):
        return None, False
    summary = os.path.join(udir, "_unified_summary.json")
    if not os.path.exists(summary) or os.path.getmtime(summary) < t0:
        log("④ 归一化层无本次产物，保留既有 unified/ 但不用于本次查看器")
        return None, False
    upages = {}
    for f in os.listdir(udir):
        m = re.match(r"page_(\d+)\.json$", f)
        if m:
            with io.open(os.path.join(udir, f), encoding="utf-8") as fh:
                upages[int(m.group(1))] = json.load(fh).get("unified", [])
    return upages, True


def fetch_channels(doc, pdf, out, chans_in, echo=None):
    """② 渠道获取(独立可跑, stages.py 与 main 共用一份实现)。

    返回 (chans, SRC_KIND)。chans_in 里已给的渠道(命令行/inbox 指定)原样保留不再获取;
    其余走 API/缓存/inbox 兜底。产物只落 out/raw/<ch>(_api), 无 PDF 就无法重取。

    MinerU 只走云端 API(2026-11-13 移除桌面版 CDP 驱动): API 失败就是这一路失败,
    不再回落到驱动本地 App。理由见 README「渠道获取」——桌面驱动的成功依赖窗口焦点、
    依赖 App 版本, 且失败方式是"卡住"而不是"报错", 与并发调度天然冲突。
    """
    log_ = echo or log
    chans = dict(chans_in)
    SRC_KIND = {k: "命令行参数" for k, v in chans.items() if v}  # 渠道来源标注, 末尾汇总一目了然
    found = scan_inbox(doc)
    for k, v in chans.items():
        if not v and k in found:
            chans[k] = found[k]; SRC_KIND[k] = "inbox"
            log_("② 渠道[%s] 从 inbox 识别: %s" % (k, found[k]))
    for k, v in try_auto(pdf, out).items():
        if v and not chans.get(k):
            chans[k] = v; SRC_KIND[k] = "auto命令"

    # 三路获取并行跑(ThreadPoolExecutor), 主线程收结果: 三家 API 互不阻塞
    from concurrent.futures import ThreadPoolExecutor
    # 篇级名额: 首跑要真的向三家服务端发起 OCR(分钟级轮询), 5 篇同时上就是 15 条长连接,
    # 服务端限流时 dots/paddle 会静默变 None —— 产物里看不出少了渠道。
    # 缓存命中时这一段秒过, 名额等于没有成本。KCI_API_PARALLEL 可调。
    import crossproc as _xp
    _api_n = int(os.environ.get("KCI_API_PARALLEL") or 3)
    def _resolve_mineru():
        if not (CFG.get("mineru_api") or {}).get("enabled", True):
            SRC_KIND["mineru"] = "API禁用"
            return None
        cached = os.path.join(out, "raw", "mineru_api")
        if any(x.endswith("_content_list_v2.json") for _, _, fs in os.walk(cached) for x in fs):
            log_("② 渠道[mineru] 复用缓存: %s" % cached)   # 强制重跑请删除 raw\mineru_api
            SRC_KIND["mineru"] = "API缓存"
            return cached
        try:
            api_dir = fetch_mineru_api(pdf, cached)
            if api_dir:
                log_("② 渠道[mineru] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["mineru"] = "API新获取"
                return api_dir
        except Exception as e:
            log_("② mineru 云端 API 失败(该路留空, 不回落桌面版): %s" % e)
        return None
    def _resolve_dots():
        if not (CFG.get("dots_api") or {}).get("enabled", True):
            return None
        cached = os.path.join(out, "raw", "dots_api")
        if os.path.exists(os.path.join(cached, "page_0.json")):
            log_("② 渠道[dots] 复用缓存: %s" % cached)   # 复用已有 API 结果; 强制重跑请删除 raw\dots_api
            SRC_KIND["dots"] = "API缓存"
            return cached
        try:
            api_dir = fetch_dots_api(pdf, cached)
            if api_dir:
                log_("② 渠道[dots] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["dots"] = "API新获取"
                return api_dir
        except Exception as e:
            log_("② dots 云端 API 失败: %s" % e)
        return None
    def _resolve_paddle():
        if not (CFG.get("paddle_api") or {}).get("enabled", True):
            return None
        cached = os.path.join(out, "raw", "paddle_api")
        if os.path.exists(os.path.join(cached, "page_0.json")):
            log_("② 渠道[paddle] 复用缓存: %s" % cached)  # 复用已有 API 结果, 省配额; 强制重跑请删除 raw\paddle_api
            SRC_KIND["paddle"] = "API缓存"
            return cached
        try:
            api_dir = fetch_paddle_api(pdf, cached)
            if api_dir:
                log_("② 渠道[paddle] 经云端 API 获取 -> %s" % api_dir)
                SRC_KIND["paddle"] = "API新获取"
                return api_dir
        except Exception as e:
            log_("② paddle 云端 API 失败: %s" % e)
        return None

    _t_fetch = time.time()
    _slot = _xp.Slots("ocr_api", n=_api_n, ttl=3600, wait=3600)
    with _slot:
        if _slot.waited > 1:
            log_("② 渠道获取排队 %.0fs (并发名额 %d 已满)" % (_slot.waited, _api_n))
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
    log_("② 三路并行获取结束, 共 %.1fs (墙钟)" % (time.time() - _t_fetch))
    log_("② 渠道来源汇总: " + " ｜ ".join("%s=%s" % (k, SRC_KIND.get(k, "无")) for k in ("mineru", "dots", "paddle")))
    return chans, SRC_KIND


def write_index(doc, out, report, cmp_fp, sizes, pdf_name):
    """⑤ 结果总览 index.html(独立可跑: 数据来自 report, 由调用方给)。

    report 每行 = (渠道标题, 状态, 块数, 韩文字符, U+FFFD, 查看器, 保留源文件数)。
    """
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
</body></html>""" % (doc, doc, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), len(sizes), pdf_name,
                     "<th>三合一</th>" if os.path.exists(cmp_fp) else "", rows)
    with io.open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(idx)
    return os.path.join(out, "index.html")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--mineru"); ap.add_argument("--dots"); ap.add_argument("--paddle")
    ap.add_argument("--out")
    ap.add_argument("--channels", help="备用配置路径: 覆盖模块级 CFG(离线 demo/CI 用, 不叠加 local 与环境变量)")
    ap.add_argument("--no-unify", action="store_true", help="不跑归一化层(unify.py), 只出单渠道+1:3 对照")
    ap.add_argument("--no-merge", action="store_true", help=r"不出跨渠道合并产物 merged\page_N.json / merged.json / merged.md")
    a = ap.parse_args(argv)
    global CFG, CFG_FP
    _io.CFG_FP = CFG_FP
    CFG = ensure_cfg()   # 惰性配置入位(模块导入时已不再读盘)
    if a.channels:  # 显式 --channels: 全配置读取改走该文件(demo/CI 用), 不叠加 channels.local.json 与环境变量
        CFG_FP = os.path.abspath(a.channels)
        _io.CFG_FP = CFG_FP
        CFG = load_cfg(a.channels, use_env=False, use_local=False)
        _io.CFG = CFG
    doc = doc_of(a.pdf)     # "xxx (1).pdf" -> "xxx"; 归一化复制后缀, 防 inbox 匹配失败
    out = a.out or os.path.join(ROOT, "out", doc)
    _start_ms = int(time.time() * 1000)
    t0 = _start_ms / 1000.0  # 供工作台判定"这次真的跑出了新产物"(留 ~2s 时钟粒度余量)
    pages_dir = os.path.join(out, "pages")
    os.makedirs(out, exist_ok=True)

    sizes = render_pages(a.pdf, pages_dir)

    chans, SRC_KIND = fetch_channels(doc, a.pdf, out,
                                     {"mineru": a.mineru, "dots": a.dots, "paddle": a.paddle})

    jobs = [
        ("mineru", "MinerU（云端 API·非OCR）", lambda s: adapt_mineru(s, sizes, mineru_calib_key(s),
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
                # 标定键按"实际来源类型"显式确定并落盘标记(写入 out 内的源目录与孪生目录), 不再靠调用路径猜。
                # 现在只有 API 来源会新产; 非 API 来源只剩 inbox 投放的历史桌面产物, 仍按 mineru 标定读取。
                mu_key = "mineru_api" if str(SRC_KIND.get(key, "")).startswith("API") else "mineru"
                if os.path.abspath(src).startswith(os.path.abspath(out) + os.sep):
                    write_calib_marker(src, mu_key)  # API 缓存目录(孪生已停建, 标记落源目录)
                else:
                    write_calib_marker(os.path.join(out, "raw", key), mu_key)  # inbox 等外部来源: 标记落归档目录
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
        # 归一化层必须本轮重算；失败/--no-unify 时保留旧数据，但不把旧数据用于本次查看器。
        if not a.no_unify:
            try:
                import unify
                ustat = unify.unify_doc(doc, os.path.dirname(os.path.abspath(out)))
                log("④ 归一化层: %s" % (ustat if isinstance(ustat, str) else json.dumps(ustat, ensure_ascii=False)[:400]))
            except Exception as e:
                log("④ 归一化层失败(👁 退回临时聚组): %s" % e)
        upages, unified_driven = _load_fresh_unified(udir, t0)
        # ④ 中间查看器也带 metadata: 本轮 ⑥ 尚未跑, 挂盘上上一轮的总览——重跑中途刷新页面不再"总览消失"
        meta4 = None
        mfp4 = os.path.join(out, "metadata.json")
        if os.path.exists(mfp4):
            try:
                meta4 = json.load(io.open(mfp4, encoding="utf-8"))
            except Exception:
                meta4 = None
        nc = build_compare_viewer(cmp_fp, "%s ｜ 1:3 渠道对照" % doc, sizes, per_ch,
                                  {k: (os.path.basename(os.path.normpath(v))
                                       if os.path.abspath(v).startswith(os.path.abspath(out) + os.sep) else k)
                                     for k, v in chans.items() if v},
                                  unified_pages=upages, unified_driven=unified_driven,
                                  meta=meta4)
        log("④ 1:3 对照查看器: %d 块 -> %s" % (nc, cmp_fp))
    elif os.path.exists(cmp_fp):
        os.remove(cmp_fp)  # 本轮全失败则清掉旧对照, 防残留

    # ⑥ 后链(2026-11-11 接线): 元数据阶段链 → ner_lite → 终版对照查看器(带元数据带+实体面板)。
    # 放一篇新 PDF 即全自动产出全部产物; webapp 拖入走同一 main, 一处接线两端生效。
    # 各步失败只记日志不阻断; ner_lite 烧 LLM token, 开关 channels.json llm.ner_lite_auto(默认开)+KCI_LLM_KEY。
    _out_root = os.path.dirname(os.path.abspath(out))
    try:
        from metapy import (m3_doc, refs_doc, frefs_doc, front_doc,
                            abstract_doc, issue_doc, cspan_doc)
        for _name, _fn in ((u"m3", m3_doc), (u"refs", refs_doc), (u"frefs", frefs_doc),
                           (u"front", front_doc), (u"abs", abstract_doc),
                           (u"issue", issue_doc), (u"cspan", cspan_doc)):
            try:
                _fn(doc, _out_root)
                log(u"⑥ 元数据 %s 完成" % _name)
            except Exception as e:
                log(u"⑥ 元数据 %s 失败(不阻断): %s" % (_name, str(e)[:120]))
    except Exception as e:
        log(u"⑥ 元数据链导入失败(不阻断): %s" % str(e)[:120])
    _llm_cfg = (load_cfg().get("llm") or {})
    # KCI_SKIP_NER=1: 本次调用跳过 ner 阶段(GUI "快速重跑" 用; 元数据/查看器照出, 只是不烧 LLM)
    if _llm_cfg.get("ner_lite_auto", True) and os.environ.get("KCI_LLM_KEY") \
            and not os.environ.get("KCI_SKIP_NER"):
        try:
            import ner_lite as _nl
            _model = _nl.make_model(_llm_cfg)
            _product, _audit = _nl.run_doc(doc, _model, echo=lambda s: log(u"  ner_lite| %s" % s))
            _nl.write_outputs(doc, _product, _audit)
            log(u"⑥ ner_lite: 提名 %d 准入 %d 记录 %d" % (
                _audit["counts"]["nominations"], _audit["counts"]["admitted"],
                _audit["counts"]["records"]))
        except Exception as e:
            log(u"⑥ ner_lite 失败(不阻断): %s" % str(e)[:150])
    else:
        log(u"⑥ ner_lite 跳过(ner_lite_auto 关 / KCI_LLM_KEY 缺 / 本次跳 NER)")
    try:
        from metapy.llmrep import rebuild_viewer
        rebuild_viewer(doc, _out_root)
        log(u"⑥ 终版对照查看器已重建(元数据带+实体面板)")
    except Exception as e:
        log(u"⑥ 终版查看器重建失败(不阻断): %s" % str(e)[:120])

    write_index(doc, out, report, cmp_fp, sizes, os.path.basename(a.pdf))
    log("⑤ 完成 ->", out)
    log("   _elapsed_ms=%d" % (int(time.time() * 1000) - _start_ms))  # 工作台据此判定成功(不看旧 index 的 mtime)
    for r in report: log("   %s | %s | 块 %s | 韩文 %s | FFFD %s | 源文件 %s" % (r[0], r[1], r[2], r[3], r[4], r[6]))



if __name__ == "__main__":
    main()
