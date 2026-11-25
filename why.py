# -*- coding: utf-8 -*-
"""why.py — 口径统一查询入口(2026-11-24): "这篇为什么红/这个刊什么体例", 一处查全。

背景: 同一类"为什么例外"的口径散在五处, 互相漂移且无交叉引用——
    qc.py(运行时判定) / journals.py(刊例事实) / metadata(字段来源) /
    gui_notes.json(GUI 展示备注) / reviewed_docs.json(历史审阅台账) /
    代码注释(守卫实证)。查一个问题要翻五个地方, 还不知道信哪份。

本工具只读聚合, 不写任何台账。权威序(回答不同问题时):

    红不红 / 缺什么   -> qc.qc_of()            唯一运行时权威, 其余一律不覆盖
    这个刊什么体例   -> journals.py 族表+注册表 结构化事实, 判据就在这里落地
    字段值从哪来     -> metadata confidence / webfill  产物自带出处
    当初为什么这么判 -> reviewed_docs.json      冻结的历史档案, 只解释过去, 不判现在
    GUI 上显示的备注 -> gui_notes.json          工作区级展示文字, 可被回填脚本覆盖
    这条守卫为什么在 -> 代码注释实证            就近源码, 改守卫前读它

用法:
    python why.py <doc>                 # 查一篇: 五处口径聚合 + 冲突标记
    python why.py --journal <刊名|DOI>   # 查一刊: 族表/白名单/注册表 + 相关篇目
    python why.py --audit               # 全库扫冲突: 备注与 QC 打架的都列出来
    python why.py <doc> --ws <区>        # 工作区口径同 failures/meta_diff
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(fp, default=None):
    try:
        with io.open(fp, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _resolve_ws(argv):
    """--ws 口径同 stages/failures/meta_diff: 显式 > 环境变量 > GUI 当前 > 主库。"""
    import workspaces
    ws_val = None
    if "--ws" in argv:
        i = argv.index("--ws")
        if i + 1 >= len(argv):
            raise SystemExit("--ws 需要一个工作区名或路径")
        ws_val = argv[i + 1]
    root, name, src = workspaces.resolve_ws_root(explicit=ws_val, here=HERE)
    return root, name, src


def _reviewed():
    """reviewed_docs.json → {doc: row}; 文件缺失/损坏返回空(它是冻结档案, 不是依赖)。"""
    d = _load(os.path.join(HERE, "reviewed_docs.json")) or {}
    return {x["doc_id"]: x for x in (d.get("documents") or []) if isinstance(x, dict)}


def _parse_note(note):
    """定稿备注 → (声称已回填的 QC中文缺项集, 声明仍缺的集, 是否声称全齐)。

    只认定稿格式的两段标记; 老式自由文本(如"原文未见文内引文证据")返回空集,
    由展示层原样呈现, 不参与对账。字段名映射同 qc.QC_REQUIRED 的中文名。
    """
    got, missing = set(), set()
    if not note:
        return got, missing, False
    claims_all = u"必抽项已齐" in note
    m1 = re.search(u"已回填[:：]([^;；。]*)", note)
    m2 = re.search(u"原文仍缺[:：]([^;；。]*)", note)
    zh = {"刊名": u"期刊", "刊": u"期刊", "出版年": u"出版年", "卷": u"卷",
          "期": u"期", "页码": u"页码", "关键词": u"关键词", "摘要": u"摘要",
          "作者": u"作者", "机构": u"机构", "引文": u"引文句/脚注",
          "脚注": u"引文句/脚注", "引文句": u"引文句/脚注"}
    for seg, dest in ((m1, got), (m2, missing)):
        if not seg:
            continue
        for token in re.split(u"[/、,，]", seg.group(1)):
            t = token.strip()
            if t in zh:
                dest.add(zh[t])
    return got, missing, claims_all


def _family_of(journal, doi_text):
    """按刊名/DOI 命中 journals.py 族表 + DOI 注册表。

    免卷白名单已于 2026-11-25 移除; 这里的 was_novol 只反映族表记载的历史体例,
    不再代表 QC 会豁免——缺卷期一律报红。
    """
    from metapy import journals
    fold = lambda s: re.sub(r"\s+", "", (s or "")).lower()
    jn = fold(journal)
    hits = []
    for f in journals.FAMILIES:
        names = [fold(x) for x in (f.get("names") or [])]
        dp = (f.get("doi_prefix") or "").lower()
        if (jn and any(jn == n or n in jn for n in names if n)) \
                or (dp and doi_text and dp in doi_text.lower()):
            hits.append(f)
    reg = journals.load_registry()
    doi_hits = [(p, v) for p, v in sorted(reg.items())
                if doi_text and p in doi_text.lower()]
    was_novol = any(u"免卷" in (f.get("layout") or "") or u"不印年卷期" in (f.get("layout") or "")
                    for f in hits)
    return hits, doi_hits, was_novol


def _guard_evidence(doc):
    """代码注释里带该 doc id 的守卫实证行(就近源码, 去重限 6 条)。"""
    out = []
    for fn in ("metapy/front.py", "metapy/issue.py", "metapy/refs.py", "metapy/abs.py"):
        fp = os.path.join(HERE, fn)
        if not os.path.isfile(fp):
            continue
        try:
            lines = io.open(fp, encoding="utf-8").read().splitlines()
        except Exception:
            continue
        for n, line in enumerate(lines, 1):
            if doc in line and ("#" in line or '"""' in line):
                out.append((fn, n, line.strip()[:110]))
                if len(out) >= 6:
                    return out
    return out


