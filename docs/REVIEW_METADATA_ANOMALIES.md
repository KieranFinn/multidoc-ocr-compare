# metadata 抽取结果逐条核查与共性分析

> 核查对象：`out/` 下已产出 `metadata.json` 的 4 篇文档，共 **113 条 `references[]` + 22 条 `footnote_refs[]`**
> 核查口径：沿用单条样例（`KCI_FI001632041_2010` 第2条 `u35_21`）的九项标准 —— 结构完整性、字段齐全性、`title/source` 切分正确性、`volume/issue/pages` 归属、`doi/notice_no/lang` 正确性、`grammar` 语义、校验层拦截、`ch_agree` 互证有效性、`confidence.level` 可信度。
> 数据来源：`out/<doc>/metadata.json`（产品）+ `out/<doc>/metadata_audit.json`（诊断层）。代码定位以 `metapy/refs.py` 当前版本为准。
> 生成时间：2026-11-03

---

## 0. 阅读指引

- **判定记号**：✅ 正确 / ⚠️ 不完整（抽到了但漏了一部分） / ❌ 错误（抽到的值是错的） / ➖ 原文确实没有该字段 / 🕳 整条丢失
- **严重度**：**P0** = 生产数据直接错（值错、整条丢）；**P1** = 结构不完整，下游消费方会误读；**P2** = 语义/口径不规范，暂不致命。
- 每条记录给 `uid` 作为锚点，可在 `out/<doc>/viewer_compare.html#p<page-1>` 直接跳到版面核对。
- 本文件只做诊断与建议，**不修改任何代码或产物**。

### 0.1 样本总表

| 文档 | 学科/语言 | references | style | grammar 分布 | footnote_refs | 整体判定 |
|---|---|---|---|---|---|---|
| `13625974` | 科学教育 / 英文为主 | 23 | bracketed | quoted 15 / remnant_quote 7 / notice 1 | 0（无脚注） | 结构正确，基本可用 |
| `KCI_FI001892171_2014` | 心理学 / 韩+英 | 22 | authoryear | quoted 15 / italic 2 / noquote 2 / unknown 2（审计层记 17/3/2） | 25（审计）| **问题最多**：整条丢失 + 条间串烧 |
| `KCI_FI001632041_2010` | 历史学 / 韩+汉 | 13 + 14 = 27 | numbered | quoted 3 / noquote 24 | 22 | 韩式五段式系统性错位 |
| `KCI_FI000895567_2003` | 地学 / 英为主 | 41 | numbered | quoted 36 / noquote 4 / italic 1 | 0 | 跨页块整块切不开，`num` 全失 |

---

## 1. 逐条分析

### 1.1 `13625974`（23 条，bracketed，英文 IEEE 型著录）

> 体例：`[n] 作者, "题名", 期刊, vol.X, no.Y, pp.a-b, 年.` —— 引号题名 + `vol./no./pp.` 标记齐全，是最适配当前正则的体例，**23 条中 19 条完全正确**。

| # | uid | 原文要点 | 判定 | 问题 |
|---|---|---|---|---|
| 1 | u9_4 | Y. Kwak, … vol.29, no.6, pp.611-625. 2009. | ✅ | 年取 2009 正确（`2009.` 得 +3 分胜出） |
| 2 | u9_6 | J. Ahn, 硕士论文 | ✅ | `source` = "Master's Thesis, Daegu National University of Education, Daegu, Korea" 正确 |
| 3 | u9_8 | S. Ji and J. Park | ✅ | 双作者正确拆分 |
| 4 | u9_10 | J. Lim, S. Lee, I. Yang and Y. Lee | ✅ | and 拆分正确 |
| 5 | u9_12 | Y. Kang, 硕士论文 | ✅ | |
| 6 | u9_14 | M. Yang and H. Yoon | ✅ | |
| 7 | u9_15 | Y. Kim | ⚠️ | 字段全对，但**块级 mid 继承**（审计 review_comment 记"跨页拼接块…无可识别[n]分段"），`level=mid` 合理 |
| 8 | u9_15 | E. Na and K. Han. | ⚠️ | **与 #7 共用同一 `uid=u9_15`、同一 `box=[125.0,194.9,1032.3,1642.7]`（整页框）**，页面锚点不可用；`pages=null`（原文无 pp.）正确 |
| 9 | u10_5 | S. Yeo | ✅ | |
| 10 | u10_7 | Ministry of Education, **Notice No. 2015-74**, Science and Curriculum, 2015. | ✅ | `notice_no="2015-74"`、`grammar="notice"`、`issue/pages=null`、`level=mid`（"政府公告型著录"）全部按设计走对 |
| 11 | u10_8 | I. Park etc., 教材 | ✅ | `et_al=true`，`authors=["I. Park"]`（et al. 未污染作者数组） |
| 12 | u10_10 | H. Cho etc. | ✅ | 同上 |
| 13 | u10_11 | S. Chang etc., **textbook” DongA**（缺开引号） | ⚠️ | `grammar="remnant_quote"` 分类正确；但 `raw` 里题名与出版社之间**没有逗号**，`source` 仍抽对（靠闭引号段回收后剩余串）|
| 14 | u10_13 | D. Shin etc., …textbook”, MIRAE N co., ltd., 2022. | 🔴 **P1** | `source="MIRAE N co., ltd"` —— **公司名内部逗号被当成段分隔符切断**，`. ltd.` 变成 `. ltd`（尾点被剥）。第 11 条的 `Kumsung Publishing Co. Ltd` 同理。`source` 不是"一个可换行的容器串"，而是被切成了片段 |
| 15 | u10_15 | S. Lee etc., …Visang, 2022. | ✅ | |
| 16 | u10_17 | D. Hyeon etc., …Co., Ltd. 2022. | 🔴 **P1** | 同 #14；另 **`Co., Ltd. 2022.` 出版社与年之间仅有空格**，`refs.py:253-254` 的"尾年剥离"补丁生效（`source="I-Scream Media Co., Ltd"`），但 `.` 被剥 |
| 17 | u10_19 | C. Kweon etc., …LTD., 2022. | 🔴 **P1** | 同 #14 |
| 18 | u10_21 | S. Lee etc., …Co.,Ltd., 2022. | 🔴 **P1** | 同 #14，且 `Co.,Ltd` 逗号无空格，切得更碎 |
| 19 | u10_2 | Y. Shin, …textbook”, CHUNJAE EDUCATION INC. 2022. | ⚠️ | 单作者 `et_al=false` 正确；`source="CHUNJAE EDUCATION INC"` 尾点剥掉 |
| 20 | u10_3 | Y. Hong, vol.8, no.4, pp.281-290 | ✅ | |
| 21 | u10_4 | H. Oh, 硕士论文 | ✅ | |
| 22 | u10_6 | Y. Hong and I. Park | ✅ | |
| 23 | u10_9 | H. Lee, A. Kim and Y. Hong | ⚠️ | `title="My Creative Thermometer’ **Program** …"` —— 题名内部的 `’`（原文明明是 `"My Creative Thermometer’ Program…"`，开引号被 OCR 吞了）让 `QUOTE_TITLE` 从中间的 `’` 起算，**丢了题名开头的 `My`**；`grammar="quoted"` 掩盖了这一点 |

**本篇小结**：字段级准确率 ≈ 19/23（83%）。问题集中在 `source` 中的 **公司名内部逗号/句点**（4 条 P1）与 **题名内嵌引号**（1 条 P1）。`grammar` 只有 `quoted/remnant_quote/notice`，无 `unknown`，体例识别稳定。

---

### 1.2 `KCI_FI001892171_2014`（22 条，authoryear，韩英混排）

> 体例：`作者, "题名", 期刊, 제N권 M호, 발행처, 年, 쪽.` —— 韩式，但**作者间分隔不统一**（空格 / `·` / `,`），这正是本篇的病灶。

#### A. `references[]`（文末参考文献区，p29–32）

