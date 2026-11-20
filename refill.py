# -*- coding: utf-8 -*-
"""refill.py — 二次收敛·只填空回刷(2026-11-19 用户定: 新本事安全兑现到老数据, 不重跑)。

做法: 对每个文档重跑 front/abs/issue 拿到"新本事视角的值", 但**只把当前为空的字段
写上**; 已有值的字段一律恢复原值(含 confidence 证据)——阶段重跑可能已把它覆写,
这里强制盖回。结构性豁免同样保留: 旧值 confidence.source 为 volume_only(原文不编
期号)/ web_record(联网回填)时, 即使值为空也不填——那不是抽不出, 是不该有。

安全标准(配合 meta_diff.py): 跑前 snapshot, 跑后 diff——所有变更必须是
"空 → 有值"; 出现 "有值 → 变化" 即为工具 bug, 立即从快照还原。

用法:
  python refill.py                      # 主工作区(out/)
  python refill.py --root E:/KCI11_17
  python refill.py --docs 13570876,13571770   # 只刷指定篇
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metapy.front import front_doc
from metapy.abs import abstract_doc
from metapy.issue import issue_doc

FIELDS = ("title", "title_en", "journal", "journal_en", "year", "volume", "issue",
          "pages", "abstract", "abstract_en", "keywords", "keywords_en",
          "authors", "affiliations")
KEEP_SRC = ("volume_only", "web_record")


def _empty(v):
    if isinstance(v, dict):
        v = v.get("value")
    return v is None or v == "" or v == [] or v == {}


def _keep_old(old_field):
    """旧值该保留吗: 有值 → 保留; 空但来源是结构性豁免(volume_only/web_record) → 也保留。"""
    if not _empty(old_field):
        return True
    if isinstance(old_field, dict):
        return ((old_field.get("confidence") or {}).get("source")) in KEEP_SRC
    return False


def refill_doc(doc, out_root):
    """单篇: 重跑三阶段 → 只合并空字段。返回 {字段: [旧, 新]}(仅实际填上的)。"""
    fp = os.path.join(out_root, doc, "metadata.json")
    if not os.path.exists(fp):
        return None
    old = json.load(io.open(fp, encoding="utf-8"))
    before = {k: old.get(k) for k in FIELDS}
    for fn in (front_doc, abstract_doc, issue_doc):
        fn(doc, out_root)                       # 阶段自写盘(可能覆写旧值)
    new = json.load(io.open(fp, encoding="utf-8"))
    merged = dict(new)
    changed = {}
    for k in FIELDS:
        ov, nv = before.get(k), new.get(k)
        if _keep_old(ov):
            if k in old:
                merged[k] = old[k]              # 有值/豁免: 强制盖回旧值
        elif _empty(ov):
            if not _empty(nv):                  # 空 → 有值: 采用新本事
                changed[k] = [ov, nv]           # merged[k] 已是 nv
    dirty = (changed
             or any(merged.get(k) != new.get(k) for k in FIELDS))
    if dirty:
        io.open(fp, "w", encoding="utf-8").write(
            json.dumps(merged, ensure_ascii=False, indent=1))
    return changed


def _selftest():
    """合并纪律冒烟: 只填空/不盖旧/豁免源保留。不触网不碰真库。"""
    ok1 = (_keep_old({"value": "X", "confidence": {}})              # 有值保留
           and _keep_old({"value": None, "confidence": {"source": "volume_only"}})
           and _keep_old({"value": None, "confidence": {"source": "web_record"}})
           and not _keep_old({"value": None, "confidence": {"source": "regex"}})
           and not _keep_old({"value": ""})
           and not _keep_old(None)
           and _keep_old(["김철수"])                                 # 列表有值保留
           and not _keep_old([]))
    print("refill selftest: %s" % ("PASS" if ok1 else "FAIL"))
    return ok1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--docs", default="")
    a = ap.parse_args()
    out_root = os.path.join(a.root, "out") if a.root else "out"
    if a.docs:
        docs = [d.strip() for d in a.docs.split(",") if d.strip()]
    else:
        docs = sorted(d for d in os.listdir(out_root)
                      if os.path.isdir(os.path.join(out_root, d))
                      and os.path.exists(os.path.join(out_root, d, "metadata.json")))
    n_fill, n_docs = 0, 0
    for i, d in enumerate(docs, 1):
        ch = refill_doc(d, out_root)
        if ch:
            n_docs += 1
            n_fill += len(ch)
            print("fill %s: %s" % (d, ", ".join(sorted(ch))))
        if i % 100 == 0:
            print("... %d/%d" % (i, len(docs)))
    print("== 只填空回刷完成: %d 篇被补, 共填 %d 个空字段 / 总 %d 篇 ==" % (n_docs, n_fill, len(docs)))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selfcheck":
        raise SystemExit(0 if _selftest() else 1)
    main()
