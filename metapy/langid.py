# -*- coding: utf-8 -*-
"""语种识别(2026-11-20 俄葡法适配): 从统一文本判定 ko/zh/ru/pt/fr/en。

qc.py 语言列、front.py/issue.py 规则分派共用一个实现。判据:
- 谚文占比 >= 0.2 → ko
- 汉字占比 >= 0.3 → zh
- 西里尔占比 >= 0.15 → ru(乌克兰语/保加利亚语等按 ru 规则处理, 元数据启发式同构)
- 拉丁: 葡/法功能词 + 特征字符打分, 分不出胜负回退 en
"""
import re

CYR_RE = re.compile(u"[\u0400-\u04FF]")
CJK_RE = re.compile(u"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HANGUL_RE = re.compile(u"[\uac00-\ud7a3]")
LAT_WORD_RE = re.compile(u"[a-zàâäãáåçéèêëíìîïñóòôõöúùûüœ]+", re.I)

PT_WORDS = {"de","da","do","das","dos","uma","ao","à","às","não","são","está","estão","para",
            "com","em","sobre","entre","desde","estudo","análise","brasil","sua","seu","pelo",
            "pela","nos","nas","por","artigo","revista","pesquisa","apresenta","resumo",
            "universidade","faculdade"}
FR_WORDS = {"de","des","du","le","la","les","une","un","est","dans","pour","avec","sur","entre",
            "étude","au","aux","en","par","son","sa","ses","france","français","française",
            "d'une","d'un","n'est","ce","que","revue","recherche","présente","résumé",
            "université","école","laboratoire"}
PT_CHARS = u"ãõ"
FR_CHARS = u"œïû"

LABELS = {"ko": u"韩文", "zh": u"中文", "ru": u"俄语",
          "pt": u"葡萄牙语", "fr": u"法语", "en": u"英文"}


def classify(text):
    """返回 'ko'/'zh'/'ru'/'pt'/'fr'/'en'/None。"""
    text = text or ""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    n = float(len(letters))
    if len(HANGUL_RE.findall(text)) / n >= 0.2:
        return "ko"
    if len(CJK_RE.findall(text)) / n >= 0.3:
        return "zh"
    if len(CYR_RE.findall(text)) / n >= 0.15:
        return "ru"
    toks = [t.lower() for t in LAT_WORD_RE.findall(text)]
    if not toks:
        return None
    pt = sum(1 for t in toks if t in PT_WORDS) + sum(text.count(c) for c in PT_CHARS)
    fr = sum(1 for t in toks if t in FR_WORDS) + sum(text.count(c) for c in FR_CHARS)
    if pt == 0 and fr == 0:
        return "en"
    if pt == fr:
        return None
    return "pt" if pt > fr else "fr"


def classify_page(j):
    """page_N.json 归一化块的语种判定(仅正文型块, 不含页眉页脚/文献)。"""
    parts = []
    for u in j.get("unified") or []:
        if u.get("type") not in ("Text", "Section-header", "Footnote"):
            continue
        parts.append(u.get("main_text") or "")
    return classify("\n".join(parts))