| # | uid | 原文要点 | 判定 | 问题 |
|---|---|---|---|---|
| 1 | u28_11 | 이경순 김교헌, "…", 한국심리학회지:건강, 제5권 1호, …, 2000. | ✅ | 空格分隔双作者正确拆开；`volume=5, issue=1` 正确 |
| 2 | u29_2 | 이옥정 지영환, "…", …, 2010. | ✅ | |
| 3 | u29_3 | 이훈진, "…", …, 2001. | ✅ | |
| 4 | u29_4 | 전현숙 손정락, "역기능적…마음챙김의 관계" | ⚠️ | 字段对；`level=low` 因 `title` 互证不足 —— **dots 读成 `마음창김`（창/챙 异体）**，`variant` 已记录。属 OCR 差异而非切分错误 |
| 5 | u29_4 | 한덕웅 이장호 전겸구, "한국관 상태-특성…" | 🔴 **P0** | **`title="한국관 상태-특성 분노 표현 척도(STAXI-K): 대학생 집단"` 但原文题名是 `한국판 상태-특성…`（판→관 OCR 错字）**；更关键的是 **`uid=u29_4` 与 #4 相同**、`box` 与 #4 完全相同 —— 两条不同条目共用一个版面锚点，**位置信息失效**。另 `issue=null`（原文确实只写 `제3권`）➖ 正确 |
| 6 | u29_7 | American Psychiatric Association, *DSM-4*, APA, 1994. | 🔴 **P0** | **`authors=["American","Psychiatric","Association"]` —— 单个机构作者被按空格切碎成 3 个人**。`refs.py:416-452` 的机构名单一作者规则只对韩文 `AFFIL_HEAD/org` 尾词或 `latcorp` 开关生效，`references[]` 路径 `latcorp=False`，拉丁机构名被空白切分击穿 |
| 7 | u29_8 | 同上 DSM-5，2013 | 🔴 **P0** | 同 #6 |
| 8 | u29_9 | Arbuckle, **AMOS Manuel**, SPSS Inc., 1997. | 🔴 **P0** | **`title=null, source=null` 全丢**；`authors=["Arbuckle","AMOS Manuel","SPSS Inc"]`。这是**软件手册型**著录（`作者, 手册名(斜体缺失), 出版社, 年`），无引号无斜体 → `grammar="unknown"`，题名无从抽取 |
| 9 | u29_10 | National Institute of Mental Health, *Mental Health and Mass Violence…*, NIH Publication, 2002. | 🔴 **P0** | **`title=null, source=null`**；`authors` 变成 4 段，其中第 3 段就是题名本身（`Mental Health and Mass Violence Evidence-Based…`）。机构名 + 长题名同时被空白切碎 |
| 10 | u30_2 | Spielberger C. D. · Krasner S. S. · Solomon E. P., *The experience…* | ⚠️ | 三作者正确；`title` 把 `…of anger. In M. P. Janisse (Ed.), Individual differences…` 整段吞进题名（**书章章名与书名未分离**）；`source=null`（原文只到 `Hemisphier` 出版社，被并入题名尾部）|
| 11 | u30_4 | Anderson J. C. · Gerbing D. W, "…", *Psychological bulletin* | ⚠️ | 字段对；但 `source="*Psychological bulletin*"` —— **斜体标记 `*…*` 未剥离**，作为来源串输出不规范（P2）|
| 12 | u30_5 | Anderson-Malico R, "Anger Management…" | ✅ | `title` 内嵌撇号 `'` 未出错 |
| 13 | u30_6 | Bandalos D. L, "Is parceling…" | ✅ | 同上，`source` 带 `*`（P2）|
| 14 | u30_7 | Clair M. E., "…", Doctoral dissertation (…) , Drexel University, 2006. | ✅ | `source` 保留 `Doctoral dissertation (Dissertation Abstracts International), Drexel University` |
| 15 | u30_8 | Hu L. T. Bentler P. M., "…" | 🔴 **P1** | **作者间只有空格 + 句点，未拆成两人**：`authors=["Hu L. T. Bentler P. M"]`（合成一段）还是拆开？实际 `authors=["Hu L. T", "Bentler P. M"]` —— 拆对了。但 `Meffert S. Metzler T. …`（#见同篇）则未拆 |
| 16 | u30_9 | McNally J. · Solomon M., "…" | ✅ | |
| 17 | u30_9 | Meffert S. Metzler T. Henn-Haase C. McCaslin S. Inslicht S. Chemtob C. "…" | 🔴 **P1** | **6 位作者用空格分隔且无 `·`，被当作一人**；`uid=u30_9` 与 #16 相同（页面框并集 `[152.2,427.3,781.4,1155.9]`），**位置锚点失效** |
| 18 | u30_9 | Patterson G., "…" | 🔴 **P1** | 同上 `uid` 冲突 |
| 19 | u30_9 | Stephens C. Long N. Miller I., "…" | 🔴 **P1** | 同上 `uid` 冲突 |
| 20 | u30_9 | Walker J. I., "Vietnam combat Veterans with legal difficulties. A psychiatric problem?" | ⚠️ | **`authors=["Walker J","I"]` —— `Walker J. I.` 被拆成"姓+缩写"两段**；`title` 中 `A psychiatric problem?` 后的 `?` 前有句点，题名内部句号未被判为切分点（结果正确）|
| 21 | u31_3 | 사이버경찰청, "순직 및 공상자 현황", http://…(2014년 3월 24일 최종 검색) | 🔴 **P1** | **`year="2014"` 取的是"检索日期"而非出版年** —— 网络资源型著录，原文根本没有出版年，`year` 是检索日。这是 `_year_pick` 的"串尾收尾 +3 分"规则被"（2014년 3월 24일 최종 검색）"中的年份欺骗 |
| 22 | u31_4 | 소방방재청, "순직 및 공상자 현황", http://… | 🔴 **P1** | 同 #21，`level=high` —— **错值拿 high**，比 low 更危险 |

#### B. `footnote_refs[]`（正文脚注引文，p2–27，共 25 条）

