# -*- coding: utf-8 -*-
"""实体/题录矩阵视图数据层: 文档 × (14 必抽项字段 + 4 实体 kind) 的单元格取值。

纯函数、零 tkinter 依赖, GUI 与 CLI 两用(2026-11-17 实体视图需求扩展):
- 字段列: metadata.json 直读; 长字段截断; 计数列(正文/参考文献/引文句/脚注)
  无值即空串, 符合"没有就空着"。
- 正文字数: unified 页 Text/Footnote 主读法总字符(QC 同口径), 按 summary mtime
  做增量持久缓存(out/.view_body_chars.json)——首次切换全库扫一次, 之后近秒开。
- 实体列: ner_lite 产物, 去重最多 4 个 ";"连接。"""
import io
import json
import os
import re

# (key, 表头, 基准宽) —— 字段列在前, 实体列在后; "机构"字段与 org 实体并存,
# 实体列表头加 "·实体" 后缀区分。
FIELDS = (("title", u"标题", 260), ("authors", u"作者", 130), ("affiliations", u"机构", 180),
          ("journal", u"期刊", 140), ("year", u"出版年", 56), ("volume", u"卷", 40),
          ("issue", u"期", 40), ("pages", u"页码", 70), ("keywords", u"关键词", 200),
          ("abstract", u"摘要", 240), ("body", u"正文", 76), ("refs", u"参考文献", 64),
          ("cspan", u"引文句", 56), ("frefs", u"脚注", 56))
KINDS = (("org", u"机构·实体", 200), ("funding", u"基金", 180),
         ("software", u"软件", 200), ("dataset", u"数据集", 180))
COLUMNS = FIELDS + KINDS
MAX_PER_CELL = 4
_BODY_CACHE = ".view_body_chars.json"


def _trunc(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + u"…"


def _body_chars(doc, out_root):
    """正文总字符(QC 同口径: unified Text/Footnote 主读法)。"""
    total = 0
    udir = os.path.join(out_root, doc, "unified")
    if not os.path.isdir(udir):
        return 0
    for name in os.listdir(udir):
        if not re.match(r"page_\d+\.json$", name):
            continue
        try:
            j = json.load(io.open(os.path.join(udir, name), encoding="utf-8"))
        except Exception:
            continue
        for u in j.get("unified") or []:
            if u.get("type") in ("Text", "Footnote"):
                total += len((u.get("main_text") or "").strip())
    return total


def cells_for(doc, out_root, ner_root):
    """单篇 → {key: 单元格文本}(字段+实体全量; 任何读取失败安静置空, 视图不炸)。"""
    out = {k: "" for k, _h, _w in COLUMNS}
    m = {}
    mfp = os.path.join(out_root, doc, "metadata.json")
    if os.path.isfile(mfp):
        try:
            m = json.load(io.open(mfp, encoding="utf-8")) or {}
        except Exception:
            m = {}
    out["title"] = _trunc((m.get("title") or {}).get("value"), 60)
    out["authors"] = _trunc(u";".join(a.get("name") or ""
                                      for a in (m.get("authors") or [])), 80)
    out["affiliations"] = _trunc(u";".join(m.get("affiliations") or []), 100)
    out["journal"] = _trunc((m.get("journal") or {}).get("value"), 50)
    out["year"] = str((m.get("year") or {}).get("value") or "")
    out["volume"] = str((m.get("volume") or {}).get("value") or "")
    out["issue"] = str((m.get("issue") or {}).get("value") or "")
    out["pages"] = (m.get("pages") or {}).get("value") or ""
    out["keywords"] = _trunc(u";".join((m.get("keywords") or [])
                                       + (m.get("keywords_en") or [])), 80)
    ab = m.get("abstract") or {}
    out["abstract"] = _trunc(ab.get("value") if isinstance(ab, dict) else "", 60)
    refs = m.get("references") or []
    out["refs"] = u"%d 条" % len(refs) if refs else ""
    cs = m.get("citation_spans") or []
    out["cspan"] = str(len(cs)) if cs else ""
    fr = m.get("footnote_refs") or []
    out["frefs"] = str(len(fr)) if fr else ""
    # 实体 kind
    fp = os.path.join(ner_root, "fds_extract", "ner_lite", doc, "ner_lite.json")
    if os.path.isfile(fp):
        try:
            recs = (json.load(io.open(fp, encoding="utf-8")).get("records")) or []
        except Exception:
            recs = []
        seen = {k: [] for k, _h, _w in KINDS}
        for r in recs:
            k = r.get("kind")
            name = (r.get("name_model") or "").strip()
            if k in seen and name and name not in seen[k]:
                seen[k].append(name)
        for k, _h, _w in KINDS:
            if seen[k]:
                out[k] = u"；".join(seen[k][:MAX_PER_CELL])
    return out


def matrix(docs, out_root, ner_root):
    """全量矩阵 + 正文字数增量缓存(以 unified summary 的 mtime 为新鲜度键)。"""
    cache_fp = os.path.join(out_root, _BODY_CACHE)
    cache = {}
    if os.path.isfile(cache_fp):
        try:
            cache = json.load(io.open(cache_fp, encoding="utf-8")) or {}
        except Exception:
            cache = {}
    dirty = False
    out = {}
    for d in docs:
        cells = cells_for(d, out_root, ner_root)
        sp = os.path.join(out_root, d, "unified", "_unified_summary.json")
        mt = os.path.getmtime(sp) if os.path.isfile(sp) else 0
        ent = cache.get(d)
        if not isinstance(ent, list) or len(ent) != 2 or ent[0] != mt:
            ent = [mt, _body_chars(d, out_root)]
            cache[d] = ent
            dirty = True
        n = ent[1]
        cells["body"] = u"%d 字" % n if n else ""
        out[d] = cells
    if dirty:
        try:
            io.open(cache_fp, "w", encoding="utf-8").write(
                json.dumps(cache, ensure_ascii=False))
        except Exception:
            pass
    return out
