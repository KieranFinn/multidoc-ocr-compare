# -*- coding: utf-8 -*-
r"""unify 归一化层(M1 骨架): 只消费 adapt 产物(out\<doc>\merged\page_N.json, 不改三家原始数据),
把三渠道对同一页的拆分结果归一化成统一对象, 交叉对比验证:
  一致的部分自动置信(high), 分歧的部分带分歧类型/置信依据进人工复检队列(review=True), 无第三态。
产出:
  out\<doc>\unified\page_N.json        该页统一对象列表(150dpi 页图像素坐标)
  out\<doc>\unified\_unified_summary.json  文档级三指标 + orphan 清单
算法四阶段:
  ① 渠道内归组(题注挂靠、段块续接, 滑窗邻域, 阈值行高自适应; 政策: 宁拆勿合)
  ② 跨渠道对齐(1:1: IoU+dice 贪心; 1:N: 包含匹配 + 大块可切分性检验; orphan -> coverage 分歧)
  ③ 共识与置信(机械规则阈值可调; HIGH=3/3+类型一致+dice>=0.95+无T1-T5; 政策: 宁送勿漏)
  ④ 页边界跨页拼接(M3 实现, M1 只留 cross_page 占位字段)
统一对象 schema:
  uid / page / box(共识几何=成员框逐坐标中位数) / type(共识类型=成员类型多数票)
  cross_page{spans} / members{ch:[unit...]} 可缺员 / readings[{ch,text,dice,flags}]
  main_text / divergence{kind: none|read|split|merge|coverage|type|xpage, detail}
  confidence{level: high|mid|low, basis} / review
用法: python unify.py <doc> [<doc>...] [--out out]
"""
import os, re, io, json, argparse, datetime, collections
import pipeline
from pipeline import _iou, _gdice, _cin, _fold_sym
import regress

ROOT = os.path.dirname(os.path.abspath(__file__))
CH = ("mineru", "dots", "paddle")

# —— 阈值(可调) ——
IOU_11 = 0.3        # 1:1 对齐 IoU 门槛
DICE_11 = 0.5       # 1:1 对齐 dice 门槛
AREA_RATIO = 2.5    # 1:1 面积比上限(超过视为异粒度, 交 1:N 处理)
DICE_CONTAIN = 0.4  # 包含匹配 dice 门槛
IOU_EMPTY = 0.5     # 双空文本块(图/表区)仅靠 IoU 匹配的门槛
DICE_HIGH = 0.95    # 自动置信的最佳 dice 门槛
DICE_MID = 0.8      # dice 低于此值直接复检
CAP_GAP = 2.5       # 题注挂靠: 与图/表块的最大行距(倍行高)
CONT_GAP = 1.6      # 段块续接: 最大垂直间距(倍行高)

