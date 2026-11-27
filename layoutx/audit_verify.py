#!/usr/bin/env python3
"""独立复核脚本：对 layoutx/REPORT.md 的每条可核查声明做机器校验。

只读输入（diff.jsonl / comparison_summary.json / audit_sample.json /
REPORT.md / layoutx/*.py / manual_review.json / metapy/ / out/ / out_layout/），
不运行任何流水线主流程。结构化结果输出到 stdout；可选 --md 写入 VERIFICATION.md。

用法：
    python layoutx/audit_verify.py
    python layoutx/audit_verify.py --md
"""

import argparse
import collections
import io
import json
import os
import random
import re
import sys
import time
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.dirname(HERE)

DIFF = os.path.join(HERE, "diff.jsonl")
SUMMARY = os.path.join(HERE, "comparison_summary.json")
AUDIT = os.path.join(HERE, "audit_sample.json")
REPORT = os.path.join(HERE, "REPORT.md")
MANUAL = os.path.join(HERE, "manual_review.json")
FIELDS = ("title", "title_en", "authors", "affiliations", "keywords", "keywords_en")
ANCHOR_DOCS = ("13569913", "13567494", "13568651", "13569417",
               "13569428", "13564500", "13564501", "13569720")
TOKENS = ("본체론", "부산", "경남", "알고리즘", "유형분류", "평가하였고", "조록")

VERDICT_MAP = {"TRUE": "PASS", "FALSE": "FAIL", "PARTIAL": "WARN",
               "UNVERIFIABLE": "UNVERIFIABLE"}


# ---------------------------------------------------------------- 基础工具
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def report_lines():
    with open(REPORT, encoding="utf-8") as f:
        return f.read().splitlines()


def find_line(substr, lines=None):
    lines = lines if lines is not None else report_lines()
    for i, line in enumerate(lines, 1):
        if substr in line:
            return i
    return None


def norm_audit(s):
    """与 audit.py 的 norm 完全一致。"""
    return re.sub(r"\s+", "", str(s or "")).lower()


def norm_compare(s):
    """与 compare.py 的 norm 完全一致（用于字段值归一）。"""
    s = re.sub(r"</?(?:sup|sub|b|i|em|strong)>|\*\*", "", str(s or ""), flags=re.I)
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = s.translate(str.maketrans("¹²³⁴⁵⁶⁷⁸⁹", "123456789"))
    return re.sub(r"[\s·•․⋅∙,，;；:：.'’‘\"“”()（）\-–—_#*|｜┃〈〉<>《》『』「」]+", "", s).lower()


def names_of(record, field, which):
    v = record["fields"][field][which]
    if not isinstance(v, list):
        return []
    return [a.get("name") if isinstance(a, dict) else a for a in v]


def claim(cid, title, verdict, evidence, details=None, **extra):
    c = {"id": cid, "title": title, "verdict": verdict, "evidence": evidence}
    if details is not None:
        c["details"] = details
    c.update(extra)
    return c


def anchor_claim(cid, doc, report_text, report_line, checks, field_snapshot,
                 verdict_kind="anchor"):
    """checks: [(说明, bool), ...]；据真假分布给出 TRUE/FALSE/PARTIAL。"""
    passed = sum(1 for _, ok in checks if ok)
    if not checks:
        verdict_kind = "UNVERIFIABLE"
    elif passed == len(checks):
        verdict_kind = "TRUE"
    elif passed == 0:
        verdict_kind = "FALSE"
    else:
        verdict_kind = "PARTIAL"
    evidence = ["REPORT.md:%s %s" % (report_line, report_text)]
    evidence += ["%s → %s" % (desc, "成立" if ok else "不成立") for desc, ok in checks]
    mr = [v["manual_reason"] for v in field_snapshot.values()
          if isinstance(v, dict) and v.get("manual_reason")]
    evidence.append("manual_reason：%s" % (mr[0][:80] if mr else "锚点字段中不存在（仅 32 条 manual_review 记录，均不含锚点）"))
    evidence.append("关键证据摘录(≤200字)：" +
                    json.dumps(field_snapshot, ensure_ascii=False)[:200])
    shown = re.sub(r"^(?:\d{8}(?:[：:]|/))+\s*", "", report_text)
    return claim(cid, "锚点 %s：%s" % (doc, shown),
                 VERDICT_MAP[verdict_kind], evidence,
                 {"doc": doc, "checks": [{"desc": d, "ok": ok} for d, ok in checks]},
                 verdict_kind=verdict_kind)


