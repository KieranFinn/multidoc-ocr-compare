# 架构

一个文档从 PDF 到"可对照的查看器 + 归一化层"要过六步。每一步的产物都落盘, 因此可以中断、重跑、
单渠道替换, 而不必把整条链路重算一遍。

```
input.pdf
  │
  ├─① render_pages()       pages/page_N.jpg (150dpi) + .src_mtime
  │
  ├─② 获取(三路并行)      mineru / dots / paddle 各拿一份"引擎原生产物"
  │      缓存优先: out/<doc>/raw/<渠道>_api
  │      缺失才调云端 API(enabled 为假则跳过 / mineru 转 CDP 桌面版)
  │
  ├─③ adapt_*()            统一中间表示 {页号: [{"cat","box","text","img"}]}
  │      box 一律换算到 150dpi 页图像素(见 docs/calibration.md)
  │
  ├─④ merge_out.py         逐页合并三渠道 -> merged/page_N.json + merged.json + merged.md
  │
  ├─⑤ unify.py             跨渠道对齐成"归并组" -> unified/page_N.json + _unified_summary.json
  │
  └─⑥ build_viewer()       viewer_<渠道>.html / viewer_compare.html / index.html
          analyze.py        跨文档指标 + 归因报告(单独运行)
```

④⑤⑥ 都在 `pipeline.main` 内按顺序调用, 任一步失败只记日志、不阻断后续查看器
(可用 `--no-merge` / `--no-unify` 显式跳过)。顺序是硬约束: `unify` 读 `merged/`。

## 统一中间表示

所有适配器只认一个结构:

```python
{ 0: [ {"cat": "Text", "box": [x0, y0, x1, y1], "text": "...", "img": "..."}, ... ], 1: [...] }
```

- `box`: 页图像素, 左上原点, 与 `pages/page_N.jpg` 同尺度。
- `cat`: 归一化类别(Section-header / Text / Footnote / Picture / Caption / Page-header /
  Page-footer / Page-number / Table / Formula), 映射表见 `NS`、`TMAP_MINERU`、`TMAP_PADDLE`。
- `img`: 仅图片块有; 相对 `out/<doc>/` 的路径。前缀由 `_tag_img()` 按**实际来源目录名**补
  (`raw/mineru_api/` 或 `raw/mineru/`), 不再硬编码 `raw/mineru/` —— 否则删掉归档副本后
  所有图片断链。
- 新渠道只需实现一个 `adapt_xxx(path, sizes) -> dict|str`; 返回整篇字符串即进入"md 模式"
  (无块级联动, 只在查看器里给全文)。`pipeline.main` 里按 `chans` 字典分派。

## 产物树

```
out/<文档名>/
├── index.html                 总览: 每渠道页数/块数/韩文/U+FFFD + 四类查看器入口
├── viewer_mineru.html         单渠道: 原图 + 块框 + 块文本(悬停/点击联动)
├── viewer_dots.html
├── viewer_paddle.html
├── viewer_compare.html        1:3 对照: 每页四栏, 渠道分色, 仅同渠道联动
├── pages/page_N.jpg           150dpi 页图(渲染缓存) + page_N.jpg.src_mtime
├── raw/
│   ├── mineru_api/            引擎原生产物(缓存; 也是重跑复用的唯一依据)
│   ├── dots_api/
│   ├── paddle_api/
│   ├── mineru/                归档副本(仅当源不在 raw/ 下时才建, 见下)
│   └── <渠道>_calib_key       该目录用的是哪套标定(避免同字节被两套坐标解读)
├── merged/                    page_N.json + merged.json + merged.md
├── merged.md
└── unified/                   page_N.json + _unified_summary.json
```

### 缓存与重跑

- 命中条件: mineru 看 `*_content_list_v2.json` 是否存在; dots/paddle 看 `page_0.json`。
- 强制重跑: 删掉对应 `raw/<渠道>_api` 目录。
- `keep_api_zip`(默认 `false`): dots 拿到的 `api_result.zip` 是否留盘。关掉能省下整批未引用
  的页图与中间 JSON(实测单篇省 20 MB 量级), 排查上游导出问题时可临时打开。
- `preserve_raw()` 只给"源目录不在 `out/<doc>/raw` 下"的情况(CDP 桌面版导出、inbox 投放)
  复制归档副本; 源已是 `raw/<渠道>_api` 时直接跳过, 不再复制给自己。
- `fetch_mineru_api` 解压前清空目标目录, 防止 batch 复用把多个 uuid 运行目录叠加在一起。

### 标定键的判定

同一个 `raw/mineru` 目录, 可能来自桌面版(1034x1012 空间), 也可能来自云端 API(1017x1008 空间)。
若靠目录名猜, 复制/改名就会让几何整体错位, 所以:

1. 获取/归档时把实际来源写进 `raw/<渠道>_calib_key`(`write_calib_marker`);
2. 读取时 `mineru_calib_key(src)` 先读该标记, 再退回目录名启发式, 最后默认桌面版;
3. 查看器、`merge_out.py`、`unify.py` 走同一函数 —— 同一份字节不会得到两套坐标。

## 失败语义

- 某渠道拿不到产物: 该渠道查看器不生成, `index.html` 标注"未提供", 其余照常。
- 适配器返回 `None` 或页数对不上: 记日志并按实际页数截断(`pi >= len(sizes)` 即停), 不静默补空页。
- dots 的页 JSON 可能是双重编码/被截断(上游导出 bug): `_parse_page_json` 先 json, 失败再正则抢救
  完整块, 并在日志里写明"经抢救"。
- U+FFFD 计数进总览表: 它是"文本完整性"的信号, 不是渲染问题。
- `merge` / `unify` 失败不阻断查看器; 但若 `unified/` 是老产物(本次没重算), main 会用
  `_unified_summary.json` 的 mtime 与本次启动时刻比对, 过期即`rmtree` 并记日志, 避免查看器
  读到上一轮的归一化结论。

## 并发

- `pipeline.main` 用 `ThreadPoolExecutor(max_workers=3)` 并行取三渠道(网络等待为主, GIL 不碍事)。
- `webapp.py` 用 `RUN_LOCK` 把多个文档串行化: 桌面版 App 只有一个任务槽, 并发会互相踢。
- 归仓线程(`watch_mineru`)与上传线程共用 `_state_mineru.json`, 只在单进程内使用。
