#!/usr/bin/env python3
"""独立复核脚本：对 layoutx/REPORT.md 的每条可核查声明做机器校验。

只读输入（diff.jsonl / comparison_summary.json / audit_sample.json /
REPORT.md / layoutx/*.py / manual_review.json / metapy/ / out/ / out_layout/），
不运行任何流水线主流程。结构化结果输出到 stdout；可选 --md 写入 VERIFICATION.md。

对新旧两版 REPORT.md 均做结构自适应解析：按小节标题定位表格/正文，
不依赖固定行号；缺失的分类键按 0 处理，不把缺失当失败。

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
CATS = ("新版更优", "一致", "双错且不同", "新版更差")
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


def section_bounds(lines, keyword):
    """返回 (标题行号, 下一节标题行号)；行号 1-based，区间左闭右开。"""
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and keyword in line:
            start = i + 1
            break
    if start is None:
        return None
    for j in range(i + 1, len(lines)):
        if lines[j].startswith("## "):
            return (start, j + 1)
    return (start, len(lines) + 1)


def section_range(lines, keyword):
    b = section_bounds(lines, keyword)
    return range(b[0], b[1]) if b else range(1, len(lines) + 1)


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


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return ", ".join(str(a.get("name")) for a in v)
        return ", ".join(str(x) for x in v)
    return str(v)


def value_match(reported, actual):
    """报告表格值常被 short() 截断为前缀+省略号，按归一化前缀比对。"""
    r = re.sub(r"[…\.]{1,3}$", "", str(reported or "")).strip()
    nr, na = norm_compare(r), norm_compare(cell_text(actual))
    # 报告表格把 Python None 写作字面量 "None"/"null"，与空值等价
    if nr in ("none", "null"):
        nr = ""
    if not nr:
        return not na
    return na.startswith(nr)


def assessment_of(v):
    return "—" if v.get("same") else v.get("assessment", "—")


def claim(cid, title, verdict, evidence, details=None, **extra):
    c = {"id": cid, "title": title, "verdict": verdict, "evidence": evidence}
    if details is not None:
        c["details"] = details
    c.update(extra)
    return c


def read_inputs():
    recs = load_jsonl(DIFF)
    by_doc = {r["doc"]: r for r in recs}
    return recs, by_doc, load_json(SUMMARY), load_json(AUDIT), report_lines()


# ---------------------------------------------------------------- 报告解析
def parse_category_counts(lines):
    """返回 {类别: (值, 行号, 来源)}；正文与对比表交叉取证。"""
    res = {}
    for i, line in enumerate(lines, 1):
        if "分类计数" in line and "合计" in line:
            for k in CATS:
                m = re.search(re.escape(k) + r"\s*(\d+)", line)
                if m:
                    res[k] = (int(m.group(1)), i, "正文「分类计数」")
            m = re.search(r"合计\s*(\d+)", line)
            if m:
                res["__total__"] = (int(m.group(1)), i, "正文「分类计数」")
    # 基线 vs 本轮对比表：取最后一列（本轮重跑）
    for i in section_range(lines, "基线 vs 本轮对比"):
        cells = [c.strip() for c in lines[i - 1].strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] in CATS and re.fullmatch(r"\d+", cells[-1]):
            res.setdefault(cells[0], (int(cells[-1]), i, "基线对比表·本轮列"))
    # 旧版简单分类表
    for i, line in enumerate(lines, 1):
        m = re.match(r"^\|\s*(新版更优|一致|双错且不同|新版更差)\s*\|\s*(\d+)\s*\|", line)
        if m:
            res.setdefault(m.group(1), (int(m.group(2)), i, "分类表"))
    return res


def parse_field_table(lines):
    res = {}
    for i in section_range(lines, "字段计数"):
        m = re.match(r"^\|\s*(title|title_en|authors|affiliations|keywords|keywords_en)\s*\|"
                     r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", lines[i - 1])
        if m:
            res[m.group(1)] = ({"same": int(m.group(2)), "new_better": int(m.group(3)),
                                "new_worse": int(m.group(4)),
                                "different_unresolved": int(m.group(5))}, i)
    return res


def parse_superset_section(lines):
    info = {"seed": None, "sample_count": None, "sample_docs": [],
            "narr_line": None, "jorok_line": None, "table_docs": []}
    for i in section_range(lines, "作者超集专项"):
        line = lines[i - 1]
        if "种子" in line and "抽取" in line:
            info["narr_line"] = i
            m = re.search(r"种子\s*\*{0,2}(\d{6,})\*{0,2}", line)
            if m:
                info["seed"] = int(m.group(1))
            m = re.search(r"抽取\s*\*{0,2}(\d+)\s*篇", line)
            if m:
                info["sample_count"] = int(m.group(1))
            tail = line.split("：")[-1]
            info["sample_docs"] = re.findall(r"135\d{5}", tail)
        if "조록" in line and ("复算" in line or "残留" in line or "清零" in line):
            info["jorok_line"] = i
        m = re.match(r"^\|\s*(\d{8})\s*\|", line)
        if m:
            info["table_docs"].append(m.group(1))
    return info


def parse_doc_list_section(lines, heading_keyword):
    docs, declared = [], None
    for i in section_range(lines, heading_keyword):
        line = lines[i - 1]
        m = re.search(r"共\s*\*\*(\d+)\s*篇\*\*", line)
        if m and declared is None:
            declared = int(m.group(1))
        m = re.match(r"^\|\s*(\d{8})\s*\|", line)
        if m:
            docs.append(m.group(1))
    return declared, docs


def parse_anchor_table(lines):
    """{doc: {field: (old, new, assessment, 行号)}}"""
    res = {}
    for i in section_range(lines, "锚点"):
        parts = [c.strip() for c in lines[i - 1].strip().strip("|").split("|")]
        if len(parts) >= 5 and re.fullmatch(r"\d{8}", parts[0]) and parts[1] in FIELDS:
            res.setdefault(parts[0], {})[parts[1]] = (parts[2], parts[3], parts[4], i)
    return res


# ---------------------------------------------------------------- 声明 R01/R02
def check_counts(recs, summary, lines):
    total = len(recs)
    cc = collections.Counter(r["category"] for r in recs)
    rep = parse_category_counts(lines)
    exp = {k: summary["categories"].get(k, 0) for k in CATS}
    ok = total == summary["total"]
    ev = ["diff.jsonl 实际行数=%d；comparison_summary.json total=%d" %
          (total, summary["total"])]
    for k in CATS:
        rval, rln, src = rep.get(k, (None, None, None))
        row_ok = (cc.get(k, 0) == exp[k] and rval == exp[k])
        ok = ok and row_ok
        if rval is None:
            ev.append("%s：diff=%d 汇总=%d；报告表格未列该行，正文声明 %d（按 %d 处理）→ %s" %
                      (k, cc.get(k, 0), exp[k], exp[k], exp[k], row_ok))
        else:
            ev.append("%s：diff=%d 汇总=%d 报告=%d(%s，REPORT.md:%s) → %s" %
                      (k, cc.get(k, 0), exp[k], rval, src, rln, row_ok))
    if "__total__" in rep:
        t, ln, _ = rep["__total__"]
        ev.append("报告正文合计=%d（REPORT.md:%s），与 diff 行数 %d %s" %
                  (t, ln, total, "一致" if t == total else "不一致"))
        ok = ok and t == total
    ev.append("注：基线对比表的「返工前基线」列为历史常量，非本轮数据，本机无法从当前产物复核。")
    return claim("R01", "diff.jsonl 总数/分类计数与汇总、报告表格/正文一致",
                 "PASS" if ok else "FAIL", ev,
                 {"total": total, "diff": dict(cc), "summary": exp,
                  "report": {k: v[0] for k, v in rep.items()}})


def check_fields(recs, summary, lines):
    total = len(recs)
    fc = collections.Counter()
    for r in recs:
        for f, v in r["fields"].items():
            fc[(f, "same" if v["same"] else v.get("assessment", "same"))] += 1
    rep = parse_field_table(lines)
    details, ev, ok = {}, [], True
    for f in FIELDS:
        got = {k[1]: n for k, n in fc.items() if k[0] == f}
        exp = {k: summary["fields"][f].get(k, 0)
               for k in ("same", "new_better", "new_worse", "different_unresolved")}
        rval, ln = rep.get(f, ({}, None))
        s = sum(got.values())
        row_ok = all(got.get(k, 0) == exp[k] == rval.get(k) for k in exp) and s == total
        ok = ok and row_ok
        details[f] = {"diff": got, "summary": exp, "report": rval, "sum": s}
        ev.append("%s：四项和=%d(=总数%d) diff=%s 汇总=%s 报告(REPORT.md:%s)=%s → %s" %
                  (f, s, total, got, exp, ln, rval, "一致" if row_ok else "不一致"))
    return claim("R02", "六字段 same/new_better/new_worse/different_unresolved 计数与汇总一致且每项和为总数",
                 "PASS" if ok else "FAIL", ev, details)


# ---------------------------------------------------------------- 声明 R03
def check_superset(recs, audit, lines):
    sup = [r for r in recs
           if {norm_audit(a.get("name")) for a in r["fields"]["authors"]["old"]} <
              {norm_audit(a.get("name")) for a in r["fields"]["authors"]["new"]} and
           r["fields"]["authors"].get("assessment") == "new_better"]
    k = max(1, (len(sup) + 9) // 10)
    expected = [r["doc"] for r in random.Random(20261127).sample(sup, k)] if sup else []
    actual = [r["doc"] for r in audit.get("author_superset_sample", [])]
    total = audit.get("author_superset_total", 0)
    ratio = len(actual) / total if total else 0
    info = parse_superset_section(lines)
    checks = {
        "seed": audit.get("seed") == 20261127 == info["seed"],
        "total": total == len(sup) and total > 0,
        "sample": sorted(actual) == sorted(expected),
        "ratio": ratio > 0.10,
        "declared_sample": sorted(info["sample_docs"]) == sorted(actual),
        "declared_count": info["sample_count"] == len(actual),
        "table": sorted(info["table_docs"]) == sorted(r["doc"] for r in sup),
    }
    ok = all(checks.values())
    ev = ["audit.py 逻辑重算：superset=%d 篇（old_names < new_names 且 assessment==new_better）" % len(sup),
          "audit_sample.json seed=%s（要求 20261127）→ %s" % (audit.get("seed"), checks["seed"]),
          "audit_sample.json total=%d（重算=%d）→ %s" % (total, len(sup), checks["total"]),
          "sample 实际=%s，random.Random(20261127).sample(k=%d) 复算=%s → %s" %
          (actual, k, expected, checks["sample"]),
          "抽样比例=%d/%d=%.1f%% > 10%% → %s" % (len(actual), total, ratio * 100, checks["ratio"]),
          "报告正文(REPORT.md:%s)声明 seed=%s、抽取 %s 篇、样本=%s，与 audit_sample 一致 → seed:%s count:%s docs:%s" %
          (info["narr_line"], info["seed"], info["sample_count"], info["sample_docs"],
           checks["seed"], checks["declared_count"], checks["declared_sample"]),
          "报告超集表 doc 列表(%d)与重算一致 → %s" % (len(info["table_docs"]), checks["table"])]
    return claim("R03", "作者超集判优 %d 篇、audit_sample.json 的 seed/total/sample 与抽样超 10%%" % len(sup),
                 "PASS" if ok else "FAIL", ev,
                 {"recomputed_total": len(sup), "sample": actual, "expected_sample": expected,
                  "ratio": ratio, "report_superset_docs": info["table_docs"]})


# ---------------------------------------------------------------- 声明 R04/R05
def check_doc_list(recs, lines, category, keyword, cid, title):
    actual = [r["doc"] for r in recs if r["category"] == category]
    declared, reported = parse_doc_list_section(lines, keyword)
    ok = actual == reported and (declared is None or declared == len(actual))
    ev = ["diff.jsonl category=='%s' 共 %d 篇：%s" % (category, len(actual), actual),
          "REPORT.md「%s」节声明共 %s 篇，表格逐条 %d 篇：%s" %
          (keyword, declared, len(reported), reported),
          "逐条顺序与集合一致 → %s" % ok]
    return claim(cid, title, "PASS" if ok else "FAIL", ev,
                 {"actual": actual, "reported": reported, "declared": declared})


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
    info = parse_superset_section(lines)
    ok = all(not hits[t] for t in TOKENS)
    ev = []
    for t in TOKENS:
        ev.append("token %s：新作者命中 %d 篇 %s" % (t, len(hits[t]), hits[t][:8] if hits[t] else ""))
    ev.append("REPORT.md:%s 声明「조록 复算…0 篇，已从新作者中清零」→ %s" %
              (info["jorok_line"], not hits["조록"]))
    ev.append("结论：7 个 token 在新作者中命中数全部为 0 → %s（仅为规则数据核对，非人工真值）" % ok)
    return claim("R06", "token 清理声明：7 个 token 是否仍出现在新作者中",
                 "PASS" if ok else "FAIL", ev, {"hits": hits})


def check_old_13570535_claim(by_doc, lines):
    """新报告已无「13570535 清掉 조록」声明；保留此 ID 以说明其状态。"""
    ln = find_line("13570535 清掉")
    if ln is None:
        r = by_doc.get("13570535")
        new_names = names_of(r, "authors", "new") if r else []
        ev = ["新 REPORT.md 中不再存在「13570535 清掉 조록」声明，原声明已撤回 → 无声明可核。",
              "当前数据供参照：diff.jsonl 13570535 新作者=%s（含 조록=%s）" %
              (new_names, any("조록" in (n or "") for n in new_names)),
              "当前报告的 조록 清零声明见 R06（REPORT.md 作者超集专项）。"]
        return claim("R07", "旧声明「13570535 清掉 조록 但保留 배성중」", "UNVERIFIABLE", ev,
                     {"claim_present": False, "new_authors": new_names},
                     anchor_verdict="UNVERIFIABLE")
    line = lines[ln - 1]
    r = by_doc.get("13570535")
    new_names = names_of(r, "authors", "new") if r else []
    claim_removed = "清掉 조록" in line
    ok = claim_removed and not any("조록" in (n or "") for n in new_names)
    ev = ["REPORT.md:%s %s" % (ln, line),
          "diff.jsonl 13570535 新作者=%s" % new_names,
          "声明与数据一致 → %s" % ok]
    return claim("R07", "旧声明「13570535 清掉 조록 但保留 배성중」",
                 "PASS" if ok else "FAIL", ev,
                 {"claim_present": True, "new_authors": new_names},
                 anchor_verdict="TRUE" if ok else "FALSE")


# ---------------------------------------------------------------- 锚点（新表：字段与判定）
def anchor_table_claim(by_doc, lines):
    rep = parse_anchor_table(lines)
    out = []
    for idx, doc in enumerate(ANCHOR_DOCS, 1):
        rows = rep.get(doc, {})
        r = by_doc.get(doc)
        checks = []
        check_lines = []
        if not rows or not r:
            verdict_kind = "UNVERIFIABLE"
            checks.append(("报告锚点表/数据中是否存在该 doc", False))
        else:
            checks.append(("报告表覆盖全部 6 个字段", len(rows) == len(FIELDS)))
            for f in FIELDS:
                row = rows.get(f)
                if row is None:
                    checks.append(("字段 %s 有报告行" % f, False))
                    continue
                rv_old, rv_new, rv_assess, ln = row
                v = r["fields"][f]
                a_ok = rv_assess == assessment_of(v)
                o_ok = value_match(rv_old, v.get("old"))
                n_ok = value_match(rv_new, v.get("new"))
                checks.append(("字段 %s 的 assessment 与 diff.jsonl 一致(REPORT.md:%s)" % (f, ln), a_ok))
                checks.append(("字段 %s 的旧值前缀一致" % f, o_ok))
                checks.append(("字段 %s 的新值前缀一致" % f, n_ok))
                if not (a_ok and o_ok and n_ok):
                    check_lines.append("REPORT.md:%s %s 报告(old=%s,new=%s,assess=%s) 实际(old=%s,new=%s,assess=%s)" %
                                       (ln, f, rv_old[:40], rv_new[:40], rv_assess,
                                        cell_text(v.get("old"))[:40], cell_text(v.get("new"))[:40],
                                        assessment_of(v)))
            passed = sum(1 for _, ok in checks if ok)
            if passed == len(checks):
                verdict_kind = "TRUE"
            elif passed == 0:
                verdict_kind = "FALSE"
            else:
                verdict_kind = "PARTIAL"
        snap = {f: assessment_of(r["fields"][f]) for f in FIELDS} if r else {}
        ev = ["报告锚点表取自 diff.jsonl，且明示「不作人工正确性断言」→ 人工真值 UNVERIFIABLE。"]
        ev += ["%s → %s" % (d, "成立" if ok else "不成立") for d, ok in checks[:4]]
        if len(checks) > 4:
            ev.append("其余 %d 项字段级核对：%d 成立 / %d 不成立" %
                      (len(checks) - 4, sum(1 for _, ok in checks[4:] if ok),
                       sum(1 for _, ok in checks[4:] if not ok)))
        ev += check_lines[:4]
        ev.append("关键证据摘录(≤200字)：" + json.dumps(snap, ensure_ascii=False)[:200])
        ev.append("manual_reason：锚点字段中不存在；manual_review.json 未覆盖任何锚点（见 R18）。")
        out.append(claim("A%d" % idx, "锚点 %s：报告字段表与 diff.jsonl 一致性" % doc,
                         VERDICT_MAP[verdict_kind], ev,
                         {"doc": doc, "checks": [{"desc": d, "ok": ok} for d, ok in checks],
                          "human_truth": "UNVERIFIABLE"},
                         verdict_kind=verdict_kind))
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
    for fn in PIPE_PY_FILES:
        src = code_only(os.path.join(HERE, fn))
        for i, line in enumerate(src.splitlines(), 1):
            for name, pat in pats.items():
                if pat.search(line):
                    found.append((fn, i, name, line.strip()))
    ln = find_line("静态扫描（剔除字符串字面量）未发现")
    ok = not found
    ev = ["扫描 %d 个 layoutx/*.py（字符串字面量已剔除）：%s" %
          (len(PIPE_PY_FILES), ", ".join(PIPE_PY_FILES)),
          "命中网络/LLM/front_doc 调用：%s" % (found or "无"),
          "REPORT.md:%s 声明静态扫描命中 0 处 → %s" % (ln, ok),
          "注：`from metapy import front` 导入的是内部纯函数，非 front_doc() 调用。"]
    return claim("R16", "代码声明：未调用 front_doc()、未使用 LLM/网络",
                 "PASS" if ok else "FAIL", ev, {"hits": found})


def check_docid_override():
    hits = []
    for fn in PIPE_PY_FILES:
        src = code_only(os.path.join(HERE, fn))
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\b135\d{5}\b", line):
                hits.append((fn, i, line.strip()[:100]))
    literal_hits = []
    for fn in PIPE_PY_FILES:
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if re.search(r"\b(?:13569913|13569417)\b", line):
                    literal_hits.append((fn, i, line.strip()[:70]))
    ln = find_line("doc-id 仅作为") or find_line("未发现按 doc-id 的分类特判")
    ok = not hits
    ev = ["REPORT.md:%s 声明比较/抽取逻辑无按 doc-id 的分类特判" % ln,
          "去字符串后 compare.py/extract.py 中的 doc-id 代码：%s" %
          ([h for h in hits if h[0] in ("compare.py", "extract.py")] or "无"),
          "去字符串后全部 layoutx/*.py 的 doc-id 代码：%s" % (hits or "无"),
          "原始源码出现（含字符串/注释）：%s" % (literal_hits or "无"),
          "doc-id 仅见 fingerprint.py 的 ANCHORS 展示清单，不参与判定 → %s" % ok]
    return claim("R17", "代码声明：比较/抽取逻辑中无按 doc-id 的分类覆盖",
                 "PASS" if ok else "FAIL", ev, {"hits": hits})


def check_manual_review(lines):
    try:
        mr = load_json(MANUAL)
        docs = {str(r["doc"]) for r in mr}
    except Exception as e:
        return claim("R18", "manual_review.json 是否覆盖锚点", "UNVERIFIABLE",
                     ["manual_review.json 读取失败：%s" % e])
    covered = [d for d in ANCHOR_DOCS if d in docs]
    missing = [d for d in ANCHOR_DOCS if d not in docs]
    ln = find_line("不作人工正确性断言")
    ev = ["manual_review.json 共 %d 条；8 个锚点被覆盖：%s" % (len(docs), covered or "无"),
          "未覆盖：%s" % missing,
          "REPORT.md:%s 明示「不作人工正确性断言」，故本报告无人工真值可供核对" % ln,
          "结论：仍属未做全量人工验证，记 WARN（不把规则判定当人工真值）"]
    return claim("R18", "manual_review.json 是否覆盖 8 个锚点",
                 "WARN" if not covered else "PASS", ev,
                 {"covered": covered, "missing": missing, "total_entries": len(docs)})


def check_write_targets():
    writes = []
    for fn in PIPE_PY_FILES:
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                m = re.search(r"open\((.*?),\s*['\"](w|wb|a)['\"]", line)
                if not m:
                    continue
                expr = m.group(1).strip()
                if "NEW" in expr or "folder" in expr:
                    target = "out_layout"
                elif "args.out" in expr:
                    target = "layoutx"
                elif any(x in expr for x in ("HERE", "MANUAL", "REPORT")):
                    target = "layoutx"
                else:
                    target = "未知：" + expr[:60]
                writes.append((fn, i, expr[:70], target))
    ln = find_line("元数据输出在")
    scopes = {w[3] for w in writes}
    ok = ln is not None and scopes.issubset({"out_layout", "layoutx"}) and \
        "out_layout" in scopes and "layoutx" in scopes
    ev = ["代码位置：%s 全部位于 pipeline/layoutx/" % ", ".join(PIPE_PY_FILES)]
    for fn, i, expr, target in writes:
        ev.append("写入 %s:%s open(%s,'w') → %s" % (fn, i, expr, target))
    ev.append("REPORT.md:%s 声明：元数据输出在 out_layout，diff/summary/audit/REPORT/clusters/fingerprints/VERIFICATION 在 layoutx" % ln)
    ev.append("结论：所有写目标均落在 out_layout 或 layoutx，无改写 metapy/ 与 out/ → %s" % ok)
    ev.append("注：audit_verify.py 仅在 --md 时写 VERIFICATION.md（复核脚本自身，不计入流水线输出）。")
    return claim("R19", "范围声明：代码在 layoutx、元数据在 out_layout、其余产物在 layoutx",
                 "PASS" if ok else "FAIL", ev, {"writes": writes})


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
    ln = find_line("mtime 自查")
    text = lines[ln - 1] if ln else ""
    claimed = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)
    fm = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "N/A"
    m_meta, p_meta, c_meta = latest_mtime("metapy")
    m_out, p_out, c_out = latest_mtime("out")
    m_new, p_new, c_new = latest_mtime("out_layout")
    meta_ok = len(claimed) > 0 and claimed[0] == fm(m_meta)
    out_ok = len(claimed) > 1 and claimed[1] == fm(m_out)
    ok = meta_ok and out_ok
    ev = ["REPORT.md:%s %s" % (ln, text),
          "报告声明 metapy=%s；实测=%s (%s，%d 文件) → %s" %
          (claimed[0] if claimed else "缺失", fm(m_meta), p_meta, c_meta, meta_ok),
          "报告声明 out=%s；实测=%s (%s，%d 文件) → %s" %
          (claimed[1] if len(claimed) > 1 else "缺失", fm(m_out), p_out, c_out, out_ok),
          "out_layout/ 实测最新=%s (%s，%d 文件，仅供参照)" % (fm(m_new), p_new, c_new)]
    return claim("R20", "mtime 声明：与报告给出的 metapy/out 最新文件时间一致",
                 "PASS" if ok else "FAIL", ev,
                 {"claimed": claimed, "metapy": fm(m_meta), "out": fm(m_out),
                  "out_layout": fm(m_new)})


def check_reproduction(lines):
    ln = find_line("```bash")
    block = []
    if ln:
        for line in lines[ln:ln + 10]:
            if line.strip() == "```" and block:
                break
            if line.startswith("python "):
                block.append(line.strip())
    scripts = [re.sub(r"^python\s+", "", b) for b in block]
    has_fp = any("fingerprint.py" in s for s in scripts)
    order_ok = has_fp and scripts.index("layoutx/fingerprint.py") < scripts.index("layoutx/extract.py") \
        if "layoutx/fingerprint.py" in scripts else False
    ev = ["REPORT.md:%s 复现命令=%s" % (ln, scripts),
          "fingerprint.py 在复现命令中：%s，且位于 extract.py 之前：%s" % (has_fp, order_ok)]
    for fn in ("extract.py", "fingerprint.py"):
        with open(os.path.join(HERE, fn), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if fn == "extract.py" and ("fingerprints.jsonl" in line or "clusters.json" in line):
                    ev.append("extract.py:%s 读取旧产物：%s" % (i, line.strip()[:90]))
                if fn == "fingerprint.py" and ("fingerprints.jsonl" in line or "clusters.json" in line):
                    ev.append("fingerprint.py:%s 产生该产物：%s" % (i, line.strip()[:90]))
    ev.append("静态判断：fingerprint.py 已列入且顺序正确 → %s" % order_ok)
    return claim("R21", "复现命令顺序是否合理（fingerprint.py 在 extract.py 之前）",
                 "PASS" if order_ok else "FAIL", ev, {"commands": scripts})


# ---------------------------------------------------------------- 新增：零退化 / 人工边界
def check_zero_regression(recs, summary, lines):
    doc_worse = sum(1 for r in recs if r["category"] == "新版更差")
    field_worse_diff = sum(1 for r in recs for v in r["fields"].values()
                           if v.get("assessment") == "new_worse")
    rep = parse_field_table(lines)
    field_worse_report = sum(v.get("new_worse", 0) for v, _ in rep.values())
    field_worse_summary = sum(summary["fields"][f].get("new_worse", 0) for f in FIELDS)
    ln = find_line("规则判定零退化")
    claimed = ln is not None
    ok = claimed and doc_worse == 0 and field_worse_diff == 0 and \
        field_worse_report == 0 and field_worse_summary == 0
    ev = ["REPORT.md:%s 声称「规则判定零退化：doc 级与字段级 new_worse 均为 0」" % ln,
          "diff.jsonl doc 级「新版更差」=%d" % doc_worse,
          "diff.jsonl 字段级 assessment==new_worse 的实例=%d" % field_worse_diff,
          "comparison_summary.json 各字段 new_worse 合计=%d（缺键按 0）" % field_worse_summary,
          "REPORT.md 字段计数表 new_worse 合计=%d" % field_worse_report,
          "结论：PASS 仅表示「按当前规则与数据，零退化成立」；不代表全库人工真值或准确率（见 R23）。"]
    return claim("R22", "「本轮规则判定零退化」声明", "PASS" if ok else "FAIL", ev,
                 {"doc_new_worse": doc_worse, "field_new_worse_diff": field_worse_diff,
                  "field_new_worse_summary": field_worse_summary,
                  "field_new_worse_report": field_worse_report},
                 scope="规则判定，非人工真值")


def check_human_limits(recs, lines):
    unresolved_docs = [r["doc"] for r in recs if r["category"] == "双错且不同"]
    field_unres = collections.Counter()
    for r in recs:
        for f, v in r["fields"].items():
            if v.get("assessment") == "different_unresolved":
                field_unres[f] += 1
    ln9 = find_line("全库已人工验证")
    ln_end = find_line("无全量人工真值")
    honest = ln9 is not None and ln_end is not None
    verdict = "FAIL" if not honest else "WARN"
    ev = ["REPORT.md:%s 明示不能宣称「全库已人工验证全面优于旧版」" % ln9,
          "REPORT.md:%s 明示「无全量人工真值，也无随机样本误差估计」" % ln_end,
          "仍未决：双错且不同 %d 篇；字段 different_unresolved=%s" %
          (len(unresolved_docs), dict(field_unres)),
          "结论：报告如实披露人工验证缺口 → %s；这是局限提示，不是证伪，也不是人工真值。" % verdict]
    return claim("R23", "未做全量人工验证 / 仍有未决双异的边界声明", verdict, ev,
                 {"unresolved_docs": len(unresolved_docs),
                  "field_unresolved": dict(field_unres)})


# ---------------------------------------------------------------- 汇总与输出
def run_all():
    recs, by_doc, summary, audit, lines = read_inputs()
    claims = [
        check_counts(recs, summary, lines),
        check_fields(recs, summary, lines),
        check_superset(recs, audit, lines),
        check_doc_list(recs, lines, "新版更差", "全部剩余「新版更差」", "R04",
                       "全部剩余「新版更差」doc 列表与 diff.jsonl 一致"),
        check_doc_list(recs, lines, "双错且不同", "全部剩余双异", "R05",
                       "全部剩余双异 doc 列表与 diff.jsonl 一致"),
        check_tokens(recs, by_doc, lines),
        check_old_13570535_claim(by_doc, lines),
    ]
    claims += anchor_table_claim(by_doc, lines)
    claims += [
        check_network(),
        check_docid_override(),
        check_manual_review(lines),
        check_write_targets(),
        check_mtime(lines),
        check_reproduction(lines),
        check_zero_regression(recs, summary, lines),
        check_human_limits(recs, lines),
    ]
    counts = collections.Counter(c["verdict"] for c in claims)
    return {
        "schema": "layoutx-verification/1.0",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {"diff": DIFF, "summary": SUMMARY, "audit_sample": AUDIT,
                   "report": REPORT, "manual_review": MANUAL,
                   "python_files": PIPE_PY_FILES},
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
    add("> 判定口径：PASS/FAIL 仅针对「报告文字与当前规则产出的机器可核查数据是否一致」；"
        "人工正确性一律 UNVERIFIABLE，规则判定不等于人工真值。")
    add("")
    add("## 总览")
    add("")
    add("| 结论 | 条数 |")
    add("|---|---:|")
    for k in ("PASS", "FAIL", "WARN", "UNVERIFIABLE"):
        add("| %s | %d |" % (k, s.get(k, 0)))
    add("| **合计** | **%d** |" % s["total"])
    add("")
    for verdict, label in (("FAIL", "基线关键 FAIL 项"), ("WARN", "基线关键 WARN 项")):
        items = [c for c in result["claims"] if c["verdict"] == verdict]
        if items:
            add("### %s" % label)
            add("")
            for c in items:
                add("- **%s %s**：%s" % (c["id"], c["verdict"], c["title"]))
            add("")
    add("## 逐条声明")
    add("")
    for c in result["claims"]:
        extra = ""
        if c.get("verdict_kind"):
            extra = "（数据一致性判定：**%s**；人工真值 UNVERIFIABLE）" % c["verdict_kind"]
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
