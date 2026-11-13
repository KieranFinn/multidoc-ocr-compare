# -*- coding: utf-8 -*-
"""单渠道/1:3 对照查看器、轻量 Markdown/TeX 渲染与 metadata 面板。

不读取或改写 OCR 数据；pipeline.py 保留同名 re-export。
"""
import os, re, io, json, html, hashlib
from pipeline_io import _cin, _fold_sym, _gdice, _iou

NS = {"Section-header":"sec","Text":"txt","Footnote":"ftn","Picture":"pic","Caption":"cap",
      "Page-header":"hd","Page-footer":"ft","Page-number":"pn","Table":"tbl","Formula":"fm"}


_TEX_SYM = {"cdot":"·","times":"×","div":"÷","Sigma":"Σ","sigma":"σ","lambda":"λ","Lambda":"Λ",
 "alpha":"α","beta":"β","gamma":"γ","delta":"δ","Delta":"Δ","theta":"θ","Theta":"Θ","rho":"ρ",
 "mu":"μ","pi":"π","omega":"ω","Omega":"Ω","phi":"φ","Phi":"Φ","chi":"χ","eta":"η","epsilon":"ε",
 "leq":"≤","geq":"≥","neq":"≠","approx":"≈","sim":"~","simeq":"≃","infty":"∞","partial":"∂","nabla":"∇",
 "subseteq":"⊆","subset":"⊂","supset":"⊃","supseteq":"⊇","in":"∈","ni":"∋","cup":"∪","cap":"∩",
 "pm":"±","mp":"∓","rightarrow":"→","leftarrow":"←","Rightarrow":"⇒","to":"→","mapsto":"↦",
 "circ":"∘","bullet":"•","ldots":"…","cdots":"⋯","quad":" ","qquad":" ","propto":"∝",
 "sum":"∑","prod":"∏","int":"∫","oint":"∮","sqrt":"√","sqsubseteq":"⊑","%":"%","#":"#"}
_TEX_ONE = re.compile(r"\\(mathrm|mathbf|mathit|mathsf|boldsymbol|operatorname|mathop|mathcal|mathfrak|hat|widehat|rm|bf|it|text)\s*(\{(?:[^{}]|\{[^{}]*\})*\})")
_TEX_FRAC = re.compile(r"\\[dt]?frac\s*(\{(?:[^{}]|\{[^{}]*\})*\})\s*(\{(?:[^{}]|\{[^{}]*\})*\})")
_TEX_BIG = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)\b\s*([()\[\]|.,])?")

def _tex_conv(x, _depth=0):
    r"""常见行内 LaTeX -> HTML: \mathrm{}等取内容(支持一层嵌套如 \mathrm{H_{2}O})、\frac→上下排、
    _{}/^_ → sub/sup、希腊字母/关系符。不追求完整 TeX 语法; 无法识别的命令保留原样。"""
    if _depth > 6:
        return x
    x = _TEX_BIG.sub(lambda m: m.group(1) or "", x)
    x = _TEX_ONE.sub(lambda m: _tex_conv(m.group(2)[1:-1], _depth + 1), x)
    x = _TEX_FRAC.sub(lambda m: '<i class="fr"><sup>%s</sup>&frasl;<sub>%s</sub></i>'
                      % (_tex_conv(m.group(1)[1:-1], _depth + 1), _tex_conv(m.group(2)[1:-1], _depth + 1)), x)
    # "M g S O _ { 4 }" 式逐字母空格压缩(连续 3+ 个单字母/数字才收, 降低误伤英文概率)
    x = re.sub(r"\b(?:[A-Za-z0-9] ){2,}[A-Za-z0-9]\b", lambda m: m.group(0).replace(" ", ""), x)
    x = re.sub(r"(?<=[\w\)\}\]])[ \t]*_[ \t]*\{[ \t]*([^{}]*?)[ \t]*\}", r"<sub>\1</sub>", x)
    x = re.sub(r"(?<=[A-Za-z])[ \t]*_[ \t]*([0-9])(?![0-9])", r"<sub>\1</sub>", x)
    x = re.sub(r"(?<=[\w\)\}\]])[ \t]*\^\s*\{[ \t]*([^{}]*?)[ \t]*\}", r"<sup>\1</sup>", x)
    x = re.sub(r"(?<=[\w\)\}\]])\^\s*([A-Za-z0-9+-])(?![A-Za-z0-9])", r"<sup>\1</sup>", x)
    x = re.sub(r"\\([A-Za-z]+|%|#)", lambda m: _TEX_SYM.get(m.group(1), m.group(0)), x)
    return re.sub(r"\\[,;! ]", " ", x)

def texlite(s):
    r"""查看器展示层的轻量公式渲染: $...$/\(...\) 定界式先取出转换, 再对全文残留命令
    (如 MinerU 无 $$ 定界、直接混排的 \mathrm{...})转换。data/merged 中的原始文本不变。
    无公式特征时原样返回。"""
    if not s or not any(ch in s for ch in "$\\_^"):
        return s
    s = re.sub(r"\\\((.+?)\\\)", lambda m: _tex_conv(m.group(1)), s)
    s = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), s)
    return _tex_conv(s)


_TEX_TAG = re.compile(r"&lt;(/?)(i|sup|sub)( class=\"fr\")?&gt;")
_TEX_FRASL = re.compile(r"&amp;frasl;")


def _texesc(s):
    """metadata 带展示层消毒: texlite 轻量公式 → HTML 转义 → 放行 texlite 生成的白名单标签
    (i.fr/sup/sub/&frasl;)。与 md2html 正文块同一转换口径(cspan 句/文献题名/摘要此前只转义
    不渲染, 公式残留 LaTeX 源码); 未识别命令保留原样, 无公式特征时等价纯转义。"""
    if not s:
        return ""
    h = html.escape(texlite(s), quote=False)
    h = _TEX_TAG.sub(lambda m: "<%s%s%s>" % (m.group(1), m.group(2), m.group(3) or ""), h)
    return _TEX_FRASL.sub("&frasl;", h)


def md2html(md):
    def esc(s): return html.escape(s, quote=False)
    out, in_code, tbl = [], False, []
    def flush():
        nonlocal tbl
        if tbl:
            rows = [r for r in tbl if not re.match(r"^\s*\|?[\s:-]+\|", r)]
            if rows:
                t = ["<table>"]
                for i, r in enumerate(rows):
                    cells = [c.strip() for c in r.strip().strip("|").split("|")]
                    tag = "th" if i == 0 else "td"
                    t.append("<tr>" + "".join("<%s>%s</%s>" % (tag, esc(c), tag) for c in cells) + "</tr>")
                t.append("</table>"); out.append("".join(t))
            tbl = []
    for ln in md.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code; out.append("<pre>" if in_code else "</pre>"); continue
        if in_code: out.append(esc(ln) + "\n"); continue
        if ln.strip().startswith("|") and "|" in ln.strip()[1:]: tbl.append(ln); continue
        flush()
        s = ln.rstrip()
        if not s.strip(): continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m: out.append("<h%d>%s</h%d>" % (len(m.group(1)), esc(m.group(2)), len(m.group(1)))); continue
        if re.match(r"^---+\s*$", s): out.append("<hr/>"); continue
        m = re.match(r"^!\[(.*?)\]\((.*?)\)", s.strip())
        if m: out.append('<div class="fig">[图: %s]</div>' % esc(m.group(1))); continue
        s2 = re.sub(r"^\s*[-*]\s+", "", esc(s))
        s2 = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s2)
        out.append("<p>" + texlite(s2) + "</p>")
    flush()
    return "\n".join(out).replace("\ufffd", '<span class="bad">�</span>')

_JS_CORE = r"""
var META=@@DATA@@, pinned=null, hoverGid=null, hoverBid=null;
function act(id,on){var e=document.querySelectorAll('[data-b="'+id+'"]');for(var k=0;k<e.length;k++){if(on)e[k].classList.add('on');else e[k].classList.remove('on');}}
function stat(id){var p=id.split('_');var el=document.getElementById('st_p'+p[1]);if(el&&META[id])el.textContent=' '+p[0]+' · 块 '+p[2]+' · '+META[id].cat+' · '+META[id].txt.replace(/\s+/g,' ').slice(0,40);}
document.addEventListener('mouseover',function(e){
 var eye=e.target.closest?e.target.closest('.eye'):null;
 if(eye){
  if(e.relatedTarget&&eye.contains(e.relatedTarget))return;   /* 眼睛内部移动不算重入 */
  clearTimeout(ghide);ghide=null;
  var g=eye.dataset.g;
  if(hoverGid!==g){hoverGid=g;fillMasks(g,eye.closest('section'));}  /* 同一只眼不重复重绘四个遮罩 */
  return;
 }
 var t=e.target.closest?e.target.closest('[data-b]'):null;
 if(!t||t.dataset.b===pinned||t.dataset.b===hoverBid)return;
 hoverBid=t.dataset.b;act(t.dataset.b,true);stat(t.dataset.b);});
document.addEventListener('mouseout',function(e){
 var eye=e.target.closest?e.target.closest('.eye'):null;
 if(eye){
  if(e.relatedTarget&&eye.contains(e.relatedTarget))return;
  var toEye=e.relatedTarget&&e.relatedTarget.closest?e.relatedTarget.closest('.eye'):null;
  if(!toEye){hoverGid=null;scheduleHide();}   /* 只在真的离开眼睛时排一次隐藏, 不在每次移动时重置 */
 }
 var t=e.target.closest?e.target.closest('[data-b]'):null;
 if(t&&t.dataset.b!==pinned&&(!e.relatedTarget||!t.contains(e.relatedTarget))){
  act(t.dataset.b,false);if(hoverBid===t.dataset.b)hoverBid=null;}});
document.addEventListener('click',function(e){
 var eye=e.target.closest?e.target.closest('.eye'):null;
 if(eye){act(pinned,false);pinned=(pinned===eye.dataset.g)?null:eye.dataset.g;
  hoverGid=null;   /* 钉住/取消后重新悬停同一只眼必须能重新重绘遮罩 */
  if(pinned)showPinned();else hideAllMasks();return;}
 var t=e.target.closest?e.target.closest('[data-b]'):null;
 if(pinned&&(!t||t.dataset.b!==pinned)){act(pinned,false);
  if(document.querySelector('.eye[data-g="'+pinned+'"]'))hideAllMasks();pinned=null;}
 if(!t)return;
 pinned=t.dataset.b;act(pinned,true);stat(pinned);});
document.addEventListener('click',function(e){var j=e.target.closest?e.target.closest('.jump'):null;if(!j)return;e.preventDefault();
 var el=document.getElementById(j.dataset.t);if(!el)return;el.scrollIntoView({block:'center',behavior:'smooth'});
 var id=el.dataset.b;act(id,true);setTimeout(function(){if(pinned!==id)act(id,false);},1600);});
"""