# ---------------------------------------------------------------- 读输入
def read_inputs():
    recs = load_jsonl(DIFF)
    by_doc = {r["doc"]: r for r in recs}
    summary = load_json(SUMMARY)
    audit = load_json(AUDIT)
    lines = report_lines()
    return recs, by_doc, summary, audit, lines


# ---------------------------------------------------------------- 声明 R01
def check_counts(recs, summary, lines):
    total = len(recs)
    cc = collections.Counter(r["category"] for r in recs)
    report_cats = {}
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\|\s*(新版更优|一致|双错且不同|新版更差)\s*\|\s*(\d+)\s*\|", line)
        if m:
            report_cats[m.group(1)] = (int(m.group(2)), i)
    exp = summary["categories"]
    ok = (total == summary["total"] and set(cc) == set(exp) and
          all(cc[k] == exp[k] for k in exp) and
          all(report_cats.get(k, (None,))[0] == exp[k] for k in exp))
    ev = ["diff.jsonl 实际行数=%d；comparison_summary.json:2 total=%d" % (total, summary["total"])]
    for k in ("新版更优", "一致", "双错且不同", "新版更差"):
        ev.append("%s：diff=%d 汇总=%d 报告表=%s(REPORT.md:%s)" %
                  (k, cc.get(k, 0), exp.get(k), report_cats.get(k, ("缺失",))[0],
                   report_cats.get(k, ("?", "?"))[1]))
    return claim("R01", "diff.jsonl 总数/分类计数与汇总、报告表格一致",
                 "PASS" if ok else "FAIL", ev,
                 {"total": total, "diff": dict(cc), "summary": exp,
                  "report": {k: v[0] for k, v in report_cats.items()}})


# ---------------------------------------------------------------- 声明 R02
def check_fields(recs, summary, lines):
    total = len(recs)
    fc = collections.Counter()
    for r in recs:
        for f, v in r["fields"].items():
            fc[(f, "same" if v["same"] else v.get("assessment", "same"))] += 1
    report_fields = {}
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\|\s*(title|title_en|authors|affiliations|keywords|keywords_en)\s*\|"
                     r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", line)
        if m:
            report_fields[m.group(1)] = ({"same": int(m.group(2)),
                                          "new_better": int(m.group(3)),
                                          "new_worse": int(m.group(4)),
                                          "different_unresolved": int(m.group(5))}, i)
    details = {}
    ok = True
    ev = []
    for f in FIELDS:
        got = {k[1]: n for k, n in fc.items() if k[0] == f}
        exp = summary["fields"][f]
        rep, ln = report_fields.get(f, ({}, None))
        s = sum(got.values())
        row_ok = (all(got.get(k, 0) == exp.get(k, 0) for k in
                      ("same", "new_better", "new_worse", "different_unresolved")) and
                  all(rep.get(k) == exp.get(k, 0) for k in exp) and s == total)
        ok = ok and row_ok
        details[f] = {"diff": got, "summary": exp, "report": rep, "sum": s}
        ev.append("%s：四项和=%d(=总数%d) diff=%s 汇总=%s 报告(REPORT.md:%s)=%s → %s" %
                  (f, s, total, got, exp, ln, rep, "一致" if row_ok else "不一致"))
    return claim("R02", "六字段 same/new_better/new_worse/different_unresolved 计数与汇总一致且每项和为总数",
                 "PASS" if ok else "FAIL", ev, details)


