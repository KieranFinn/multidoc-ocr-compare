# -*- coding: utf-8 -*-
"""webfill(第 16 段, 2026-11-18): 红单缺失字段(期刊/出版年/卷/期)的联网检索回填。

架构: **代码当手, LLM 当眼** —— 代码抓取候选页面, LLM 只负责"从真实页面里读出数"。
管线 LLM 是裸 chat.completions(无联网工具), "LLM 自己搜索"不可行; 由代码完成检索与实证。
纪律(只填空不覆盖 + 双闸 + 三标注):
  闸1 来源实证: 声称的年/卷/期必须能在抓回的来源页文本里找到(grep, 兼容 vol.5/5권/no.2/2호);
  闸2 语料对账: (卷,期) 须过同刊页码账本 fit_issue; 年份在合法域。
  三标注: confidence.source="web_record" + audit 留痕 + 备注追加 "[回填:联网检索]+URL"。
与批次 4c 硬约束("LLM 产物不回灌 metadata")的关系: 本段是用户 2026-11-18 明确拍板的
**显式例外** —— 例外成立的前提就是上面三件套齐全, 缺一不许写库。
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from metapy.common import _fold          # noqa: E402  (fold 复用)
from metapy import journals as jreg      # noqa: E402  (账本对账)
import ner_lite                          # noqa: E402  (_env_key: 用户变量注册表直读, 免进程重启)

FIELDS = ("journal", "year", "volume", "issue")
YEAR_OK = set(range(1900, 2027))


def _llm_cfg():
    fp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "channels.json")
    try:
        return (json.load(io.open(fp, encoding="utf-8")).get("llm") or {})
    except Exception:
        return {}


def missing_fields(meta):
    return [k for k in FIELDS if not ((meta.get(k) or {}).get("value") not in (None, "", []))]


def query_card(doc, meta):
    t = (meta.get("title") or {}).get("value") or ""
    aus = "; ".join((a.get("name") or "") for a in (meta.get("authors") or []))[:120]
    jn = (meta.get("journal") or {}).get("value") or ""
    pg = (meta.get("pages") or {}).get("value") or ""
    return (u"KCI 编号: %s\n标题: %s\n作者: %s\n刊名(如有): %s\n印刷页码(如有): %s"
            % (doc, t, aus, jn, pg))


def _fetch(url, timeout=15, cap=200000):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(cap)
    return raw.decode("utf-8", "replace")


def search_pages(queries, fetcher=None, max_per_query=3, deadline=None):
    """代码检索: Naver(本机唯一可达, 2026-11-18 实测) → Bing 兜底; DDG 已证被墙移除。
    deadline: 时间戳(float), 过后即停(分级预算的一部分)。"""
    import time as _tm
    fetcher = fetcher or _fetch
    out, seen = [], set()

    def _grab(url):
        if url in seen:
            return None
        seen.add(url)
        try:
            t = fetcher(url)
            return re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
        except Exception:
            return None

    for q in queries:
        html = None
        for tmpl in ("https://search.naver.com/search.naver?where=web&query=%s",
                     "https://www.bing.com/search?q=%s"):
            if deadline and _tm.time() > deadline:
                return out
            try:
                html = fetcher(tmpl % __import__("urllib.parse", fromlist=["quote"]).quote(q))
                break
            except Exception:
                continue
        if not html:
            continue
        # 检索结果页本身是富源: 引擎索引摘要常含 "刊名 年 vol.x no.y"(KCI 落地页是 JS 壳抓不到,
        # 2026-11-18 实跑实证) —— 剥标签后作为独立"页"喂给 LLM。
        txt = re.sub(r"<[^>]+>", " ", html)
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) > 400:
            out.append(("search:" + q[:60], txt))
        links = re.findall(r'href="(https?://[^"]+)"', html)
        k = 0
        for u in links:
            if deadline and _tm.time() > deadline:
                return out
            if any(s in u for s in ("duckduckgo.com", "bing.com", "microsoft.com",
                                    "go.microsoft", "w3.org", "naver.com", "pstatic.net")):
                continue
            body = _grab(u)
            if body:
                out.append((u, body))
                k += 1
            if k >= max_per_query:
                break
    return out


def _fetch(url, timeout=15, cap=200000, accept=None):
    """单次抓取: 每 recv 有 timeout, 且读取总时长硬顶 2×timeout(防 drip-feed 拖死,
    2026-11-18 实跑: KCI 对高频 IP drip-feed, read(cap) 无总时限会跑 140s+)。"""
    import urllib.request, time
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
               "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
               "Connection": "close"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    chunks, got, t0 = [], 0, time.time()
    while got < cap:
        if time.time() - t0 > timeout * 2:
            break
        try:
            b = resp.read(16384)
        except Exception:
            break
        if not b:
            break
        chunks.append(b)
        got += len(b)
    try:
        resp.close()
    except Exception:
        pass
    return b"".join(chunks).decode("utf-8", "replace")


def _doi_csl(page_text, fetcher=None):
    """Tier0: 原文印有 DOI → doi.org 内容协商(CSL JSON)一次拿全(刊名/年/卷/期)。"""
    fetcher = fetcher or _fetch
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>\)]+", page_text or "")
    if not m:
        return None
    doi = m.group(0).rstrip(".,")
    try:
        txt = fetcher("https://doi.org/" + doi, timeout=12,
                      accept="application/vnd.citationstyles.csl+json")
        j = json.loads(txt)
    except Exception:
        return None
    yr = j.get("issued", {}).get("date-parts", [[None]])[0][0]
    vol = j.get("volume")
    iss = j.get("issue")
    ct = j.get("container-title") or ""
    if isinstance(ct, list):
        ct = ct[0] if ct else ""
    if not any([yr, vol, iss, ct]):
        return None
    def _int(v):
        try:
            return int(str(v).strip())
        except Exception:
            return None
    return {"found": True, "journal": ct or None, "year": _int(yr),
            "volume": _int(vol), "issue": _int(iss),
            "evidence_url": "https://doi.org/" + doi,
            "evidence_quote": (ct or "")[:60], "path": "doi_csl"}


def _kci_meta_parse(html, url, title=None):
    """KCI 文章页 citation_* meta → claim; title 折叠消歧(可选)。"""
    metas = dict(re.findall(r"<meta\s+name=\"(citation_[a-z_]+)\"\s+content=\"([^\"]*)\"",
                             html))

    def _int(v):
        v = (v or "").strip()
        try:
            return int(v)
        except Exception:
            return None
    ct = (metas.get("citation_title") or "").replace(u"학술논문", "").strip(" :")
    if title:
        # 标点不敏感折叠(韩文语料 ',' vs '·' vs ' ' 常见异体, 2026-11-18 selftest 实证)
        _nf = lambda s: re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())
        want = _nf(title)
        if want and _nf(ct)[:12] not in want and want[:12] not in _nf(ct):
            return None
    yr = None
    md = re.search(r"((?:19|20)\d{2})", metas.get("citation_publication_date") or "")
    if md:
        yr = int(md.group(1))
    vol = _int(metas.get("citation_volume"))
    iss = _int(metas.get("citation_issue"))
    jn = (metas.get("citation_journal_title") or "").strip() or None
    fp = _int(metas.get("citation_firstpage"))
    lp = _int(metas.get("citation_lastpage"))
    if not any([yr, vol, iss, jn]):
        return None
    claim = {"found": True, "journal": jn, "year": yr, "volume": vol, "issue": iss,
             "evidence_url": url, "evidence_quote": (jn or ct or "")[:50],
             "path": "kci_meta"}
    if fp and lp:
        claim["pages"] = "%d-%d" % (fp, lp)
    return claim


def _kci_by_artid(art_id, title=None, fetcher=None):
    """doc id 自带 ART id(KCI_FI001536751_2000 型) → KCI 文章页直查, 零搜索零 LLM。"""
    fetcher = fetcher or _fetch
    url = "https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=%s" % art_id
    try:
        t = fetcher(url, timeout=15)
    except Exception:
        return None
    return _kci_meta_parse(t, url, title)


def _kci_via_naver(title, fetcher=None):
    """Tier1: Naver 定位 KCI 文章页(代码当手), citation_* meta 解析(零 LLM)。
    返回 (claim, art_url) 或 (None, None)。标题消歧: citation_title 折叠匹配。"""
    fetcher = fetcher or _fetch
    from urllib.parse import quote
    q = u"site:kci.go.kr \"%s\"" % (title or "")[:40]
    html = None
    for tmpl in ("https://search.naver.com/search.naver?where=web&query=%s",
                 "https://search.naver.com/search.naver?where=web&query=%s"):
        try:
            html = fetcher(tmpl % quote(q.encode("utf-8")), timeout=12)
            break
        except Exception:
            try:
                html = fetcher(tmpl % quote((title or "")[:40].encode("utf-8")), timeout=12)
            except Exception:
                return None, None
    if not html:
        return None, None
    links = re.findall(r"https?://(?:www\.)?kci\.go\.kr/[^\s\"'<>]*arti_id=(ART\d+)", html)
    seen, arts = set(), []
    for a in links:
        if a not in seen:
            seen.add(a)
            arts.append(a)
    want = _fold(title or "")
    for art in arts[:3]:
        url = "https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=%s" % art
        try:
            t = fetcher(url, timeout=15)
        except Exception:
            continue
        claim = _kci_meta_parse(t, url, title)
        if claim:
            return claim, url
    return None, None


def _regex_record(card, pages):
    """代码快车道: 抓回页里直接正则提著录(KISS 带标签字段 / Naver 摘要 "刊名 年 vol.x no.y" 体例)。
    凑齐 >=2 个数值字段(年/卷/期)才返回 claim; 证据=命中页 URL + 命中行。LLM 只兜正则够不着的篇。"""
    pat_snip = re.compile(r"([가-힣A-Za-z·&\'　 ]{2,30}?)\s+(20\d{2}|19\d{2})\s*"
                          r"(?:vol\.?|Vol\.?)\s*(\d{1,3})\s*[,，]?\s*(?:no\.?|No\.?)\s*(\d{1,3})")
    pat_kiss = re.compile(r"(?:간행물명|간행지)\s*[:：]?\s*(《?[^《》<>\[\]|]{2,40}?)\s*[,，|]")
    pat_kvol = re.compile(r"(?:권호|호수)\s*[:：]?\s*(\d{1,3})\s*권?\s*(?:[,/]|\s)\s*(\d{1,3})\s*호")
    pat_kyr = re.compile(r"(?:발행연도|발행년도|연도)\s*[:：]?\s*((?:19|20)\d{2})")
    card_jn = u""
    for ln in card.split("\n"):
        if ln.startswith(u"刊名"):
            card_jn = ln.split(u":", 1)[-1].strip()
    for u, t in pages:
        if u.startswith("search:"):
            continue
        txt = re.sub(r"<[^>]+>", " ", t)
        txt = re.sub(r"\s+", " ", txt)
        m = pat_snip.search(txt)
        if not m:
            continue
        jn = m.group(1).strip().strip(u",，")
        jn = re.split(u"[—–·:｜|>/]", jn)[-1].strip()   # 摘要前常带标题前缀, 取分隔符后段
        yr, vol, iss = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if yr not in YEAR_OK:
            continue
        return {"found": True, "journal": jn, "year": yr, "volume": vol, "issue": iss,
                "evidence_url": u, "evidence_quote": m.group(0), "path": "regex"}
    for u, t in pages:
        if "kiss.kstudy" not in u:
            continue
        txt = re.sub(r"<[^>]+>", " ", t)
        txt = re.sub(r"\s+", " ", txt)
        mk = pat_kiss.search(txt)
        mv = pat_kvol.search(txt)
        my = pat_kyr.search(txt)
        if mv and my:
            jn = mk.group(1).strip("《》 ,，") if mk else (card_jn or "")
            return {"found": True, "journal": jn or None, "year": int(my.group(1)),
                    "volume": int(mv.group(1)), "issue": int(mv.group(2)),
                    "evidence_url": u, "evidence_quote": mv.group(0)[:60], "path": "regex"}
    return None


def _llm_candidates(cfg):
    """主 endpoint + channels 兜底清单, 逐个试(主 key 401 时走 zjlab 等备选, 2026-11-18 实跑实证)。"""
    cfg = cfg or _llm_cfg()
    out = [{"endpoint": cfg.get("endpoint"), "model": cfg.get("model"),
            "api_key_env": cfg.get("api_key_env") or "KCI_LLM_KEY"}]
    for ch in cfg.get("channels") or []:
        out.append({"endpoint": ch.get("endpoint"), "model": ch.get("model"),
                    "api_key_env": ch.get("api_key_env")})
    return [c for c in out if c.get("endpoint")]


def _llm_read(card, pages, cfg, opener=None):
    """LLM 当眼: 从抓回页面里找该文的官方著录并结构化。返回 claim dict 或 None。"""
    import urllib.request
    cands = _llm_candidates(cfg)
    if not cands:
        return None
    chunks = []
    for u, t in pages[:6]:
        txt = re.sub(r"<[^>]+>", " ", t)
        txt = re.sub(r"\s+", " ", txt)
        chunks.append(u"[%s]\n%s" % (u, txt[:4000]))
    prompt = (u"下面是检索某篇韩国学术文章得到的若干网页。请找出该文的官方著录页"
              u"(KCI/期刊官网优先), 并读出著录信息。\n%s\n\n网页:\n%s\n\n"
              u"只输出 JSON: {\"found\": true/false, \"journal\": \"刊名(原文语种)\", \"year\": 整数或null,"
              u" \"volume\": 整数或null, \"issue\": 整数或null, \"evidence_url\": \"你读的那页URL\","
              u" \"evidence_quote\": \"该页含年/卷/期的那句原文\"}" % (card, u"\n\n".join(chunks)))
    payload = {"model": cfg.get("model"), "temperature": cfg.get("temperature", 0),
               "messages": [{"role": "user", "content": prompt}]}
    if opener is not None:                      # 契约自检注入
        raw = opener(payload)
        return _parse_envelope(raw)
    import time as _time
    last_err = None
    for c in cands:
        key = ner_lite._env_key(c.get("api_key_env") or "KCI_LLM_KEY") or ""
        if not key:
            last_err = u"缺 key(%s)" % c.get("api_key_env")
            continue
        body = dict(payload, model=c.get("model") or payload.get("model"))
        # 机构 LiteLLM 网关机群缓存不一致(2026-11-18 实跑): 同一 key 时而 401(节点未同步)
        # 时而 429(已过认证但并发满) —— 两类都重试换节点, 429 视为"认证已通过"。
        for attempt in range(4):
            req = urllib.request.Request(c["endpoint"], data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + key})
            try:
                return _parse_envelope(urllib.request.urlopen(req, timeout=30).read())
            except urllib.error.HTTPError as ex:
                try:
                    detail = ex.read(120).decode("utf-8", "replace")
                except Exception:
                    detail = ""
                last_err = u"%s HTTP%d: %s" % (c.get("model"), ex.code, detail[:70])
                if ex.code in (401, 429, 500, 502, 503):
                    _time.sleep(min(2 + attempt * 2, 6))
                    continue
                break
            except Exception as ex:
                last_err = u"%s: %s" % (c.get("model"), str(ex)[:80])
                break
    _LAST_ERR[0] = last_err
    return None


_LAST_ERR = [None]


def _parse_envelope(raw):
    txt = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    content = txt
    try:                       # 标准 chat 信封 → 取 message.content 再解内层 JSON
        env = json.loads(txt)
        content = env["choices"][0]["message"]["content"]
    except Exception:
        pass
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    return obj if isinstance(obj, dict) and obj.get("found") else None
    txt = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    content = txt
    try:                       # 标准 chat 信封 → 取 message.content 再解内层 JSON
        env = json.loads(txt)
        content = env["choices"][0]["message"]["content"]
    except Exception:
        pass
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    return obj if isinstance(obj, dict) and obj.get("found") else None


def _norm_forms(v):
    return set(str(v)) | {"vol.%s" % v, "no.%s" % v, "%s권" % v, "%s호" % v,
                          "제 %s" % v, u"제%s" % v, "Vol.%s" % v, "No.%s" % v,
                          "%s집" % v, u"vol. %s" % v, u"no. %s" % v}


def verify_claim(claim, pages, meta, ledger):
    """双闸: 闸1 来源实证(声称值须见于来源页); 闸2 账本对账。"""
    url = claim.get("evidence_url") or ""
    bodies = {u: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)) for u, t in pages}
    body = bodies.get(url, "")
    allbody = u" ".join(bodies.values())   # 闸1 放宽: 任一抓回页(含检索摘要页)实证即可
    quote = (claim.get("evidence_quote") or "")
    if quote and _fold(quote)[:40] not in _fold(allbody):
        return False, u"引文与来源页不符"
    yr = claim.get("year")
    if yr is not None:
        try:
            yr = int(yr)
        except Exception:
            return False, u"年份非法"
        if yr not in YEAR_OK:
            return False, u"年份出域"
        if str(yr) not in allbody and u"%d년" % yr not in allbody:
            return False, u"年份未见于来源页"
    for k_zh, key in ((u"卷", "volume"), (u"期", "issue")):
        v = claim.get(key)
        if v is None:
            continue
        try:
            v = int(v)
        except Exception:
            return False, u"%s非法" % k_zh
        if not any(f in allbody for f in _norm_forms(v)):
            return False, u"%s未见于来源页" % k_zh
    jn = (claim.get("journal") or "").strip()
    if jn:
        cur = ((meta.get("journal") or {}).get("value") or "")
        if cur and _fold(cur) != _fold(jn) and _fold(cur) not in _fold(jn) \
                and _fold(jn) not in _fold(cur):
            return False, u"刊名与既有值冲突"
    vol, iss = claim.get("volume"), claim.get("issue")
    pg = (meta.get("pages") or {}).get("value")
    if (vol is not None or iss is not None) and jn and pg:
        fit = jreg.fit_issue(jn, yr if yr in YEAR_OK else None, pg, ledger)
        if fit and (fit[1] != (int(vol) if vol is not None else fit[1])
                    or fit[2] != (int(iss) if iss is not None else fit[2])):
            return False, u"与语料账本冲突(该刊页码应属 %s)" % (fit,)
    return True, ""


def webfill_doc(doc, out_root, cfg=None, fetcher=None, opener=None):
    doc_dir = os.path.join(out_root, doc)
    meta_fp = os.path.join(doc_dir, "metadata.json")
    if not os.path.isfile(meta_fp):
        return None
    meta = json.load(io.open(meta_fp, encoding="utf-8"))
    miss = [k for k in FIELDS + ("pages",) if (meta.get(k) or {}).get("value") in (None, "")]
    if not miss:
        return {"doc": doc, "filled": {}, "reason": u"无缺失"}
    card = query_card(doc, meta)
    title = ((meta.get("title") or {}).get("value") or "")[:60]
    cfg = cfg if cfg is not None else _llm_cfg()
    result = {"doc": doc, "missing": miss, "pages_fetched": 0, "filled": {}}
    import socket, time as _tm
    socket.setdefaulttimeout(20)              # 防 drip-feed 拖死
    _deadline = _tm.time() + 90               # 单篇总预算 90s
    pages = []
    claim = None
    # 0) FI-ART 直查: doc id 自带 ART id → KCI 文章页, 零搜索零 LLM
    _mar = re.match(r"KCI_FI([0-9A-Za-z]+)_", doc)
    if _mar:
        claim = _kci_by_artid("ART" + _mar.group(1), title, fetcher=fetcher)
        if claim:
            pages = [(claim["evidence_url"], u"KCI citation meta")]
    # 0.5) DOI-CSL: 原文首页有 DOI → doi.org 一次出全
    if not claim and _tm.time() < _deadline:
        first_text = u""
        u0 = os.path.join(doc_dir, "unified", "page_0.json")
        if os.path.isfile(u0):
            try:
                j0 = json.load(io.open(u0, encoding="utf-8"))
                first_text = u" ".join((u.get("main_text") or "")
                                       for u in j0.get("unified") or [])
            except Exception:
                pass
        claim = _doi_csl(first_text, fetcher=fetcher)
    # 1) Naver→KCI meta 2) 宽搜页正则(需 pages, 预算内)
    if not claim and _tm.time() < _deadline:
        queries = [u"site:kci.go.kr \"%s\"" % title[:40],
                   u"%s %s KCI" % (title, doc),
                   u"\"%s\" 연구논문" % title[:40]]
        pages = search_pages(queries, fetcher=fetcher, deadline=_deadline)
        result["pages_fetched"] = len(pages)
        if pages:
            _t = (meta.get("title") or {}).get("value") or ""
            _kc, _url = _kci_via_naver(_t, fetcher=fetcher)
            if _kc:
                claim, pages = _kc, pages + [(_url, u"KCI citation meta")]
            if not claim:
                claim = _regex_record(card, pages)
    # 3) LLM 兜底(预算内)
    if not claim and _tm.time() < _deadline:
        claim = _llm_read(card, pages, cfg, opener=opener)
    if not claim:
        result["reason"] = u"未找到官方著录(分级均无果或超时): %s" % (_LAST_ERR[0] or "")
        _audit(doc_dir, result)
        return result
    ok, why = verify_claim(claim, pages, meta, jreg.issue_ledger(out_root))
    if not ok:
        result["reason"] = u"验证未过: %s" % why
        result["claim"] = claim
        _audit(doc_dir, result)
        return result
    url = claim.get("evidence_url") or (pages[0][0] if pages else "")
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    for k, zh in (("journal", u"刊名"), ("year", u"出版年"),
                  ("volume", u"卷"), ("issue", u"期"), ("pages", u"页码")):
        if k in miss and claim.get(k) not in (None, ""):
            meta[k] = {"value": int(claim[k]) if k == "year" and str(claim[k]).isdigit()
                       else claim[k], "uid": None,
                       "confidence": {"level": "high", "source": "web_record"}}
            result["filled"][k] = meta[k]["value"]
    if result["filled"]:
        meta["generated"] = now
        io.open(meta_fp, "w", encoding="utf-8").write(
            json.dumps(meta, ensure_ascii=False, indent=1))
        result["evidence_url"] = url
        result["evidence_quote"] = claim.get("evidence_quote")
    _audit(doc_dir, result)
    return result


def _audit(doc_dir, result):
    fp = os.path.join(doc_dir, "metadata_audit.json")
    try:
        a = json.load(io.open(fp, encoding="utf-8")) if os.path.isfile(fp) else {}
    except Exception:
        a = {}
    a["webfill"] = result
    io.open(fp, "w", encoding="utf-8").write(json.dumps(a, ensure_ascii=False, indent=1))


def note_line(result, miss=None):
    # 备注行(2026-11-19 定稿格式): [回填:联网检索] 已回填:字段; 原文仍缺:缺项。来源:出处。
    # miss 缺项列表(质检口径中文名), None = 无(必抽项已齐)。
    if not result or not (result.get("filled") or {}):
        return None
    zh = {"journal": u"刊名", "year": u"出版年", "volume": u"卷", "issue": u"期",
          "pages": u"页码"}
    order = ("journal", "year", "volume", "issue", "pages")
    got = u"/".join(zh[k] for k in order if k in result["filled"]) \
        or u"/".join(zh.get(k, k) for k in result["filled"])
    miss_s = u"、".join(miss) if miss else u"无(必抽项已齐)"
    url = result.get("evidence_url") or ""
    if "kci.go.kr" in url:
        src = u"KCI官方著录"
    elif "doi.org" in url:
        src = u"doi.org"
    elif "kiss.kstudy" in url:
        src = u"KISS"
    elif "dbpia" in url:
        src = u"DBpia"
    else:
        src = url.split("/")[2] if "//" in url else (url or u"联网著录")
    return u"[回填:联网检索] 已回填:%s; 原文仍缺:%s。来源:%s。" % (got, miss_s, src)


def _selftest():
    """契约自检: mock 抓取与 LLM, 不触网。跑法: python -m metapy.webfill"""
    import tempfile, os, json as _json
    fails = []

    def fake_fetch(url, **_kw):
        if "duckduckgo" in url or "bing.com" in url:
            return ('<a href="https://kci.go.kr/fake/13569969">x</a>'
                    '<a href="https://example.org/noise">y</a>')
        if "kci.go.kr/fake" in url:
            return (u"<html><body>중국사회과학논총 2023 vol.5, no.2, pp.45 - 72 "
                    u"(2023년) 5권 2호</body></html>")
        if "example.org" in url:
            return u"<html><body>无关页面 nothing here</body></html>"
        raise IOError("no route")

    def fake_opener(payload):
        return _json.dumps({"choices": [{"message": {"content": _json.dumps({
            "found": True, "journal": u"중국사회과학논총", "year": 2023,
            "volume": 5, "issue": 2,
            "evidence_url": "https://kci.go.kr/fake/13569969",
            "evidence_quote": u"중국사회과학논총 2023 vol.5, no.2"})}}]}).encode("utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        dd = os.path.join(tmp, "T1")
        os.makedirs(os.path.join(dd, "unified"))
        meta = {"title": {"value": u"중국의 정책 과정 분석"},
                "authors": [{"name": u"조영남"}],
                "journal": {"value": None}, "year": {"value": None},
                "volume": {"value": None}, "issue": {"value": None},
                "pages": {"value": "45-72"}}
        io.open(os.path.join(dd, "metadata.json"), "w", encoding="utf-8").write(
            _json.dumps(meta, ensure_ascii=False))
        r = webfill_doc("T1", tmp, cfg={"endpoint": "mock://x", "model": "m",
                                        "api_key_env": "KCI_LLM_KEY"},
                        fetcher=fake_fetch, opener=fake_opener)
        if (r.get("filled") or {}).get("volume") != 5 or (r.get("filled") or {}).get("issue") != 2:
            fails.append("fill: %s" % r)
        meta2 = _json.load(io.open(os.path.join(dd, "metadata.json"), encoding="utf-8"))
        if (meta2.get("volume") or {}).get("confidence", {}).get("source") != "web_record":
            fails.append("provenance: %s" % meta2.get("volume"))
        nl = note_line(r)
        if not nl or u"联网检索" not in nl:
            fails.append("note_line: %s" % nl)

        # 反例: 正则无果 + LLM 坏 claim(值不在页面上) → 双闸拒填
        def bad_fetch(url, **_kw):
            if "duckduckgo" in url or "bing.com" in url or "naver" in url:
                return '<a href="https://example.org/noise">y</a>'
            if "example.org" in url:
                return u"<html><body>중국사회과학논총 라는 간행물이 존재하긴 함</body></html>"
            raise IOError("no route")

        def bad_opener(payload):
            return _json.dumps({"choices": [{"message": {"content": _json.dumps({
                "found": True, "journal": u"중국사회과학논총", "year": 1999,
                "volume": 77, "issue": 88,
                "evidence_url": "https://kci.go.kr/fake/13569969"})}}]}).encode("utf-8")
        io.open(os.path.join(dd, "metadata.json"), "w", encoding="utf-8").write(
            _json.dumps(meta, ensure_ascii=False))
        r2 = webfill_doc("T1", tmp, cfg={"endpoint": "mock://x", "model": "m",
                                         "api_key_env": "KCI_LLM_KEY"},
                         fetcher=bad_fetch, opener=bad_opener)
        if r2.get("filled"):
            fails.append("bad claim not rejected: %s" % r2)

    # FI-ART 直查: doc id 带 ART id → KCI meta 直出, 零搜索零 LLM
    def kci_fetch(url, **_kw):
        if "ART001536751" in url:
            return (u'<meta name="citation_title" content="학술논문 : 통합문법: 주제,초점과 시점">'
                    u'<meta name="citation_journal_title" content="언어와 정보 사회">'
                    u'<meta name="citation_publication_date" content="2000/12">'
                    u'<meta name="citation_volume" content="1">'
                    u'<meta name="citation_issue" content="">'
                    u'<meta name="citation_firstpage" content="145">'
                    u'<meta name="citation_lastpage" content="175">')
        raise IOError("no route")
    _c = _kci_by_artid("ART001536751", u"통합문법: 주제·초점과 시점", fetcher=kci_fetch)
    if not (_c and _c.get("journal") == u"언어와 정보 사회" and _c.get("year") == 2000
            and _c.get("volume") == 1 and _c.get("pages") == "145-175"):
        fails.append("kci_by_artid: %s" % _c)
    _c2 = _kci_by_artid("ART001536751", u"완전히 다른 논문 제목입니다", fetcher=kci_fetch)
    if _c2 is not None:
        fails.append("kci title disambiguation failed: %s" % _c2)
    # 边界: 纯汉字(非韩文音节)标题归一化后为空串 → 消歧自动跳过(ART id 来自文件名, 误配风险低)
    _c3 = _kci_by_artid("ART001536751", u"完全不相干的标题", fetcher=kci_fetch)
    if _c3 is None:
        fails.append("kci cjk-title should pass through by artid")

    print("webfill selftest: %s" % ("PASS" if not fails else "FAIL %s" % fails))
    return not fails


if __name__ == "__main__":
    raise SystemExit(0 if _selftest() else 1)
