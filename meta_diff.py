# -*- coding: utf-8 -*-
"""题录元数据回归闸门(2026-11-18): 抽取器改动的准入裁判。

教训: 2026-11-18 一次全库重跑让 794/962 篇 metadata 变动, 其中混入 JNAH 卷期误换、
title_en 被覆写、英文标题误当作者等一批回归 —— 没有前后对照就是拆东墙补西墙。
本工具把那次临时搓的对照流程固化:

  python meta_diff.py snapshot [--tag NAME] [--ws 名或路径]   # 全库 metadata.json 打快照(zip)
  python meta_diff.py diff [--tag NAME] [--ws 名或路径]       # 当前状态 vs 快照: 逐字段 diff + QC 等级迁移
                                              # 输出 _meta_diff_<tag>.json + 控制台摘要
  # 工作区解析: --ws > KCI_WS_ROOT 环境变量 > GUI 当前工作区(.gui_prefs.json) > 主库。

用法(改 metapy/front.py|issue.py 任何守卫后):
  1. snapshot(改动前)
  2. 只重跑目标聚类(stages.py --batch <docs> front,abs,issue …)
  3. diff —— 三道闸:
     a. QC 恶化表: ok/novol/nonregular -> bad 的篇数必须为 0(假绿纠正类另审);
     b. 变更字段组统计(异常组合如 volume/issue 互换须逐篇过印刷证据);
     c. _meta_diff_<tag>.json 人工抽验。
对照字段: title/title_en/authors/affiliations + journal/journal_en/year/volume/issue/
pages + keywords/keywords_en —— 与 gui.py 表格列一致。
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
sys.path.insert(0, HERE)
# 工作区覆盖(2026-11-24 收敛口径, 与 stages/failures.py 同): --ws 显式值 >
# KCI_WS_ROOT 环境变量 > GUI 当前工作区(.gui_prefs.json) > 主库。
# 快照 tag 默认带工作区名前缀, 防止互相误选; ner_root 必须跟工作区走, 否则 QC 闸会把
# 已被本区 NER 满足的"机构"误判为缺失(failures.py 同款 bug, 2026-11-19 实锤)。
WS = (os.environ.get("KCI_WS_ROOT") or "").replace("\\", "/")
if WS:
    OUT = os.path.join(WS, "out")
NER_TRIAL = os.path.join(WS if WS else HERE, "out_llm_trial")
WS_PREFIX = (os.path.basename(WS.rstrip("/\\")) + "-") if WS else ""


def _apply_ws(root):
    """CLI --ws 应用: 重指 OUT/NER_TRIAL/WS_PREFIX(快照 tag 前缀随工作区名)。"""
    global WS, OUT, NER_TRIAL, WS_PREFIX
    WS = root.replace("\\", "/")
    OUT = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    NER_TRIAL = os.path.join(root, "out_llm_trial") if root \
        else os.path.join(HERE, "out_llm_trial")
    WS_PREFIX = (os.path.basename(root.rstrip("/\\")) + "-") if root else ""

FIELDS = ("title", "title_en", "authors", "affiliations",
          "journal", "journal_en", "year", "volume", "issue", "pages",
          "keywords", "keywords_en")


def _docs():
    return sorted(d for d in os.listdir(OUT)
                  if os.path.isdir(os.path.join(OUT, d))
                  and os.path.exists(os.path.join(OUT, d, "metadata.json")))


def _snap(m):
    out = {}
    for k in ("journal", "journal_en", "year", "volume", "issue", "pages", "title", "title_en"):
        v = m.get(k)
        out[k] = v.get("value") if isinstance(v, dict) else v
    for k in ("authors", "affiliations", "keywords", "keywords_en"):
        out[k] = m.get(k)
    return out


def snapshot(tag=None):
    tag = tag or (WS_PREFIX + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S"))
    fp = os.path.join(HERE, "_meta_snapshot_%s.zip" % tag)
    zf = zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED)
    n = 0
    for doc in _docs():
        zf.write(os.path.join(OUT, doc, "metadata.json"), doc + "/metadata.json")
        n += 1
    zf.close()
    print("snapshot %s: %d 篇 -> %s" % (tag, n, os.path.basename(fp)))
    return fp


def _load_snap(fp):
    base = {}
    zf = zipfile.ZipFile(fp)
    for name in zf.namelist():
        doc = name.split("/")[0]
        try:
            base[doc] = json.loads(zf.read(name).decode("utf-8"))
        except Exception:
            pass
    return base


def diff(tag=None):
    import qc
    snaps = sorted(f for f in os.listdir(HERE)
                   if re.match(r"_meta_snapshot_.*\.zip$", f)
                   and f.startswith("_meta_snapshot_" + WS_PREFIX))
    if not snaps:
        raise SystemExit("无快照, 先 python meta_diff.py snapshot")
    fp = None
    for cand in reversed(snaps):
        t = re.match(r"_meta_snapshot_(.+)\.zip$", cand).group(1)
        if tag is None or t == tag:
            fp = os.path.join(HERE, cand)
            tag = t
            break
    if not fp:
        raise SystemExit("找不到 tag=%s 的快照" % tag)
    base = _load_snap(fp)
    diffs = {}
    worsened = []
    for doc in _docs():
        before = _snap(base.get(doc) or {})
        try:
            after = _snap(json.load(io.open(os.path.join(OUT, doc, "metadata.json"),
                                            encoding="utf-8")))
        except Exception:
            continue
        changed = {k: (before.get(k), after.get(k)) for k in FIELDS
                   if before.get(k) != after.get(k)}
        if changed:
            diffs[doc] = changed
        lb = qc.qc_of(doc, out_dir=OUT, ner_root=NER_TRIAL)["level"] if doc in base else None
        la = qc.qc_of(doc, out_dir=OUT, ner_root=NER_TRIAL)["level"]
        if lb in ("", "ok", "novol", "nonregular") and la == "bad" and lb is not None:
            worsened.append((doc, lb or "ok", la))
    out_fp = os.path.join(HERE, "_meta_diff_%s.json" % tag)
    io.open(out_fp, "w", encoding="utf-8").write(json.dumps(
        {d: {k: [b, a] for k, (b, a) in ch.items()} for d, ch in diffs.items()},
        ensure_ascii=False, indent=1))
    print("diff vs %s: 变更 %d 篇 -> %s" % (tag, len(diffs), os.path.basename(out_fp)))
    print("QC 恶化(ok/novol/nonregular->bad): %d 篇 %s" % (len(worsened), worsened[:10]))
    c = Counter(tuple(sorted(ch.keys())) for ch in diffs.values())
    print("字段组统计:")
    for k, v in c.most_common(15):
        print("  %3d %s" % (v, "/".join(k)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["snapshot", "diff"])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--ws", default=None, help="工作区名或路径; 缺省 环境变量KCI_WS_ROOT > GUI当前工作区 > 主库")
    a = ap.parse_args()
    root, name, src = _resolve(a.ws)
    if a.ws is not None or root:
        _apply_ws(root)
        print("[工作区] %s (%s) · 来源 %s" % (name, root or HERE, src))
    if a.cmd == "snapshot":
        snapshot(a.tag)
    else:
        diff(a.tag)


def _resolve(explicit):
    import workspaces
    try:
        return workspaces.resolve_ws_root(explicit=explicit, here=HERE)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
