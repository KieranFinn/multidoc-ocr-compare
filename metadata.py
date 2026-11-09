# -*- coding: utf-8 -*-
r"""M5 元数据字段层 CLI 入口(实现已拆至 metapy/ 包: common=共享阈值与工具,
m3=跨页拼接, refs=①文献条目, frefs=⑤全部编号脚注, front=②标题作者机构, abs=③摘要, llmrep=④LLM兜底+报告,
cspan=行内引文句, issue=本篇期刊/年/卷/期/页+关键词, ner=实体抽取[数据落 out_llm_trial/])。
用法不变: python metadata.py <doc>... [--out out] [--stage m3|refs|frefs|front|abs|llm|dual|report|ner|ner_audit|cspan|issue] [--viewer]"""
from metapy import main

if __name__ == "__main__":
    main()
