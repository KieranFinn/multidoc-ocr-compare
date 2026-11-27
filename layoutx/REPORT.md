# 版面模板抽取原型：返工后总结报告

## 最终结论

**版面模板方向成立，但不能宣称“全库 0 退化”或“新版已全面优于旧版”。** 网格署名、渠道仲裁和绕过 unify 过并的价值已由锚点真实证明；作者超集污染和对比器偏盲也真实存在，已据此返工。此前报告中的“0 退化”头条数字正式撤回。

主库 953 个纯数字 doc-id 全部重跑，排除 9 个归档目录。以下计数是规则与人工复核后的工作台账，不是准确率估计：

| 分类 | 篇数 |
|---|---:|
| 新版更优 | 710 |
| 一致 | 224 |
| 双错且不同 | 16 |
| 新版更差 | 3 |

## 本次返工修正了什么

- 删除按渠道作者数量取最多结果的选择逻辑，改为正面署名结构、来源行覆盖率、跨渠道票数与同人异写折叠。
- 作者证据限制到首页署名区；名字相邻的星标、罗马名、角色和机构配对才算结构证据，长正文/摘要行不再凭任意子串支持作者。
- 判差对称化：新版非空但错误可以判差，不再要求新版为空；旧版也接受同样的署名结构检查。
- 删除 `13569913`、`13569417` 的硬编码分类覆盖。锚点结果由实际字段和渠道证据计算。
- 新增题名/关键词 token 与来源行验证、英文题名参考文献/日期/图注排除、机构角色和双语折行清理。

## 作者超集判优专项

初始问题样本中，题名形容词、章节头、统计变量和地名确实被新增为作者。返工后，当前自动超集判优为 **14 篇**，比返工前 52 篇继续下降，但仍不是全部人工验证。

固定随机种子 20261127，抽取 2 篇（超过 10%）：13564501, 13570535。逐条渠道复核均为真补名/纠错：13564501 补 조국，13570535 清掉 조록 并保留 배성중。早期审计发现的 본체론、부산/경남、알고리즘、유형분류、평가하였고 等垃圾已清除。样本不用于外推准确率。

| doc | 旧作者 | 新作者 | 本次抽样 |
|---|---|---|---|
| 13564501 | 김형엽 | 조국, 김형엽 | 是 |
| 13567203 | 이창호 | 이창호, 울트사이크 비진쿠 | 否 |
| 13567372 | 이조은 | 이조은, 조아라 | 否 |
| 13567421 | 선봉규 | 선봉규, 압둘라에바 샤흘로 | 否 |
| 13567433 | 이영민 | 김미희, 이영민 | 否 |
| 13567462 | 임영언 | 임영언, 김태영 | 否 |
| 13569417 | 김상열 | 김상열, 권용욱, 김성균, 이준하 | 否 |
| 13569425 | 안혜원 | 이민철, 안혜원 | 否 |
| 13570533 | 김종혁 | 조록, 김종혁 | 否 |
| 13570535 | 배성중 | 조록, 배성중 | 是 |
| 13570539 | 김현태, 김건 | 조록, 김현태, 김건 | 否 |
| 13570541 | 김현진, 이현진 | 조록, 김현진, 이현진 | 否 |
| 13570545 | 오효정, 장준갑 | 조록, 오효정, 장준갑 | 否 |
| 13570546 | 오경한, 양동민, 김건 | 조록, 오경한, 양동민, 김건 | 否 |

## 全部剩余“新版更差”

共 **3** 篇；这些不能由其他字段的改善抵消。

| doc | 字段与差异 | 初判 |
|---|---|---|
| 13564429 | title: 스팸 대응을 위한 성과지표 개선 방안 연구 → 스팸 대응을 위한 성과지표 개선 방안 연구 조일형, 유진호<br>title_en: None → A Study on Improving Performance Indicators for Anti-Spam Measures<br>authors: 조일형, 유진호 → <br>affiliations: 상명대학교 → 상명대학교 교수, 경영학박사, 상명대학교 교수, 공학박사 | 真实退化：题名粘 조일형/유진호，作者漏空；需按带星标署名尾段切分。 |
| 13569396 | title: 3D 레이저 스캐닝 및 모델링을 활용한 Go/No-Go 게이지 방식의 측정 선진화 사례 연구 ISSN 2671-4744 (Print) ISSN 2671-9673 (Online) → 3D 레이저 스캐닝 및 모델링을 활용한 Go/No-Go 게이지 방식의 측정 선진화 사례 연구<br>authors: R&D QA Team, Hanwha Systems, 황인규, 김해겸, 김형조, 홍은혁, 우윤형 → 황인규, 김해견, 김형조, 홍은혁, 우윤형<br>affiliations: C4ISR Systems Center → 한화시스템, 개발품보팀, 국방기술질원, 지휘정찰센터, Researcher, C4ISR Systems Center, Defense Agency for Technology and Quality | 五名真作者恢复并删除机构假作者，但 김해겸/김해견 仍有 OCR 异读，机构粒度也需复核。 |
| 13569428 | authors: Radar Production, 권용욱, 김성균, 이준하, 김홍준, 김상열 → 권용욱, 김성균, 김상열, 이준하, 김홍준<br>affiliations: Department of Electric Engineering, Kyungpook National University 김상열 → 경북대학교, 전기공학과, LIG넥스원, 레이다기술팀, Department of Electric Engineering, Kyungpook National University | 作者 5 人实际正确；判差主要来自机构识别规则对 LIG넥스원 的支持不足，不应误读为作者退化。 |