def show_doc(doc, root, ws_name):
    import qc
    out_dir = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    ner_root = os.path.join(root, "out_llm_trial") if root \
        else os.path.join(HERE, "out_llm_trial")
    dd = os.path.join(out_dir, doc)
    notes = _load(os.path.join(root or HERE, "gui_notes.json")) or {}
    rev = _reviewed()
    m = _load(os.path.join(dd, "metadata.json")) or {}

    print("=" * 68)
    print("【%s】 工作区=%s" % (doc, ws_name))
    print("=" * 68)

    # 权威 1: 运行时 QC
    r = qc.qc_of(doc, out_dir, ner_root)
    verdict = {"": "通过", "novol": "历史免卷档(已取消, 老台账残留)",
               "nonregular": "非正规论文", "bad": "异常"}.get(r["level"], r["level"])
    print("\n[1 权威·红绿] qc.qc_of → %s" % verdict)
    if r["issues"]:
        print("   缺项: %s" % ", ".join(r["issues"]))
    print("   语言=%s  (红绿只认这一处, 下面任何说法都不覆盖它)" % r.get("language", "?"))

    # 权威 2: 刊例事实
    journal = ((m.get("journal") or {}).get("value") if isinstance(m.get("journal"), dict)
               else m.get("journal")) or ""
    doi_text = " ".join(str(m.get(k) or "") for k in ("doi", "doi_url"))
    fams, doi_hits, was_novol = _family_of(journal, doi_text)
    print("\n[2 权威·体例] journals.py / qc.py")
    print("   刊名=%s  历史免卷体例=%s (QC 不再豁免, 缺卷期照常报红)" % (journal or "(空)", "是" if was_novol else "否"))
    for f in fams:
        print("   族[%s] extractor=%s" % (f["key"], (f.get("extractor") or "")[:80]))
        print("          gaps=%s" % (f.get("gaps") or "无")[:160])
    for p, v in doi_hits:
        print("   DOI注册表[%s] → %s (%s)" % (p, v.get("journal"), v.get("source")))
    # 免卷口径已于 2026-11-25 统一: QC 无豁免, 族表只记历史体例, 不再存在三处漂移。

    # 权威 3: 字段来源
    print("\n[3 权威·来源] metadata confidence / webfill")
    for k in ("journal", "year", "volume", "issue", "pages", "title", "authors"):
        v = m.get(k)
        if not v:
            continue
        val = (v.get("value") if isinstance(v, dict) else v)
        src = (v.get("confidence") or {}).get("source") if isinstance(v, dict) else None
        if src:
            shown = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)[:50]
            print("   %-9s = %s  [source=%s]" % (k, shown[:50], src))
    if m.get("webfill"):
        print("   webfill 字段: %s" % ", ".join(m["webfill"] if isinstance(m["webfill"], list)
                                            else m["webfill"].keys()))

    # 历史档案
    row = rev.get(doc)
    print("\n[4 历史档案] reviewed_docs.json (2026-11-17 冻结, 只解释过去)")
    if row:
        print("   %s · %s" % (row.get("processed_at", "?"), row.get("result", "?")))
        print("   %s" % (row.get("note") or "")[:300])
    else:
        print("   (无记录)")

    # 展示备注
    note = notes.get(doc, "")
    print("\n[5 展示备注] gui_notes.json (%s)" % (os.path.join(root or HERE, "gui_notes.json")))
    print("   %s" % (note[:300] if note else "(无备注)"))

    # 守卫实证
    ev = _guard_evidence(doc)
    print("\n[6 规则实证] 代码注释中提及本篇的守卫")
    if ev:
        for fn, n, line in ev:
            print("   %s:%d  %s" % (fn, n, line))
    else:
        print("   (无)")

    # 冲突检测: 按备注定稿格式逐项对账, 不靠关键词粗判
    #   格式 "[回填:联网检索] 已回填:卷/期; 原文仍缺:引文(脚注二选一)。来源:..."
    print("\n[冲突检测]")
    bad = r["level"] == "bad"
    conflicts = []
    filled, still_missing, claims_all = _parse_note(note)
    if claims_all and bad:
        conflicts.append("备注称'必抽项已齐', 但 QC 仍判异常 —— 回填未落盘或备注过时")
    for zh in set(r["issues"]) & filled:
        conflicts.append("备注称已回填 '%s', 但 QC 仍报缺 —— 回填未落盘" % zh)
    extra = [zh for zh in r["issues"] if zh not in still_missing and not claims_all]
    if extra and still_missing:
        conflicts.append("QC 新报缺 %s, 备注仍缺项只写了 %s —— 备注未同步" %
                         ("/".join(extra), "/".join(sorted(still_missing))))
    if bad and row and row.get("result") == "通过":
        conflicts.append("历史台账记'通过', 但 QC 现判异常 —— 后链回归或判据收紧")
    if (not bad) and row and row.get("result") == "原文证据不足":
        conflicts.append("历史台账记'证据不足', 但 QC 现已通过 —— 规则已修好, 档案过时(仅提示)")
    if note and ("免卷" in note or "不印卷期" in note) and was_novol:
        conflicts.append("历史备注称免卷, 但 QC 已取消该豁免(2026-11-25) —— 备注过时, 仅提示")
    if conflicts:
        for c in conflicts:
            print("   ⚠ " + c)
    else:
        print("   无矛盾(备注/台账/QC 口径一致)")


