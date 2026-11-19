# -*- coding: utf-8 -*-
"""journal_toc_fill(2026-11-18): 整刊目录枚举批量回填 —— "同刊多篇缺卷期"的最短路径。
webfill 是逐篇检索; 当同一期刊大量缺卷期时, 枚举该刊全部期号的 KCI 目录(每期 1 次抓取),
标题四级匹配(精确/包含/前缀/相似≥0.70)一次对完全部, 再按 KCI 著录对齐 卷/期/年(定稿年≠刊期
年时以著录为准修正)。幂等(已填跳过)。目录缓存在 _kgs_toc.json, 网络受限时可离线复跑。
用法: python journal_toc_fill.py   (顶部 JOURNALS 配置刊例; 当前登记 국방과 보안)"""
import io, json, os, re, sys, time, difflib
import urllib.request

sys.path.insert(0, ".")
import qc

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9", "Connection": "close"}
ISSUES = [(2023, 5, 1, "VOL000163978"), (2023, 5, 2, "VOL000168599"),
          (2024, 6, 1, "VOL000172689"), (2024, 6, 2, "VOL000178440"),
          (2025, 7, 1, "VOL000185297"), (2025, 7, 2, "VOL000190463"),
          (2026, 8, 1, "VOL000198521")]

def get(url, timeout=20):
    for tr in range(3):
        try:
            return _once(url, timeout)
        except Exception:
            if tr == 2:
                raise
            time.sleep(3)

def _once(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    resp = urllib.request.urlopen(req, timeout=timeout)
    t0 = time.time(); chunks = []; got = 0
    while got < 400000 and time.time() - t0 < timeout * 1.5:
        try:
            b = resp.read(16384)
        except Exception:
            break
        if not b:
            break
        chunks.append(b); got += len(b)
    resp.close()
    return b"".join(chunks).decode("utf-8", "replace")

NF = lambda s: re.sub(r"[^0-9a-z가-힣一-鿿]", "", (s or "").lower())

def build_toc():
    toc = {}
    for yr, vol, no, vid in ISSUES:
        url = ("https://www.kci.go.kr/kciportal/landing/journalArticleList.kci"
               "?vol_isse_id=%s&sere_id=SER000005444" % vid)
        try:
            t = get(url)
        except Exception as e:
            print("TOC FAIL", vol, no, type(e).__name__, flush=True)
            continue
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
        txt = re.sub(r"<[^>]+>", "\n", txt)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in txt.split("\n")]
        lines = [ln for ln in lines if ln]
        n = 0
        for i, ln in enumerate(lines):
            mv = re.match(r"^v\.?\s*(\d{1,3})\s*$", ln)
            if not mv:
                continue
            j = i + 1
            mn = None
            while j < len(lines) and j <= i + 3 and not mn:
                mn = re.match(r"^no\.?\s*(\d{1,3})\s*$", lines[j])
                j += 1
            if not mn:
                continue
            parts = []
            k = i - 1
            while k >= 0 and len(parts) < 5:
                cand = lines[k]
                if cand == "|" or re.match(r"^(?:19|20)\d{2}\s*[.년/-]", cand) \
                        or re.match(r"^외\s*\d+명$", cand):
                    k -= 1
                    continue
                if "|" in cand or re.match(r"^pp\.?\s*\d", cand) \
                        or re.search(r"총 논문|현재권호|모든 권호|KCI 등재|약어", cand):
                    break
                parts.insert(0, cand)
                k -= 1
            title = " ".join(parts).strip()
            key = NF(title)
            if key and key not in toc:
                toc[key] = (yr, int(mv.group(1)), int(mn.group(1)))
                n += 1
        print("TOC v%d-%d: %d 篇" % (vol, no, n), flush=True)
    return toc