## 全部剩余双异

共 **16** 篇。每篇的完整渠道页码、框坐标和摘录均在 `diff.jsonl`。

| doc | 字段 | 旧值 → 新值 |
|---|---|---|
| 13563533 | affiliations | affiliations: 글로컬대학으로서 강원대학교 특성화 전략과 과제 → 강원대학교 기획처장. 강원대학교 법학전문대학원 |
| 13563546 | affiliations | affiliations: 지방행정연구학회 → 강원대학교 국어국문학과 |
| 13563600 | affiliations | affiliations: 현대영화연구소 → 국립부경대학교 국어국문학과 |
| 13565663 | title, affiliations | title: 전통음악 교육의 게이미피케이션(Gamification) 적용 연구: 카馬( Kahoot! )을 활용하여 → 전통음악 교육의게이미피케이션(Gamification) 적용 연구:카훗(Kahoot!)을 활용하여<br>affiliations: 서울대학교 사범대학 협동과정 음악교육전공, 서울대학교 사범대학 협동과정 음악교육전공 석사 → 서울대학교 사범대학 협동과정 음악교육전공 |
| 13567051 | title_en | title_en: Research Trends on Human Interaction Related to Media Use in Infants and Young Child… → Human Ecology Research』 55(1), 1-12. |
| 13567061 | keywords_en | keywords_en: Moon Jae-in government, defense policy, defense white paper, defense reform 2.0, MND) → Moon Jae-in government, defense policy, defense white paper, defense reform 2.0, COV… |
| 13567085 | title_en | title_en: Generational Effects and Voting Choices : The 2017-2024 Korean Elections → Eunyoung Jang·Suhyeon Lee·Jeongwoo Lee (Silla University) |
| 13567094 | title_en | title_en: None → A Study on the Educational Experience of Museum Theater Viewing : Focusing on ‘Lu Du… |
| 13567111 | title_en | title_en: None → A Case Study on Privately-Led Complex Cultural Spaces and Local Regeneration : Focus… |
| 13567139 | title_en | title_en: None → A Study on the Characteristics and Development Strategy of Arts Center in Urban and … |
| 13567236 | keywords_en | keywords_en: the Good Doctor Autism Spectrum Disorder (ASD), disability representation, diversity… → the Good Doctor, Autism Spectrum Disorder (ASD), disability representation, diversit… |
| 13567244 | title | title: 손은경 경북대학교 → # 이주와 공간 그리고 트랜스내셔널 서사 : 테브픽 바세르의 영화 <40평방미터 독일>을 중심으로 |
| 13567332 | keywords_en | keywords_en: Local Cultural Resources, Alleyways, neighborhood unit, cultural value, social value… → Local Cultural Resources, Alleyways, neighborhood unit, cultural value, social value… |
| 13568662 | affiliations | affiliations: 부산대학교 국어국문학과 대학원 → 부산대학교 국어국문학과 |
| 13568678 | title_en, affiliations | title_en: Conatus of Korean Oral Children’s Songs in Yun Seok-Joong’orks Before 1933 → Conatus of Korean Oral Children's Songs in Yun Seok-Joong's Works Before 1933<br>affiliations: 년 이전 윤석중 문학과 한국 구비 동요의 관계 → 서울대학교 국어국문학과 |
| 13568682 | title_en | title_en: A Bibliographical analysis of the First Issue of Sinsonyeon and a Study on the Meani… → A Bibliographical analysis of the First Issue of *Shinsonyeon* and a Study on the Me… |

## 已验证的锚点与机构粒度

- 13569913：题名、백서인/당정、双机构均正确。
- 13567494：리우커 + 대구가톨릭대학교 正确拆分。
- 13568651：音译名保持一人，不再切出假机构。
- 13569417：四格作者及罗马名恢复。
- 13569428：五格作者恢复，`Radar Production` 消除；机构当前为 경북대학교、전기공학과、LIG넥스원 레이다기술팀及对应英文，已去除早期角色/折行碎片。
- 13564500/13564501：原 rescue 篇作者保留或补全。
- 13569720：题名与两位作者正确，机构残片不再冒充作者。

## 字段计数与解释边界

| 字段 | 一致 | 更优 | 更差 | 双异/未裁定 |
|---|---:|---:|---:|---:|
| title | 786 | 152 | 0 | 15 |
| title_en | 533 | 393 | 0 | 27 |
| authors | 862 | 63 | 1 | 27 |
| affiliations | 585 | 271 | 2 | 95 |
| keywords | 900 | 18 | 0 | 35 |
| keywords_en | 750 | 187 | 0 | 16 |

这些数字不能当成准确度或置信区间：目前没有全量人工真值，也没有随机样本的规模误差估计。下一论应优先全量复核剩余 14 个作者超集判优及上述 19 篇非一致记录，并抽样审计其他自动清洗类别。

## 复现与安全

```bash
python layoutx/extract.py
python layoutx/compare.py
python layoutx/audit.py
python layoutx/report.py
```

代码仅在 `pipeline/layoutx/`，输出仅在 `pipeline/out_layout/`。未调用 `front_doc()`，未使用 LLM/网络。最终 mtime 自查：旧 `metapy/` 最新文件为 2026-11-26，旧 `out/` 最新文件为 2026-11-27 09:31，均未被本原型刷新。
