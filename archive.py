# -*- coding: utf-8 -*-
"""文档归档: 把已确认的文献移出工作区, 并作为"送入处理"前的判重凭据。

为什么需要(2026-11-13 用户提出)
--------------------------------
1. 工作台只该显示"还要处理的"文档。确认过的会一直占着列表, 每次刷新都要重算一遍质检。
2. webapp 的 `watch_uploads()` 只要在 uploads 看到 PDF 就自动开工; 已归档的文档若不拦,
   重新下载一次 = 白跑一遍 OCR + LLM。所以归档必须同时是**判重凭据**, 不只是搬文件。

物理形态
--------
    archive/index.json          权威索引(唯一判重凭据 + 元数据快照)
    archive/<doc>/source.pdf    原 PDF(改名固定, 便于恢复)
    archive/<doc>/out/          原 out/<doc>/ 全部产物, 原样保留

搬移而非删除: 同卷 rename 是瞬时的, 恢复就是搬回来。`index.json` 存的是**元数据快照**而不是
路径——将来若清理了大件(pages/raw), 索引仍是"这篇我核对过、题录是什么、哪几项缺"的唯一凭据,
也是导出"已确认文献清单"的数据源。

三层判重(精度优先, 处置分级)
----------------------------
    hash  同 sha256                 -> 硬拦, 确证同一份文件
    docid 归一化文件名相同, hash 异 -> 默认拦, 疑似重新下载的版本(可强制放行)
    title 标题/文件名折叠相似        -> 只提示, 可能是同篇另一版, 也可能只是撞名

为什么第三层不拦: 误拦一篇新文章, 用户会以为程序坏了; 漏拦一次重复, 只多花几分钟。
两个方向的代价不对称, 所以只给确证的判据拦人。
"""
import csv
import datetime
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata

import qc
from docid import doc_of

__all__ = ["ARCH_DIR", "INDEX_FP", "norm_doc", "load_index", "save_index",
           "archived_set", "is_archived", "check_pdf", "archive_docs",
           "restore_doc", "list_items", "export_markdown", "export_csv", "item_of"]

ROOT = os.path.dirname(os.path.abspath(__file__))
ARCH_DIR = os.path.join(ROOT, "archive")
INDEX_FP = os.path.join(ARCH_DIR, "index.json")
UP_DIR = os.path.join(ROOT, "uploads")
OUT_DIR = os.path.join(ROOT, "out")

VERSION = 2               # 1: 只有 doc/path; 2: 加元数据快照(qc/题录/hash)
TITLE_HIT = 0.90          # 标题折叠相似度门槛: 0.90 以下同域标题撞车太多, 会变成噪音
TITLE_MIN_LEN = 8         # 太短的标题(如 "서론")折叠后不足以判相似

_CACHE = {"fp": None, "mtime": None, "items": {}}


# ---------------------------------------------------------------- 基础工具
def norm_doc(name):
    """文件名 -> doc id。实现收在 docid.py, 与 GUI / webapp / CLI 同一份。"""
    return doc_of(name)


def _fold(s):
    """匹配口径折叠: NFKC + 小写 + 去空白与标点。只用于判重比较, 不改任何产品字段。

    不 import metapy.common._foldv: 那条链会拉进 pipeline/unify(重依赖), 归档要在纯 CLI
    环境下也能跑。判重阈值是本模块私有的, 与抽取器口径无耦合。
    """
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[\s\W_]+", "", s)


