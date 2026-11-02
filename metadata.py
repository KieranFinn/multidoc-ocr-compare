# -*- coding: utf-8 -*-
r"""M5 元数据字段层 CLI 入口(实现已拆至 metapy/ 包: common=共享阈值与工具,
m3=跨页拼接, refs=①文献条目, front=②标题作者机构, abs=③摘要, llmrep=④LLM兜底+报告)。
用法不变: python metadata.py <doc>... [--out out] [--stage m3|refs|front|abs|llm|report] [--viewer]"""
from metapy import main

if __name__ == "__main__":
    main()
