# 渠道配置与获取

## 配置文件的三层结构

`pipeline.load_cfg()` 按顺序合并, 后者覆盖前者(逐段浅合并, 不做深递归):

1. `channels.json` —— 入库基线: 标定值、模型名、超时、开关。**不含任何 token。**
2. `channels.local.json` —— 本地私有覆盖, 已 `.gitignore`。只写想改的键, 其余继承基线。
3. 环境变量 `MINERU_API_TOKEN` / `PADDLE_API_TOKEN` —— 覆盖对应渠道的 token(适合 CI)。

`python pipeline.py --channels <file>` 把基线整个换成指定文件, 并且**不再叠加第 2、3 层**
(离线 demo 与 CI 用它, 免得读到本机真实 token)。`cal()` 等函数每次调用都重读三层合并结果,
所以工作台改完配置下一个文档就生效。

示例:

```json
{
  "keep_api_zip": false,
  "mineru":     {"src_w": 1034, "src_h": 1012, "x_off": 17.8, "y_off": 12.3},
  "mineru_api": {"token": "", "enabled": true, "src_w": 1017, "src_h": 1008, "x_off": 13, "y_off": 8,
                 "model_version": "vlm", "language": "korean", "is_ocr": false,
                 "enable_formula": true, "enable_table": true,
                 "run_timeout": 300, "queue_timeout": 7200},
  "dots_api":   {"base": "https://dotsocr.xiaohongshu.com", "model": "dots.mocr",
                 "prompt": "prompt_layout_all_en", "enabled": true,
                 "run_timeout": 300, "queue_timeout": 7200},
  "paddle_api": {"token": "", "enabled": true, "base": "https://paddleocr.aistudio-app.com",
                 "model": "PaddleOCR-VL", "run_timeout": 300, "queue_timeout": 7200},
  "desktop":    {"exe": "", "landing": "", "port": 9223, "kill_existing": false}
}
```

要点:

- `enabled`: 各 `*_api` 段默认 `true`。置 `false` 即跳过该云端渠道; mineru 置 `false` 时
  直接落到 CDP 桌面版, 其余两家则视为"未提供"。
- `run_timeout` / `queue_timeout`: 单次推理与服务端排队各自的上限(秒)。`run_timeout` 只累计
  `running` 状态的时长, 排队久不会误杀。
- `keep_api_zip`: 是否保留 dots 下载的 `api_result.zip`(默认 `false`)。
- `src_w / src_h / x_off / y_off` 全为 `null` 或缺省时按 **identity** 处理(引擎导出的坐标已等于
  页图像素), 这是离线 demo/CI 免标定的方式, 真实引擎请按 docs/calibration.md 填实测值。
- `enable_formula/enable_table` 请写布尔值; 若写字符串, 代码会把 `"false"` 显式解析为假
  (`bool("false")` 在 Python 里是 `True`, 这是个容易踩的坑)。
- `desktop.kill_existing`: 默认 `false`。打开后启动 CDP 前会结束同名 App 进程 ——
  早期版本无条件 `taskkill /IM MinerU.exe /F`, 会连带杀掉用户自己的实例, 已改。
- 桌面版路径也可由环境变量 `MINERU_EXE` / `MINERU_LANDING` / `MINERU_CDP_PORT` 提供
  (`mineru_drive.py`), 优先于 `desktop` 段。

## 每个渠道怎么拿产物

优先级: **已有产物 → 云端 API(未禁用时) → (仅 mineru)CDP 桌面版兜底**。

| 渠道 | 缓存目录 | 命中判据 | 上游要点 |
| --- | --- | --- | --- |
| mineru | `raw/mineru_api/` | 存在 `*_content_list_v2.json` | 官方 batch 签名上传; OSS 裸 PUT 必须零额外 header, 否则 `SignatureDoesNotMatch`; `waiting-file` 超 180s 直接失败 |
| dots | `raw/dots_api/` | 存在 `page_0.json` | Gradio5 内部接口: `/config` → `/upload` → `fn1` 预热 → `fn0` 取 prompt → `fn6` 推理 → 下载 zip; SSE 必须逐行解析 |
| paddle | `raw/paddle_api/` | 存在 `page_0.json` | 异步 jobs 轮询; 产物 page JSON 是归一化坐标 |

也可以完全离线: 把引擎产物放进 `inbox/<文档名>__<渠道>/`(`__mineru`/`__dots`/`__paddle`),
`pipeline.py` 会按名称或内容签名识别。**匹配是严格的**: 目录名必须以文档名前缀开头, 不做跨文档
兜底(曾经因此把旧文档的块画到新文档上)。

## 上游已知怪癖(踩过, 记下来省时间)

- **dots**: 提交参数里的组件 state 必须是对象, 不能是字符串; 下载的 zip 内页 JSON 可能是
  双重编码(JSON 里再套 JSON 字符串), 极端情况整页被截断 —— 适配器会容错解析并正则抢救完整块。
- **MinerU**: 上传 URL 是预签名的, `urllib` 自动补的 `Content-Type` 就会导致验签失败;
  导出的 `content_list_v2.json` 键名随版本变化(新版有 `page_header_content` 等), 拍平函数按
  `*_content` 后缀兜底。
- **Paddle**: 单文件导出可能缺少页号, 适配器按块序号均匀推断页号 —— 这是有损的,
  能让用户手工指定页号就别依赖它。

## Web 工作台的配置面板

`python webapp.py` 后右上角"渠道配置"改的是 `channels.local.json`(不是入库的 `channels.json`),
白名单只放行 `paddle_api / mineru_api / dots_api` 三段, 按旧值类型强转, 跳过空串和 `_` 前缀键,
写盘用临时文件替换。下个文档生效, 不用重启。

## 新增一个渠道

1. 写 `adapt_xxx(path, sizes, cfg_key)`, 返回统一中间表示(`{页号: [块]}`)或整篇字符串。
2. 在 `channels.json` 加一段标定(`src_w/src_h/x_off/y_off`, 未知就先写 `null` 走 identity)。
3. `pipeline.main` 的 `chans` 分派里注册, 并在 `_resolve_xxx` 里实现"缓存 → API → 兜底"。
4. 若是"能力型"插件(只做附加分析, 不参与比对), 通过独立接口挂, 不要改三家主干。
5. 跑一次 `python examples/make_demo.py` 的同类离线样例, 确认查看器能出框、`merged/` 有该渠道。