VIEWER_TMPL = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>@@TITLE@@</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f4f5f7;color:#222}
header{position:sticky;top:0;background:#1f2937;color:#fff;padding:10px 18px;z-index:9}
header b{font-size:15px}nav{display:inline-block;margin-left:16px}
nav a{color:#9ca3af;margin:0 4px;text-decoration:none;font-size:14px}nav a:hover{color:#fff}
.back{color:#fbbf24;margin-right:12px;text-decoration:none}
.wrap{max-width:1560px;margin:0 auto;padding:14px}
section{margin:20px 0}h2{font-size:15px;color:#374151;border-left:4px solid #2563eb;padding-left:8px}
.stat{color:#6b7280;font-weight:normal;font-size:12px;margin-left:10px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pane{background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.ptitle{background:#eef2ff;color:#3730a3;font-size:12px;padding:5px 10px;border-bottom:1px solid #e5e7eb}
.imgw{position:relative}
.imgw img{width:100%;display:block}
.imgw svg{position:absolute;left:0;top:0;width:100%;height:100%}
.r{fill-opacity:.14;stroke-width:1.2,stroke-opacity:.9;cursor:pointer;vector-effect:non-scaling-stroke}
.r.txt{fill:#6b7280;stroke:#6b7280}.r.sec{fill:#2563eb;stroke:#2563eb}
.r.ftn{fill:#d97706;stroke:#d97706}.r.pic{fill:#7c3aed;stroke:#7c3aed}
.r.cap{fill:#0891b2;stroke:#0891b2}.r.hd,.r.ft,.r.pn{fill:none;stroke:#dc2626;stroke-dasharray:5 3}
.r.tbl{fill:#059669;stroke:#059669}.r.fm{fill:#db2777;stroke:#db2777}
.r.on{fill-opacity:.38;stroke-opacity:1;stroke-width:3.2}
.txt{padding:8px 10px;max-height:980px;overflow:auto;font-size:14px;line-height:1.8}
.blk table{border-collapse:collapse;margin:4px 0;font-size:12px}.blk td,.blk th{border:1px solid #d1d5db;padding:2px 6px}
.blk{border-left:4px solid #e5e7eb;padding:4px 8px;margin:4px 0;border-radius:0 6px 6px 0;cursor:pointer;background:#fafafa}
.blk .tag{display:inline-block;font-size:10px;color:#9ca3af;border:1px solid #e5e7eb;border-radius:4px;padding:0 5px;margin-right:6px;background:#fff}
.blk.on{background:#fef9c3;box-shadow:0 0 0 2px #f59e0b inset}
.blk.emp{background:#fef2f2;border-left-color:#dc2626}
.bad{background:#fee2e2;color:#dc2626;padding:0 3px;border-radius:3px;font-weight:bold}
a.bad,a.empf{cursor:pointer;text-decoration:none}
.empf{background:#f3e8ff;color:#7c3aed;padding:0 5px;border-radius:3px;font-weight:bold;font-size:12px}
i.fr{font-style:normal;white-space:nowrap}
.tcap{font-size:12px;color:#374151;padding:3px 0 5px;font-weight:bold}
.pfig img{max-width:100%;border:1px solid #e5e7eb;border-radius:4px;display:block}
svg.picr{width:100%;height:auto;display:block;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.r.emp{stroke:#dc2626;stroke-dasharray:4 3}
.lg{display:inline-block;margin-left:14px;font-size:12px}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 3px;vertical-align:-1px}
.fullmd{padding:14px 18px;font-size:14px;line-height:1.9;max-width:1100px;margin:0 auto;background:#fff;min-height:600px}
</style></head><body>
<header><a class="back" href="index.html">‹ 三渠道总览</a><b>@@TITLE@@</b><nav>跳页：@@NAV@@</nav>@@LAYBTN@@
<span class="lg"><i style="background:#2563eb"></i>标题 <i style="background:#6b7280"></i>正文 <i style="background:#d97706"></i>脚注 <i style="background:#7c3aed"></i>图片 <i style="background:#0891b2"></i>图注 <i style="background:#059669"></i>表格 <i style="background:#fff;border:1px dashed #dc2626"></i>页眉页脚页码 <i style="background:#fee2e2;border:1px solid #dc2626"></i>空块</span>
</header>
@@BODY@@
<script>
@@CORE@@
var ghide=null;   /* _JS_CORE 的 clearTimeout(ghide) 在这里也要有定义 */
function scheduleHide(){}
function hideAllMasks(){}
function showPinned(){}
</script>
</body></html>"""

_TAB_BAD = re.compile(r"</?(script|style|iframe|object|embed|form|button|a|link|meta)\b[^>]*>", re.I)
_TAB_ON = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)

_BLK_TAG_OK = re.compile(r"&lt;(/?(?:sub|sup|b|i|dive)(?:\s+class=\"fr\")?\s*/?)&gt;", re.I)

def _blk_html(t):
    """正文块展示层: texlite 转行内公式 -> 全转义消毒 -> 白名单标签(sub/sup/b/i/dive/br)放行 -> md 强调/换行。
    源文本自带的 <sub>/<sup>/<DIVE>(MinerU/Paddle 已有 HTML)借此按原意渲染, 其余标签一律转义显示。"""
    e = html.escape(texlite(t), quote=False)
    e = _BLK_TAG_OK.sub(r"<\1>", e)
    e = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e)
    e = re.sub(r"\*(.+?)\*", r"<i>\1</i>", e)
    return e.replace("&amp;frasl;", "&frasl;").replace("\n", "<br/>").replace("\ufffd", '<span class="bad">\ufffd</span>')


_CMP_DOT = {"mineru": "m", "dots": "d", "paddle": "p"}
_CMP_CN = {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}

def _cmp_preview(t, limit=240):
    """遮罩内的块文本预览: 表格块渲染清洗后的真表格(深色遮罩样式), 纯文本走 _blk_html(公式/sub/sup)。"""
    t = (t or "").strip()
    if "<table" in t:
        h = _TAB_BAD.sub("", t)
        h = _TAB_ON.sub("", h)
        if "$" in h or "\\" in h:
            h = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), h)
        return '<div class="pv pvt">%s</div>' % h
    return '<div class="pv">%s</div>' % _blk_html(re.sub(r"\s+", " ", t)[:limit])

def _cmp_group_masks(g, chs):
    """小眼睛遮罩内容: 返回 (panes[4], 是否值得标眼, 锚框)。
    panes[0]=原图栏(三渠道总览表), panes[1:]=各渠道栏(该渠道视角的差异分析)。g=[(ch,i,blk),...]"""
    texts = {c: (b.get("text") or "") for c, i, b in g}
    boxes = {c: b.get("box") or [0, 0, 0, 0] for c, i, b in g}
    blks = {c: b for c, i, b in g}
    filled = {c: t for c, t in texts.items() if t.strip()}
    main_c = next((c for c in chs if c in filled), None)
    main_t = texts.get(main_c, "")
    flags = []
    have = [c for c, i, b in g]
    if len(have) < 3:
        flags.append("渠道缺失:" + "/".join(c for c in chs if c not in have))
    vs = list(filled.values())
    n_diff = sum(1 for x in range(len(vs)) for y in range(x+1, len(vs)) if _gdice(vs[x], vs[y]) < 0.9)
    if n_diff: flags.append("文本分歧")
    if any("\ufffd" in t for t in filled.values()): flags.append("FFFD")
    if any(b.get("xmerge") for c, i, b in g):
        flags.append("跨页并入(" + {"strong": "文本证据", "layout": "排版推断"}.get(
            next((b.get("xev") for c, i, b in g if b.get("xmerge")), ""), "") + ")")
    if any(not (b.get("text") or "").strip() and b.get("cat") != "Picture" for c, i, b in g): flags.append("空块")
    anchor = boxes.get(main_c) or boxes[have[0]]
    rows = []
    for c in chs:
        if c in filled:
            b = blks[c]
            mark = []
            if "\ufffd" in texts[c]: mark.append("FFFD")
            if b.get("xmerge"): mark.append("跨页并入(" + {"strong": "文本证据", "layout": "排版推断"}.get(b.get("xev"), "") + ")")
            if not (b.get("text") or "").strip() and b.get("cat") != "Picture": mark.append("空块")
            rows.append("<tr><td><i class='dot %s'></i>%s</td><td>✓ %s</td><td>%d</td><td>%d</td><td>%.2f</td><td>%s</td></tr>" % (
                _CMP_DOT[c], _CMP_CN[c], b.get("cat", "Text"), len(texts[c]),
                len(re.findall(r"[가-힣]", texts[c])),
                _iou(boxes[c], anchor), " ".join(mark) or "-"))
        else:
            rows.append("<tr class='miss'><td><i class='dot %s'></i>%s</td><td colspan=5>✗ 未识别此区域</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c]))
    pane0 = ("<h5>三渠道总览（本区域）</h5><table><tr><th>渠道</th><th>检出</th><th>字符</th><th>韩文</th>"
             "<th>框IoU</th><th>标记</th></tr>%s</table><p class='fl'>%s</p>"
             % ("".join(rows), " · ".join(flags) or "三家一致"))
    panes = [pane0]
    for c in chs:
        head = "<h5>%s 视角</h5>" % _CMP_CN[c]
        if c not in blks:
            panes.append(head + "<p class='miss'>✗ 本渠道未识别到此区域（另两家有）</p>"); continue
        b, t = blks[c], texts[c]
        if not t.strip():
            body = "<p class='miss'>本渠道此区域识别为空</p>"
            if b.get("xmerge"):
                body += "<p>段首已被引擎并入上一页末块（跨页并入，页码混入风险）</p>"
            panes.append(head + body); continue
        if c == main_c:
            st = "主读法来源" + ("（三家文本一致）" if not n_diff else "")
        else:
            d = _gdice(t, main_t)
            st = "与主读法 dice=%.2f → %s" % (d, "实质一致" if d >= 0.9 else "存在分歧")
        body = "<p>%s</p>%s" % (st, _cmp_preview(t))
        if c != main_c and n_diff:
            body += "<p class='pm'>主读法(%s) 对照:</p>%s" % (_CMP_CN[main_c], _cmp_preview(main_t))
        if b.get("cap"):
            body += "<p class='pm'>题注: %s</p>" % html.escape(b["cap"][:120])
        panes.append(head + body)
    return panes, bool(flags), anchor

def _tag(b):
    """块标签: 类型 + 跨页合并标记(MinerU 把下页段首并入上页末块的缺陷)"""
    t = b["cat"]
    if b.get("xmerge"):
        t += " · 跨页并入"
    elif b.get("xmerge_src"):
        t += " · 含下页段首"
    return t

def _blk_text(b, img_prefix=None, page_img=None):
    """块文本渲染: Table 块嵌入清洗后的 HTML 表格(白名单去危险标签/事件属性);
    Picture 块渲染真实图片(引擎抽取图优先, 否则从原页图按 bbox 裁区), 题注随文本附在后面;
    其余空文本块给占位说明(识别为空)。img_prefix: raw/<渠道>/ 归档目录前缀; page_img: (原页图URL, (W,H))"""
    t = (b.get("text") or "").strip()
    if b.get("cat") == "Table" and t.startswith("<") and "<table" in t:
        html_ = _TAB_BAD.sub("", t)
        html_ = _TAB_ON.sub("", html_)
        if "$" in html_ or "\\" in html_:  # 表格单元格内的行内公式
            html_ = re.sub(r"\$([^$\n]+?)\$", lambda m: _tex_conv(m.group(1)), html_)
        if b.get("cap"):
            html_ = '<div class="tcap">%s</div>' % _blk_html(b["cap"]) + html_
        return html_  # 本地单文件查看器, 去危险标签与 on* 属性后原样嵌入
    fig = ""
    if b.get("cat") == "Picture":
        # img 已有全前缀(adapt_mineru/merged.json 口径: raw/<dir>/images/...)则不再拼——
        # 新鲜 per_ch 与 rebuild_viewer 剥回的引擎相对路径两种形态都要能渲染
        if b.get("img") and b["img"].startswith("raw/"):
            fig = '<div class="pfig"><img src="%s" loading="lazy"/></div>' % b["img"]
        elif b.get("img") and img_prefix:
            fig = '<div class="pfig"><img src="%s%s" loading="lazy"/></div>' % (img_prefix, b["img"])
        elif page_img:
            url, (PW, PH) = page_img
            x0, y0, x1, y1 = b.get("box") or [0, 0, 0, 0]
            fig = ('<svg class="picr" viewBox="%s %s %s %s" preserveAspectRatio="none">'
                   '<image width="%s" height="%s" href="%s"/></svg>'
                   % (round(x0, 1), round(y0, 1), round(max(2.0, x1 - x0), 1), round(max(2.0, y1 - y0), 1), PW, PH, url))
    if t:
        body = _blk_html(t)
        return fig + body if fig else body
    if fig:
        return fig
    if b.get("xmerge"):
        return '<span style="color:#b45309;font-style:italic">（识别为空——段首已被引擎并入上一页末块，悬停上一页末块可见原文）</span>'
    ph = "（图片区域）" if b.get("cat") == "Picture" else "（识别为空）"
    return '<span style="color:#9ca3af;font-style:italic">%s</span>' % ph


def build_viewer(out_fp, title, sizes, per_page=None, md_text=None, calib_note=None, img_prefix=None):
    n = len(sizes)
    nav = "".join('<a href="#p%d">%d</a>' % (i, i+1) for i in range(n))
    secs, data = [], {}
    if md_text is None:
        for p in range(n):
            W, H = sizes[p]
            blocks = (per_page or {}).get(p) or []
            if blocks:
                rb, rects = [], []
                nemp, first_emp, first_ffd = 0, None, None
                for i, b in enumerate(blocks):
                    bid = "%d_%d" % (p, i)
                    raw = b.get("text") or ""
                    data[bid] = {"cat": b["cat"], "txt": raw[:60]}
                    is_emp = not raw.strip() and b.get("cat") != "Picture"
                    if is_emp:
                        nemp += 1; first_emp = first_emp or bid
                    if "\ufffd" in raw:
                        first_ffd = first_ffd or bid
                    anchor = ""
                    if bid == first_emp: anchor = ' id="emp_%s"' % bid
                    elif bid == first_ffd: anchor = ' id="ffd_%s"' % bid
                    cls = "blk emp" if is_emp else "blk"
                    rb.append('<div class="%s" data-b="%s"%s><span class="tag">%s</span>%s</div>' % (cls, bid, anchor, _tag(b), _blk_text(b, img_prefix, ("pages/page_%d.jpg" % p, (W, H)))))
                    box = b["box"]
                    rcls = NS.get(b["cat"], "txt") + (" emp" if is_emp else "")
                    rects.append('<rect data-b="%s" class="r %s" x="%s" y="%s" width="%s" height="%s"/>' % (
                        bid, rcls, box[0], box[1], max(2, box[2]-box[0]), max(2, box[3]-box[1])))
                fffd = sum((b.get("text") or "").count("\ufffd") for b in blocks)
                flag = ""
                if fffd:
                    flag += ' <a class="bad jump" data-t="ffd_%s" title="跳到本页首个含错误字符的块">FFFD×%d</a>' % (first_ffd, fffd)
                if nemp:
                    flag += ' <a class="empf jump" data-t="emp_%s" title="跳到本页首个空块">空块×%d</a>' % (first_emp, nemp)
                body = '''<div class="grid">
  <div class="pane"><div class="ptitle">原文件（悬停/点击区块联动）</div>
   <div class="imgw"><img src="pages/page_%d.jpg"/><svg viewBox="0 0 %d %d" preserveAspectRatio="none">%s</svg></div></div>
  <div class="pane"><div class="ptitle">解析输出（块级）</div><div class="txt">%s</div></div>
 </div>''' % (p, W, H, "".join(rects), "\n".join(rb))
            else:
                flag = ""
                body = '''<div class="grid">
  <div class="pane"><div class="ptitle">原文件</div><img src="pages/page_%d.jpg"/></div>
  <div class="pane"><div class="ptitle">解析输出</div><div class="txt" style="color:#9ca3af">该渠道此页无结构化输出</div></div>
 </div>''' % p
            secs.append('<section id="p%d"><h2>第 %d 页%s <span class="stat" id="st_p%d"></span></h2>%s</section>' % (p, p+1, flag, p, body))
        body_html = '<div class="wrap">%s</div>' % "\n".join(secs)
    else:
        body_html = '<div class="fullmd">%s</div>' % md2html(md_text)
    v = VIEWER_TMPL.replace("@@CORE@@", _JS_CORE).replace("@@TITLE@@", title).replace("@@NAV@@", nav)
    v = v.replace("@@LAYBTN@@", "")
    v = v.replace("@@BODY@@", body_html).replace("@@DATA@@", json.dumps(data, ensure_ascii=False))
    if calib_note:
        v = v.replace("<head>", "<head>\n<!-- calib: %s -->" % calib_note, 1)
    with io.open(out_fp, "w", encoding="utf-8") as fh:
        fh.write(v)
    return len(data)

COMPARE_TMPL = u"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>@@TITLE@@</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f4f5f7;color:#222}
header{position:sticky;top:0;background:#1f2937;color:#fff;padding:10px 18px;z-index:9}
header b{font-size:15px}nav{display:inline-block;margin-left:16px}
nav a{color:#9ca3af;margin:0 4px;text-decoration:none;font-size:14px}nav a:hover{color:#fff}
.back{color:#fbbf24;margin-right:12px;text-decoration:none}
.wrap{max-width:1960px;margin:0 auto;padding:14px}
section{margin:20px 0}h2{font-size:15px;color:#374151;border-left:4px solid #2563eb;padding-left:8px}
.stat{color:#6b7280;font-weight:normal;font-size:12px;margin-left:10px}
.grid{display:grid;grid-template-columns:1.15fr 1fr 1fr 1fr;gap:10px}
.pane.meta{grid-column:1/-1}   /* 元数据横带: 原第5格(窄列折行+右侧死白)改整行贯穿 */
.pane.meta.ov .ptitle{background:#7c3aed}   /* 第1页总览横带: 紫头与普通横带(灰头)区分 */
.mfw{font-size:12px;padding:6px 8px}
body.boxov .imgw .r.umb,body.boxov .imgw .r.um{opacity:1}   /* 裁决1(§47): LAYBTN 降为框叠加诊断——只读叠加, 不切换布局/不藏渠道栏 */
#laybtn{cursor:pointer;border:1px solid #cbd5e1;background:#f8fafc;border-radius:5px;padding:1px 8px;font-size:12px;margin-left:8px}
#laybtn.on{background:#dbeafe;border-color:#2563eb}
.mfh{font-size:11px;color:#6b7280;font-weight:bold;margin:6px 0 3px;border-top:1px solid #e5e7eb;padding-top:5px}
.mfg:first-child .mfh{border-top:none;margin-top:0;padding-top:0}
.mf{padding:3px 6px;margin:2px 0;border-radius:5px;cursor:pointer;background:#fafafa;border-left:3px solid #e5e7eb}
.mf:hover{background:#fef9c3}
.mf .md{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.mf.high{border-left-color:#16a34a}.mf.mid{border-left-color:#d97706}.mf.low{border-left-color:#dc2626}
.mf.high .md{background:#16a34a}.mf.mid .md{background:#d97706}.mf.low .md{background:#dc2626}
.mf .esl{color:#6b7280;font-size:11px}
.mf .lk{color:#2563eb;font-size:11px;margin-left:6px}
.mft{font-size:12px;line-height:1.6;color:#374151;word-break:break-word}
.mf table{border-collapse:collapse;width:100%;font-size:11px;table-layout:fixed}
.mf td{padding:1px 4px;border-bottom:1px solid #f3f4f6;vertical-align:top;white-space:normal;word-break:break-word}
.mf td.cnum{white-space:nowrap;width:26px}
.mfg table{border-collapse:collapse;width:100%;font-size:11px;table-layout:fixed}
.mfg table.refs td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;word-break:normal}
.mfg table.refs td.rawtext{white-space:pre-wrap;overflow:visible;word-break:break-word}
.mfg table.cspt td{white-space:normal;word-break:break-word}   /* 引文句句子换行, 引文高亮不再被裁掉 */
.mftag{display:inline-block;font-size:9px;color:#6b7280;border:1px solid #e5e7eb;border-radius:3px;padding:0 3px;margin-right:4px;vertical-align:1px}
.ck{display:inline-block;border-radius:5px;padding:1px 7px;margin:2px 3px 2px 0;font-size:11px;font-weight:600}
.ck.ok{background:#e4f6ef;color:#087b5c}.ck.no{background:#fdebec;color:#bd353d}
.ckg{font-size:10px;color:#9aa6b8;margin:0 2px 0 10px;font-weight:700}
.mf tr:hover{background:#fef9c3}
details.mfd summary{cursor:pointer;color:#6b7280;font-size:11px}
.mbadge{font-size:10px;border-radius:3px;padding:0 4px;margin-left:6px}
.mbadge.ok{background:#dcfce7;color:#16a34a}
.mbadge.bad{background:#fee2e2;color:#dc2626}
.pane{background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
.ptitle{font-size:12px;padding:5px 10px;border-bottom:1px solid #e5e7eb;color:#fff;font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ptitle.src{background:#374151}.ptitle.mineru{background:#2563eb}.ptitle.dots{background:#d97706}.ptitle.paddle{background:#059669}
.imgw{position:relative}
.imgw img{width:100%;display:block}
.imgw svg{position:absolute;left:0;top:0;width:100%;height:100%}
.r{fill-opacity:.14;stroke-width:2.4;stroke-opacity:.9;cursor:pointer;vector-effect:non-scaling-stroke;opacity:0;transition:opacity .12s}
.r.txt{fill:#6b7280;stroke:#6b7280}.r.sec{fill:#2563eb;stroke:#2563eb}
.r.ftn{fill:#d97706;stroke:#d97706}.r.pic{fill:#7c3aed;stroke:#7c3aed}
.r.cap{fill:#0891b2;stroke:#0891b2}.r.hd,.r.ft,.r.pn{fill:none;stroke:#dc2626;stroke-dasharray:5 3}
.r.tbl{fill:#059669;stroke:#059669}.r.fm{fill:#db2777;stroke:#db2777}
.r.on{opacity:1;fill-opacity:.38;stroke-opacity:1;stroke-width:2}
.txt{padding:8px 10px;max-height:640px;overflow:auto;font-size:13px;line-height:1.75}
.blk table{border-collapse:collapse;margin:4px 0;font-size:12px}.blk td,.blk th{border:1px solid #d1d5db;padding:2px 6px}
.blk{border-left:4px solid #e5e7eb;padding:4px 8px;margin:4px 0;border-radius:0 6px 6px 0;cursor:pointer;background:#fafafa}
.blk .tag{display:inline-block;font-size:10px;color:#9ca3af;border:1px solid #e5e7eb;border-radius:4px;padding:0 5px;margin-right:6px;background:#fff}
.blk.on{background:#fef9c3;box-shadow:0 0 0 2px #f59e0b inset}
.blk.mineru.on{background:#dbeafe;box-shadow:0 0 0 2px #2563eb inset}
.blk.dots.on{background:#fef3c7;box-shadow:0 0 0 2px #d97706 inset}
.blk.paddle.on{background:#d1fae5;box-shadow:0 0 0 2px #059669 inset}
.blk.emp{background:#fef2f2;border-left-color:#dc2626}
a.bad,a.empf{cursor:pointer;text-decoration:none}
.empf{background:#f3e8ff;color:#7c3aed;padding:0 5px;border-radius:3px;font-weight:bold;font-size:12px}
i.fr{font-style:normal;white-space:nowrap}
.tcap{font-size:12px;color:#374151;padding:3px 0 5px;font-weight:bold}
.pfig img{max-width:100%;border:1px solid #e5e7eb;border-radius:4px;display:block}
svg.picr{width:100%;height:auto;display:block;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.r.emp{stroke:#dc2626;stroke-dasharray:4 3}
.none{color:#9ca3af;font-size:13px;padding:14px 10px}
.bad{background:#fee2e2;color:#dc2626;padding:0 3px;border-radius:3px;font-weight:bold}
.pane{position:relative}
.eye{position:absolute;z-index:5;width:20px;height:20px;line-height:19px;text-align:center;background:rgba(31,41,55,.88);border:1.5px solid #fbbf24;border-radius:50%;cursor:pointer;font-size:11px;transform:translate(-50%,-50%);user-select:none}
.eye:hover,.eye.pin{background:#fbbf24}
.eye.lm{background:rgba(245,158,11,.92);border-color:#fef3c7}.eye.lm:hover,.eye.lm.pin{background:#f59e0b}
.eye.ll{background:rgba(220,38,38,.92);border-color:#fecaca}.eye.ll:hover,.eye.ll.pin{background:#ef4444}
.eye.lc{background:rgba(124,58,237,.92);border-color:#ddd6fe}.eye.lc:hover,.eye.lc.pin{background:#8b5cf6}
.eye.lo{background:rgba(37,99,235,.92);border-color:#bfdbfe}.eye.lo:hover,.eye.lo.pin{background:#3b82f6}
.r.um{fill:none;stroke:#111827;stroke-width:1.2;stroke-dasharray:10 5;opacity:0;transition:opacity .15s;pointer-events:none}
.r.um.on{opacity:.85}   /* 2026-11-10 共识虚线框默认隐, 随遮罩(悬停/钉👁)点亮, 不再常显压扫描图 */
.r.umb{fill:none;stroke:#fff;stroke-width:2.5;opacity:0;transition:opacity .15s;pointer-events:none}
.r.umb.on{opacity:.9}
.r.umb.flash,.r.um.flash{opacity:1}   /* 闪橙框独立于显隐态 */
.r.cite-target{fill:#fbbf24;fill-opacity:0;stroke:#f59e0b;stroke-width:1.5;stroke-opacity:0;opacity:0;pointer-events:none}
.r.cite-target.cite-on{opacity:1;fill-opacity:.16;stroke-opacity:1}
.r.flash{stroke:#f59e0b!important;stroke-width:2.5;fill:#fbbf24;fill-opacity:.25;opacity:1}   /* 点击跳转后原图块橙框闪烁 */
#citepop{display:none;position:fixed;right:16px;bottom:16px;width:360px;max-width:80vw;background:#fff;border:2px solid #f59e0b;border-radius:8px;z-index:30;box-shadow:0 10px 30px rgba(0,0,0,.25);padding:8px;pointer-events:none}
#citepoptitle{font-size:12px;font-weight:bold;color:#92400e;margin-bottom:5px}
#citesvg{display:block;width:100%;height:auto;max-height:60vh;background:#fff}
#citesvg .cpbox{fill:#fbbf24;fill-opacity:.18;stroke:#f59e0b;stroke-width:3;vector-effect:non-scaling-stroke}
.pmask{position:absolute;left:0;right:0;top:27px;bottom:0;background:#111827;color:#e5e7eb;display:none;z-index:6;padding:10px 12px;font-size:12px;line-height:1.6;overflow:auto;text-align:left;pointer-events:none}
.pmask h5{margin:0 0 6px;font-size:12px;color:#fff;border-bottom:1px solid #4b5563;padding-bottom:4px}
.pmask table{border-collapse:collapse;width:100%;margin:4px 0}
.pmask th,.pmask td{border:1px solid #4b5563;padding:2px 6px;text-align:left;font-weight:normal;font-size:11px;white-space:nowrap}
.pmask th{background:rgba(255,255,255,.08);color:#9ca3af}
.pmask .pv{background:rgba(255,255,255,.06);border-radius:6px;padding:6px 8px;margin:4px 0;color:#d1d5db;word-break:break-word;font-size:12px}
.pmask .pv.pvt{padding:4px 2px}
.pmask .pv table{border-collapse:collapse;margin:2px 0;width:100%}
.pmask .pv td,.pmask .pv th{border:1px solid #6b7280;padding:2px 5px;font-size:11px;color:#e5e7eb;font-weight:normal;text-align:left;white-space:normal}
.pmask .pm{color:#fbbf24;margin:6px 0 2px}
.pmask table.umt{margin:2px 0 6px}
.pmask table.umt td{border:1px solid #374151;font-size:11px;white-space:normal;word-break:break-word}
.pmask table.umt td:first-child{white-space:nowrap;color:#9ca3af;width:1%}
.pv.dfx{font-family:Consolas,monospace;font-size:12px;line-height:1.7}
.df.del{color:#f87171;text-decoration:line-through}
.df.add{color:#34d399;font-weight:bold}
.pmask .fl{color:#fbbf24;font-weight:bold;margin:6px 0 0}
.pmask .miss{color:#f87171}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.dot.m{background:#60a5fa}.dot.d{background:#fbbf24}.dot.p{background:#34d399}
.lg{display:inline-block;margin-left:14px;font-size:12px}.lg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 3px;vertical-align:-1px}
</style></head><body>
<header><a class="back" href="index.html">‹ 三渠道总览</a><b>@@TITLE@@</b><nav>跳页：@@NAV@@</nav>@@LAYBTN@@
<span class="lg"><i style="background:#2563eb"></i>标题 <i style="background:#6b7280"></i>正文 <i style="background:#d97706"></i>脚注 <i style="background:#7c3aed"></i>图片 <i style="background:#0891b2"></i>图注 <i style="background:#059669"></i>表格 <i style="background:#fff;border:1px dashed #dc2626"></i>页眉页脚页码 <i style="background:#fee2e2;border:1px solid #dc2626"></i>空块</span>
</header>
@@BODY@@
<script>
@@CORE@@
var UBOX=@@UBOX@@, ghide=null;

function fillMasks(gid,sec){var u=UBOX[gid];if(!u||!u.panes)return;var panes=u.panes;
 var ms=sec.querySelectorAll('.pane:not(.meta) > .pmask');
 for(var k=0;k<ms.length&&k<panes.length;k++){ms[k].innerHTML=panes[k];ms[k].style.display='block';}
 var umb=sec.querySelector('rect.umb[data-u="'+gid+'"]');
 if(umb){umb.classList.add('on');var um=umb.nextElementSibling;
  if(um&&(um.getAttribute('class')||'').split(' ').indexOf('um')>=0)um.classList.add('on');}}
function hideAllMasks(){var ms=document.querySelectorAll('.pmask');for(var k=0;k<ms.length;k++)ms[k].style.display='none';
 document.querySelectorAll('.eye.pin').forEach(function(x){x.classList.remove('pin');});
 document.querySelectorAll('rect.um.on,rect.umb.on').forEach(function(r){r.classList.remove('on');});}
function showPinned(){if(!pinned)return;hideAllMasks();var eye=document.querySelector('.eye[data-g="'+pinned+'"]');
 if(eye){fillMasks(pinned,eye.closest('section'));eye.classList.add('pin');}}
function scheduleHide(){if(ghide)clearTimeout(ghide);
 /* 只由"真正离开眼睛"触发一次, 不再由每次 mouseover 重置; 80ms 仅用于允许跨到相邻眼睛,
    旧版 250ms + 每次移动重置 = 指针一动就永远不收, 表现为移开有明显延迟。*/
 ghide=setTimeout(function(){ghide=null;
  if(pinned&&document.querySelector('.eye[data-g="'+pinned+'"]'))showPinned();else hideAllMasks();},80);}
function citeHover(mf,on){
 var sec=mf.closest('section');
 var target=mf.dataset.e ? 'rect.cite-target[data-e="'+mf.dataset.e+'"]' : 'rect.cite-target[data-u="'+mf.dataset.u+'"]';
 sec.querySelectorAll(target).forEach(function(r){r.classList.toggle('cite-on',on);});
 var pop=document.getElementById('citepop');
 if(!on){if(pop)pop.style.display='none';return;}
 var r=sec.querySelector(target);
 if(!r||!pop)return;
 var svg=r.ownerSVGElement,vb=svg.viewBox.baseVal;
 var x=parseFloat(r.getAttribute('x')),y=parseFloat(r.getAttribute('y'));
 var w=parseFloat(r.getAttribute('width')),h=parseFloat(r.getAttribute('height')),pad=60;
 var x0=Math.max(0,x-pad),y0=Math.max(0,y-pad);
 var x1=Math.min(vb.width,x+w+pad),y1=Math.min(vb.height,y+h+pad);
 var img=sec.querySelector('.imgw img');
 if(!img)return;
 var cs=document.getElementById('citesvg');
 cs.setAttribute('viewBox',[x0,y0,x1-x0,y1-y0].join(' '));
 cs.innerHTML='<image href="'+img.getAttribute('src')+'" x="0" y="0" width="'+vb.width+'" height="'+vb.height+'"/>'
   +'<rect class="cpbox" x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'"/>';
 document.getElementById('citepoptitle').textContent='原图块定位 · '+mf.dataset.u;
 pop.style.display='block';
}
var citeKey=null;   /* 同一行内移动不重复重建原图弹层 */
document.addEventListener('mouseover',function(e){
 var mf=e.target.closest?e.target.closest('.refs .mf[data-u]'):null;
 if(!mf)return;
 var k=mf.dataset.e||mf.dataset.u;
 if(k!==citeKey){citeKey=k;citeHover(mf,true);}
});
document.addEventListener('mouseout',function(e){
 var mf=e.target.closest?e.target.closest('.refs .mf[data-u]'):null;
 if(mf&&(!e.relatedTarget||!mf.contains(e.relatedTarget))){citeKey=null;citeHover(mf,false);}
});
function centerBlock(sec,fracY){
 if(!sec)return;
 var imgw=sec.querySelector('.imgw');if(!imgw)return;
 var rc=imgw.getBoundingClientRect();
 if(rc.height<=0)return;
 var top=rc.top+window.pageYOffset+fracY*rc.height-window.innerHeight/2;
 window.scrollTo({top:Math.max(0,top),behavior:'smooth'});
}
function _fracY(svg,y,h){
 var H=(svg&&svg.viewBox&&svg.viewBox.baseVal&&svg.viewBox.baseVal.height)||0;
 return H?(y+h/2)/H:0.3;
}
function flashAt(r){
 var sec=r.closest('section');if(!sec)return;
 centerBlock(sec,_fracY(r.ownerSVGElement,parseFloat(r.getAttribute('y'))||0,
                         parseFloat(r.getAttribute('height'))||0));
 r.classList.add('flash');
 setTimeout(function(){r.classList.remove('flash');},1600);
}
function jumpToUid(uid){
 var u=UBOX[uid];if(!u||!u.box)return false;
 var bx=u.box;
 var sec=document.getElementById('p'+bx[0]);if(!sec)return false;
 var imgw=sec.querySelector('.imgw');
 var svg=imgw?imgw.querySelector('svg'):null;
 centerBlock(sec,_fracY(svg,bx[2],bx[4]-bx[2]));
 if(svg){
  var rc=document.createElementNS('http://www.w3.org/2000/svg','rect');
  rc.setAttribute('class','r umb flash');
  rc.setAttribute('x',bx[1]);rc.setAttribute('y',bx[2]);
  rc.setAttribute('width',Math.max(2,bx[3]-bx[1]));rc.setAttribute('height',Math.max(2,bx[4]-bx[2]));
  svg.appendChild(rc);
  setTimeout(function(){if(rc.parentNode)rc.parentNode.removeChild(rc);},1600);
 }
 return true;
}
document.addEventListener('click',function(e){
 var mf=e.target.closest?e.target.closest('.mf'):null;if(!mf)return;
 act(pinned,false);pinned=null;
 clearTimeout(ghide);
 hideAllMasks();
 var sec=mf.closest('section');
 if(mf.dataset.e){var r1=document.querySelector('section rect.cite-target[data-e="'+mf.dataset.e+'"]');if(r1){flashAt(r1);hintCentered(sec,mf.dataset.u);return;}}
 if(mf.dataset.u&&jumpToUid(mf.dataset.u))hintCentered(sec,mf.dataset.u);
});
function hintCentered(sec,uid){
 if(!sec||!uid)return;
 var st=document.getElementById('st_'+sec.id);
 if(st) st.textContent=' 源块位置已居中 · '+uid+'（橙框闪烁处）';
}
var lb=document.getElementById('laybtn');
if(lb){lb.onclick=function(){document.body.classList.toggle('boxov');lb.classList.toggle('on');};
 if(location.hash==='#meta'){document.body.classList.add('boxov');lb.classList.add('on');}}
</script>
<div id="citepop"><div id="citepoptitle">原图块定位</div><svg id="citesvg" preserveAspectRatio="xMidYMid meet"></svg></div>
</body></html>"""

def _dice_verdict(d):
    """dice 值配色结论(深色遮罩底色上的可读色)。"""
    if d >= 0.95:
        return "<span style='color:#34d399'>%.2f 一致</span>" % d
    if d >= 0.8:
        return "<span style='color:#fbbf24'>%.2f 小分歧</span>" % d
    return "<span style='color:#f87171'>%.2f 离群</span>" % d


def _token_diff(main, ch):
    """数字/西文词对比(语言无关裁决, 不懂韩文也能判读): 返回差异描述或 None。
    剥离 HTML 标签(sub/sup 等)后取数字/西文词, 集合差集去重(计数差一般不影裁决)。"""
    tok = lambda s: set(re.findall(r"[0-9]+(?:[.,][0-9]+)*|[A-Za-z]+",
                                   _fold_sym(re.sub(r"<[^>]+>", " ", s or ""))))
    a, b = tok(main), tok(ch)
    if a == b:
        return None
    parts = []
    if a - b:
        parts.append("仅主读法有: " + " ".join(sorted(a - b)[:12]))
    if b - a:
        parts.append("仅本渠道有: " + " ".join(sorted(b - a)[:12]))
    return " ｜ ".join(parts)


def _char_diff(main, ch):
    """主读法 vs 本渠道读法的逐字 diff(比较口径: 符号折叠+剥空白, 故剩余差异=真字符差异)。
    返回 HTML: 一致部分原样, 主有本无标红删除线(漏), 本渠道多出/不同标绿(多/异)。
    完全一致返回 None。"""
    import difflib
    a = re.sub(r"\s+", "", _fold_sym(main))
    b = re.sub(r"\s+", "", _fold_sym(ch))
    if not b or a == b:
        return None
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(a[i1:i2]))
        elif op == "replace":
            out.append('<span class="df del">%s</span><span class="df add">%s</span>'
                       % (html.escape(a[i1:i2]), html.escape(b[j1:j2])))
        elif op == "delete":
            out.append('<span class="df del">%s</span>' % html.escape(a[i1:i2]))
        else:
            out.append('<span class="df add">%s</span>' % html.escape(b[j1:j2]))
    s = "".join(out)
    return s if len(s) <= 6000 else None  # 过长(表格html等)不回溯, 退回预览


_UNI_CN = {"none": "一致", "read": "读法分歧", "split": "拆分分歧", "merge": "合并分歧",
           "coverage": "覆盖缺失", "type": "类型分歧", "xpage": "跨页涉入"}

def _unified_group_panes(u, chs):
    """unified 对象的差异遮罩: ([原图栏总览, 各渠道栏...], 眼色级别)。
    与 _cmp_group_masks 同构(4 pane 对齐 .pmask), 前端显隐/钉住逻辑零改动。"""
    conf, div = u["confidence"], u["divergence"]
    lvl = "lc" if div["kind"] == "coverage" else {"mid": "lm", "low": "ll"}.get(conf["level"], "lm")
    rd = {r["ch"]: r for r in u["readings"]}
    rows = []
    for c in chs:
        if c in u["members"]:
            us = u["members"][c]
            nch = sum(len(re.sub(r"\s+", "", m.get("text") or "")) for m in us)  # 非空白字符数(空格风格不干扰漏字判断)
            mark = " ".join(rd.get(c, {}).get("flags", [])) or "-"   # .get 兜底: unified 被并发写撕裂时(13563526 实证)不许崩整页
            rows.append("<tr><td><i class='dot %s'></i>%s</td><td>✓ %s×%d</td><td>%d</td><td>%s</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c], us[0]["cat"], len(us), nch, mark))
        else:
            rows.append("<tr class='miss'><td><i class='dot %s'></i>%s</td><td colspan=3>✗ 未识别此区域</td></tr>"
                        % (_CMP_DOT[c], _CMP_CN[c]))
    pane0 = ("<h5>统一对象 %s ｜ %s · %s · %s</h5><table><tr><th>渠道</th><th>在场</th><th>字符</th><th>标记</th></tr>%s</table>"
             "<p class='fl'>分歧 <b>%s</b>: %s</p><p class='fl'>置信依据: %s</p>%s"
             % (u["uid"], u["type"], conf["level"], _UNI_CN.get(div["kind"], div["kind"]), "".join(rows),
                div["kind"], html.escape(div.get("detail", "")), html.escape(conf.get("basis", "")),
                ('<p class="pm">主读法:</p>%s' % _cmp_preview(u.get("main_text") or "")) if u.get("main_text") else ""))
    panes = [pane0]
    for c in chs:
        head = "<h5>%s 视角</h5>" % _CMP_CN[c]
        if c not in u["members"]:
            panes.append(head + "<p class='miss'>✗ 本渠道未识别到此区域（另两家有）→ coverage 分歧，"
                                "待人工确认是漏检还是别家误检</p>")
            continue
        r = rd.get(c) or {}
        filled = (r.get("text") or "").strip()
        show_pv = (not filled) or (r.get("dice", 1.0) < 0.95) or bool(r.get("flags"))
        if not filled:
            verdict = "空文本块"
        else:
            verdict = "dice " + _dice_verdict(r.get("dice", 0.0))
        # 成员块列表(split/merge 时尤其关键: 各家切成几块、每块什么是人工要看的证据)
        mrows = []
        for m in u["members"][c]:
            mt = (m.get("text") or "").strip()
            mrows.append("<tr><td>%s</td><td class=t>%s</td></tr>" % (
                m.get("cat", "?"),
                html.escape((re.sub(r"\s+", " ", mt)[:60]) if mt else ("（空）" if m.get("cat") != "Picture" else "（图区）"))))
        body = "<p>%s ｜ %s</p><table class=umt>%s</table>" % (
            verdict,
            ("flags: " + " ".join(r.get("flags") or [])) if r.get("flags") else "无标记",
            "".join(mrows))
        # 自动修复读法(渠道内缺陷已机械修复: 溢出裁剪/组内去重; 原文留档成员表)
        if r.get("text_fixed"):
            body += ("<p class='pm'>✅ 自动修复读法（溢出裁剪/组内去重，确认后可代替原文使用；原样见成员表）:</p>%s"
                     % _cmp_preview(r["text_fixed"]))
        # 数字/西文词对比(语言无关): 不懂韩文也能裁决的差异
        td = _token_diff(u.get("main_text") or "", r.get("text") or "") if filled else None
        if td:
            body += "<p class='pm'>数字/西文词对比（不懂韩文也能裁决）: %s</p>" % html.escape(td)
        # 逐字 diff(用户核心诉求: 渠道间字符差异/错漏的直接视图)优先于文本预览
        dfx = _char_diff(u.get("main_text") or "", r.get("text") or "") if filled else None
        if dfx:
            body += ("<p class='pm'>与主读法逐字 diff（口径: 符号折叠+去空白；"
                     "<span class='df del'>红删=主有本无(漏)</span>／<span class='df add'>绿增=本渠道多/异</span>）:</p>"
                     "<div class='pv dfx'>%s</div>") % dfx
        elif show_pv:
            body += _cmp_preview(r.get("text") or "")
        caps = [m.get("cap") for m in u["members"][c] if m.get("cap")]
        if caps:
            body += "<p class='pm'>题注: %s</p>" % html.escape("\n".join(caps)[:120])
        panes.append(head + body)
    return panes, lvl



def _esc(s):
    """metadata 面板展示转义(总览/横带共用)。"""
    return html.escape(str(s if s is not None else ""), quote=False)


def _mf_row(lv, uid, inner):
    """metadata 行(总览/本页横带共用, 2026-11-10 收敛双份闭包): 置信色条 + 可选证据 uid 锚。"""
    u = ' data-u="%s"' % html.escape(str(uid), quote=True) if uid else ""
    return '<div class="mf %s"%s>%s</div>' % (lv, u, inner)


def _mf_dot():
    return '<i class="md"></i>'


def _evidence_key(kind, item):
    text = item.get("raw") or item.get("sentence") or item.get("title") or ""
    return hashlib.sha256((kind + "\0" + text).encode("utf-8")).hexdigest()[:16]


def _evidence_member_box(u, item):
    target = item.get("raw") or item.get("title") or ""
    target_fold = re.sub(r"\s+", "", _fold_sym(target)).casefold()
    exact = []
    for members in (u.get("members") or {}).values():
        for member in members:
            text_fold = re.sub(r"\s+", "", _fold_sym(member.get("text") or "")).casefold()
            if target_fold and target_fold in text_fold and member.get("box"):
                exact.append(member["box"])
    if exact:
        return min(exact, key=lambda b: max(0, b[2] - b[0]) * max(0, b[3] - b[1]))
    best, score = None, 0.0
    for members in (u.get("members") or {}).values():
        for member in members:
            if not member.get("box"):
                continue
            value = _gdice(target, member.get("text") or "")
            if value > score:
                best, score = member["box"], value
    return best if best is not None and score >= 0.5 else u["box"]


def _cspan_sentence_groups(cspans):
    groups = {}
    for span in cspans:
        key = (span.get("page"), span.get("uid"), span.get("sentence") or "")
        groups.setdefault(key, []).append(span)
    return list(groups.values())


def _meta_cell(meta, pno, audit=None, choices=None):
    """metadata 面板(方案A): 只列落在本页(pno, 1 基)的字段——原图×本页字段对照。
    每行 lv 色条=置信, data-u=unified uid(点击钉住该块的证据遮罩/闪共识框)。
    组: 题录(标题/作者/机构) → 摘要(折叠全文) → 文献(表格行 + 本页编号连续徽标)。
    §38 双读: review_comment 与编号连续徽标(L1)取自 audit sidecar(按 uid join, 产品 meta 只有 level/source)。
    批次4d: choices(out_llm_trial/_choice.json)=双渠道人工选择, 文献行尾回显徽标; 匹配优先级
    (kind,raw_head) 精确 → uid → index 兜底(兜底加 ? 提示漂移)——index 重跑即漂移, uid 有 §45 漂移前科。"""
    if not meta:
        return ""
    grp = []

    esc = _esc
    # 批次4d 双渠道选择映射(raw_head 优先, uid/index 兜底)
    ch_by_rh, ch_by_uid, ch_by_idx = {}, {}, {}
    for c in (choices or []):
        ch_by_rh[(c.get("kind"), c.get("raw_head"))] = c
        ch_by_uid.setdefault((c.get("kind"), c.get("uid")), c)
        ch_by_idx.setdefault((c.get("kind"), c.get("index")), c)
    # —— 题录 ——
    t = meta.get("title") or {}
    if t.get("value") and (t.get("page") or 1) == pno:
        grp.append(("题录", _mf_row((t.get("confidence") or {}).get("level", "mid"), t.get("uid"),
                                 _mf_dot() + "<b>标题</b> " + esc(t["value"][:90]))))
    te = meta.get("title_en") or {}
    if te.get("value") and (te.get("page") or 1) == pno:
        grp.append(("题录", _mf_row((te.get("confidence") or {}).get("level", "mid"), te.get("uid"),
                                 _mf_dot() + "<b>标题(英)</b> " + esc(te["value"][:90]))))
    aus = [a for a in (meta.get("authors") or []) if (a.get("page") or 1) == pno]
    if aus:
        rows = []
        for a in aus:
            nm = a.get("name") or ""
            if a.get("roman"):
                nm += " (%s)" % a["roman"]
            if a.get("role"):
                nm += " [%s]" % a["role"]
            af = a.get("affiliation") or ""
            rows.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(nm), esc(af[:40])))
        grp.append(("题录", _mf_row("mid", aus[0].get("uid"),
                                 _mf_dot() + "<b>作者</b><table>%s</table>" % "".join(rows))))
    affs = meta.get("affiliations") or []
    if pno == 1 and affs:
        grp.append(("题录", _mf_row("mid", None, _mf_dot() + "<b>机构</b> " + esc(" / ".join(affs)[:110]))))
    # —— 摘要 ——
    for k, lab in (("abstract", "摘要"), ("abstract_en", "摘要(英)")):
        f = meta.get(k) or {}
        if f.get("value") and (f.get("page") or 1) == pno:
            v = f["value"]
            inner = _mf_dot() + "<b>%s</b> " % lab
            if len(v) > 140:
                inner += ('<details class="mfd"><summary>%s…(全文 %d 字)</summary>'
                          '<div class="mft">%s</div></details>' % (_texesc(v[:140]), len(v), _texesc(v)))
            else:
                inner += _texesc(v)
            grp.append(("摘要", _mf_row((f.get("confidence") or {}).get("level", "mid"),
                                    (f.get("confidence") or {}).get("uid_head"), inner)))
    # —— 文献(本页条目) + 脚注引文(本页, --stage frefs 独立键 footnote_refs) ——
    def _raw_head(s):
        from metapy.common import RAW_HEAD_LEN, _foldv
        return _foldv(s or "")[:RAW_HEAD_LEN]

    def _ref_rows(refs_list, au_map, kind):
        trs = []
        for r in refs_list:
            lv = (r.get("confidence") or {}).get("level", "low")
            au = "; ".join(r.get("authors") or [])
            au_d = esc(au)   # 横带宽列: 不再硬截, CSS 单行省略 + 行 tooltip
            if r.get("title"):
                ti = r["title"]
            elif kind == "fref":
                ti = r.get("raw") or "(无原文)"
            else:
                ti = "(未抽出)"
            tags = ""
            if r.get("grammar"):
                tags += '<span class="mftag">%s</span>' % esc(r["grammar"])
            if r.get("et_al"):
                tags += '<span class="mftag">et_al</span>'
            if r.get("notice_no"):
                tags += '<span class="mftag">Notice No.%s</span>' % esc(r["notice_no"])
            c = ch_by_rh.get((kind, _raw_head(r.get("raw")))) \
                or ch_by_uid.get((kind, r.get("uid"))) \
                or ch_by_idx.get((kind, r.get("index")))
            if c:
                pick = c.get("pick")
                other = c.get("llm_value") if pick == "regex" else c.get("regex_value")
                disp = u"已选LLM" if pick == "llm" else u"已选正则"
                if not ch_by_rh.get((kind, _raw_head(r.get("raw")))):
                    disp += "?"   # 兜底匹配(可能漂移)
                tip2 = html.escape(json.dumps(other, ensure_ascii=False)[:300], quote=True)
                tags += '<span class="mftag" title="舍弃值: %s">%s</span>' % (tip2, disp)
            if r.get("title"):
                ti_d = tags + _texesc(ti)
            elif kind == "fref":
                ti_d = tags + '<span class="mftag">脚注原文</span>' + _texesc(ti)
            else:
                ti_d = tags + "<span style='color:#dc2626'>%s</span>" % _texesc(ti)
            src_d = _texesc(r.get("source") or "")
            rc = (au_map.get(r.get("uid")) or {}).get("review_comment")
            tip = html.escape(("%s | %s | %s | %s%s%s" % (au, r.get("year") or "?",
                                                      r.get("title") or "", r.get("source") or "",
                                                      " | Notice No." + r["notice_no"] if r.get("notice_no") else "",
                                                      " | 复核: " + rc if rc else ""))[:400], quote=True)
            trs.append('<tr class="mf %s" data-u="%s" data-e="%s" title="%s"><td class="cnum"><i class="md"></i>#%s</td>'
                       "<td>%s</td><td class=\"cnum\">%s</td><td>%s</td><td>%s</td></tr>"
                       % (lv, html.escape(str(r.get("uid") or ""), quote=True), _evidence_key(kind, r), tip,
                          r.get("num") if r.get("num") is not None else "-",
                          au_d, esc(r.get("year") or "?"), ti_d, src_d))
        return trs

    au_by_uid = {x.get("uid"): x for x in (((audit or {}).get("references") or [])
                                            + ((audit or {}).get("footnote_refs") or []))}
    tbl_head = ('<table class="refs"><colgroup><col style="width:52px">'
                '<col style="width:20%"><col style="width:46px"><col>'
                '<col style="width:26%"></colgroup>')
    cspt_head = ('<table class="refs cspt"><colgroup><col style="width:52px">'
                 '<col style="width:20%"><col style="width:46px"><col>'
                 '<col style="width:26%"></colgroup>')
    def _raw_rows(items, kind):
        rows = []
        for item in items:
            text = item.get("raw") or (item.get("title") if kind == "ref" else "") or "(无原文)"
            tip = re.sub(r"</?(?:sub|sup|b|i|dive)\b[^>]*>", "", text, flags=re.I)
            lv = (item.get("confidence") or {}).get("level", "low")
            rows.append('<tr class="mf %s" data-u="%s" data-e="%s" title="%s"><td class="cnum">'
                        '<i class="md"></i>#%s</td><td colspan="4" class="rawtext">'
                        '<span class="mftag">原文</span>%s</td></tr>'
                        % (lv, html.escape(str(item.get("uid") or ""), quote=True),
                           _evidence_key(kind, item), html.escape(tip[:400], quote=True),
                           item.get("num") if item.get("num") is not None else "-", _blk_html(text)))
        return rows

    refs = [r for r in (meta.get("references") or []) if r.get("page") == pno]
    if refs:
        val = (audit or {}).get("ref_validation") or {}
        seq_ok = (val.get("num_sequence") or {}).get("ok")
        badge = ""
        if seq_ok is not None:
            badge = '<span class="mbadge %s">编号连续%s</span>' % ("ok" if seq_ok else "bad",
                                                                    "✓" if seq_ok else "✗")
        grp.append(("文献%s" % badge,
                    _mf_dot() + '<b>本页文献 %d 条(原文)</b>' % len(refs)
                    + tbl_head + "".join(_raw_rows(refs, "ref")) + "</table>"))

    fnrefs = [r for r in (meta.get("footnote_refs") or []) if r.get("page") == pno]
    if fnrefs:
        grp.append(("脚注",
                    _mf_dot() + '<b>本页脚注 %d 条(原文)</b>' % len(fnrefs)
                    + tbl_head + "".join(_raw_rows(fnrefs, "fref")) + "</table>"))
    # —— 行内引文句(--stage cspan): 同段同句合并展示; 引文高亮+对号徽标; hover=原图块框 ——
    cspans = [s for s in (meta.get("citation_spans") or []) if s.get("page") == pno]
    if cspans:
        def _hl_group(sent, spans):
            offsets = []
            for s in spans:
                off = s.get("offset") or []
                if len(off) == 2 and 0 <= off[0] < off[1] <= len(sent):
                    offsets.append((off[0], off[1]))
            merged = []
            for a, b in sorted(offsets):
                if merged and a < merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                else:
                    merged.append((a, b))
            out, cursor = [], 0
            for a, b in merged:
                out.extend((_texesc(sent[cursor:a]), "<mark>", _texesc(sent[a:b]), "</mark>"))
                cursor = b
            out.append(_texesc(sent[cursor:]))
            return "".join(out)
        ref_by_idx = {i: r for i, r in enumerate(meta.get("references") or [])}
        kind_tag = {"narrative": u"名字+年", "parenthetical": u"括号", "numeric": u"数字"}
        lv_map = {"exact": "high", "partial": "mid", "unlinked": "low"}
        lv_rank = {"high": 0, "mid": 1, "low": 2}
        crows = []
        groups = _cspan_sentence_groups(cspans)
        for group in groups:
            first = group[0]
            sent = first.get("sentence") or ""
            kinds = list(dict.fromkeys(s.get("kind") or "?" for s in group))
            confs = list(dict.fromkeys(s.get("confidence") or "" for s in group))
            levels = [lv_map.get(s.get("confidence"), "mid") for s in group]
            level = max(levels, key=lambda lv: lv_rank[lv])
            links, seen = [], set()
            for s in group:
                for lr in s.get("linked_refs") or []:
                    idx = lr.get("index")
                    if idx in seen:
                        continue
                    seen.add(idx)
                    tgt = ref_by_idx.get(idx)
                    if tgt is not None:
                        links.append("<span class=mftag>→ #%s %s</span>" % (
                            tgt.get("num") if tgt.get("num") is not None else "-",
                            esc((tgt.get("title") or "(未抽出)")[:38])))
            tip = html.escape(("%s | %s" % (sent, "/".join(confs)))[:400], quote=True)
            kind_s = "/".join(kind_tag.get(k, k) for k in kinds)
            conf_s = "".join('<span class="mftag">%s</span>' % esc(c) for c in confs)
            crows.append('<tr class="mf %s" data-u="%s" title="%s"><td class="cnum">'
                         '<i class="md"></i>%s</td><td colspan="3">%s%s</td><td>%s</td></tr>'
                         % (level, html.escape(str(first.get("uid") or ""), quote=True), tip,
                            esc(kind_s), conf_s, _hl_group(sent, group), "".join(links)))
        units = ('<span class="mftag">%d 处引文</span>' % len(cspans)
                 if len(groups) != len(cspans) else "")
        grp.append(("引文句",
                    _mf_dot() + '<b>本页引文句 %d 条</b>' % len(groups) + units
                    + cspt_head + "".join(crows) + "</table>"))
    if not grp:
        return ""
    out, last = [], None
    for gname, html_s in grp:
        if gname != last:
            out.append('<div class="mfg"><div class="mfh">%s</div>' % gname)
            last = gname
        out.append(html_s)
    out.append("</div>" * len({g[0] for g in grp}))
    return '<div class="pane meta"><div class="ptitle src">metadata（本页字段 · 点击行=看证据）</div>' \
           '<div class="mfw">%s</div><div class="pmask"></div></div>' % "".join(out)


def _meta_overview(meta, audit=None, ents=None, body_chars=0):
    """metadata 总览横带(只挂第 1 页 section): 实体清单(16 项✓/✗) + 全部抽取字段一览——题录/
    来源(期刊年卷期页)/关键词/摘要(折叠全文)/三类大列表计数(refs/frefs/cspan 全库分布+徽标,
    不展开, 明细在每页横带)。body_chars=unified 正文总字符(实体清单"正文"项判据)。
    有 uid 的字段行保留 data-u, 点击=钉第 1 页该块证据(与每页横带同一机制)。"""
    import collections
    if not meta:
        return ""
    esc = _esc

    def dist_text(cnt):
        return " / ".join("%s %d" % (k, cnt[k]) for k in ("high", "mid", "low") if cnt.get(k))

    def page_span(items):
        ps = sorted({(x.get("page") or 1) for x in items})
        return "第 %d-%d 页" % (ps[0], ps[-1]) if ps else ""

    grp = []
    # —— 实体清单(2026-11-12): 分组一眼看全抽出/未抽——题录/来源/内容/引证/文献/实体;
    #    脚注与引文句是正文页内引证(期刊中多互斥, 同篇只用一种), 同归"引证"组;
    #    参考文献=文末文献列表, 独立"文献"组。字段类取 metadata, 实体类取 ner 产物 ——
    def _has(v):
        return bool(v and str(v).strip())
    _kinds = {}
    for _pg in (ents or {}).values():
        for _items in _pg.values():
            for _it in _items:
                _kinds[_it["kind"]] = _kinds.get(_it["kind"], 0) + 1
    _grp_checks = [
        ("题录", [("标题", _has((meta.get("title") or {}).get("value"))),
                  ("作者", bool(meta.get("authors"))),
                  ("机构", bool(meta.get("affiliations")) or _kinds.get("org", 0) > 0
                   or any(str((a.get("affiliation") or "")).strip()
                          for a in (meta.get("authors") or []) if isinstance(a, dict)))]),
        ("来源", [("期刊", _has((meta.get("journal") or {}).get("value"))),
                  ("出版年", _has((meta.get("year") or {}).get("value"))),
                  ("卷", _has((meta.get("volume") or {}).get("value"))),
                  ("期", _has((meta.get("issue") or {}).get("value"))),
                  ("页码", _has((meta.get("pages") or {}).get("value")))]),
        ("内容", [("关键词", bool(meta.get("keywords") or meta.get("keywords_lines"))),
                  ("摘要", _has((meta.get("abstract") or {}).get("value"))),
                  ("正文", body_chars >= 500)]),
        ("引证", [("脚注", bool(meta.get("footnote_refs"))),
                  ("引文句", bool(meta.get("citation_spans")))]),
        ("文献", [("参考文献", bool(meta.get("references")))]),
        ("实体", [("基金", _kinds.get("funding", 0) > 0),
                  ("研究软件", _kinds.get("software", 0) > 0),
                  ("科学数据集", _kinds.get("dataset", 0) > 0)]),
    ]
    _cells = ""
    for _gname, _items in _grp_checks:
        _cells += '<span class="ckg">%s</span>' % _gname
        _cells += "".join('<span class="ck %s">%s %s</span>'
                          % ("ok" if ok else "no", "✓" if ok else "✗", name)
                          for name, ok in _items)
    grp.append(("实体清单", _mf_row("mid", None, _mf_dot() + "<b>实体清单</b> " + _cells)))
    # —— 题录 ——
    def _pg_hint(f):
        return "" if (f.get("page") or 1) == 1 else '<span class="mftag">证据在第 %d 页</span>' % f["page"]

    t = meta.get("title") or {}
    if t.get("value"):
        grp.append(("题录", _mf_row((t.get("confidence") or {}).get("level", "mid"), t.get("uid"),
                                 _mf_dot() + "<b>标题</b> " + _pg_hint(t) + esc(t["value"][:120]))))
    te = meta.get("title_en") or {}
    if te.get("value"):
        grp.append(("题录", _mf_row((te.get("confidence") or {}).get("level", "mid"), te.get("uid"),
                                 _mf_dot() + "<b>标题(英)</b> " + _pg_hint(te) + esc(te["value"][:120]))))
    aus = meta.get("authors") or []
    if aus:
        trs = []
        for a in aus:
            nm = a.get("name") or ""
            if a.get("roman"):
                nm += " (%s)" % a["roman"]
            if a.get("role"):
                nm += " [%s]" % a["role"]
            trs.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(nm), esc((a.get("affiliation") or "")[:48])))
        grp.append(("题录", _mf_row("mid", aus[0].get("uid"),
                                 _mf_dot() + "<b>作者 %d 人</b><table>%s</table>" % (len(aus), "".join(trs)))))
    affs = meta.get("affiliations") or []
    if affs:
        grp.append(("题录", _mf_row("mid", None, _mf_dot() + "<b>机构 %d 个</b> " % len(affs)
                                 + esc(" / ".join(affs)[:160]))))
    # —— 来源(--stage issue: 本篇期刊/年/卷/期/页) ——
    for k, lab in (("journal", u"期刊"), ("journal_en", u"期刊(英)"), ("year", u"年"),
                   ("volume", u"卷"), ("issue", u"期"), ("pages", u"页")):
        f = meta.get(k) or {}
        if f.get("value") is not None:
            # 行带来源行 uid(issue 阶段 _uid0 回填; 页码推断兜底无锚则 None 不可跳)
            grp.append(("来源", _mf_row((f.get("confidence") or {}).get("level", "mid"),
                                    f.get("uid"),
                                    _mf_dot() + "<b>%s</b> %s" % (lab, esc(f["value"])))))
    # —— 关键词(韩/英双字段, 2026-11-09 用户拍板: 有双板的都提取) ——
    # 行带 evidence uid(audit.issue.keywords.source*): 点击=跳到原文关键词行所在块并橙框闪烁。
    kw_src = (((audit or {}).get("issue") or {}).get("keywords") or {})
    kws = meta.get("keywords") or []
    if kws:
        grp.append(("关键词", _mf_row("mid", (kw_src.get("source") or {}).get("uid"),
                                  _mf_dot() + "<b>关键词 %d 个</b> " % len(kws) + esc(", ".join(kws)[:200]))))
    kws_en = meta.get("keywords_en") or []
    if kws_en:
        grp.append(("关键词", _mf_row("mid", (kw_src.get("source_en") or {}).get("uid"),
                                  _mf_dot() + "<b>关键词(英) %d 个</b> " % len(kws_en)
                                  + esc(", ".join(kws_en)[:200]))))
    # —— 摘要(折叠全文) ——
    for k, lab in (("abstract", u"摘要"), ("abstract_en", u"摘要(英)")):
        f = meta.get(k) or {}
        if f.get("value"):
            v = f["value"]
            inner = _mf_dot() + "<b>%s</b> " % lab
            if (f.get("page") or 1) != 1:
                inner += '<span class="mftag">证据在第 %d 页</span>' % f["page"]
            if len(v) > 140:
                inner += ('<details class="mfd"><summary>%s…(全文 %d 字)</summary>'
                          '<div class="mft">%s</div></details>' % (_texesc(v[:140]), len(v), _texesc(v)))
            else:
                inner += _texesc(v)
            grp.append(("摘要", _mf_row((f.get("confidence") or {}).get("level", "mid"),
                                    (f.get("confidence") or {}).get("uid_head"), inner)))
    # —— 三类大列表: 全库计数 + 分布(明细在每页横带) ——
    refs = meta.get("references") or []
    if refs:
        val = (audit or {}).get("ref_validation") or {}
        seq_ok = (val.get("num_sequence") or {}).get("ok")
        badge = ('<span class="mbadge %s">编号连续%s</span>' % ("ok" if seq_ok else "bad",
                                                                 u"✓" if seq_ok else u"✗")) if seq_ok is not None else ""
        cnt = collections.Counter((r.get("confidence") or {}).get("level") for r in refs)
        grp.append(("文献", _mf_row("mid", None,
                                 _mf_dot() + "<b>参考文献 %d 条</b>%s %s · %s" % (
                                     len(refs), badge, dist_text(cnt), page_span(refs)))))
    fnrefs = meta.get("footnote_refs") or []
    if fnrefs:
        cnt = collections.Counter((r.get("confidence") or {}).get("level") for r in fnrefs)
        grp.append(("文献", _mf_row("mid", None,
                                 _mf_dot() + "<b>脚注 %d 条</b> %s · %s" % (
                                     len(fnrefs), dist_text(cnt), page_span(fnrefs)))))
    cspans = meta.get("citation_spans") or []
    if cspans:
        cc = collections.Counter({"exact": "high", "partial": "mid", "unlinked": "low"}.get(
            s.get("confidence"), "mid") for s in cspans)
        ck = collections.Counter(s.get("kind") for s in cspans)
        kinds = " / ".join("%s %d" % ({"narrative": u"名字+年", "parenthetical": u"括号",
                                       "numeric": u"数字"}.get(k, k), n)
                           for k, n in ck.most_common())
        sentence_count = len(_cspan_sentence_groups(cspans))
        unit_count = " · %d 处引文" % len(cspans) if sentence_count != len(cspans) else ""
        grp.append(("引文句", _mf_row("mid", None,
                                   _mf_dot() + "<b>引文句 %d 条</b>%s 对号 %s · %s · %s" % (
                                       sentence_count, unit_count, dist_text(cc), kinds, page_span(cspans)))))
    if ents:
        seen = {}
        for pg in sorted(ents):
            for uid in sorted(ents[pg], key=str):
                for it in ents[pg][uid]:
                    k = (it["kind"], it["name"])
                    if k in seen:
                        seen[k]["n"] += 1
                    else:
                        seen[k] = {"it": it, "uid": uid, "n": 1}
        for (kind, name), v in sorted(seen.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            it = v["it"]
            ann = u"".join('<span class="mftag">%s</span>' % _ANN_ZH.get(a, _esc(a))
                           for a in it["ann"])
            inner = (_mf_dot() + "<b>%s</b> %s <span class=\"esl\">×%d</span>%s"
                     % (_NER_KIND_ZH.get(kind, _esc(kind)), _esc(name), v["n"], ann))
            grp.append((u"实体", _mf_row("mid", v["uid"], inner)))
    if not grp:
        return ""
    out, last = [], None
    for gname, html_s in grp:
        if gname != last:
            out.append('<div class="mfg"><div class="mfh">%s</div>' % gname)
            last = gname
        out.append(html_s)
    out.append("</div>" * len({g[0] for g in grp}))
    return ('<div class="pane meta ov"><div class="ptitle ov">metadata 总览（全部抽取字段 · 明细按页见下方横带）</div>'
            '<div class="mfw">%s</div><div class="pmask"></div></div>' % "".join(out))



_NER_KIND_ZH = {"funding": u"基金", "software": u"软件", "dataset": u"数据集", "org": u"机构"}
_ANN_ZH = {"multi_block": u"多块", "multi_occurrence": u"块内多次", "extended": u"已外延",
           "cross_block": u"跨块", "mention_in_biblio_footnote": u"著录脚注提及"}


def _load_ner_lite(out_fp):
    """ner_lite 实体(2026-11-10 换代, RULES §6): 读 out_llm_trial 产物, 索引 {page1基: {uid: [条目]}}。
    文件缺失/损坏 = 无实体(None), viewer 行为不变。"""
    doc_dir = os.path.dirname(os.path.abspath(out_fp))
    fp = os.path.join(os.path.dirname(os.path.dirname(doc_dir)), "out_llm_trial",
                      "fds_extract", "ner_lite", os.path.basename(doc_dir), "ner_lite.json")
    if not os.path.isfile(fp):
        return None
    try:
        recs = json.load(io.open(fp, encoding="utf-8")).get("records") or []
    except Exception:
        return None
    idx = {}
    for r in recs:
        for loc in r.get("locations") or []:
            for s in loc.get("segments") or []:
                if not s.get("uid"):
                    continue
                idx.setdefault(s.get("page"), {}).setdefault(s["uid"], []).append({
                    "kind": r.get("kind"), "name": r.get("name_model"),
                    "slice": s.get("slice"), "ann": r.get("annotations") or []})
    return idx or None


def _entity_cell(by_uid):
    """ner_lite 实体面板: kind 徽标 + 名称 + 原文切片 + 批注; 行点击沿用 .mf[data-u] 定位闪块。"""
    if not by_uid:
        return ""
    rows = []
    for uid, items in sorted(by_uid.items(), key=lambda kv: str(kv[0])):
        for it in items:
            ann = u"".join('<span class="mftag">%s</span>' % _ANN_ZH.get(a, _esc(a))
                           for a in it["ann"])
            inner = ("<b>%s</b> %s <span class=\"esl\">%s</span>%s"
                     % (_NER_KIND_ZH.get(it["kind"], _esc(it["kind"])),
                        _esc(it["name"]), _esc((it["slice"] or u"")[:80]), ann))
            rows.append(_mf_row("mid", uid, _mf_dot() + inner))
    if not rows:
        return ""
    return (u'<div class="pane meta ent"><div class="ptitle src">ner_lite 实体'
            u'（本页 · 点击行=定位源块）</div>%s</div>' % u"".join(rows))


def build_compare_viewer(out_fp, title, sizes, per_ch, src_dirs=None, unified_pages=None, unified_driven=False, meta=None, audit=None, choices=None):
    """1:3 对照查看器: 每页 左原图(叠全部块框) + 右三栏各渠道块列表。
    per_ch = {"mineru": per_page|md字符串|None, ...}; 块 id 带渠道前缀, 同渠道内 rect<->文本联动, 无跨渠道联动。
    src_dirs = {ch: raw 目录 basename} 供引擎抽取图按实际来源目录引用(不再硬编码 raw/<ch>/, API 文档源是 <ch>_api)。
    unified_pages = {页号0起: [unified对象...]} 存在时, 👁差异标注与遮罩改由 unify 归一化层驱动:
    review=true 的对象出眼(眼色按置信分级: mid=琥珀 low=红 coverage=紫), 遮罩=统一对象视角(分歧类型/置信依据/各家读法dice),
    原图栏叠加共识几何虚线框。
    unified_driven=False(老产物/--no-unify)时走旧的临时聚组逻辑; 为 True 时旧的差异眼一律不出——
    两套判据叠加会把归一化层判定无分歧的块又点亮一批, 复检队列被人为放大。"""
    n = len(sizes)
    nav = "".join('<a href="#p%d">%d</a>' % (i, i + 1) for i in range(n))
    secs, data, groups_json = [], {}, {}
    ents = _load_ner_lite(out_fp)   # ner_lite 实体(可缺省): per-page 面板, 点击行=定位闪块
    chs = ("mineru", "dots", "paddle")
    for p in range(n):
        W, H = sizes[p]
        rects, col_html = [], {}
        nemp, first_emp, first_ffd, nffd = 0, None, None, 0
        by_ch = {}
        for ch in chs:
            src = per_ch.get(ch)
            blocks = (src or {}).get(p) if isinstance(src, dict) else None
            by_ch[ch] = list(enumerate(blocks or []))
            if blocks:
                rb = []
                for i, b in enumerate(blocks):
                    bid = "%s_%d_%d" % (ch, p, i)
                    raw = b.get("text") or ""
                    data[bid] = {"cat": b["cat"], "txt": raw[:60]}
                    is_emp = not raw.strip() and b.get("cat") != "Picture"
                    if is_emp:
                        nemp += 1; first_emp = first_emp or bid
                    if "\ufffd" in raw:
                        nffd += 1; first_ffd = first_ffd or bid
                    anchor = ""
                    if bid == first_emp: anchor = ' id="emp_%s"' % bid
                    elif bid == first_ffd: anchor = ' id="ffd_%s"' % bid
                    cls = "blk %s%s" % (ch, " emp" if is_emp else "")
                    rb.append('<div class="%s" data-b="%s"%s><span class="tag">%s</span>%s</div>'
                              % (cls, bid, anchor, _tag(b), _blk_text(b, "raw/%s/" % (src_dirs or {}).get(ch, ch),
                                                                 ("pages/page_%d.jpg" % p, (W, H)))))
                    box = b["box"]
                    rcls = NS.get(b["cat"], "txt") + (" emp" if is_emp else "")
                    rects.append('<rect data-b="%s" class="r %s" x="%s" y="%s" width="%s" height="%s"/>' % (
                        bid, rcls, box[0], box[1], max(2, box[2] - box[0]), max(2, box[3] - box[1])))
                col_html[ch] = '<div class="txt">%s</div>' % "\n".join(rb)
            elif isinstance(src, str):
                col_html[ch] = '<div class="none">md 模式无块级</div>'
            elif src is None:
                col_html[ch] = '<div class="none">无产物</div>'
            else:
                col_html[ch] = '<div class="none">该渠道此页无结构化输出</div>'
        eyes = []
        if unified_driven:
            # —— unified 模式: 眼/遮罩由归一化层驱动(review=true 才出眼), 原图叠加共识几何框 ——
            for ui, u in enumerate(unified_pages.get(p) or []):
                sev = min((r["dice"] for r in u["readings"] if (r["text"] or "").strip()), default=1.0)
                hint = ""
                if not u.get("review"):
                    if sev >= 0.5:
                        continue
                    hint = " · 某家读法严重异常(dice=%.2f)" % sev  # 信息提示眼: high 语义不变, 但异常读法要让人能看见
                panes, lvl = _unified_group_panes(u, chs)
                if hint:
                    lvl = "lo"
                gid = u["uid"]  # 与 unified\page_N.json 的 uid 一致(列表已按阅读序重排, 勿用 enumerate 序号)
                groups_json[gid] = {"panes": panes}
                bx = u["box"]
                rw, rh = max(2, bx[2] - bx[0]), max(2, bx[3] - bx[1])
                rects.append('<rect class="r umb" data-u="%s" x="%s" y="%s" width="%s" height="%s"/>'
                             % (gid, bx[0], bx[1], rw, rh))  # 白色衬底让共识框在深色块区/白底都可见
                rects.append('<rect class="r um" x="%s" y="%s" width="%s" height="%s"/>' % (bx[0], bx[1], rw, rh))
                eyes.append('<div class="eye %s" data-g="%s" style="left:%.2f%%;top:%.2f%%" title="%s · %s">👁</div>'
                            % (lvl, gid, (bx[0] + bx[2]) / 2.0 / W * 100, (bx[1] + bx[3]) / 2.0 / H * 100,
                               u["confidence"]["level"], u["divergence"]["kind"] + hint))
        else:
            # —— 跨渠道分组: 有差异/问题的区域在原文栏标小眼睛, 悬停时四栏浮遮罩(差异分析) ——
            groups = []
            for ch in chs:
                for i, b in by_ch[ch]:
                    if not b.get("box"):
                        continue
                    for g in groups:
                        c0, i0, b0 = g[0]
                        if b0.get("box") and (_iou(b["box"], b0["box"]) >= 0.3
                                              or _gdice(b.get("text"), b0.get("text")) >= 0.5
                                              or _cin(b["box"], b0["box"])):
                            g.append((ch, i, b))
                            break
                    else:
                        groups.append([(ch, i, b)])
            for gi, g in enumerate(groups):
                # 结构性组(页眉/页脚/页码)不算差异标注, 不出眼睛(避免一页一堆噪音按钮)
                if all(b.get("cat") in ("Page-header", "Page-footer", "Page-number") for c, i, b in g):
                    continue
                panes, interesting, anchor = _cmp_group_masks(g, chs)
                if not interesting:
                    continue
                gid = "G%d_%d" % (p, gi)
                groups_json[gid] = {"panes": panes}
                eyes.append('<div class="eye" data-g="%s" style="left:%.2f%%;top:%.2f%%" title="悬停/点击：四栏差异分析遮罩">👁</div>'
                            % (gid, (anchor[0] + anchor[2]) / 2.0 / W * 100, (anchor[1] + anchor[3]) / 2.0 / H * 100))
        # refs/frefs 按条目内容选择成员框；cspan 保持 unified 块级定位。
        if meta is not None and unified_pages is not None:
            evidence = ([("ref", s) for s in (meta.get("references") or [])]
                        + [("fref", s) for s in (meta.get("footnote_refs") or [])]
                        + [("cspan", s) for s in (meta.get("citation_spans") or [])])
            cite_uids = {s.get("uid") for _, s in evidence
                         if s.get("page") == p + 1 and s.get("uid")}
            for u in unified_pages.get(p) or []:
                if u.get("uid") not in cite_uids:
                    continue
                seen = set()
                for kind, item in evidence:
                    if item.get("page") != p + 1 or item.get("uid") != u.get("uid"):
                        continue
                    ekey = _evidence_key(kind, item) if kind in ("ref", "fref") else ""
                    if ekey in seen:
                        continue
                    seen.add(ekey)
                    bx = _evidence_member_box(u, item) if ekey else u["box"]
                    data_e = ' data-e="%s"' % ekey if ekey else ""
                    rects.append('<rect class="r cite-target" data-u="%s"%s x="%s" y="%s" width="%s" height="%s"/>'
                                 % (u["uid"], data_e, bx[0], bx[1], max(2, bx[2] - bx[0]), max(2, bx[3] - bx[1])))
        img_cell = ('<div class="pane"><div class="ptitle src">原文件（区块联动 · 👁=%s）</div>'
                    '<div class="imgw"><img src="pages/page_%d.jpg"/><svg viewBox="0 0 %d %d" preserveAspectRatio="none">%s</svg>%s</div>'
                    '<div class="pmask"></div></div>'
                    % ("unified 复检项(悬停👁=遮罩+共识框)" if unified_pages is not None else "差异分析",
                       p, W, H, "".join(rects), "".join(eyes)))
        ch_cells = "".join('<div class="pane ch"><div class="ptitle %s">%s</div>%s<div class="pmask"></div></div>'
                           % (ch, {"mineru": "MinerU", "dots": "dots.ocr", "paddle": "PaddleOCR"}[ch], col_html[ch]) for ch in chs)
        ov_cell = ""
        if meta is not None:
            if p == 0:
                # 实体清单"正文"项判据: unified 全篇 Text/Footnote 主读法总字符
                _body = sum(len((u.get("main_text") or "").strip())
                            for _us in (unified_pages or {}).values() for u in _us
                            if u.get("type") in ("Text", "Footnote"))
                ov_cell = _meta_overview(meta, audit, ents, _body) or ""   # 实体并入总览带"实体"分组(RULES §6), 不另起横带
            ch_cells += _meta_cell(meta, p + 1, audit, choices) or ""
        ch_cells += _entity_cell((ents or {}).get(p + 1)) or ""
        flag = ""
        if nffd:
            flag += ' <a class="bad jump" data-t="ffd_%s" title="跳到本页首个含错误字符的块">FFFD×%d</a>' % (first_ffd, nffd)
        if nemp:
            flag += ' <a class="empf jump" data-t="emp_%s" title="跳到本页首个空块">空块×%d</a>' % (first_emp, nemp)
        secs.append('<section id="p%d"><h2>第 %d 页%s <span class="stat" id="st_p%d"></span></h2><div class="grid">%s%s%s</div></section>' % (p, p + 1, flag, p, ov_cell, img_cell, ch_cells))
    v = COMPARE_TMPL.replace("@@TITLE@@", title).replace("@@NAV@@", nav)
    v = v.replace("@@CORE@@", _JS_CORE)
    v = v.replace("@@LAYBTN@@", ('<button id="laybtn" title="块边界诊断: 叠加显示全部统一对象框(只读叠加, 不切换布局)">'
                                 "块框叠加</button>") if meta is not None else "")
    v = v.replace("@@BODY@@", '<div class="wrap">%s</div>' % "\n".join(secs))
    v = v.replace("@@DATA@@", json.dumps(data, ensure_ascii=False))
    # 全量块坐标表 {uid: {box, panes?}}: metadata 条目点击跳转定位(box)+复查组遮罩(panes) 并一张表,
    # 含无眼/无框的普通块(review=false 且读法健康时 umb/cite-target 都不画), 非 unified 模式仅空表。
    ubox = {}
    if unified_pages is not None:
        for p0 in sorted(unified_pages):
            for u in unified_pages[p0] or []:
                if u.get("uid") and u.get("box"):
                    ubox[u["uid"]] = {"box": [p0] + list(u["box"])}
    for gid, g in groups_json.items():
        ubox.setdefault(gid, {"box": None})
        ubox[gid]["panes"] = g["panes"]
    v = v.replace("@@UBOX@@", json.dumps(ubox, ensure_ascii=False))
    with io.open(out_fp, "w", encoding="utf-8") as fh:
        fh.write(v)
    return len(data)