def _sha256(fp, chunk=1 << 20):
    h = hashlib.sha256()
    with io.open(fp, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _arch_root(arch_root=None):
    return arch_root or ARCH_DIR


def _index_fp(arch_root=None):
    return INDEX_FP if not arch_root else os.path.join(arch_root, "index.json")


# ---------------------------------------------------------------- 索引读写
def load_index(arch_root=None):
    """读索引。文件损坏时**抛异常, 不静默重建**——重建等于把归档记录抹掉。

    小文件每 2 秒被 watch 读一次, 所以按 mtime 命中缓存, 稳态下不重复解析。
    """
    fp = _index_fp(arch_root)
    if not os.path.isfile(fp):
        return {"version": VERSION, "updated_at": None, "items": {}}
    mt = os.path.getmtime(fp)
    if _CACHE["fp"] == fp and _CACHE["mtime"] == mt and _CACHE["items"]:
        return {"version": VERSION, "updated_at": _CACHE.get("updated_at"),
                "items": _CACHE["items"]}
    try:
        with io.open(fp, encoding="utf-8") as fh:
            j = json.load(fh) or {}
    except Exception as e:
        raise ValueError("归档索引损坏: %s (%s)" % (fp, e))
    if not isinstance(j.get("items"), dict):
        raise ValueError("归档索引格式不对(items 不是字典): %s" % fp)
    j.setdefault("version", VERSION)
    j.setdefault("updated_at", None)
    _CACHE.update({"fp": fp, "mtime": mt, "items": j["items"],
                   "updated_at": j.get("updated_at")})
    return j


def save_index(idx, arch_root=None):
    """原子写 + 保留上一版为 .bak(误操作能救一次)。"""
    root = _arch_root(arch_root)
    if not os.path.isdir(root):
        os.makedirs(root)
    fp = _index_fp(arch_root)
    idx["version"] = VERSION
    idx["updated_at"] = _now()
    tmp = fp + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True))
    if os.path.isfile(fp):
        try:
            os.replace(fp, fp + ".bak")
        except OSError:
            pass
    os.replace(tmp, fp)
    _CACHE.update({"fp": fp, "mtime": os.path.getmtime(fp),
                   "items": idx["items"], "updated_at": idx.get("updated_at")})
    return fp


def archived_set(arch_root=None):
    try:
        return set(load_index(arch_root)["items"].keys())
    except ValueError:
        return set()


def is_archived(doc, arch_root=None):
    return norm_doc(doc) in archived_set(arch_root)


def item_of(doc, arch_root=None):
    return load_index(arch_root)["items"].get(norm_doc(doc))


# ---------------------------------------------------------------- 判重
def _title_ratio(a, b):
    x, y = _fold(a), _fold(b)
    if not x or not y or min(len(x), len(y)) < TITLE_MIN_LEN:
        return 0.0
    if x == y:
        return 1.0
    # 截断/加副标题: 按标题命名的文件常被文件系统截断(Windows 长名), 或用户只打了前半段,
    # 此时整条标题的序列相似度会掉到 0.85 上下——但内容确信是同篇。短串是长串子串且占比
    # >=70% 时按完全命中处理(2026-11-13 演练实证: 韩文标题截 40 字后 ratio 0.89 漏判)。
    short, lng = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) >= 12 and short in lng and len(short) / float(len(lng)) >= 0.70:
        return 1.0
    return difflib.SequenceMatcher(None, x, y).ratio()