| num | uid | 原文要点 | 判定 | 问题 |
|---|---|---|---|---|
| 1 | u1_4 | NIMH, *Mental Health and Mass violence…*, NIH Publication, 2002, p.5. | 🔴 **P1** | `title` 抽对；`source=null`（原文 `NIH Publication` 是出版方串，被吞）；审计 `review_comment` 的 `variant` 却给出 **paddle 读法 `순직 및 공상자 현황`** —— 同一块内混入了别人的内容 |
| 5 | u2_4 | APA, *DSM-4*, APA, 1994, p.558. | ⚠️ | `authors=["American Psychiatric Association"]`（**这条拆对了**，与 #6/#7 矛盾，说明切分结果依赖上下文长度）；`source=null` |
| 6 | u2_4 | APA, *DSM-5*, APA, 2013, p.271. | ⚠️ | 同 #5，`uid` 与 #5 相同 |
| 8 | u3_4 | Clair M. E., "…", Doctoral dissertation…, 2006, p.3443. | ⚠️ | 字段对；`level=mid` 因块级复检 |
| 9 | u3_4 | Meffert S. **외**, "…", Journal of Traumatic Stress… | 🔴 **P1** | **`authors=["Meffert S","외"]` —— 韩语省略标记 `외`(=et al.) 进了作者数组**，且 `et_al=false`。`refs.py:192` 的 `et_al` 规则只认 `etc./et al.`，不认韩文 `외` |
| 10 | u4_4 | Walker J. I., "…", Vol.138, No.10(1981), p.1384-1385. | ✅ | `pages="1384-1385"`（`p.` 前缀被剥）|
| 12 | u5_3 | 박상혁 **외**, "…", 2011, 77쪽. | 🔴 **P1** | 同 #9：`authors=["박상혁","외"]`、`et_al=false`；`pages=null`（原文 `77쪽` 是引用页非起止页，➖ 可接受）|
| 13 | u5_3 | Anderson-Malico R, "…", …, p.17. | ⚠️ | 字段对；`pages=null`（原文确实只有 `p.17` 单页，`PP_RE` 要求起止，➖）|
| 15 | u5_3 | Spielberger C. D. **외**, *The experience…* | 🔴 **P1** | `authors=["Spielberger C","D","외"]` —— 缩写被拆 + `외` 入数组，两个错叠加 |
| 16 | u6_3 | 전현숙·손정락, **"역기능적 분노표현 및 비합리적 신념과 마음창김의 관계, 한국심리학회지: 일반, 제30권 2호, 한국심리학회, 2011, 377쪽.** | 🕳 **P0（最严重）** | **原文题名缺闭引号（引号被吞），于是 `QUOTE_TITLE` 不中 → 走 noquote 分支（`tailflow=True`）→ 年前逗号段分层时取 `hsegs[1]` 当题名**，结果：`title="377쪽"`（**页码成了题名**）、`source=null`、`authors` 第二段 = `손정락, "역기능적…한국심리학회` （**题名 + 期刊 + 卷期全被塞进作者数组**）。`grammar="noquote"` 报了，但 `_validate_fields` 的"题名碎片"只查长度 ≥4 与是否含字母，`377쪽` 含韩文照样通过 → **`level=mid`，看不见** |
| 17 | u6_3 | 이경순 김교헌, "…" | ✅ | |
| 18 | u6_3 | 김교헌 전경구, "…" | ✅ | |
| 21 | u7_4 | McNally J. Solomon M., "…", p.20. | ✅ | |
| 22 | u7_4 | **Julian D. Ford/김정휘 외 역, 앞의 글, 152쪽. (23) Patterson G., "…", 2003, p.215.** | 🕳 **P0** | **脚注 22 与脚注 23 串成一条**（行首 `(23)` 不匹配 `FOOTNOTE_CITE`，因为该标记是 `(23)` 圆括号形态而非 `23)`）。结果 `authors=["Julian D. Ford/김정휘 외 역","앞의 글","152쪽. (23) Patterson G"]` —— **题名文字进了作者数组**；`title/source/volume/issue` 全部取自后半条 Patterson。**脚注 23 从此不存在**（`footnote_ref_validation` 只计数 25 条，无法发现）|
| 24 | u7_4 | 박성수, "…", 박사학위논문, 동국대학교, 2002, 4쪽. | ⚠️ | `source="박사학위논문, 동국대학교"` —— **`mcut` 只切到卷期页标记处，学位论文的"类型, 学校"两段被整段塞进 source**（P2，可接受但不规范）|
| 25 | u9_5 | 이옥정 지영환, "…", 2010, 182쪽. | ✅ | |
| 26 | u10_3 | 은헌정 **외**, "…", 2005, 303-304쪽. | 🔴 **P1** | `authors=["은헌정","외"]`；且 `pages=null` —— **原文 `303-304쪽` 是起止页，`PP_RE/PG_RANGE` 不认 `쪽` 后缀**，合法页区间丢失 |
| 27 | u11_5 | 한덕웅 외, "…", 제3권, 1998, 18쪽. | 🔴 **P1** | `authors=["한덕웅","외"]`；`issue=null`（原文 `제3권` 无号，➖）|
| 28 | u13_3 | Bandalos D. L., "Is parcelling…" | ⚠️ | `title="Is parcelling really necessary?…"`（`parceling`→`parcelling` 是原文如此，非错误）|
| 29 | u13_3 | Anderson J. C.**, · **Gerbing D. W., "…" | ✅ | `authors_raw="Anderson J. C., · Gerbing D. W"` 里的 `., ·` 三连符被 `_split_authors` 正确处理 |
| 30 | u14_4 | Arbuckle, AMOS manuel, SPSS Inc., 1997, **p.201.** | 🔴 **P0** | **`title="p.201"`** —— 页码成了题名；`source=null`；`authors=["Arbuckle","AMOS manuel","SPSS Inc"]`。与 `references[]` #8 同一条被抽了两次，两次都错 |
| 31 | u14_4 | Hu L. T. · Bentler P. M., "…" | ✅ | |
| 34 | u24_5 | 이훈진, "…", **한국심리학회 위와 같은 결과들은 … (一大段正文) … 한국심리학회지: 임상, 제20권 3호, …, 2001, 443쪽.** | 🕳 **P0** | **脚注块跨了整段正文**：正文段落没有行首引文标记，被 `_fnote_collect` 的"无标记段落并入前一条"规则（`refs.py:899-901`）整段并进脚注 34。`source` 变成 `"한국심리학회 위와 같은 결과들은 …(约 500 字正文)…"`，`title` 反而抽对了。**该条 `raw` 已不是一条文献，而是"文献+正文"混合体** |
| 35 | u25_2 | Stephens C. 외, "…" | 🔴 **P1** | `authors=["Stephens C","외"]` |
| 36 | u26_3 | Novaco R. W., *Anger Control…*, Oxford, 1975, p.10. | ⚠️ | `authors=["Novaco R","W"]` 缩写被拆；`source=null`（原文 `Oxford` 是出版社）|

**本篇小结**：`references[]` 22 条中字段完全正确的约 9 条（41%）；`footnote_refs[]` 25 条中完全正确约 7 条（28%）。**这是四篇里质量最差的**，且它的 `ch_agree` 大多显示 1.0（见 §2.3）。

---

### 1.3 `KCI_FI001632041_2010`（27 条，numbered，韩式历史学著录）

> 体例**一个文件里混了两套**：p36 上半是「國史」类（团体作者 + 书，`저자(년), 书名, 出版社`），p36 下半到 p37 是「論文」类（`저자(년), 소속/표제, 논문명, 출처, 출판사`）。**这正是单条样例 `u35_21` 错了的根因**，下面逐条看清哪些中招、哪些没中。

#### A. 國史段（p36，#1–#13，`u35_6`–`u35_18`）

| # | uid | 原文 | title / source | 判定 | 问题 |
|---|---|---|---|---|---|
| 1 | u35_6 | 노혁진·장미영·심재연(1994), 춘천군의 도요지 및 관방유적 **『춘천군의 역사와 문화유적』**, 한림대 박물관·강원도·춘천군 | title="춘천군의 역사와 문화유적" / source="한림대 박물관·강원도·춘천군" | 🔴 **P0** | **`『』`（书名号）被 `QUOTE_TITLE` 命中，单书名成了"题名"，真正的篇章名 `춘천군의 도요지 및 관방유적` 被整段丢弃**。`volume/issue/pages` 全 null。`level=high` |
| 2 | u35_7 | 김기옥(1986), 한국전쟁초기, 춘천·홍천전투가 전세에 미친 영향, 軍史 제12호, 국방부 전사편찬위원회 | title="한국전쟁초기" / source="춘천·홍천전투가 전세에 미친 영향" | 🔴 **P0** | **单条样例本体**：题名/source 错位一格，`軍史` 与出版者双丢，`issue=12` 对但期刊名没了 |
| 3 | u35_8 | 충북대 중원문화연구소(2000), 춘천 삼악산성 | title/source 对，source=null | ✅ | 单段式，抽对；`source=null` 因原文确实只有题名 ➖ |
| 4 | u35_9 | 육군사관학교 육군박물관(2001), 강원도 화천군·춘천시 군사유적 지표조사 보고서 | 同 #3 | ✅ | 机构名作作者，未拆碎 |
| 5 | u35_10 | 문화재청·강원도·춘천시·강원문화재연구소(2003), 문화유적 분포지도 춘천시 | `authors` 4 段全保留 | ✅ | `·` 路径不剔机构，设计到位 |
| 6 | u35_11 | 강원문화재연구소(2004), 春川地域 支石墓群 發掘調査 報告書 | ✅ | ✅ | 混排汉字题名 |
| 7 | u35_12 | 춘천공립보통학교 **편**(1915년경 발간), 春川郡郷土史料 | title 对；`authors_raw="춘천공립보통학교 편"`→`authors=["춘천공립보통학교"]` | ⚠️ | `편` 剥除正确；但 **`(1915년경 발간)` 的"경"(约)丢失**，`year="1915"` 无标记说明"约"。`level=low`（title 互证不足：mineru 读法含完整 7. 前缀）|
| 8 | u35_13 | 조선총독부(1942), 조선보물고적조사자료 | ✅ | ✅ | |
| 9 | u35_14 | 심경호(1995), 茶山과 春川, 강원대학교 출판부 | title/source 对 | ✅ | 两段式，抽对 |
| 10 | u35_15 | 강원도(1995), 江原道史(역사편) | title="江原道史(역사편)" | ✅ | 括号配对保留（A4 补丁生效）|
| 11 | u35_16 | 春川市(1996), 春川百年史 | ✅ | ✅ | `lang=ko`（汉字计入韩文，A5 补丁生效）|
| 12 | u35_17 | 국방부 군사편찬연구소(2005), **6·25戰爭史 2, 北韓의 全面南侵과 初期 防禦戰鬪** | title="6·25戰爭史 2" / source="北韓의 全面南侵과 初期 防禦戰鬪" | 🔴 **P0** | **与 #2 同型错位**：`6·25戰爭史 2`（丛书卷次）被当题名，真实的篇章副标题 `北韓의 全面南侵과 初期 防禦戰鬪` 被当来源。`volume=null`（卷次 `2` 本该进 volume，**被写死进题名串**）|
| 13 | u35_18 | 강원도사편찬위원회(2010), 강원도사 2(선사시대) | title="강원도사 2(선사시대)" | ✅ | 单段式；但 `volume=null` —— **卷次 `2` 已在题名内部，未拆到 `volume`**（P1，与 #12 同源）|

#### B. 論文段（p36 末 – p37，#1–#14，`u35_20`–`u36_11`）

| # | uid | 原文 | title / source / issue | 判定 | 问题 |
|---|---|---|---|---|---|
| 1 | u35_20 | 김택균(1983), 春川 貊國說에 관한 硏究, **석사학위 논문**, 강원대학교 대학원 | title 对 / source="석사학위 논문" | ⚠️ | `source` 只取了第一段 `석사학위 논문`，**学校 `강원대학교 대학원` 被丢**（`source` 单槽只能装一段）。`level=low` |
| 2 | u35_21 | 김기옥(1986), 한국전쟁초기, …（同 A#2 重复出现） | 同 A#2 | 🔴 **P0** | **同一篇文献在 國史段与論文段各出现一次，两条都错位** |
| 3 | u35_22 | 오영섭(1996), 春川離宮攷, **아시아문화 12**, 한림대학교 아시아문화연구소 | title 对 / source="아시아문화 12" / volume,issue=null | ⚠️ | **`아시아문화 12` 未被 `KW_NO/KW_JI/VOL_RE` 命中**（原文无 `제N호` 形态，只有裸数字 `12`）→ `volume/issue` 双 null，`12` 留在 source 里。`level=low` |
| 4 | u36_0 | 유재춘(1999), 북한강 유역의 전란과 성곽 연구, **강원문화사연구 제4집** | source="강원문화사연구" / issue="4" | ✅ | `제N집` 拆出成功（A6 补丁），**是本段唯一把卷期拆干净的** |
| 5 | u36_1 | 심재연(2000), 봉의산성 소고, 춘주문화 제15호, 춘천문화원 | source="춘주문화" / issue="15" | ✅ | `제N호` 拆出 |
| 6 | u36_2 | 김영호(2001), …, 戰史 제3호, 군사편찬연구소 | source="戰史" / issue="3" | ✅ | |
| 7 | u36_3 | 남정옥(2001), …, 戰史 제3호, 군사편찬연구소 | 同 #6 | ✅ | |
| 8 | u36_4 | 유재춘(2001), 강원지역 관방유적의 연구현황과 과제, **육군사관학교 육군박물관** | source="육군사관학교 육군박물관" | ⚠️ | 三段式（无期刊），最后一段是机构，被当 source；原文本身无卷期 ➖ |
| 9 | u36_5 | 유재춘(2004), 春川 牛頭山城考, 강원문화사연구 제9집 | source/issue 对 | ✅ | |
| 10 | u36_6 | 한종섭(2005), **貊國 왕도 유적 조사에 대하여 春川貊國의 歷史的 硏究**, 춘천시강원향토문화연구회 | title=整串（篇章名+书名粘连）/ source=机构 | 🔴 **P0** | **篇章名与书名之间无任何分隔符（原文缺逗号/书名号），粘连成一串**；`source` 只能是机构名。`level=low` |
| 11 | u36_8 | 노병식, **「춘천 삼악산성의 축조양상」**, 한국성곡학회 … 발표문, 2005년 11월 19일(개최장소 : …) | title/source 对；`year="2005"` | ⚠️ | `『』「」` 引号对命中正确；**`source` 吞掉了会议日期与地点 `2005년 11월 19일(개최장소 : 춘천국립박물관 대강당)`**，因为 `mcut` 未在 `발표문` 后止步。`grammar="quoted"` |
| 12 | u36_9 | 김용규(2005), 춘천시민과 함께 한 춘천대첩, 춘천대첩 55주년 기념 학술심포지엄 요록집, 강원대학교 인문과학연구소 주관 | source="춘천대첩 55주년 기념 학술심포지엄 요록집" | ⚠️ | **主办单位 `강원대학교 인문과학연구소 주관` 丢失**（`source` 单槽）。`level=low` |
| 13 | u36_10 | 석영준(2005), 춘천전투의 역사적 재조명-춘천지구 전투가 6.25전쟁에 미친 영향을 중심으로-, 춘천대첩… | title/source 对 | ⚠️ | 题名内 `6.25전쟁` 的 `25` 未被年份陷阱误抓（好）；但**主办单位同样丢失** |
| 14 | u36_11 | 유재춘(2005), 춘천지역의 성곽유적 연구, **春州文化 제20호**, 춘천문화원 | source/issue 对 | ✅ | `春州文化` vs `춘주문화`（#5）同一刊物的汉字/韩文两种写法，各自独立正确 |

#### C. 脚注引文（22 条）

| num | uid | 原文要点 | 判定 | 问题 |
|---|---|---|---|---|
| 2 | u2_5 | 유재춘, 북한강 유역의 전란과 성곽 연구 , 강원문화사연구 제4집, 1999. | ✅（字段）/ 🟡 互证 | `level=low`：dots 读成 `성곡`（곽/곡），`variant` 已记。**字段本身正确** |
| 3 | u2_6 | 유재춘, 春川 牛頭山城考 , 강원문화사연구 제9집, 2004. | ✅ | |
| 4 | u2_7 | 노병식, 춘천 삼악산성의 축조양상 , … 발표문, 2005년11월 19일(…) . | ✅（字段）/ 🔴 P1 | `source` 同样吞进日期地点；但 `raw` 里题名后有个空格 `춘천 삼악산성의 축조양상 ,`（**开引号被吞**），`grammar="noquote"` 未被判为 `remnant_quote` |
| 5 | u2_8 | 유재춘, 춘천지역의 성곽유적 연구 , 春州文化 제20호, 춘천문화원, 2005. | ✅（字段）/ 🟡 互证 | 同 #2 的 곽/곡 问题，`level=low` |
| 13 | u5_5 | 한종섭, **'좌國 왕도 유적 조사에 대하여'**, **『春川좌國の歴史的研究』**, 춘천시 · 강원향토문학연구회, 2005. | title="春川좌國の歴史的研究" / source="춘천시 · 강원향토문화연구회" | 🔴 **P0** | **`QUOTE_TITLE` 优先命中 `『』`（书名），把单引号里的篇章名 `'좌國 왕도 유적 조사에 대하여'` 丢掉了**；`authors=["한종섭","'좌國 왕도 유적 조사에 대하여'"]` —— **篇章名进了作者数组**。另 OCR 把 `貊國` 读成 `좌國`（三渠道皆同，属 OCR 问题）|
| 18 | u6_10 | 김택균, **「春川 貓國說에 관한 研究」**, 강원대학교 대학원 석사논문, 1983, 55쪽 참조 | title="春川 貓國說에 관한 研究" | 🔴 **P1** | `title` 正确（与 #1 的 `貊國` 不同，**OCR 两个不同读法**：本条 `貓國`，条 1 `貊國`，源文献同一篇却抽出两种题名）；`source="강원대학교 대학원 석사논문"`；`55쪽 참조` 的引用页未抽（➖）。`level=low` |
| 19 | u7_6 | 조선총독부, 『조선보물고적조사자료』, 1942, 526쪽. | ✅ | |
| 20 | u7_6 | 육군사관학교 육군박물관, 『강원도 화천군·춘천시 군사유적 지표조사 보고서』, 2001, 96쪽. | ✅ | 但 `uid=u7_6` 与 #19 相同，**box 完全相同** `[174.4,1242.7,1001.7,1355.0]` |
| 22 | u7_6 | **우두산성에 대한 상세한 내용은 유재춘의** 「春川 牛頭山城考」(『江原文化史研究』9집, 강원향토문화연구회, 2004) 참조. | title="春川 牛頭山城考" / source="(『江原文化史研究』9집, 강원향토문화연구회" | 🔴 **P0** | **`authors` = 散文 5 个词 `["우두산성에","대한","상세한","내용은","유재춘의"]`** —— 句首引导语进了作者数组；`source` 以未闭合的 `(` 开头、丢了 `9집` 的卷次分离；`uid` 与 #19/#20 相同 |
| 23 | u7_6 | 유재춘, 「春川 牛頭山城考」, 『江原文化史研究』제9집, 강원향토문화연구회, 2004. | ✅ | 与 #22 是同一文献的**规范著录**，本条抽对 |
| 25 | u8_6 | 노혁진 · 장미영 · 심재연, 「춘천군의 도요지 및 관방유적」**『춘천군의 역사와 문화유적』**, 한림대 박물관 · 강원도 · 춘천군, 1994, 264쪽. （正文标记 `# 25`） | title/source 对 | ✅ | **两条题名都在**，靠 `」『` 相邻分隔正确切分 —— 反证了 #1 的 `『』` 单独出现时会吞掉篇章名；`num="25"` 与正文 `# 25` 吻合，属"引文区首条"而非漏编号 |
| 26 | u8_7 | 춘천공립보통학교 편, 『春川郡鄉土史料』**(1915년경 발간) 第十章 沿革及名勝舊蹟 二. 名勝舊蹟.** | title="春川郡鄉土史料" / source="(1915년경 발간) 第十章 沿革及名勝舊蹟 二. 名勝舊蹟" | 🔴 **P1** | **章节定位 `第十章 …` 被塞进 source**；`year=1915` 从括号内年取，未标"경(约)" |
| 27 | u8_8 | 문화재청·강원도·춘천시·강원문화재연구소, 『문화유적 분포지도 춘천시』, 2003, 292쪽. | ✅ | `authors` 4 段正确 |
| 29 | u9_6 | 충북대 중원문화연구소, 『춘천 삼악산성』, 2000, 249·250쪽. | ✅ | |
| 30 | u9_6 | **『춘천 삼악산성』(충북대 중원문화연구소, 2001)과** 노병식의 「춘천 삼악산성의 축조양상」(…) 참조. | title="춘천 삼악산성" / source="(충북대 중원문화연구소" | 🔴 **P0** | **一条里含两部文献，只抽第一部**；`authors=[]`（`authors_raw=null`）；`source` 以未闭合 `(` 开头；**年份 2001 与 #29 的 2000 冲突**（同一本书两种记法），两条无法合并 |
| 38 | u11_6 | 심재연, 「봉의산성 소고」, 『춘주문화』 제15호, 춘천문화원, 2000. | ✅ | |
| 44 | u13_10 | **이하의 蒙古 침입에 관한 내용은** 『江原道史』(江原道, 1995)의 제4편 제5장 제3절의 「對蒙抗爭期의 江原」(周采赫 집필)을 주로 참고하였다. | title="江原道史" / source="(江原道" / `authors`=["이하의 蒙古 침입에 관한 내용은"] | 🔴 **P0** | **散文引导语进作者数组**；`source` 断在 `(江原道`；**`제4편 제5장 제3절` 的卷/章/节结构全部丢失**；`level=high` —— 错得最狠却给了 high |
| 46 | u15_7 | **이하 왜구침입과 관계된 주요 내용은** 『江原道史』(江原道, 1995, 609~612쪽)와 『高麗史』를 참고하여 서술함. | 同 #44 / `authors`=["이하 왜구침입과 관계된 주요 내용은"] | 🔴 **P0** | 同型；**`609~612쪽` 页区间未抽**（`~` 分隔符不在 `PG_RANGE` 里）|
| 51 | u18_6 | 春川市, 『春川百年史』, 1996, 2,189쪽. | ✅ | **`2,189쪽` 千位逗号页码未误判为卷期**（好）；但 `pages=null` |
| 65 | u21_7 | 심경호, 茶山과 春川 , 강원대학교 출판부, 1995, **pp.61～62·p.171 참조** | title="pp.61～62·p.171 참조" | 🕳 **P0** | **页码区间成了题名！** `title` 完全错；`authors=["심경호","茶山과 春川"]` —— **题名进了作者数组**；`source=null`。原因：题名两侧被空格与引号剥光后 `QUOTE_TITLE` 不中，`tailflow` 分层时 `hsegs[1]` 恰好是 `茶山과 春川`，但上游 `_seg_clean`/`_unwrap_tail_cite` 把 `pp.61～62·p.171 참조` 当成了后半段。`level=low` |
| 68 | u22_6 | **춘천 이궁에 대한 연구는** 오영섭, 「春川離宮殼」, 『아시아문화』 12, …, 1996에 자세하다. | title="春川離宮殼" / source="『아시아문화』 12, 한림대학교 아시아문화연구소" | 🔴 **P1** | **散文引导语 `춘천 이궁에 대한 연구는` 进作者数组**（`authors_raw="춘천 이궁에 대한 연구는 오영섭"`）；`12` 未被拆为卷次；OCR `離宮殼`（应为 `離宮攷`，**与 references #3 `春川離宮攷` 不一致**）；`level=low` |
| 78 | u25_7 | 이하 전투 개요에 대한 것은 「한국전쟁초기, 춘천·홍천전투가 전세에 미친 영향」…의 <제5장 3절 춘천-가평전투> 부분 참조. | title="한국전쟁초기, 춘천·홍천전투가 전세에 미친 영향" / source="(김기옥, 『軍史』" / `year="2005"` | 🔴 **P0** | **一年内含 4 部文献，只抽第一部**；`year=2005` 取自**最后的** `6·25戰爭史 2 (2005)`，与被抽题名的 1986 年不符；`authors` = 引导语 5 词；`source` 以未闭合 `(` 开头；`level=high` |

**本篇小结**：27 条 references 中完全正确约 12 条（44%）；22 条脚注中完全正确约 8 条（36%）。**两套体例混排是核心病因**，`source` 单槽是系统性缺陷。

---

### 1.4 `KCI_FI000895567_2003`（41 条，numbered，英文地球化学著录）

> 体例：`Author, A. B., 年, "题名," 期刊, v. N, pp. a-b.` —— **英文正规体例，理论上最适配**。但本篇 41 条 **`num` 全部为 `null`**，说明条目切分层完全没认出编号。

| # | uid | 原文要点 | 判定 | 问题 |
|---|---|---|---|---|
| 1 | u12_14 | **김정진, 김수진, 김윤경, 2003, "…", 자원환경지질, v. 36, pp. 103-109. 박상준, 황지호, 최선규, 오창환, 1999, "…" …** | title=김정진条 / `year="1999"` / source=자원환경지질 | 🔴 **P0** | **一条记录里塞了两篇文献**（김정진 2003 + 박상준 1999，OCR 连成一段无分段）。`year` 取了串尾的 1999（`+3` 尾收尾规则），**与被抽题名的 2003 不符**；`authors_raw="김정진, 김수진, 김윤경"` 只含第一篇作者 |
| 2 | u12_14 | Barcelona, M. J., …, 1989, "…," Water Resources Research, v. 25 | ✅ title/source 对 | 🔴 **P0** | `authors=["Barcelona M. J","Holm T. R","Schock M. R","George G. K"]` —— **姓与缩写被并成一段 `Barcelona M. J`**（逗号把 `Barcelona` 与 `M. J.` 切开后又被 `_split_authors` 的 `Surname, X. X.` 合并逻辑粘回，但合并成 `Surname + initial` 而不是标准 `M. J. Barcelona`），下游无法区分姓与名 |
| 3 | u12_14 | Bigham, J. M., … | 同 #2 | 🔴 P0 | 同型 |
| 4 | u12_14 | Bowell, R. J. and Bruce, I., 1995, "…," **Cornwall**, Applied Geochemistry, v. 10 | 🕳 **P0** | **`source="Cornwall, Applied Geochemistry"`** —— 原文题名后有小地名 `Cornwall` 插在期刊前，被当作来源的一部分；`title` 尾部留着逗号（`…Levant Mine,`）|
| 5 | u12_14 | Brown, G., 1980, "Associated minerals," **In: Brindley, G. W. and Brown, G., (eds) Crystal structures of clay minerals and their X-ray identification**, Mineralogical Society, London, pp. 361-410. | 🕳 **P1** | **书章型**：`source` 把 `In: 编者, (eds) 书名` 整段装成来源，**书名 `Crystal structures…` 没有独立字段可放**，`volume=null`；`title` 尾部逗号 |
| 6–10 | u12_14 / u13_3 | Carlson/Childs/Cravotta/Dixon/Dixon/Doner（6 条书章+期刊混排） | ⚠️ | **共 11 条 `In: …` 书章**（#5、#8、#9、#10、#11、#13、#14、#27、#29、#31、#40），`source` 全部是 `In: 编者, (eds) 书名, 出版社` 整串 —— **同 #5**。这是 `source` 单槽的必然结果：一本书的"书名+编者+出版社"三要素只能挤在一个串里 |
| 11 | u13_3 | Grim, R. E., 1968, **Clay mineralogy**, McGraw-Hill, 596p. | ✅ | ✅ | 无引号书型，`grammar="noquote"`，title/source 对 |
| 12 | u13_3 | Hem, J. D., 1985, "…," **U. S Geological Survey Water-Supply Paper 2254.** | ⚠️ | **`source="U. S Geological Survey Water-Supply Paper 2254"`** —— `U. S` 中间空格使 `US` 被切成 `U.`+`S`（OCR 问题）；报告系列号保留在 source（可接受）|
| 13 | u13_3 | Hounslow, A. W., 1995, Water quality data: analysis and interpretation, Lewis Publishers, 397p. | ✅ | ✅ | |
| 14 | u13_3 | Hurlbut & Klein, 1977, Manual of mineralogy, John Wiley & Sons, 532p. | ✅ | ✅ | |
| 15 | u13_3 | Kehew, A. E., 2001, Applied chemical hydrogeology, Prentice Hall, 368p. | ✅ | ✅ | |
| 16 | u13_3 | Lazaroff, N., Sigal, W. and Wasserman, A., 1982, "…," Applied and Environmental Microbiology, v. 43, **No. 4**, pp. 924-938. | ✅ | `issue="4"` 正确 | |
| 17 | u13_3 | Lindberg & Runnells, 1984, "…," Science, v. 225 | ✅ | ✅ | |
| 18 | u13_3 | McCarty, D. K., Moore, J. N., and Marcus, W. A., 1998, "…," Applied Geochemistry, **v. 13. pp. 165-176.** | ✅ | `pages="165-176"` 正确（`v. 13.` 后的句点未阻断）|
| 19 | u13_3 | Mchardy & Birnie, 1987, "…," **In : Wilson…**（`In` 与 `:` 间有空格） | ⚠️ | `source="In : Wilson…"` —— **`In :` 变体未归一**，与 `In:` 并存 |
| 20 | u14_2 | Nordstrom, D. K., **1982a**, "Aqueous pyrite oxidation…," In: Kittrick… | ⚠️ | `year="1982a"` 正确保留后缀；`source` 书章整串 |
| 21 | u14_2 | Nordstrom, D. K., **1982b**, "The effect of sulfate…," *Geochimica et Cosmochimica Acta*, v. 46 | 🕳 **P0** | **`title="Geochimica et Cosmochimica Acta"`、`source="b, \"The effect of sulfate…*, *Geochimica et Cosmochimica Acta*"` —— 题名与期刊完全对调！** 根因：`ITALIC_TITLE` 在 `QUOTE_TITLE` 之前命中（`refs.py:150-157` 顺序），而 `1982b` 后缀使 `myear.start()` 落在 `1982b` 的 `1982` 处，`rest` 从 `b` 起算。**这是 A 类错位 + b 类后缀 + 斜体题名三重叠加** |
| 22 | u14_2 | Park, C. Y., …, **2000a**, "…," *Journal of the Korean Institute…*, v. 37 | ⚠️ | `source="*Journal of the Korean Institute…*"` —— **斜体星号未剥** |
| 23 | u14_2 | Park, C. Y., …, **2000b**, "…," *Economic and Environmental Geology*, v. 33 | ⚠️ | 同 #22（斜体星号）+ 后缀保留 |
| 24 | u14_2 | Roussel, C., Bril, H. and Fernandez, A., 1999, "…," *Earth & Planetary Sciences*, v. 329 | ⚠️ | 同 #22；`Earth & Planetary Sciences` 的 `&` 未触发 `and` 拆分（正确，它是刊名内 `&`）|
| 25 | u14_2 | Russell & Fraser, 1994, "Infrared methods," In: Wilson… | ⚠️ | 书章型；`source` 内含 `*Clay mineralogy…*` |
| 26 | u14_2 | Salomons, W., 1995, "…," *Journal of Geochemical Exploration*, v. 52 | ⚠️ | 斜体星号 |
| 27 | u14_2 | Sanchez-Moral, S., Ordonez, S., **Garcia del Cura, M. A.**, Hoyos, M. and Canaveras, J. C., 1998, "…," *Chemical Geology*, v. 149 | 🕳 **P0** | **`authors` = `["Sanchez-Moral S","Ordonez S","Garcia del Cura","M. A. Hoyos","M. Canaveras","J. C"]` —— 6 人变 6 段但切点全错**：`Garcia del Cura, M. A.` 的姓被切成 `Garcia del Cura` 与 `M. A. Hoyos` 两段，`Canaveras, J. C.` 被切成 `M. Canaveras` 与 `J. C`。**复合姓 + 逗号缩写合并逻辑失灵** |
| 28 | u14_2 | Schwertmann, U. and Fischer, W. R., 1973, **"Natural "amorphous" ferric hydroxide,"** | 🕳 **P0** | **`title="Natural"`、`source="amorphous\" ferric hydroxide,\" *Geoderma*"`** —— **题名内部的配对双引号 `"amorphous"` 截断了题名**，括号内嵌引号未被识别为嵌套。`volume="10"` 仍抽对。`grammar="quoted"` |
| 29 | u14_2 | Smykatz-Kloss, W., 1974, "…," **Springer-Verlag Berlin Heidelberg New York, 185p.** | ⚠️ | `source` 含出版社与总页数 `185p`（原文如此 ➖ 可接受）|
| 30 | u15_? | Sullivan, Mattigod & Sobek, 1986, "…," *Environmental Science and Technology*, v. 20, No. 10 | ⚠️ | 斜体星号 |
| 31 | u14_2 | Sullivan, Yelton & Reddy, 1988, **"Iron sulfide oxidation and the chemistry of acid generation":** *Environmental Geology and Water Science*, v. 11, No. 3 | ⚠️ | `source=": *Environmental Geology and Water Science*"` —— **前导冒号未剥**，`title` 尾部冒号也被剥掉（丢信息）|
| 32 | u15_? | Timpson, M. E., … 1986, "…," Soil Science Society of America Journal, v. 50 | ✅ | ✅ | |
| 33 | u14_2 | Wagner, D. P., …, 1982, "…," In: Kittrick… | ⚠️ | 书章型 |
| 34 | u15_? | Whittig, Deyo & Tanji, 1982, "…," Soil Science Society of America Journal, v. 46 | ✅ | ✅ | |
| 35 | u14_2 | Wilson, M. J., 1987, "X-ray powder diffraction methods," **In : Wilson…** | ⚠️ | `In :` 变体 |
| 36 | u14_2 | Yu, J. Y. and Heo, B., 2001, "…," Applied Geochemistry, v. 26 | ✅（字段）/ 🔴 互证 | **`ch_agree.authors.agree=0.0`**，`variant` 三条读法都返回 `Nordstrom, D. K., 1982a, "Aq` —— **`_field_agree` 的 `_near_snip` 没定位到 `Yu`，而是给了整块开头的 Nordstrom**。该条 `level=low`（降档对了），但**证据本身是错的**，评审者会被误导 |

**本篇小结**：41 条中 `title/source` 都对约 15 条（37%）；**`num` 全 null（0/41）**；**`uid` 只有 3 个不同值**（`u12_14`/`u13_3`/`u14_2`），**整页级跨页块被当成一整条**，41 条共用 3 个版面锚点 —— **位置信息在这篇里彻底失效**。`level` 分布：low 12 / mid 29 / high 0。

---

## 2. 跨样本共性

### 2.1 共性一：切分层把「体例」当成了「一种固定层数」，四种体例共用同一套段号（**最高频、影响面 100%**）

`refs.py:197-254` 的抽取逻辑是：先按引号/斜体命中题名，再"题名后到第一个卷期页标记之间"取 `source`（单槽）。**一旦体例不是"作者-题名-来源"三层，就会整体错位一格**。

| 体例 | 样本 | 真实层数 | 被当成 | 后果 |
|---|---|---|---|---|
| 英文 `Author, 年, "题名," 期刊, v.N` | `KCI_FI000895567_2003` | 4 层 | 3 层 | 当地名插在期刊前（`Cornwall`）→ 串进 source |
| 韩式 `저자(년), 표제, 논문명, 출처, 출판사` | `KCI_FI001632041_2010` 論文段 | **5 层** | 3 层 | **题名/source 整体错位一格**（`u35_7`、`u35_17`） |
| 书章 `Author, 年, "章名," In: 编者, 书名, 出版社` | `KCI_FI000895567_2003` 11 条 | 6 层 | 3 层 | 书名/编者/出版社挤成 source 一个串 |
| 软件手册/机构报告 `Author, 手册名, 出版社, 年` | `KCI_FI001892171_2014` #8/#9 | 3 层但**无标记** | — | `title/source` 双 null |

**这不是"个别难例"，而是"体例识别缺失"**：`grammar` 字段只回答了"题名用什么符号包着"（`quoted/italic/remnant_quote/notice/noquote/unknown`），**回答不了"条目有几段、每段是什么"**。

### 2.2 共性二：`source` 单槽是系统性的结构缺陷（**跨 4 篇，至少 31 条受损**）

`source` 定义是"容器"，但现实里"容器"至少含 **期刊名 / 卷 / 期 / 出版社 / 编者 / 会议名 / 地点 / 日期** 七要素，而代码把前四个有独立字段（`volume/issue/pages/doi`），后三个**没有落脚点**，只能塞进 `source` 或丢弃：

| 丢失的子要素 | 样本数 | 例子 |
|---|---|---|
| 出版者/主办单位 | ≥ 6 | `u35_20` 丢 `강원대학교 대학원`；`u36_9/u36_10` 丢 `강원대학교 인문과학연구소 주관` |
| 会议日期与地点 | 2 | `u36_8`、`u2_7`（`2005년 11월 19일(개최장소 : …)` 塞进 source）|
| 书名（书章型） | 11 | `KCI_FI000895567_2003` 全部 `In: …` |
| 编者 | 11 | 同上 |
| 卷次 `12`（无 `제N호` 形态） | 2 | `u35_22`、`u22_6` |
| 篇章名（被书名号覆盖） | 3 | `u35_6`、`u5_5`、`u36_6` |

**反例证明是结构问题而非规则问题**：`u8_6` 因为原文把 `」『` 紧邻写成 `「춘천군의 도요지 및 관방유적」『춘천군의 역사와 문화유적』`，篇章名与书名**都被抽出来了**（`title` + `source` 恰好两层）；`u35_6` 只写 `『춘천군의 역사와 문화유적』`，篇章名就直接消失。**同一个逻辑，输入差一个字符就从中对到全错。**

### 2.3 共性三：`ch_agree` 只能证明"字读对了"，证明不了"段切对了" —— 且 `title` 探针在长条目上会误命中

`_field_agree`（`refs.py:601-604`）判题名的口径是「`title` 去引号折叠后取前 30 字，是否出现在该渠道读法全文里」。**它测的是"这串字符存在于版面"，不是"这串字符是题名"**：

- **命中率虚高**：`u35_21`（错位那条）`ch_agree` 全 1.0；`u22_6`（引导语进作者）`authors.agree=1.0`；`KCI_FI001892171_2014` 的 #4/#5（`uid` 冲突、题名 OCR 错）里绝大多数 1.0。
- **长块必错**：`KCI_FI000895567_2003` 的块 `u12_14` 是一整页（`box` 高 723pt），里面**所有**条目的题名都在读法全文里 → **前 30 字探针必然命中** → `agree=1.0`。只有 `Yu` 那条因首作者 `Yu` 在块首没出现才掉到 0。
- **反例**：摘要层（`abs.py`）的 `ch_agree` 有 `uid_head` 锚点，题名层没有 —— 口径不一致。

**结论**：`ch_agree=1.0` **不能作为"字段正确"的证据**，只能作为"字形被三家一致读出"的证据。而 §2.6 会说明 `level=high` 恰恰是从它推出来的。

### 2.4 共性四：`_validate_fields` 的容错方向是"拦住空值"，不是"拦住错值"

`refs.py:280-305` 七项检查里，只有两项会动到"已抽出的值"（题名碎片、题名与来源雷同），而这两项都**靠长度和严格相等**：

| 错值 | 应被拦 | 实际 | 原因 |
|---|---|---|---|
| `title="377쪽"`（页码） | 题名碎片 | ❌ 放过 | 长度 4、含韩文 → 不触发 |
| `title="p.201"`（页码） | 题名碎片 | ❌ 放过 | 长度 7、含字母 → 不触发 |
| `title="pp.61～62·p.171 참조"` | 题名碎片 | ❌ 放过 | 同上 |
| `title="한국전쟁초기"` vs `source="춘천·홍천전투가…"` | 题名与来源错位 | ❌ 放过 | 只查**严格相等**，近错位不查 |
| `source="Cornwall, Applied Geochemistry"` | 来源含非载体成分 | ❌ 无此检查 | 无规则 |
| `authors=["American","Psychiatric","Association"]` | 作者含碎片 | ❌ 放过 | 只查"纯数值" |

**而被拦下的错值一律 `title=None`**（`refs.py:793-794`），于是错值**变成空值**，`_validate_fields` 报的 `题名缺` 也看不出是"原文没有"还是"抽错了"。

### 2.5 共性五：条目边界失败 → 整条丢 / 两条串 / 一年含多文献（**21 条受损，其中 5 条真丢**）

| 形态 | 样本 | 损失 |
|---|---|---|
| 条内串烧（一条 raw 含两篇文献） | `KCI_FI000895567_2003` #1；`KCI_FI001632041_2010` u9_6(#30)、u25_7(#78)；`KCI_FI001892171_2014` #22 | 5 篇文献被"半吞"（后半篇的题名/作者全错） |
| 整条丢失 | `KCI_FI001892171_2014` 脚注 23（被并进 22） | 1 条 |
| 整段正文并入脚注 | `KCI_FI001892171_2014` 脚注 34 | 1 条（`source` 变正文） |
| 散文引导语并入 | `u13_10(#44)`、`u15_7(#46)`、`u22_6(#68)`、`u25_7(#78)`、`u7_6(#22)` | 5 条（`authors` 变成散文片段） |
| `num` 全失 | `KCI_FI000895567_2003` 41 条 | 41 条（100%）|
| `num` 分两段各 1..13/1..14 | `KCI_FI001632041_2010` | ➖ 正确（设计如此，`num_sequence.runs` 已识别）|

**根因链**：OCR/版面层把"整页参考文献"识别成**一个块**（`KCI_FI000895567_2003` 的 `u12_14` 就是），`_split_entries_block` 依赖**行首** `^N.` / `^[N]` 标记切条；一旦标记不在行首（被 OCR 合并成流）或体例是 authoryear（无编号），就只能退化成"整个块 = 一条"。`KCI_FI001892171_2014` 的 `uid` 冲突（多条共用 `u29_4`/`u30_9`/`u7_4`）就是同一现象的另一面。

### 2.6 共性六：置信分级与真实错误**负相关**（最危险的一条）

把 113 + 22 条按 `confidence.level` 与"字段是否真错"交叉：

| 文档 | high | mid | low | 其中**实际字段错误**的 high 条目 |
|---|---|---|---|---|
| `13625974` | 21 | 2 | 0 | 0（24/24 对，只有 4 条 P1）|
| `KCI_FI001892171_2014` | ~10 | ~10 | ~2 | **≥4**：`u31_4`（检索日当年份）、脚注 22（条串烧）、脚注 12 之外的其他… |
| `KCI_FI001632041_2010` | 14 | 3 | 10 | **≥3**：`u35_7`（错位）、`u13_10`(#44 引导语)、`u25_7`(#78 多文献年）|
| `KCI_FI000895567_2003` | 0 | 29 | 12 | 0（**全篇没给过 high**，因为 41 条块级全 mid）|

**规律**：
- `13625974`（体例规整）→ **high 21/23，且全对**，置信分级有效。
- `KCI_FI000895567_2003`（体例规整但块级全 mid）→ **high 0**，分级失效（全被块级拖到 mid）。
- `KCI_FI001632041_2010`（体例混排）→ **high 14 条里有 3 条整条错**。
- `KCI_FI001892171_2014`（最乱）→ **high 里混着"检索日当年份"这种硬错**。

`_grade_entry`（`refs.py:611-686`）的判据只有三样：**块级继承 / `agree<0.67` / `issues`**。这三样在 §2.3、§2.4 已证明都拦不住"切分错位"。所以 **`level` 实质衡量的是"OCR 是否稳定"，而不是"字段是否正确"**。

### 2.7 共性七：`llm_pending_fields` 只挑空值，**26 条"错值"没有任何一层上报**

`llmrep.py` 的待补清单规则 = "字段为 None"。四篇合计 `pending` 约 20 项（`13625974` 1 项、`KCI_FI001632041_2010` 13 项、`KCI_FI001892171_2014` 7 项…），**`called=0, applied=0, enabled=false`**。而本报告 §1 标出的 **P0 错值 30 余条，一条都不在 pending 里**：

- `u35_7` 的 `title="한국전쟁초기"` 不是 None → 不进 pending
- `u21_7` 的 `title="pp.61～62·p.171 참조"` 不是 None → 不进 pending
- `u31_3` 的 `year="2014"` 不是 None → 不进 pending

**这是"最该问的反而没问"**，与单条样例分析的盲区 ⑦ 完全一致，且在 4 篇上复现。

### 2.8 特性（各篇的个性问题）

| 特性 | 文档 | 说明 |
|---|---|---|
| **体例最规整、结果最好** | `13625974` | bracketed + `vol./no./pp.` 齐全，`high` 21/23。唯一问题是公司名内逗号（4 条）|
| **`num` 100% 丢失** | `KCI_FI000895567_2003` | 整页块切不开；41 条共用 3 个 `uid` |
| **`b` 后缀破坏体例识别** | `KCI_FI000895567_2003` #21 | `1982b` 让 `rest` 从 `b` 起算 → 题名/来源对调。**这是可复现的确定性 bug** |
| **韩文 `외` 未识为 et al.** | `KCI_FI001892171_2014` 多条 | `authors` 里出现 `"외"`、`et_al=false`。与英文 `etc.` 处理不对称 |
| **机构名被空白切碎** | `KCI_FI001892171_2014` #6/#7/#9 | `references[]` 路径 `latcorp=False`，`American Psychiatric Association` 变 3 人 |
| **检索日当年份** | `KCI_FI001892171_2014` #21/#22 | 网络资源型，`year` 取错且 `level=high` |
| **书章无独立字段** | `KCI_FI000895567_2003` 11 条 | `In:` 整串进 source |
| **`《》『』` 覆盖篇章名** | `KCI_FI001632041_2010` #1/#13脚注/#36 | 书名号优先于引号，篇章名被丢 |
| **斜体星号残留** | `KCI_FI000895567_2003`、`KCI_FI001892171_2014` | `source="*Psychological bulletin*"` 直接带 `*` 输出 |
| **`uid` 冲突（一页多块共框）** | 除 `13625974` 外三篇 | 详情见 §2.5 |

---

## 3. 归因：一张图看清

```
版面层(unify) ── 整页参考文献=1块 / 一页多块共框
      │
      ├─► 条目切分层(_split_entries_block)
      │      └─ 只认行首 [n] / N. ；authoryear 与"被合并成流的编号"→ 整块=1条
      │            └─► num 全 null / 一条含多文献 / 条目串烧 / uid 冲突
      │
      ├─► 字段抽取层(_extract_fields)
      │      ├─ 体例固定为"作者-题名-来源"三层 → 五段式/书章 整体错位一格
      │      ├─ source 单槽 → 出版者/编者/会议/卷次 无处可放
      │      ├─ QUOTE_TITLE 只看"" ' ""『』「」"，不看体例 → 书名号覆盖篇章名
      │      └─ 无体例识别 → grammar 回答"有没有引号"而非"几段"
      │
      ├─► 校验层(_validate_fields)
      │      └─ 只拦长度/严格相等；错值不是空值 → 放过
      │
      ├─► 互证层(_field_agree)
      │      └─ 字符串存在性（前30字）→ 长块必中 → agree 虚高
      │
      └─► 分级层(_grade_entry)
             └─ 继承块级 + agree + issues → 上面三层的错都被继承 → high 误报
                    │
                    └─► LLM 层(llm_pending_fields)
                           └─ 只收空值 → 错值永不上报
```

**一句话**：**问题不在某一处规则，而在"没有体例识别的分字段体系 + 单槽 source + 只能看见空值的校验链"**。改其中任何一层都不够，错误会从相邻层以另一种形式冒出来。

---

## 4. 建议（按投入产出排序）

### P0（不改则数据不可用，建议立即处理）

1. **加体例识别层，产出"段角色序列"**
   - 新增 `entry_layout` 字段（或复用 `grammar` 扩展）：`en_journal` / `en_book` / `en_chapter` / `ko_5seg` / `ko_book` / `ko_thesis` / `notice` / `unknown`
   - 段角色标注：`author(year) → [표제] → title → container → publisher`，**每段一个角色**，而不是固定三段
   - 判据：`In:` 前缀 + `(eds)` → chapter；`저자(년),` + ≥4 逗号段 → ko_5seg；`석사학위논문/박사학위논문` → thesis
   - **验证锚点**：`u35_7`（应改为 `title=춘천·홍천전투가 전세에 미친 영향, source=軍史, issue=12`）；`u35_6`（应改为 `title=춘천군의 도요지 및 관방유적, source=춘천군의 역사와 문화유적`）

2. **`source` 拆成结构体**（至少加 3 个字段）
   - `container_title`（期刊/书名/会议名）
   - `publisher`（出版社/主办单位）
   - `editors`（书章编者）
   - `source` 保留为**原始串**（向后兼容），新字段供下游消费
   - **验证锚点**：`KCI_FI000895567_2003` 的 11 条 `In:` 应有 `container_title=Acid sulfate weathering, editors=Kittrick J. A. …`

3. **修条目边界**：整块无行首编号时，按"合法年份 + 作者串模式"二次切分
   - 对 `KCI_FI000895567_2003`：`\b\d{4}[a-z]?,\s` 前后锚定，61 处年份 → 至少能切出大部分条目
   - 对脚注 `(23)` 圆括号形态加入 `FOOTNOTE_CITE`
   - 对"无标记段落并入前一条"，加"若该段 ≥N 字且不含年份/卷期页特征，则不并入（另起散文块）"
   - **验证锚点**：`KCI_FI000895567_2003` 的 `num` 应为 1..41；`KCI_FI001892171_2014` 脚注 23 应独立成条

### P1（准确性，建议下一迭代）

4. **校验层加"位置合理性"检查**（不是长度，是模式）：
   - `title` 匹配 `^pp?\.\d|^\d+쪽|^p\.\d` → 题名碎片（拦 `377쪽`、`p.201`、`pp.61～62…`）
   - `source` 含 `In:`/`(eds)` 且无独立 `container_title` → 报"书章未拆"
   - `source` 与 `title` **长度比 > 3** 且 source 不含载体特征词（`Journal|학회|회지|研究|Press|University|제\d+[권호집]`）→ 报"题名来源疑似错位"（可命中 `u35_7`）
   - `authors` 任一段含空格（≥2 词）且非机构尾部 → 报"作者疑似含引导语"（可命中 `u13_10`/`u15_7`/`u22_6`）

5. **修 `ch_agree` 探针**：
   - `title` 探针不该用前 30 字，应改用**该字段在块读法中的相对位置**做约束（复用 `abs.py` 的 `uid_head` 思路）
   - 长块（`box` 高 > 页高 50%）内所有 `agree=1.0` 应直接标"探针失效"而非"一致"
   - 误命中时（`Yu` 那条给出 `Nordstrom` 片段）应把 `variant` 标为 `unresolved` 而不是当证据

6. **修 OCR/符号层**（低成本高收益）：
   - `_split_authors` 加 `외` → `et_al=true` 并剔除（与 `etc.` 对称）
   - `references[]` 路径开 `latcorp`，或加"全大写/首字母大写 ≥3 词 + 机构尾词 → 单一作者"
   - 输出前统一剥 `*…*` 斜体标记
   - `In :` 归一为 `In:`
   - 页区间支持 `~`/`～`（`609~612쪽`）

7. **`_year_pick` 拒收检索日**：`retrieved|최종 검색|accessed|URL` 附近的年份 → 排除；网络资源型 `year` 允许 None

### P2（口径规范，可排期）

8. **斜体/引号标记入产品前剥离**（`source="*Psychological bulletin*"` 不应带 `*`）
9. **`title` 尾部标点统一剥离**（`…Levant Mine,`、`…acid generation`）
10. **`grammar` 语义扩展**：明确区分"体例无引号"与"引号被 OCR 吞"（`noquote` vs `lost_quote`），后者应进 `llm_pending_fields`
11. **`llm_pending_fields` 增加"置信矛盾"来源**：`level=high` 但触发了 §4.4 的"位置合理性"报错 → 进 pending

### 建议的验证方式

- `<4 篇语料>` 在 `metapy/refs.py:309-373` 有 `SELFTEST` 冻结口径，**改规则后先跑 `python -m metapy.refs`**。但注意：现有 SELFTEST 的期望值**大多是"当前错误行为"**（如 `u"10. 강원도(1995), 江原道史(역사편)"` 期望 title=整串），改动时应**同步更新期望值**，否则自检会保护错误。
- 本报告每条 P0 都给了 `uid` + `raw` 原文，可直接作为新 GT 用例加入 SELFTEST。

---

## 附录 A：各文档 `confidence.level` 分布

| 文档 | references high/mid/low | footnote_refs high/mid/low | `llm.pending` | `block_review=true` 条数 |
|---|---|---|---|---|
| `13625974` | 21 / 2 / 0 | —（无脚注） | 1 | 2（`u9_15` ×2，跨页拼接）|
| `KCI_FI001892171_2014` | 10 / 8 / 4 | 3 / 8 / 14 | 7 | 11（含 5 条共 `u30_9`）|
| `KCI_FI001632041_2010` | 14 / 3 / 10 | 5 / 7 / 10 | 13 | 7 |
| `KCI_FI000895567_2003` | 0 / 29 / 12 | —（无脚注） | 0 | **41（100%）** |

## 附录 B：本报告未覆盖的部分

- `title` / `title_en` / `authors`（文前）/ `affiliations` / `abstract` / `keywords_lines` 的文前层（`front.py` / `abs.py`）未逐条核查，本报告只涉参考文献与脚注引文层。
- `KCI_FI001892171_2014` 的 `references[]` 在 `metadata.json` 中有 22 条、`metadata_audit.json` 中同样 22 条，但**正文实际有 ~44 条**（`num` 由 `2` 直接跳到 `12`、`12→21`、`22→24`、`24→34`、`34→36`）—— **约 22 条整条丢失**，详见 §2.5。**这可能是最严重的问题单点**，建议优先在本篇上做条目边界回归。
- `KCI_FI001632041_2010` 的 `references[]` 第 14–27 条（`num` 1..14 的第二段）与第一段（`num` 1..13）是**同一篇论文的两个子文献表**（國史 / 論文），`style` 却只有一个值 `numbered`，`num` 重复出现 1、2、3… —— 下游若把 `references[]` 当字典按 `num` 索引会**静默覆盖**。