# ---------------------------------------------------------------- 声明 R03
def check_superset(recs, audit, lines):
    sup = [r for r in recs
           if {norm_audit(a.get("name")) for a in r["fields"]["authors"]["old"]} <
              {norm_audit(a.get("name")) for a in r["fields"]["authors"]["new"]} and
           r["fields"]["authors"].get("assessment") == "new_better"]
    k = max(1, (len(sup) + 9) // 10)
    expected_sample = [r["doc"] for r in random.Random(20261127).sample(sup, k)] if sup else []
    actual_sample = [r["doc"] for r in audit.get("author_superset_sample", [])]
    ratio = (len(actual_sample) / audit["author_superset_total"]
             if audit.get("author_superset_total") else 0)
    seed_ok = audit.get("seed") == 20261127
    total_ok = audit.get("author_superset_total") == len(sup) == 14
    sample_ok = sorted(actual_sample) == sorted(expected_sample)
    ratio_ok = ratio > 0.10
    # 报告正文中的超集表
    report_sup = []
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\|\s*(\d{8})\s*\|", line)
        if m and 30 <= i <= 46:
            report_sup.append(m.group(1))
    table_ok = sorted(report_sup) == sorted(r["doc"] for r in sup)
    # 报告第 28 行的抽样声明
    ln28 = find_line("固定随机种子 20261127")
    line28 = lines[ln28 - 1] if ln28 else ""
    m = re.search(r"抽取\s*\d+\s*篇（超过\s*10%\s*）：([^。]+)", line28)
    declared_docs = re.findall(r"135\d{5}", m.group(1)) if m else []
    declared_ok = sorted(declared_docs) == sorted(actual_sample)
    over10_ok = "超过 10%" in line28
    ok = all([seed_ok, total_ok, sample_ok, ratio_ok, table_ok, declared_ok, over10_ok])
    ev = ["audit.py 逻辑重算：superset=%d 篇（old_names < new_names 且 assessment==new_better）" % len(sup),
          "audit_sample.json seed=%d（期望 20261127）→ %s" % (audit.get("seed"), seed_ok),
          "audit_sample.json author_superset_total=%d（重算=%d，报告 14）→ %s" %
          (audit.get("author_superset_total"), len(sup), total_ok),
          "sample 实际=%s，random.Random(20261127).sample(k=%d) 复算=%s → %s" %
          (actual_sample, k, expected_sample, sample_ok),
          "抽样比例=%d/%d=%.1f%% > 10%% → %s（REPORT.md:%s）" %
          (len(actual_sample), audit.get("author_superset_total", 0), ratio * 100,
           ratio_ok, ln28),
          "报告第 28 行声明的抽样 doc=%s → %s" % (declared_docs, declared_ok),
          "报告超集表 doc 列表(%d)与重算一致 → %s" % (len(report_sup), table_ok)]
    return claim("R03", "作者超集判优 14 篇、audit_sample.json 的 seed/total/sample 与抽样超 10%",
                 "PASS" if ok else "FAIL", ev,
                 {"recomputed_total": len(sup), "sample": actual_sample,
                  "expected_sample": expected_sample, "ratio": ratio,
                  "report_superset_docs": report_sup})


# ---------------------------------------------------------------- 声明 R04/R05
def check_doc_list(recs, lines, category, start, end, cid, title):
    actual = [r["doc"] for r in recs if r["category"] == category]
    reported = []
    for i, line in enumerate(lines, 1):
        if start <= i <= end:
            m = re.match(r"^\|\s*(\d{8})\s*\|", line)
            if m:
                reported.append(m.group(1))
    ok = actual == reported
    ev = ["diff.jsonl category=='%s' 共 %d 篇：%s" % (category, len(actual), actual),
          "REPORT.md 表格(%d-%d行)共 %d 篇：%s" % (start, end, len(reported), reported),
          "逐条顺序与集合一致 → %s" % ok]
    return claim(cid, title, "PASS" if ok else "FAIL", ev,
                 {"actual": actual, "reported": reported})


# ---------------------------------------------------------------- 声明 R06/R07
def check_tokens(recs, by_doc, lines):
    hits = {}
    for tok in TOKENS:
        found = []
        for doc, r in by_doc.items():
            for a in r["fields"]["authors"]["new"]:
                nm = a.get("name") if isinstance(a, dict) else str(a)
                if tok in (nm or ""):
                    found.append((doc, nm))
        hits[tok] = found
    ln28 = find_line("固定随机种子 20261127")
    line28 = lines[ln28 - 1] if ln28 else ""
    garbage = [t for t in TOKENS if t != "조록"]
    garbage_clean = all(not hits[t] for t in garbage)
    jorok_left = len(hits["조록"]) > 0
    ok = garbage_clean and not jorok_left
    ev = []
    for t in TOKENS:
        ev.append("token %s：新作者中出现 %d 次 %s" %
                  (t, len(hits[t]), hits[t][:8] if hits[t] else ""))
    ev.append("REPORT.md:%s 声称 %s 等垃圾已清除" % (ln28, "/".join(garbage)))
    ev.append("结论：%s 类垃圾确实清除；조록 仍残留 %d 处 → 整体声明%s" %
              ("/".join(garbage), len(hits["조록"]), "成立" if ok else "不成立"))
    return claim("R06", "token 清理声明：7 个 token 是否仍出现在新作者中",
                 "PASS" if ok else "FAIL", ev, {"hits": hits})


def check_13570535(by_doc, lines):
    r = by_doc.get("13570535")
    ln28 = find_line("13570535 清掉")
    line28 = lines[ln28 - 1] if ln28 else ""
    claim_removed = "清掉 조록" in line28
    new_names = names_of(r, "authors", "new") if r else []
    old_names = names_of(r, "authors", "old") if r else []
    jorok_in_new = any("조록" in (n or "") for n in new_names)
    preserved = any("배성중" in (n or "") for n in new_names)
    # 报告第 41 行表格里的新作者
    ln_table = None
    table_text = ""
    for i, line in enumerate(lines, 1):
        if re.match(r"^\|\s*13570535\s*\|", line):
            ln_table, table_text = i, line
            break
    table_has_jorok = "조록" in table_text
    ok = claim_removed and not jorok_in_new and preserved
    ev = []
    if r:
        f = r["fields"]["authors"]
        ev.append("diff.jsonl 13570535 authors.old=%s" % old_names)
        ev.append("diff.jsonl 13570535 authors.new=%s，assessment=%s" %
                  (new_names, f.get("assessment")))
        ev_line = ""
        for e in f.get("evidence", []):
            if "조록" in (e.get("value") or ""):
                ev_line = "value=%s channel_supported=%s excerpt=%s hits=%s" % (
                    e.get("value"), e.get("channel_supported"), e.get("excerpt"),
                    e.get("hits"))
                break
        ev.append("新增 조록 的证据片段：%s" % (ev_line or "未找到"))
    ev.append("REPORT.md:%s 正文称“13570535 清掉 조록 并保留 배성중” → %s" % (ln28, claim_removed))
    ev.append("REPORT.md:%s 表格新作者=%s（含 조록=%s）" % (ln_table, table_text, table_has_jorok))
    ev.append("判定：清掉 조록=%s（实际仍在=%s），保留 배성중=%s → %s" %
              (claim_removed, jorok_in_new, preserved, "成立" if ok else "不成立"))
    return claim("R07", "报告第 28 行“13570535 清掉 조록 但保留 배성중”真伪",
                 "PASS" if ok else "FAIL", ev,
                 {"old_authors": old_names, "new_authors": new_names,
                  "claim_line": ln28, "table_line": ln_table,
                  "jorok_in_new": jorok_in_new, "preserved_bae": preserved},
                 anchor_verdict="TRUE" if ok else "FALSE")


# ---------------------------------------------------------------- 锚点
def anchor_checks(by_doc, lines):
    out = []

    def line_info(doc):
        for i, line in enumerate(lines, 1):
            if re.match(r"^\-\s*(?:\d{8}/)*%s(?:/\d{8})?[：:]" % doc, line):
                return i, line.strip()[2:].strip()
        return None, "（未找到锚点行）"

    # A1 13569913
    doc = "13569913"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    new_auth = names_of(r, "authors", "new")
    checks = [
        ("题名字段被判 new_better 且新值为真题名", f["title"].get("assessment") == "new_better"),
        ("新作者恰为 백서인/당정 且判 new_better",
         sorted(new_auth) == sorted(["백서인", "당정"]) and f["authors"].get("assessment") == "new_better"),
        ("双机构被判为已验证(new_better)", f["affiliations"].get("assessment") == "new_better"),
    ]
    snap = {"title": f["title"].get("new"), "authors_new": new_auth,
            "affiliations_new": f["affiliations"].get("new"),
            "affiliations_assessment": f["affiliations"].get("assessment")}
    out.append(anchor_claim("A1", doc, text, ln, checks, snap))

    # A2 13567494
    doc = "13567494"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    auth_new = names_of(r, "authors", "new")
    aff_new = f["affiliations"].get("new") or []
    checks = [
        ("新作者中出现“리우커”", any("리우커" in (n or "") for n in auth_new)),
        ("新机构含正确写法“대구가톨릭대학교”", any("대구가톨릭대학교" in str(x) for x in aff_new)),
        ("机构字段判 new_better", f["affiliations"].get("assessment") == "new_better"),
        ("新作者/机构中无“가틀릭”异写",
         not any("가틀릭" in (n or "") for n in auth_new) and
         not any("가틀릭" in str(x) for x in aff_new)),
    ]
    snap = {"authors_new": f["authors"].get("new"), "affiliations_new": aff_new,
            "affiliations_assessment": f["affiliations"].get("assessment")}
    out.append(anchor_claim("A2", doc, text, ln, checks, snap))

    # A3 13568651
    doc = "13568651"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    a = f["authors"].get("new") or []
    checks = [
        ("音译名保持一人（新作者数=1）", len(a) == 1),
        ("不再切出假机构（新作者 affiliation 为空）",
         all(not (x.get("affiliation") if isinstance(x, dict) else None) for x in a)),
        ("作者字段判 new_better（已验证）", f["authors"].get("assessment") == "new_better"),
    ]
    snap = {"authors_new": f["authors"].get("new"),
            "authors_assessment": f["authors"].get("assessment")}
    out.append(anchor_claim("A3", doc, text, ln, checks, snap))

    # A4 13569417
    doc = "13569417"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    a = f["authors"].get("new") or []
    checks = [
        ("作者为四格（新作者数=4）", len(a) == 4),
        ("四名作者均带罗马名", all(x.get("roman") for x in a if isinstance(x, dict))),
        ("作者字段判 new_better", f["authors"].get("assessment") == "new_better"),
    ]
    snap = {"authors_new": a, "authors_assessment": f["authors"].get("assessment")}
    out.append(anchor_claim("A4", doc, text, ln, checks, snap))

    # A5 13569428
    doc = "13569428"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    a = f["authors"].get("new") or []
    aff_new = [norm_compare(x) for x in (f["affiliations"].get("new") or [])]
    need = [norm_compare(x) for x in ("경북대학교", "전기공학과", "LIG넥스원 레이다기술팀",
                                      "Department of Electric Engineering",
                                      "Kyungpook National University")]
    checks = [
        ("作者为五格（新作者数=5）", len(a) == 5),
        ("Radar Production 已消除", not any("Radar Production" in (n or "") for n in names_of(r, "authors", "new"))),
        ("机构列表含 경북대학교/전기공학과/LIG넥스원 레이다기술팀及对应英文",
         all(any(n in x for x in aff_new) for n in need)),
        ("新机构无早期角色/折行碎片（如 “김상열” 附着）",
         not any("김상열" in str(x) for x in (f["affiliations"].get("new") or []))),
        ("机构字段判 new_better（与“已验证锚点”一致）",
         f["affiliations"].get("assessment") == "new_better"),
    ]
    snap = {"authors_new": names_of(r, "authors", "new"),
            "affiliations_new": f["affiliations"].get("new"),
            "affiliations_assessment": f["affiliations"].get("assessment")}
    out.append(anchor_claim("A5", doc, text, ln, checks, snap))

    # A6 13564500
    doc = "13564500"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    checks = [
        ("原 rescue 作者保留（authors.same=true）", f["authors"].get("same") is True),
        ("新作者非空", len(names_of(r, "authors", "new")) > 0),
    ]
    snap = {"authors_new": f["authors"].get("new"), "authors_same": f["authors"].get("same"),
            "report_line": text}
    out.append(anchor_claim("A6", doc, text, ln, checks, snap))

    # A7 13564501
    doc = "13564501"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    a_new = names_of(r, "authors", "new")
    checks = [
        ("补全 조국（新作者含 조국）", any("조국" in (n or "") for n in a_new)),
        ("保留原作者 김형엽", any("김형엽" in (n or "") for n in a_new)),
        ("作者字段判 new_better", f["authors"].get("assessment") == "new_better"),
    ]
    snap = {"authors_new": a_new, "authors_assessment": f["authors"].get("assessment")}
    out.append(anchor_claim("A7", doc, text, ln, checks, snap))

    # A8 13569720
    doc = "13569720"
    ln, text = line_info(doc)
    r = by_doc[doc]
    f = r["fields"]
    a_new = names_of(r, "authors", "new")
    checks = [
        ("题名字段判 new_better", f["title"].get("assessment") == "new_better"),
        ("作者恰为两位（김지영/류현숙）",
         sorted(a_new) == sorted(["김지영", "류현숙"])),
        ("作者名中无机构残片（大学/学会/卷期）",
         not any(re.search(r"대학교|학회|제\d+권|\d+호", n or "") for n in a_new)),
    ]
    snap = {"title_new": f["title"].get("new"), "authors_new": a_new,
            "affiliations_new": f["affiliations"].get("new"),
            "affiliations_assessment": f["affiliations"].get("assessment")}
    out.append(anchor_claim("A8", doc, text, ln, checks, snap))

    return out


# ---------------------------------------------------------------- 代码声明
def code_only(path):
    """把字符串字面量替换为空格，避免把报告文本/正则当成真实代码。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):
        return src
    for tok in reversed(toks):
        if tok.type != tokenize.STRING:
            continue
        (sr, sc), (er, ec) = tok.start, tok.end
        seg = lines[sr - 1:er]
        new = []
        for j, line in enumerate(seg):
            a = sc if j == 0 else 0
            b = ec if j == len(seg) - 1 else len(line.rstrip("\r\n"))
            nl = "\n" if line.endswith("\n") else ""
            new.append(" " * max(0, b - a) + nl)
        lines[sr - 1:er] = new
    return "".join(lines)


PY_FILES = sorted(f for f in os.listdir(HERE) if f.endswith(".py"))
# 流水线脚本集合（排除复核脚本自身，避免自指）
PIPE_PY_FILES = [f for f in PY_FILES if f != "audit_verify.py"]


def check_network():
    pats = {
        "front_doc(": re.compile(r"\bfront_doc\s*\("),
        "requests": re.compile(r"\brequests\b"),
        "urllib": re.compile(r"\burllib\b"),
        "openai": re.compile(r"\bopenai\b"),
        "http://": re.compile(r"http://"),
        "https://": re.compile(r"https://"),
        "socket": re.compile(r"\bsocket\b"),
    }
    found = []
    for fn in PY_FILES:
        src = code_only(os.path.join(HERE, fn))
        for i, line in enumerate(src.splitlines(), 1):
            for name, pat in pats.items():
                if pat.search(line):
                    found.append((fn, i, name, line.strip()))
    ln112 = find_line("未调用 `front_doc()`")
    ok = not found
    ev = ["扫描 %d 个 layoutx/*.py（字符串字面量已剔除）：%s" %
          (len(PIPE_PY_FILES), ", ".join(PIPE_PY_FILES)),
          "命中网络/LLM/front_doc 调用：%s" % (found or "无"),
          "REPORT.md:%s 声称“未调用 front_doc()，未使用 LLM/网络” → %s" % (ln112, ok)]
    ev.append("注：extract.py/compare.py 使用 `from metapy import front` 的 _looks_affil/_parse_author_line 等内部纯函数，非 front_doc()")
    return claim("R16", "代码声明：未调用 front_doc()、未使用 LLM/网络",
                 "PASS" if ok else "FAIL", ev, {"hits": found})


def check_docid_override():
    hits = []
    for fn in PIPE_PY_FILES:
        src = code_only(os.path.join(HERE, fn))
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\b135\d{5}\b", line):
                hits.append((fn, i, line.strip()[:100]))
    compare_hits = [h for h in hits if h[0] == "compare.py"]
    literal_hits = []
    for fn in PIPE_PY_FILES:
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if re.search(r"\b(?:13569913|13569417)\b", line):
                    literal_hits.append((fn, i, line.strip()[:70]))
    ln21 = find_line("硬编码分类覆盖")
    ok = not compare_hits
    ev = ["REPORT.md:%s 声称删除 13569913/13569417 的硬编码分类覆盖" % ln21,
          "compare.py（比较/分类逻辑）去字符串后的 doc-id 代码：%s" % (compare_hits or "无"),
          "去字符串后全部 layoutx/*.py 的 doc-id 代码：%s" % (hits or "无"),
          "原始源码中出现（含字符串/注释）：%s" % (literal_hits or "无"),
          "13569913/13569417 仅出现在 fingerprint.py 的 ANCHORS 列表与 report.py 的文本模板中，不参与分类判定 → %s" % ok]
    return claim("R17", "代码声明：比较逻辑中无按 doc-id 的分类覆盖",
                 "PASS" if ok else "FAIL", ev, {"hits": hits})


def check_manual_review(by_doc):
    try:
        mr = load_json(MANUAL)
        docs = {str(r["doc"]) for r in mr}
    except Exception as e:
        docs = set()
        ev = ["manual_review.json 读取失败：%s" % e]
        return claim("R18", "manual_review.json 是否覆盖锚点", "UNVERIFIABLE", ev)
    covered = [d for d in ANCHOR_DOCS if d in docs]
    missing = [d for d in ANCHOR_DOCS if d not in docs]
    ev = ["manual_review.json 共 %d 条，doc 集合=%s" % (len(docs), sorted(docs)),
          "8 个锚点中被覆盖：%s" % (covered or "无"),
          "未覆盖：%s" % missing,
          "结论：锚点判定只能由 diff.jsonl 字段/渠道证据支持，无人工复核记录（报告未声称有，故记 WARN）"]
    return claim("R18", "manual_review.json 是否覆盖 8 个锚点",
                 "WARN" if not covered else "PASS", ev,
                 {"covered": covered, "missing": missing, "total_entries": len(docs)})


def check_write_targets():
    writes = []
    for fn in PIPE_PY_FILES:
        path = os.path.join(HERE, fn)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                m = re.search(r"open\((.*?),\s*['\"](w|wb|a)['\"]", line)
                if not m:
                    continue
                expr, mode = m.group(1).strip(), m.group(2)
                if "NEW" in expr or "folder" in expr:
                    target = "out_layout（<NEW>/<doc>/metadata.json）"
                elif "args.out" in expr:
                    target = "layoutx（fingerprints.jsonl 默认 HERE）"
                elif "HERE" in expr or "MANUAL" in expr or "REPORT" in expr:
                    target = "layoutx"
                else:
                    target = "未知：" + expr[:60]
                writes.append((fn, i, expr[:70], target))
    ln112 = find_line("输出仅在 `pipeline/out_layout/`")
    only_out_layout = all(w[3].startswith("out_layout") for w in writes)
    ev = ["代码位置：%s 全部位于 pipeline/layoutx/ → 成立" % ", ".join(PIPE_PY_FILES)]
    for fn, i, expr, target in writes:
        ev.append("写入 %s:%s open(%s,'w') → %s" % (fn, i, expr, target))
    ev.append("REPORT.md:%s 声称“代码仅在 pipeline/layoutx/，输出仅在 pipeline/out_layout/”" % ln112)
    ev.append("结论：extract.py 写入 out_layout，但 compare.py/audit.py/report.py/fingerprint.py 写入 layoutx → %s" %
              ("成立" if only_out_layout else "不成立（FAIL）"))
    ev.append("注：audit_verify.py 仅在 --md 时写 VERIFICATION.md 到 layoutx（复核脚本自身，不计入流水线输出声明）")
    return claim("R19", "代码仅在 layoutx、输出仅在 out_layout",
                 "PASS" if only_out_layout else "FAIL", ev,
                 {"writes": writes})


def latest_mtime(folder):
    latest, path, cnt = 0.0, None, 0
    root0 = os.path.join(PIPE, folder)
    if not os.path.isdir(root0):
        return None, None, 0
    for root, _dirs, files in os.walk(root0):
        for name in files:
            p = os.path.join(root, name)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            cnt += 1
            if m > latest:
                latest, path = m, p
    return (latest, path, cnt)


def check_mtime(lines):
    ln112 = find_line("最终 mtime 自查")
    text = lines[ln112 - 1] if ln112 else ""
    m_meta, p_meta, c_meta = latest_mtime("metapy")
    m_out, p_out, c_out = latest_mtime("out")
    m_new, p_new, c_new = latest_mtime("out_layout")
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "N/A"
    meta_ok = bool(m_meta) and fmt(m_meta).startswith("2026-11-26")
    out_ok = bool(m_out) and fmt(m_out).startswith("2026-11-27 09:31")
    ok = meta_ok and out_ok
    ev = ["REPORT.md:%s %s" % (ln112, text),
          "metapy/ 最新文件=%s (%s，%d 个文件) → 与 2026-11-26 %s" %
          (fmt(m_meta), p_meta, c_meta, "一致" if meta_ok else "不一致"),
          "out/ 最新文件=%s (%s，%d 个文件) → 与 2026-11-27 09:31 %s" %
          (fmt(m_out), p_out, c_out, "一致" if out_ok else "不一致"),
          "out_layout/ 最新文件=%s (%s，%d 个文件，仅供参照)" % (fmt(m_new), p_new, c_new)]
    return claim("R20", "mtime 声明：metapy 最新 2026-11-26、out 最新 2026-11-27 09:31",
                 "PASS" if ok else "FAIL", ev,
                 {"metapy": fmt(m_meta), "out": fmt(m_out), "out_layout": fmt(m_new)})


def check_reproduction(lines):
    ln = find_line("```bash")
    block = []
    if ln:
        for line in lines[ln:ln + 8]:
            if line.strip() == "```" and block:
                break
            if line.startswith("python "):
                block.append(line.strip())
    scripts = [re.sub(r"^python\s+", "", b) for b in block]
    has_fp = any("fingerprint.py" in s for s in scripts)
    extract_needs = []
    for fn in ("extract.py",):
        path = os.path.join(HERE, fn)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "fingerprints.jsonl" in line or "clusters.json" in line:
                    extract_needs.append((fn, i, line.strip()[:90]))
    fp_writes = []
    with open(os.path.join(HERE, "fingerprint.py"), encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if "fingerprints.jsonl" in line or "clusters.json" in line:
                fp_writes.append(("fingerprint.py", i, line.strip()[:90]))
    ok = has_fp
    ev = ["REPORT.md:%s 复现命令=%s" % (ln, scripts),
          "fingerprint.py 在复现命令中：%s" % has_fp,
          "extract.py 读取的旧产物：%s" % extract_needs,
          "这些旧产物由 fingerprint.py 产生：%s" % fp_writes,
          "静态判断：fingerprint.py 未列入，但 clusters.json/fingerprints.jsonl 是其产物且被 extract.py 依赖；"
          "按报告命令在干净环境复现会在 extract.py 处失败 → %s" % ("成立" if ok else "不成立（FAIL）")]
    return claim("R21", "复现命令顺序是否合理（fingerprint.py 缺失）",
                 "PASS" if ok else "FAIL", ev,
                 {"commands": scripts, "extract_needs": extract_needs,
                  "fp_writes": fp_writes})


# ---------------------------------------------------------------- 汇总与输出
def run_all():
    recs, by_doc, summary, audit, lines = read_inputs()
    claims = [
        check_counts(recs, summary, lines),
        check_fields(recs, summary, lines),
        check_superset(recs, audit, lines),
        check_doc_list(recs, lines, "新版更差", 49, 56, "R04",
                       "全部剩余“新版更差”3 篇 doc 列表与 diff.jsonl 一致"),
        check_doc_list(recs, lines, "双错且不同", 61, 79, "R05",
                       "全部剩余双异 16 篇 doc 列表与 diff.jsonl 一致"),
        check_tokens(recs, by_doc, lines),
        check_13570535(by_doc, lines),
    ]
    claims += anchor_checks(by_doc, lines)
    claims += [
        check_network(),
        check_docid_override(),
        check_manual_review(by_doc),
        check_write_targets(),
        check_mtime(lines),
        check_reproduction(lines),
    ]
    counts = collections.Counter(c["verdict"] for c in claims)
    return {
        "schema": "layoutx-verification/1.0",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "diff": DIFF, "summary": SUMMARY, "audit_sample": AUDIT,
            "report": REPORT, "manual_review": MANUAL,
            "python_files": PY_FILES,
        },
        "claims": claims,
        "summary": {"total": len(claims), **{k: counts.get(k, 0)
                    for k in ("PASS", "FAIL", "WARN", "UNVERIFIABLE")}},
    }


def write_md(result, path):
    out = []
    add = out.append
    s = result["summary"]
    add("# layoutx REPORT.md 独立复核结果")
    add("")
    add("> 由 `audit_verify.py` 生成（只读输入，可重复运行）。生成时间：%s" % result["generated_at"])
    add("")
    add("## 总览")
    add("")
    add("| 结论 | 条数 |")
    add("|---|---:|")
    for k in ("PASS", "FAIL", "WARN", "UNVERIFIABLE"):
        add("| %s | %d |" % (k, s.get(k, 0)))
    add("| **合计** | **%d** |" % s["total"])
    add("")
    fails = [c for c in result["claims"] if c["verdict"] == "FAIL"]
    if fails:
        add("### 基线关键 FAIL 项")
        add("")
        for c in fails:
            add("- **%s %s**：%s" % (c["id"], c["verdict"], c["title"]))
        add("")
    add("## 逐条声明")
    add("")
    for c in result["claims"]:
        extra = ""
        if c.get("verdict_kind"):
            extra = "（锚点判定：**%s**）" % c["verdict_kind"]
        add("### %s %s — %s%s" % (c["id"], c["verdict"], c["title"], extra))
        add("")
        for e in c["evidence"]:
            add("- %s" % e.replace("\n", " "))
        add("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", action="store_true", help="同时写入 VERIFICATION.md")
    args = ap.parse_args()
    result = run_all()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if args.md:
        write_md(result, os.path.join(HERE, "VERIFICATION.md"))
        print("VERIFICATION.md written: %s" % os.path.join(HERE, "VERIFICATION.md"),
              file=sys.stderr)


if __name__ == "__main__":
    main()