TERM = regress.TERMINAL | set(u".。!?…:;：；")
HEAD_START = re.compile(r"^\s*(\[?\d{1,3}\]?[\).、]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+[.、]|#|\d+(\.\d+)*[.、]\s)")
STRUCT = {"Page-header", "Page-footer", "Page-number"}  # 结构性块: 位置即证据, 放宽文本判据
PICS = {"Picture", "Table"}


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _x_overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    return max(0.0, w) / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def _median(vals):
    s = sorted(vals)
    return s[len(s) // 2] if s else 0.0


def _dice_dir(small, big):
    """方向包含相似度: 较短文本的 bigram 在较长文本(全文)中的命中率。
    1:N 包含匹配的文本判据(_gdice 只取前 80 字, 子块落在大块中后段时归零, 故需此方向版)。
    比较口径同 _gdice: 符号折叠(不改原文) + 去空白。"""
    small = re.sub(r"\s+", "", _fold_sym(small))
    big = re.sub(r"\s+", "", _fold_sym(big))[:2000]
    if len(small) < 2 or len(big) < 2:
        return 0.0
    B = set(big[i:i + 2] for i in range(len(big) - 1))
    return sum(1 for i in range(len(small) - 1) if small[i:i + 2] in B) / float(len(small) - 1)


# ---------- 阶段① 渠道内归组(宁拆勿合) ----------

def intra_group(blocks, h_line):
    """阅读序排序后, 题注挂靠(Caption->最近的图/表块) + 段块续接(无终止标点+同栏+近距)。
    blocks: 单渠道一页块列表; 返回 unit 列表(每 unit 含 parts=原块下标列表, cap 挂靠题注)。"""
    order = sorted(range(len(blocks)),
                   key=lambda i: (round((blocks[i].get("box") or [0, 0, 0, 0])[1] / 18.0),
                                  (blocks[i].get("box") or [0, 0, 0, 0])[0]))
    used = [False] * len(blocks)
    units = []
    # 题注挂靠: Caption 块并入最近的同栏图/表块
    for i in order:
        b = blocks[i]
        if b.get("cat") != "Caption" or used[i]:
            continue
        bb = b.get("box") or [0, 0, 0, 0]
        best, bd = None, None
        for j in order:
            if used[j] or j == i or blocks[j].get("cat") not in ("Picture", "Table"):
                continue
            pb = blocks[j].get("box") or [0, 0, 0, 0]
            below = bb[1] - pb[3]          # 题注在图下方
            above = pb[1] - bb[3]          # 题注在图上方
            gap = below if below >= -2 else (above if above >= -2 else None)
            if gap is None or gap > CAP_GAP * h_line:
                continue
            if _x_overlap(bb, pb) < 0.3:
                continue
            if bd is None or gap < bd:
                best, bd = j, gap
        if best is not None:
            p = blocks[best]
            p["parts"] = p.get("parts") or [best]
            p["parts"].append(i)
            p["cap"] = ((p.get("cap") or "") + "\n" + (b.get("text") or "")).strip()
            used[i] = True
    # 段块续接: 无终止标点 + 下一块非标题样 + 同栏 + 近距
    for k, i in enumerate(order):
        if used[i]:
            continue
        b = blocks[i]
        t = (b.get("text") or "").rstrip()
        if not t or t[-1] in TERM or b.get("cat") != "Text":
            continue
        bb = b.get("box") or [0, 0, 0, 0]
        for j in order[k + 1:]:
            if used[j]:
                continue
            nb, nt = blocks[j], (blocks[j].get("text") or "").strip()
            if not nt or blocks[j].get("cat") != "Text" or HEAD_START.match(nt):
                continue
            jb = nb.get("box") or [0, 0, 0, 0]
            gap = jb[1] - bb[3]
            if gap < -2 or gap > CONT_GAP * h_line or _x_overlap(bb, jb) < 0.5:
                continue
            b["parts"] = b.get("parts") or [i]
            b["parts"].append(j)
            b["box"] = _union(bb, jb)
            b["text"] = (b.get("text") or "") + "\n" + (nb.get("text") or "")
            used[j] = True
            break
    for i in order:
        if used[i]:
            continue
        b = blocks[i]
        b["parts"] = b.get("parts") or [i]
        used[i] = True
        u = {"cat": b.get("cat", "Text"), "box": b.get("box") or [0, 0, 0, 0],
             "text": b.get("text") or "", "parts": b["parts"]}
        for k in ("img", "cap", "text_from", "xmerge", "xev"):
            if b.get(k):
                u[k] = b[k]
        units.append(u)
    return units


# ---------- 阶段② 跨渠道对齐 ----------

def _pair_score(u, v):
    """跨渠道单元配对分: 同粒度 1:1(IoU+dice+面积比守卫) / 异粒度包含匹配 / 结构性块放宽; 不配返回 None。"""
    iou = _iou(u["box"], v["box"])
    contain = _cin(u["box"], v["box"]) or _cin(v["box"], u["box"])
    if iou <= 0 and not contain:
        return None
    # 结构性块(页眉/页脚/页码): 页码归一化相等或文本互证即可(短文本 dice 算不出, 位置即证据)
    if u["cat"] in STRUCT and v["cat"] in STRUCT:
        du = re.sub(r"\D+", "", u["text"] or "")
        dv = re.sub(r"\D+", "", v["text"] or "")
        if iou >= 0.25 or (du and du == dv) or _gdice(u["text"], v["text"]) >= DICE_11:
            return 1.6 + iou
    d = _gdice(u["text"], v["text"])
    au, av = _area(u["box"]), _area(v["box"])
    ratio = max(au, av) / max(1.0, min(au, av))
    both_empty = not u["text"].strip() and not v["text"].strip()
    one_empty = (not u["text"].strip()) != (not v["text"].strip())
    if iou >= IOU_11 and ratio <= AREA_RATIO and (d >= DICE_11 or both_empty):
        return 2.0 + iou + d + (0.2 if u["cat"] == v["cat"] else 0)
    if iou >= IOU_EMPTY and both_empty:
        return 1.5 + iou + (0.2 if u["cat"] == v["cat"] else 0)
    # 图/表块: html 结构/抽取差异大, 几何一致即配(文本不作判据; 如同表的 flatten 泄漏 JSON)
    if u["cat"] in PICS and v["cat"] in PICS and iou >= 0.35:
        return 1.4 + iou
    if contain:
        dd = _dice_dir(u["text"], v["text"]) if len(u["text"]) <= len(v["text"]) else _dice_dir(v["text"], u["text"])
        # 图/表块一方空文本(题注内嵌/未抽出)同属图区, 几何包含即证据
        pic_mix = one_empty and u["cat"] in PICS and v["cat"] in PICS
        if d >= DICE_CONTAIN or both_empty or dd >= 0.5 or pic_mix:
            return 1.0 + max(d, dd) + iou
    return None


def cross_align(ch_units):
    """贪心并查集聚组 -> [{ch: [unit...]}]; 同渠道多单元共存(异粒度)保留在组内待阶段③判定。"""
    ids = [(ch, i) for ch in CH for i in range(len(ch_units.get(ch) or []))]
    parent = {x: x for x in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ca, ia = ids[a]
            cb, ib = ids[b]
            s = _pair_score(ch_units[ca][ia], ch_units[cb][ib])
            if s is not None:
                edges.append((s, ids[a], ids[b]))
    edges.sort(key=lambda e: -e[0])
    for _, a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups = {}
    for x in ids:
        groups.setdefault(find(x), {}).setdefault(x[0], []).append(ch_units[x[0]][x[1]])
    return list(groups.values())


def divisibility(big, smalls):
    """大块可切分性检验: [n] 编号计数 -> 能切则按 [n] 切段与子块 dice 配对; 返回 (可切, 明细)。"""
    t = big.get("text") or ""
    marks = list(regress.NUM_MARK.finditer(t))
    if len(marks) >= 2:
        segs, last = [], 0
        for m in marks:
            segs.append(t[last:m.start()])
            last = m.start()
        segs.append(t[last:])
        segs = [s.strip() for s in segs if s.strip()]
        hit = 0
        for s in smalls:
            st = (s.get("text") or "").strip()
            if st and max((max(_gdice(st, g), _dice_dir(st, g)) for g in segs), default=0) >= DICE_11:
                hit += 1
        return True, "[n]编号x%d 切段x%d 配对%d/%d" % (len(marks), len(segs), hit, len(smalls))
    return False, "无可识别[n]分段(行数/文本量比检验 M2 细化)"


# ---------- 阶段③ 共识与置信 ----------

def _space_quality(t):
    """空格质量(主读法选取用): 粘连 run 少 + 孤立单音节少 优先; 平手时空格多优先(英文靠空格分词,
    韩文粘连/孤立音节由前两项压掉——如 MinerU "저 자 소 개" 音节间过度切分不得当选)。
    注意 glue_runs 只数韩文 [가-힣]{7,}, 英文粘连不在其列, 故英文平手走 -spaces 是对的。"""
    iso = len(re.findall(r"(?:^|\s)[가-힣](?=\s|$)", t or ""))
    return (len(regress.glue_runs(t)) + iso, -t.count(" "))


def make_unified(g, pno, W, H, in_ref, uid, h_line=None):
    present = [c for c in CH if g.get(c)]
    flat = [u for c in present for u in g[c]]
    # 共识几何/类型(逐渠道主类型再多数票, 等权)
    box = [round(_median([u["box"][k] for u in flat]), 1) for k in range(4)]
    ch_cat = {}
    for c in present:
        cats = [u["cat"] for u in g[c]]
        ch_cat[c] = max(set(cats), key=cats.count)
    type_c = max(set(ch_cat.values()), key=list(ch_cat.values()).count)
    # 成员文本(同渠道多单元换行拼接)与主读法:
    # 存在 >=0.95 互证对时在该对内取空格质量更好者(离群读法不可能当选); 否则全体候选中取空格质量最好者(留档)
    texts = {c: "\n".join((u["text"] or "").strip() for u in g[c] if (u["text"] or "").strip())
             for c in present}
    filled = {c: t for c, t in texts.items() if t.strip()}
    fc = list(filled)
    pair_best, pair = 0.0, None
    for x in range(len(fc)):
        for y in range(x + 1, len(fc)):
            d0 = _gdice(filled[fc[x]], filled[fc[y]])
            if d0 > pair_best:
                pair_best, pair = d0, (fc[x], fc[y])
    if pair and pair_best >= DICE_HIGH:
        main_c = min(pair, key=lambda c: _space_quality(filled[c]))
    else:
        main_c = min(filled, key=lambda c: _space_quality(filled[c])) if filled else None
    main_t = filled.get(main_c, "")
    readings = []
    for c in present:
        t = texts[c]
        flags = []
        if not t.strip() and ch_cat.get(c) != "Picture":
            flags.append("空块")
        if "\ufffd" in t:
            flags.append("FFFD")
        if any(u.get("xmerge") for u in g[c]):
            flags.append("跨页并入")
        d = round(_gdice(t, main_t), 3) if (t.strip() and main_t.strip()) else (1.0 if not filled else 0.0)
        if t.strip() and d < DICE_MID:
            flags.append("离群读法(dice=%.2f)" % d)
        readings.append({"ch": c, "text": t, "dice": d, "flags": flags})
    # 渠道内缺陷检测 + 自动修复(2026-10-30, "划分时错误后处理可救"三件套之二):
    # ① 文本溢出: 显式行数 > 框高可容纳行数(仅判有显式换行的文本; 阅读序尾部行即越界部分, 修=裁掉尾部)
    # ② 重复识读: 同渠道多单元块间 dice≥0.6(raw 口径, 计入渠道缺陷画像); 修=组内重复行只留一份
    # 修复结果写 reading["text_fixed"](原样 text 不动, 证据保留); 主读法选择不消费修复文本(防单边修复进共识)
    hline = max(8.0, h_line or 18.0)
    for c in present:
        us = g[c]
        rd_c = next(r for r in readings if r["ch"] == c)
        fixes = []
        for u0 in us:
            t0 = u0.get("text") or ""
            lines = [l for l in t0.split("\n") if l.strip()]
            cap = max(1, int(round((u0["box"][3] - u0["box"][1]) / hline)))
            if len(lines) > cap:
                rd_c["flags"].append("文本溢出(%d行>框容%d行,已修复)" % (len(lines), cap))
                fixes.append("\n".join(lines[:cap]))
            else:
                fixes.append(t0)
        fixed = "\n".join(f for f in fixes if f)
        if len(us) > 1:
            dmax = max((_gdice(us[i]["text"], us[k]["text"])
                        for i in range(len(us)) for k in range(i + 1, len(us))), default=0.0)
            if dmax >= 0.6:
                seen, out, removed = set(), [], False
                for l0 in fixed.split("\n"):
                    k0 = re.sub(r"\s+", "", _fold_sym(l0))
                    if k0 and k0 in seen:
                        removed = True
                        continue
                    seen.add(k0)
                    out.append(l0)
                if removed:
                    fixed = "\n".join(out)
                rd_c["flags"].append("疑似重复识读(块间dice=%.2f%s)" % (dmax, ",已去重" if removed else ""))
        if fixed != (rd_c["text"] or ""):
            rd_c["text_fixed"] = fixed
    best = max((r["dice"] for r in readings), default=1.0)
    trunc = regress.check_entry(main_t, box, H) if (in_ref and main_t.strip()) else []
    # 异粒度(同渠道多单元 / 覆盖关系)与可切分性
    counts = {c: len(g[c]) for c in present}
    gran = any(n > 1 for n in counts.values())
    split_info = ""
    if gran and len(present) > 1:
        for c in present:
            if counts[c] == 1:
                big = g[c][0]
                smalls = [u for o in present if o != c for u in g[o]]
                inside = [u for u in smalls if _cin(u["box"], big["box"]) or _iou(u["box"], big["box"]) >= 0.3]
                if len(inside) >= 2:
                    ok, split_info = divisibility(big, inside)
                    break
        else:
            split_info = "各渠道粒度不一致(mineru×%d/dots×%d/paddle×%d)" % tuple(
                counts.get(c, 0) for c in ("mineru", "dots", "paddle"))
    xpage = any(u.get("xmerge") for u in flat)
    # 分歧类型
    if len(present) == 1:
        kind = "coverage"
        detail = "仅 %s 检出此区域" % present[0]
    elif len(set(ch_cat.values())) > 1:
        kind = "type"
        detail = "类型分歧: " + ", ".join("%s=%s" % (c, ch_cat[c]) for c in present)
    elif gran:
        kind = "split"
        detail = split_info
    elif xpage:
        kind = "xpage"
        detail = "成员含跨页并入标记(xmerge)"
    elif filled and best < DICE_HIGH:
        kind = "read"
        detail = "最佳 dice=%.2f(<%.2f)" % (best, DICE_HIGH)
    else:
        kind, detail = "none", "三家一致" if len(present) == 3 else ""
    # 置信(机械规则按拍板口径: 对象级看最佳dice=双路印证; 政策: 宁送勿漏 -> 非 high 即 review)
    type_ok = len(set(ch_cat.values())) == 1
    reason = []
    if len(present) < 3:
        reason.append("缺员:" + "/".join(c for c in CH if c not in present))
    if not type_ok:
        reason.append("类型分歧")
    if best < DICE_HIGH and filled:
        reason.append("dice=%.2f<%0.2f" % (best, DICE_HIGH))
    for f in ("T2", "T4"):
        if f in trunc:
            reason.append("%s命中" % f)
    if xpage:
        reason.append("跨页涉入")
    if gran:
        reason.append("异粒度")
    if trunc and any(f in trunc for f in ("T1", "T5", "T3")):
        reason.append("T1/T3/T5待GT")
    # 噪音豁免(2026-10-30 产品反馈"干扰项"治理): 眼睛只保留"人能据此做决定"的分歧。
    # T1-T5 判据(为参考文献条目层设计)只封顶不降级(修订原"T2·T4命中→low"拍板; T 判据仍在
    # regress.py 对条目层生效, M5 接 GT)——
    # ① 页边距类标注分歧: 成员类别全集 ⊆ {页眉,页脚,页码,Text}(如 www.kci.go.kr 被 paddle 标 Text),
    #    内容互证已成立, 类别风格差非内容差异 -> 视为共识(实体类 Footnote/Caption/Section-header/Table
    #    的分歧有语义价值[脚注保留/题注归属], 绝不豁免)
    _MARGIN = set(u"Page-header Page-footer Page-number Text".split())
    if kind == "type" and set(ch_cat.values()) <= _MARGIN:
        kind, detail = "none", "页边距类别标注分歧(非内容差异,不出眼)"
        type_ok = True
        reason = [r for r in reason if r != "类型分歧"]
    # ② 纯 T 命中: 条目层关注事项, 块级视为共识(纯T时 kind 必为 none——read/split 必有 dice/异粒度 reason)
    if reason and all(r.startswith("T") for r in reason):
        reason = []
    if not reason and len(present) == 3 and type_ok and (best >= DICE_HIGH or not filled):
        level = "high"
    elif len(present) == 1 or best < DICE_MID or xpage:
        level = "low"
    else:
        level = "mid"
    basis = "3/3在场·类型一致·最佳dice=%.2f·无T命中" % best if level == "high" else "; ".join(reason) or "采信留档"
    members = {c: [{k: u[k] for k in ("cat", "box", "text", "parts", "cap", "img") if k in u} for u in g[c]]
               for c in present}
    return {"uid": uid, "page": pno, "box": box, "type": type_c,
            "cross_page": {"spans": False},
            "members": members, "readings": readings,
            "main_text": main_t,
            "divergence": {"kind": kind, "detail": detail},
            "confidence": {"level": level, "basis": basis},
            "review": level != "high"}


# ---------- 文档级 ----------

def ref_region(doc_dir, n):
    """参考文献区页范围(任一渠道最后一个 참고문헌 类 Section-header 起, 至 Abstract/초록 止)。"""
    ref_from = None
    for p in range(n):
        fp = os.path.join(doc_dir, "merged", "page_%d.json" % p)
        if not os.path.exists(fp):
            continue
        j = json.load(io.open(fp, encoding="utf-8"))
        for b in j["merged"]:
            if b.get("cat") == "Section-header" and regress.REF_HEAD.search(b.get("text") or ""):
                ref_from = p
    if ref_from is None:
        return None, None
    for p in range(ref_from, n):
        fp = os.path.join(doc_dir, "merged", "page_%d.json" % p)
        if not os.path.exists(fp):
            continue
        j = json.load(io.open(fp, encoding="utf-8"))
        for b in j["merged"]:
            if b.get("cat") == "Section-header" and regress.ABSTRACT_HEAD.search(b.get("text") or ""):
                return ref_from, p
    return ref_from, n


def unify_doc(doc, out_root):
    doc_dir = os.path.join(out_root, doc)
    udir = os.path.join(doc_dir, "unified")
    if not os.path.isdir(os.path.join(doc_dir, "merged")):
        print("跳过 %s: 无 merged 产物" % doc)
        return None
    pages = sorted(int(re.match(r"page_(\d+)\.json$", f).group(1))
                   for f in os.listdir(os.path.join(doc_dir, "merged")) if re.match(r"page_\d+\.json$", f))
    ref_from, ref_to = ref_region(doc_dir, len(pages))
    os.makedirs(udir, exist_ok=True)
    stats = {"groups": 0, "oto": 0, "orphan": 0, "mapped_blocks": 0, "raw_blocks": 0,
             "levels": {"high": 0, "mid": 0, "low": 0},
             "kinds": {}, "orphans": [],
             "ch_defects": {ch: collections.Counter() for ch in CH}}
    for p in pages:
        j0 = json.load(io.open(os.path.join(doc_dir, "merged", "page_%d.json" % p), encoding="utf-8"))
        H = j0["h"]
        heights = sorted(max(1.0, b["box"][3] - b["box"][1]) for b in j0["merged"])
        h_line = heights[len(heights) // 2] if heights else 18.0
        # 行高估算是 P20(小高度分位): 正文/脚注行块是页面里数量最多小盒, 中位数会被大段块推高,
        # 用中位数当行高会把"3 行脚注装进 166px"误判成溢出(1632041 实况, 误伤率>80%)
        line_h = heights[min(len(heights) - 1, int(len(heights) * 0.2))] if heights else 18.0
        # 重新按渠道归组(与 load_page_units 同路径, 避免读两遍)
        ch_units, n_raw = {}, 0
        for ch in CH:
            blks = [dict(b) for b in j0["merged"] if b.get("ch") == ch]
            n_raw += len(blks)
            if blks:
                ch_units[ch] = intra_group(blks, h_line)
        stats["raw_blocks"] += n_raw
        groups = cross_align(ch_units)
        in_ref = ref_from is not None and ref_from <= p < (ref_to if ref_to is not None else len(pages))
        unified = []
        for gi, g in enumerate(groups):
            u = make_unified(g, p + 1, j0["w"], H, in_ref, "u%d_%d" % (p, gi), line_h)
            unified.append(u)
            stats["groups"] += 1
            stats["levels"][u["confidence"]["level"]] += 1
            stats["kinds"][u["divergence"]["kind"]] = stats["kinds"].get(u["divergence"]["kind"], 0) + 1
            present = u["members"].keys()
            if len(present) == 3 and all(len(v) == 1 for v in u["members"].values()):
                stats["oto"] += 1
            n_blk = sum(len(m.get("parts") or [1]) for v in u["members"].values() for m in v)
            for r in u["readings"]:  # 渠道缺陷画像: flags 按渠道计数(M4 渠道质量统计的原料)
                for fl in r["flags"]:
                    stats["ch_defects"][r["ch"]][fl.split("(")[0]] += 1
            if len(present) == 1:
                stats["orphan"] += 1
                ch = list(present)[0]
                stats["orphans"].append({"uid": u["uid"], "page": p + 1, "ch": ch,
                                         "cat": u["type"], "box": u["box"],
                                         "head": (u["main_text"] or "")[:60]})
            else:
                stats["mapped_blocks"] += n_blk
        unified.sort(key=lambda u: (round(u["box"][1] / 18.0), u["box"][0]))
        io.open(os.path.join(udir, "page_%d.json" % p), "w", encoding="utf-8").write(json.dumps({
            "doc": doc, "page": p + 1, "w": j0["w"], "h": H, "in_ref": in_ref,
            "unified": unified}, ensure_ascii=False))
    cov = stats["mapped_blocks"] / max(1, stats["raw_blocks"])
    oto = stats["oto"] / max(1, stats["groups"])
    orph = stats["orphan"] / max(1, stats["groups"])
    metrics = {"coverage": round(cov, 4), "one_to_one": round(oto, 4), "orphan_rate": round(orph, 4),
               "raw_blocks": stats["raw_blocks"], "groups": stats["groups"]}
    summary = {"doc": doc, "pages": len(pages),
               "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "metrics": metrics, "levels": stats["levels"], "kinds": stats["kinds"],
               "ch_defects": {ch: dict(v) for ch, v in stats["ch_defects"].items()},
               "orphans": stats["orphans"]}
    io.open(os.path.join(udir, "_unified_summary.json"), "w", encoding="utf-8").write(
        json.dumps(summary, ensure_ascii=False, indent=1))
    print("== %s ==" % doc)
    print("  映射覆盖率 %5.1f%% (目标>=98%%)  1:1 映射率 %5.1f%%  orphan 率 %5.1f%% (目标<=2%%)" % (
        cov * 100, oto * 100, orph * 100))
    print("  置信: high=%d mid=%d low=%d  分歧: %s" % (
        stats["levels"]["high"], stats["levels"]["mid"], stats["levels"]["low"],
        ", ".join("%s=%d" % (k, v) for k, v in sorted(stats["kinds"].items()))))
    print("  渠道缺陷画像: %s" % "; ".join(
        "%s={%s}" % (ch, ", ".join("%s×%d" % (k, v) for k, v in stats["ch_defects"][ch].most_common()))
        for ch in CH if stats["ch_defects"][ch]))
    print("  orphan 清单 (%d 条, 明细见 unified\\_unified_summary.json):" % len(stats["orphans"]))
    for o in stats["orphans"][:20]:
        print("    p%-3d %-7s %-13s %s" % (o["page"], o["ch"], o["cat"], o["head"][:40].replace("\n", " ")))
    if len(stats["orphans"]) > 20:
        print("    ... 其余 %d 条见 summary" % (len(stats["orphans"]) - 20))
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+", help="文档名(out 下的目录名)")
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args()
    for doc in a.docs:
        unify_doc(doc, a.out)


if __name__ == "__main__":
    main()