def show_journal(query, root, ws_name):
    import qc
    from metapy import journals
    out_dir = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    fams, doi_hits, was_novol = _family_of(query, query)
    reg = journals.load_registry()
    # DOI 注册表也按刊名反查
    for p, v in sorted(reg.items()):
        if query and query.lower() in str(v.get("journal") or "").lower():
            if p not in [x[0] for x in doi_hits]:
                doi_hits.append((p, v))

    print("=" * 68)
    print("【刊例查询】%s  工作区=%s" % (query, ws_name))
    print("=" * 68)
    print("\n[族表 journals.py FAMILIES]")
    if fams:
        for f in fams:
            print("   key=%s names=%s doi=%s" % (f["key"], f.get("names"), f.get("doi_prefix")))
            print("   layout=%s" % (f.get("layout") or "")[:160])
            print("   extractor=%s" % (f.get("extractor") or "")[:160])
            print("   gaps=%s" % (f.get("gaps") or "无")[:300])
    else:
        print("   (未登记族)")
    print("\n[免卷口径] 已取消(2026-11-25): 族表历史体例=%s, QC 不再豁免缺卷期" %
          ("是" if was_novol else "否"))
    print("\n[DOI 注册表]")
    for p, v in doi_hits:
        print("   %s → %s / %s [%s] %s" % (p, v.get("journal"), v.get("journal_en"),
                                          v.get("source"), (v.get("note") or "")[:120]))
    if not doi_hits:
        print("   (无)")

    # 相关篇目
    print("\n[相关篇目] out/ 下 metadata 命中该刊的文档")
    docs = []
    qfold = re.sub(r"\s+", "", query).lower()
    for d in sorted(os.listdir(out_dir)):
        mp = os.path.join(out_dir, d, "metadata.json")
        if not os.path.isfile(mp):
            continue
        m = _load(mp) or {}
        jv = m.get("journal")
        jv = (jv.get("value") if isinstance(jv, dict) else jv) or ""
        if qfold and qfold in re.sub(r"\s+", "", jv).lower():
            docs.append(d)
    print("   命中 %d 篇: %s" % (len(docs), ", ".join(docs[:20])))


def audit(root, ws_name):
    import qc
    out_dir = os.path.join(root, "out") if root else os.path.join(HERE, "out")
    notes = _load(os.path.join(root or HERE, "gui_notes.json")) or {}
    rev = _reviewed()
    rows = []
    for d in sorted(os.listdir(out_dir)):
        if not os.path.isfile(os.path.join(out_dir, d, "metadata.json")):
            continue
        r = qc.qc_of(d, out_dir)
        if r["level"] != "bad":
            continue
        note = notes.get(d, "")
        row = rev.get(d)
        filled, still_missing, claims_all = _parse_note(note)
        why = []
        if claims_all:
            why.append("备注称已齐")
        if set(r["issues"]) & filled:
            why.append("备注称已回填%s" % "/".join(sorted(set(r["issues"]) & filled)))
        if row and row.get("result") == "通过":
            why.append("台账记通过")
        if not note and not row:
            why.append("无任何说明")
        if why:
            rows.append((d, ", ".join(r["issues"][:4]), "; ".join(why)))
    print("=" * 68)
    print("【冲突扫描】QC 判异常、但备注/台账口径不一致: %d 篇" % len(rows))
    print("=" * 68)
    for d, miss, why in rows:
        print("   %-22s %-28s %s" % (d, miss, why))


def main():
    argv = list(sys.argv[1:])
    journal_mode = "--journal" in argv
    audit_mode = "--audit" in argv
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--ws":
            i += 2          # --ws 的值只给工作区解析, 不当查询键
            continue
        if a == "--journal":
            i += 1          # 开关本身跳过; 紧跟的值仍是查询键, 下一轮收进 args
            continue
        if not a.startswith("--"):
            args.append(a)
        i += 1
    if not args and not audit_mode:
        raise SystemExit(__doc__)
    root, name, src = _resolve_ws(argv)
    print("[工作区] %s (%s) · 来源 %s" % (name, root or HERE, src))
    if audit_mode:
        audit(root, name)
    elif journal_mode:
        show_journal(args[0], root, name)
    else:
        show_doc(args[0], root, name)


if __name__ == "__main__":
    main()
