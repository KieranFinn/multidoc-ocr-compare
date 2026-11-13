# -*- coding: utf-8 -*-
r"""M5 元数据字段层 —— `metapy` 包的薄壳(兼容入口, 不是常规入口)。

**常规重跑不要用这个命令。** `python stages.py <doc> <stage>` 已覆盖 m3 / refs / frefs / front /
abs / issue / cspan 全部字段阶段, 而且会校验上游产物、按依赖顺序执行、跑完还能接着建查看器。
这里之所以还留着, 只因为三个**实验阶段**只在 `metapy` 里实现, `stages.py` 没有对应段:

    --stage llm      LLM 兜底填空(与启发式结果对比)
    --stage dual     双渠道逐块对比(数据落 out_llm_trial/)
    --stage report   全量字段报告

实现已拆至 `metapy/`: common=共享阈值与工具, m3=跨页拼接, refs=①文献条目, frefs=⑤全部编号脚注,
front=②标题作者机构, abs=③摘要, llmrep=④LLM兜底+报告, cspan=行内引文句,
issue=本篇期刊/年/卷/期/页+关键词。

用法: python metadata.py <doc>... [--out out] [--stage m3|refs|frefs|front|abs|llm|dual|report|cspan|issue] [--viewer]
实体抽取(基金/软件/数据集)用 ner_lite.py(LLM 提名+程序定位; 产物与审计落 out_llm_trial/fds_extract/ner_lite/)。
"""
from metapy import main

if __name__ == "__main__":
    main()