def check_pdf(pdf_path, meta=None, arch_root=None, name_hint=None):
    """送入处理前的判重。返回命中列表(空 = 干净), 每项 {level, doc, reason, item}。

    level: "hash" 硬拦 / "docid" 默认拦 / "title" 只提示。
    meta 可传已解析的 metadata(有 title/journal/year 时判得更准); 送入前通常只有文件名,
    此时第三层退化为"文件名 vs 归档标题"——用标题命名的下载文件仍能命中。
    name_hint: 上传落盘时会先写成 `xxx.pdf.part`, 那种名字 norm_doc 算不对, 用它传原始文件名。
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        return []
    doc = norm_doc(name_hint or pdf_path)
    try:
        digest = _sha256(pdf_path)
    except OSError:
        digest = None
    size = os.path.getsize(pdf_path)
    items = load_index(arch_root)["items"]
    hits = []

    for d, it in items.items():
        if digest and it.get("sha256") == digest:
            hits.append({"level": "hash", "doc": d, "item": it,
                         "reason": "同一份文件(sha256 相同), 归档于 %s" % (it.get("archived_at") or "?")})
            continue
        if norm_doc(d) == doc:
            hits.append({"level": "docid", "doc": d, "item": it,
                         "reason": "同名不同文件(疑似重新下载的版本), 归档于 %s" % (it.get("archived_at") or "?")})
            continue
        # 第三层: 候选标题。两个细节都是实证来的:
        # ① 必须用 basename——传全路径会把上级目录名折进比较;
        # ② 必须去扩展名——".pdf" 折成 3 个字母挂在末尾, 韩文标题下足以把 ratio 从 1.0 压到 0.85。
        cands = [re.sub(r"\.pdf$", "", os.path.basename(pdf_path), flags=re.I),
                 (meta or {}).get("title") or ""]
        for cand in cands:
            best, which = 0.0, None
            for t in (it.get("title"), it.get("title_en")):
                if not t:
                    continue
                r = _title_ratio(cand, t)
                if r > best:
                    best, which = r, t
            if best >= TITLE_HIT:
                # 同年同刊时置信度更高; 至少要有标题相似这一条硬信号
                year_ok = (meta or {}).get("year") and str((meta or {}).get("year")) == str(it.get("year"))
                hits.append({"level": "title", "doc": d, "item": it,
                             "reason": "标题相似 %.2f%s: %s" % (
                                 best, "(同年)" if year_ok else "", (which or "")[:48])})
                break
    # hash 命中优先, 其余按 docid > title
    order = {"hash": 0, "docid": 1, "title": 2}
    hits.sort(key=lambda h: order.get(h["level"], 9))
    return hits


def is_duplicate(pdf_path, meta=None, arch_root=None, name_hint=None):
    """是否应拦截(硬拦或默认拦)。title 级只是提示, 不算重复。"""
    return [h for h in check_pdf(pdf_path, meta, arch_root, name_hint)
            if h["level"] in ("hash", "docid")]


# ---------------------------------------------------------------- 归档
def _snapshot(doc, dd):
    """搬移前采集: 题录快照 + 质检 + 后验(有就跑)。搬走之后就再也读不到了。"""
    m = {}
    mfp = os.path.join(dd, "metadata.json")
    if os.path.isfile(mfp):
        try:
            m = json.load(io.open(mfp, encoding="utf-8")) or {}
        except Exception:
            m = {}

    def val(k):
        v = m.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, list):
            return [str(x) for x in v if x not in (None, "")]
        return None if v in (None, "", [], {}) else str(v)

    aus = []
    for a in (m.get("authors") or []):
        if isinstance(a, dict):
            aus.append(str(a.get("name") or ""))
        else:
            aus.append(str(a))
    affs = [str(x) for x in (m.get("affiliations") or []) if x]

    q = qc.qc_of(doc, out_dir=os.path.dirname(dd.rstrip("/\\")))
    return {
        "title": val("title"), "title_en": val("title_en"),
        "journal": val("journal"), "year": val("year"),
        "volume": val("volume"), "issue": val("issue"), "pages": val("pages"),
        "authors": [x for x in aus if x], "affiliations": affs,
        "qc_level": q.get("level") or "", "qc_issues": list(q.get("issues") or []),
    }


def _move_path(src, dst):
    """优先 rename(同卷瞬时); 跨卷/被占用时退回复制+删除。目标已存在则抛异常(绝不覆盖)。"""
    if os.path.exists(dst):
        raise OSError("目标已存在: %s" % dst)
    parent = os.path.dirname(dst)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    try:
        os.rename(src, dst)
    except OSError:
        shutil.move(src, dst)


def find_pdfs(doc, up_root=None):
    """uploads 里所有归一后等于 doc 的 PDF(含 `xxx（1）.pdf` 这类重复下载)。"""
    up = up_root or UP_DIR
    out = []
    if not os.path.isdir(up):
        return out
    for f in sorted(os.listdir(up)):
        if f.lower().endswith(".pdf") and norm_doc(f) == norm_doc(doc):
            out.append(os.path.join(up, f))
    return out


def archive_docs(docs, note="", out_root=None, up_root=None, arch_root=None,
                 meta_note=None):
    """归档一批文档。返回 {ok: [doc], skipped: [(doc, 原因)], warnings: [(doc, 原因)]}。

    不删任何东西: PDF 与 out/ 产物都是移动。同一 doc 命中多个 PDF 时只移第一个,
    其余原样留在 uploads 并记 warning(可能是用户自己下载了两份, 不该替他删)。

    meta_note: {doc: 单篇批注}, 覆盖 note。
    """
    out_root = out_root or OUT_DIR
    up_root = up_root or UP_DIR
    idx = load_index(arch_root)
    items = idx["items"]
    res = {"ok": [], "skipped": [], "warnings": []}
    meta_note = meta_note or {}

    for raw in docs:
        doc = norm_doc(raw)
        if not doc:
            res["skipped"].append((str(raw), "doc 名为空"))
            continue
        if doc in items:
            res["skipped"].append((doc, "已在归档中(%s)" % (items[doc].get("archived_at") or "?")))
            continue

        dd = os.path.join(out_root, doc)
        has_out = os.path.isdir(dd)
        pdfs = find_pdfs(doc, up_root)
        if not has_out and not pdfs:
            res["skipped"].append((doc, "out 无产物且 uploads 无 PDF"))
            continue

        dst = os.path.join(_arch_root(arch_root), doc)
        if os.path.exists(dst):
            res["skipped"].append((doc, "archive/%s 已存在(残留目录, 需手工处理)" % doc))
            continue

        snap = _snapshot(doc, dd) if has_out else {}
        digest = None
        for p in pdfs:
            try:
                digest = _sha256(p)
                size = os.path.getsize(p)
                break
            except OSError:
                continue
        else:
            size = None

        try:
            os.makedirs(dst)
            if has_out:
                _move_path(dd, os.path.join(dst, "out"))
            pdf_name = None
            if pdfs:
                src_pdf = pdfs[0]
                pdf_name = os.path.basename(src_pdf)
                _move_path(src_pdf, os.path.join(dst, "source.pdf"))
                if len(pdfs) > 1:
                    res["warnings"].append(
                        (doc, "uploads 里还有 %d 个同名 PDF 未动: %s"
                         % (len(pdfs) - 1, ", ".join(os.path.basename(x) for x in pdfs[1:]))))
        except Exception as e:
            # 回滚: 已搬进去的挪回原位, 别留半截状态
            try:
                if os.path.isdir(os.path.join(dst, "out")) and not os.path.isdir(dd):
                    _move_path(os.path.join(dst, "out"), dd)
                sp = os.path.join(dst, "source.pdf")
                if os.path.isfile(sp) and pdfs:
                    _move_path(sp, pdfs[0])
                shutil.rmtree(dst, ignore_errors=True)
            except Exception:
                pass
            res["skipped"].append((doc, "搬移失败: %s" % e))
            continue

        items[doc] = {
            "doc": doc, "archived_at": _now(), "note": meta_note.get(doc, note or ""),
            "sha256": digest, "size": size, "pdf_name": pdf_name,
            "has_out": has_out,
        }
        items[doc].update(snap)
        res["ok"].append(doc)

    if res["ok"]:
        save_index(idx, arch_root)
    return res


def restore_doc(doc, out_root=None, up_root=None, arch_root=None, keep_note=False):
    """撤销归档: 产物搬回 out/, PDF 搬回 uploads/ 用原名, 并从索引删除。返回 (ok, message)。"""
    out_root = out_root or OUT_DIR
    up_root = up_root or UP_DIR
    doc = norm_doc(doc)
    idx = load_index(arch_root)
    it = idx["items"].get(doc)
    if not it:
        return False, "不在归档中"
    src = os.path.join(_arch_root(arch_root), doc)
    if not os.path.isdir(src):
        return False, "archive/%s 目录不存在(索引与磁盘不一致)" % doc
    dst_out = os.path.join(out_root, doc)
    if os.path.isdir(dst_out):
        return False, "out/%s 已存在, 拒绝覆盖" % doc
    pdf_src = os.path.join(src, "source.pdf")
    pdf_name = it.get("pdf_name") or (doc + ".pdf")
    pdf_dst = os.path.join(up_root, pdf_name)
    if os.path.isfile(pdf_src) and os.path.exists(pdf_dst):
        return False, "uploads/%s 已存在, 拒绝覆盖" % pdf_name

    src_out = os.path.join(src, "out")
    if os.path.isdir(src_out):
        _move_path(src_out, dst_out)
    if os.path.isfile(pdf_src):
        if not os.path.isdir(up_root):
            os.makedirs(up_root)
        _move_path(pdf_src, pdf_dst)
    shutil.rmtree(src, ignore_errors=True)

    idx["items"].pop(doc, None)
    save_index(idx, arch_root)
    qc.clear_cache(doc)
    msg = "已恢复到工作区" + ("(含 PDF)" if os.path.isfile(pdf_dst) else "(无 PDF, 仅产物)")
    if keep_note and it.get("note"):
        msg += " · 归档批注: %s" % it["note"]
    return True, msg


# ---------------------------------------------------------------- 展示/导出
def list_items(arch_root=None, q=""):
    """按归档时间倒序; q 在 doc/标题/刊名/作者里做子串匹配。"""
    items = list(load_index(arch_root)["items"].values())
    if q:
        fq = _fold(q)

        def hit(it):
            blob = " ".join(str(it.get(k) or "") for k in
                            ("doc", "title", "title_en", "journal", "note"))
            blob += " " + " ".join(it.get("authors") or [])
            return fq in _fold(blob)
        items = [it for it in items if hit(it)]
    items.sort(key=lambda it: it.get("archived_at") or "", reverse=True)
    return items


def export_markdown(items):
    """已确认文献清单: 可直接贴进交付文档/参考文献附录。"""
    L = ["# 已归档文献清单", "",
         "共 %d 篇 · 导出 %s" % (len(items), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")), "",
         "| # | 标题 | 作者 | 刊名 | 年 | 卷(期) | 页码 | 归档时间 | 备注 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        def c(v):
            return str(v or "").replace("|", "\\|").replace("\n", " ")
        vol = c(it.get("volume"))
        if it.get("issue"):
            vol += "(%s)" % it["issue"]
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, c(it.get("title")), c(", ".join(it.get("authors") or [])),
            c(it.get("journal")), c(it.get("year")), vol, c(it.get("pages")),
            c((it.get("archived_at") or "").replace("T", " ")[:16]), c(it.get("note"))))
    return "\n".join(L) + "\n"


def export_csv(items):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["doc", "标题", "作者", "刊名", "年", "卷", "期", "页码",
                "质检", "缺项", "归档时间", "备注", "sha256"])
    for it in items:
        w.writerow([it.get("doc"), it.get("title"), ", ".join(it.get("authors") or []),
                    it.get("journal"), it.get("year"), it.get("volume"), it.get("issue"),
                    it.get("pages"),
                    "通过" if not it.get("qc_issues") else "缺 " + "/".join(it.get("qc_issues") or []),
                    "/".join(it.get("qc_issues") or []),
                    (it.get("archived_at") or "").replace("T", " "),
                    it.get("note"), it.get("sha256")])
    return buf.getvalue()


# ---------------------------------------------------------------- 自检
def _selftest():
    """在临时目录里验三层判重与搬移/恢复。跑完自清理, 不碰真实 archive/。"""
    import tempfile
    ok = fail = 0
    # 自检用的长标题: 必须足够长, 否则"截 80% + .pdf"仍能靠 ratio 滑过 0.90, 测不出扩展名缺陷
    TITLE = "A Study of Test Archiving Behaviour and Its Long-Term Effects on Korean Academic Journals"
    tmp = tempfile.mkdtemp(prefix="arch_st_")
    outs, ups, arch = (os.path.join(tmp, x) for x in ("out", "uploads", "archive"))
    for d in (outs, ups, arch):
        os.makedirs(d)

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("  FAIL %s %s" % (name, extra))

    try:
        # 造一篇: out/<doc>/metadata.json + uploads/<doc>.pdf
        doc = "KCI_FI900000001_2020"
        dd = os.path.join(outs, doc)
        os.makedirs(dd)
        with io.open(os.path.join(dd, "metadata.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": {"value": "A Study of Test Archiving Behaviour and Its Long-Term Effects on Korean Academic Journals"},
                       "journal": {"value": "Journal of Tests"}, "year": {"value": "2020"},
                       "volume": {"value": "12"}, "issue": {"value": "3"},
                       "pages": {"value": "1-20"},
                       "authors": [{"name": "Kim Test"}], "affiliations": ["Test University"],
                       "references": ["ref"]}, fh)
        pdf = os.path.join(ups, doc + ".pdf")
        with io.open(pdf, "wb") as fh:
            fh.write(b"%PDF-1.4 test payload\n")

        # norm_doc 归一(与 gui.doc_of 一致)
        chk("norm_doc 去(1)",
            norm_doc("KCI_FI900000001_2020（1）.pdf") == doc)
        chk("norm_doc 大小写", norm_doc("X.PDF") == "X")
        # 标题截断: 按标题命名的文件常被文件系统截断, 整串 ratio 会掉到 0.85 上下,
        # 短串是长串前缀且占比 >=70% 时必须按命中处理(阈值 0.70 就是这条的分界)
        _t = "A Study of Test Archiving Behaviour in Korean Academic Journals"
        _cut = _t[:int(len(_t) * 0.8)]
        chk("标题截断仍命中", _title_ratio(_cut, _t) >= TITLE_HIT,
            "%r -> %.3f" % (_cut[-12:], _title_ratio(_cut, _t)))
        chk("截太短不硬判", _title_ratio(_t[:int(len(_t) * 0.5)], _t) < TITLE_HIT)
        chk("无关标题不命中", _title_ratio("Particle Physics in the LHC Era", _t) < TITLE_HIT)

        # 归档
        r = archive_docs([doc], note="单测批注", out_root=outs, up_root=ups, arch_root=arch)
        chk("归档成功", r["ok"] == [doc], r)
        chk("out 已移走", not os.path.isdir(dd))
        chk("产物落位", os.path.isdir(os.path.join(arch, doc, "out")))
        chk("PDF 落位", os.path.isfile(os.path.join(arch, doc, "source.pdf")))
        chk("uploads 已空", not os.path.isfile(pdf))
        it = item_of(doc, arch) or {}
        chk("题录快照", (it.get("title") or "").startswith("A Study of Test Archiving"), it.get("title"))
        chk("批注落库", it.get("note") == "单测批注", it.get("note"))
        chk("hash 落库", bool(it.get("sha256")))
        chk("索引已写", os.path.isfile(os.path.join(arch, "index.json")))
        chk("is_archived", is_archived(doc, arch))

        # 三层判重
        # ① hash: 把同一份字节换个名字放回去
        p2 = os.path.join(ups, "totally_new_name.pdf")
        shutil.copyfile(os.path.join(arch, doc, "source.pdf"), p2)
        h = check_pdf(p2, arch_root=arch)
        chk("hash 命中", h and h[0]["level"] == "hash", h)
        chk("hash 算拦截", bool(is_duplicate(p2, arch_root=arch)))

        # ② docid: 同名但内容不同
        p3 = os.path.join(ups, doc + ".pdf")
        with io.open(p3, "wb") as fh:
            fh.write(b"%PDF-1.4 different bytes\n")
        h3 = check_pdf(p3, arch_root=arch)
        chk("docid 命中", any(x["level"] == "docid" for x in h3), h3)
        chk("docid 算拦截", bool(is_duplicate(p3, arch_root=arch)))

        # ③ title: 用标题当文件名, 内容全新
        # 用"标题前 80% + .pdf": 这正是用户按标题存盘、又被文件系统截断的形态。
        # 修复前曾因把 ".pdf" 折进比较, 韩文/长英文标题下 ratio 掉到 0.85 漏判。
        p4 = os.path.join(ups, (TITLE[:int(len(TITLE) * 0.8)]).strip() + ".pdf")
        with io.open(p4, "wb") as fh:
            fh.write(b"%PDF-1.4 third payload\n")
        h4 = check_pdf(p4, arch_root=arch)
        chk("title 命中", any(x["level"] == "title" for x in h4), h4)
        chk("title 不拦", not is_duplicate(p4, arch_root=arch), h4)

        # 干净文件不命中
        p5 = os.path.join(ups, "unrelated_paper.pdf")
        with io.open(p5, "wb") as fh:
            fh.write(b"%PDF-1.4 unrelated\n")
        chk("干净不命中", check_pdf(p5, arch_root=arch) == [])

        # 重复归档 = 幂等跳过
        r2 = archive_docs([doc], out_root=outs, up_root=ups, arch_root=arch)
        chk("幂等跳过", not r2["ok"] and r2["skipped"], r2)

        # 恢复
        for junk in (p2, p3, p4, p5):     # 判重演练造的 PDF 先清掉, 否则恢复会被"同名已存在"正确拦下
            if os.path.isfile(junk):
                os.remove(junk)
        okr, msg = restore_doc(doc, out_root=outs, up_root=ups, arch_root=arch)
        chk("恢复成功", okr, msg)
        chk("产物回位", os.path.isdir(dd))
        chk("PDF 回位", os.path.isfile(pdf))
        chk("索引已清", not is_archived(doc, arch))
        chk("恢复后不拦", not check_pdf(pdf, arch_root=arch))

        # 导出
        md = export_markdown(list_items(arch))
        chk("导出表头", "已归档文献清单" in md and "共 0 篇" in md)
        chk("导出 csv 表头", export_csv([]).startswith("doc,标题"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("archive selftest: %d/%d PASS" % (ok, ok + fail))
    return ok == ok + fail


# ---------------------------------------------------------------- CLI
def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="archive.py", description="KCI 文档归档与判重")
    ap.add_argument("docs", nargs="*", help="doc id(可多个)")
    ap.add_argument("--add", action="store_true", help="归档给定 doc")
    ap.add_argument("--restore", action="store_true", help="恢复给定 doc")
    ap.add_argument("--note", default="", help="归档批注")
    ap.add_argument("--check", metavar="PDF", help="判重演练: 只看会不会拦, 不动文件")
    ap.add_argument("--list", action="store_true", help="列出已归档")
    ap.add_argument("--export", metavar="PATH", help="导出清单(.md/.csv)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return 0 if _selftest() else 1
    if a.check:
        hits = check_pdf(a.check)
        if not hits:
            print("干净: 未命中任何归档记录")
        for h in hits:
            print("[%s] %s — %s" % (h["level"], h["doc"], h["reason"]))
        return 0
    if a.export:
        items = list_items()
        body = export_csv(items) if a.export.lower().endswith(".csv") else export_markdown(items)
        with io.open(a.export, "w", encoding="utf-8-sig" if a.export.lower().endswith(".csv") else "utf-8") as fh:
            fh.write(body)
        print("已导出 %d 篇 -> %s" % (len(items), a.export))
        return 0
    if a.restore:
        for d in a.docs:
            ok, msg = restore_doc(d)
            print(("  OK  " if ok else "  SKIP") + " %s: %s" % (d, msg))
        return 0
    if a.add and a.docs:
        r = archive_docs(a.docs, note=a.note)
        print("归档 %d 篇" % len(r["ok"]))
        for d in r["ok"]:
            print("  OK   " + d)
        for d, why in r["skipped"]:
            print("  SKIP %s: %s" % (d, why))
        for d, why in r["warnings"]:
            print("  WARN %s: %s" % (d, why))
        return 0
    # 默认 --list
    items = list_items()
    if not items:
        print("归档为空 (%s)" % ARCH_DIR)
        return 0
    print("%-28s %-6s %-22s %s" % ("doc", "质检", "归档时间", "标题"))
    for it in items:
        q = "通过" if not it.get("qc_issues") else "缺 " + "/".join(it.get("qc_issues") or [])
        print("%-28s %-6s %-22s %s" % (
            it.get("doc"), q, (it.get("archived_at") or "").replace("T", " ")[:16],
            (it.get("title") or "")[:52]))
    print("\n共 %d 篇" % len(items))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