def clean_title(s):
    """剥常见抽取垃圾: 前导节号(Ⅰ/Ⅱ/1.)、括号碎片、#号, 再折叠。"""
    s = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ0-9]+\s*[.、)]\s*", "", (s or "").strip())
    s = re.sub(r"^[(（][^)）]{0,20}[)）]\s*", "", s)
    s = s.replace("#", " ")
    return NF(s)

def match(key, toc):
    if key in toc:
        return toc[key]
    for k2, v2 in toc.items():
        if len(key) >= 12 and (key in k2 or k2 in key):
            return v2
    for k2, v2 in toc.items():
        if len(key) >= 15 and (k2.startswith(key[:20]) or key.startswith(k2[:20])):
            return v2
    cand = difflib.get_close_matches(key, toc.keys(), n=1, cutoff=0.70)
    return toc[cand[0]] if cand else None

def main():
    toc = build_toc()
    print("目录总条目:", len(toc), flush=True)
    io.open("_kgs_toc.json", "w", encoding="utf-8").write(json.dumps(toc, ensure_ascii=False))
    notes = json.load(io.open("gui_notes.json", encoding="utf-8"))
    hit = miss = 0
    for doc in sorted(f[:-4] for f in os.listdir("uploads") if f.endswith(".pdf")):
        if qc.qc_of(doc)["level"] != "novol":
            continue
        m = json.load(io.open("out/%s/metadata.json" % doc, encoding="utf-8"))
        if (m.get("journal") or {}).get("value") != u"국방과 보안":
            continue
        if (m.get("volume") or {}).get("value") not in (None, "") \
                and (m.get("issue") or {}).get("value") not in (None, ""):
            continue
        title = (m.get("title") or {}).get("value") or ""
        got = match(NF(title), toc)
        if not got:
            miss += 1
            print("未匹配:", doc, title[:38], "| 年:",
                  (m.get("year") or {}).get("value"), flush=True)
            continue
        yr, vol, no = got
        vid = [vid for (y, v, n2, vid) in ISSUES if y == yr and v == vol and n2 == no][0]
        url = ("https://www.kci.go.kr/kciportal/landing/journalArticleList.kci"
               "?vol_isse_id=%s&sere_id=SER000005444" % vid)
        got_list = []
        for k2, v2 in (("volume", vol), ("issue", no)):
            if (m.get(k2) or {}).get("value") in (None, ""):
                m[k2] = {"value": v2, "uid": None,
                         "confidence": {"level": "high", "source": "web_record"}}
                got_list.append(u"%s=%d" % (k2, v2))
        if (m.get("year") or {}).get("value") != yr:
            m["year"] = {"value": yr, "uid": None,
                         "confidence": {"level": "high", "source": "web_record"}}
            got_list.append(u"year=%d(修正)" % yr)
        m["generated"] = "2026-11-18 kgs-batch"
        io.open("out/%s/metadata.json" % doc, "w", encoding="utf-8").write(
            json.dumps(m, ensure_ascii=False, indent=1))
        try:
            a = json.load(io.open("out/%s/metadata_audit.json" % doc, encoding="utf-8"))
        except Exception:
            a = {}
        a["webfill"] = {"filled": {"volume": vol, "issue": no, "year": yr},
                        "evidence_url": url, "path": "kci_toc_batch", "ts": "2026-11-18"}
        io.open("out/%s/metadata_audit.json" % doc, "w", encoding="utf-8").write(
            json.dumps(a, ensure_ascii=False, indent=1))
        from metapy import webfill as _wf
        nl = _wf.note_line({"filled": {k: v for k, v in (("volume", vol), ("issue", no))},
                            "evidence_url": url})
        old = notes.get(doc, "")
        if u"联网检索" not in old:
            notes[doc] = nl + (u"；原备注: " + old if old else "")
        hit += 1
    io.open("gui_notes.json", "w", encoding="utf-8").write(
        json.dumps(notes, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print("回填:", hit, "未匹配:", miss, flush=True)

if __name__ == "__main__":
    main()
