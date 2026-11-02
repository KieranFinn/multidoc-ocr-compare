# multidoc-ocr-compare

**[English](README.md) · [中文](README.zh-CN.md)**

> 这是中英双语 README 的**英文版**。中文版见 [README.zh-CN.md](README.zh-CN.md)。

Run **several OCR / document-parsing engines on the same PDF**, put every block into one coordinate
space, and look at them side by side — per page, per block, with the original scan behind them.

Built for scanned academic PDFs (Korean / Chinese / English) where single-engine output *looks* fine
but silently disagrees with itself across pages. The point is not "which engine scores higher": it is
**attribution** — when three engines disagree on a page, is the document weird or is one engine broken?

```
 PDF --> render 150 dpi pages --> fetch 3 engines (cache -> API -> desktop fallback)
                                     |
                                     v
                           adapt to {page: [{cat, box, text}]}
                                     |
                     +---------------+----------------+
                     v                                v
         per-channel viewers + 1:3 compare        merged/ + metrics
```

## Live example

A real run is checked into the repo — [`examples/13625974/`](examples/13625974/): an 11-page Korean
journal article (창의정보문화연구 2023, DOI `10.32823/jcic.9.4.202311.347`) processed by all three
engines.

| View | Rendered page | Source |
| --- | --- | --- |
| Overview + metrics | [open it rendered](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/index.html) | [index.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/index.html) |
| 1:3 comparison viewer (4 columns, hover to link boxes and text) | [open it rendered](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_compare.html) | [viewer_compare.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/viewer_compare.html) |
| Single-channel viewers | [MinerU](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_mineru.html) · [dots.ocr](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_dots.html) · [PaddleOCR-VL](https://kieranfinn.github.io/multidoc-ocr-compare/examples/13625974/viewer_paddle.html) | [viewer_mineru.html](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/viewer_mineru.html) |
| Merged text | — | [merged.md](https://github.com/KieranFinn/multidoc-ocr-compare/blob/main/examples/13625974/merged.md) |

> Why the extra column: GitHub renders `.html` files **as source code**, so a plain repo link can never
> show the viewer. The "rendered" links point at the same folder served as static files. If they are not
> live yet, enable Pages once (Settings → Pages → Deploy from a branch → `main` / `/`), or just clone
> the repo and open the file locally.

What that run shows on 11 pages:

| Channel | Blocks | Korean chars | U+FFFD |
| --- | ---: | ---: | ---: |
| MinerU (batch API) | 200 | 8,929 | 0 |
| dots.ocr (Gradio 5) | 196 | 9,024 | 0 |
| PaddleOCR-VL-1.6 (cloud job) | 214 | 9,032 | 0 |

Text capture agrees within **1.2 %** across the three engines, yet block counts differ by ~9 %:
splitting granularity is not text quality. The unified pass reports 610 raw blocks → 176 logical
groups, `one_to_one` 0.898, `coverage` 1.0, `orphan_rate` 0.0, and attributes every disagreement to
either the document (all three engines agree on the oddity) or a single engine.

> Prefer locally? Clone the repo and open `examples/13625974/index.html`, or serve it:
> `python -m http.server -d examples/13625974`.

## What you get

| Output | What it is |
| --- | --- |
| `out/<doc>/index.html` | overview: pages, blocks, CJK char counts, U+FFFD count, source of each channel |
| `viewer_<channel>.html` | one engine: page image + its boxes, hover/click to link block and text |
| `viewer_compare.html` | the 1:3 view: four columns per page (image + three engines), colour-coded, boxes on hover |
| `merged/` (+ `merged.md`) | per-page merged blocks with provenance, built by `merge_out.py` |
| `unified/` | merge groups across engines + `_unified_summary.json` (coverage, one_to_one, orphan_rate), built by `unify.py` |
| `_analysis_report.html` / `_analysis_summary.csv` | cross-document metrics + attribution, built by `analyze.py` (run separately) |

## Try it offline (no account, no network)

```bash
pip install -r requirements.txt
python examples/make_demo.py            # writes examples/demo/: a synthetic PDF + three mock engine outputs
python pipeline.py examples/demo/samples/demo_kr.pdf \
    --mineru examples/demo/inbox/demo_kr__mineru \
    --dots   examples/demo/inbox/demo_kr__dots \
    --paddle examples/demo/inbox/demo_kr__paddle \
    --channels examples/demo/channels.json \
    --out examples/demo/out
python -m http.server -d examples/demo/out/demo_kr   # then open /index.html
```

`--channels` swaps the whole config for `examples/demo/channels.json`, which has **identity
calibration** (`src_w/src_h: null` — engine coordinates already *are* page pixels) and every cloud
channel disabled. The demo document is synthetic Korean text with three deliberate disagreements
(an outlier reading, an empty block, a missing page number) so the viewers and the unified pass have
something to show. Nothing third-party, no token, no network. CI runs exactly this.

## Run it on your own PDFs

```bash
cp channels.local.example.json channels.local.json   # fill in tokens; this file is gitignored
python pipeline.py mypaper.pdf                        # fetch all three channels, build viewers
python webapp.py                                      # or: drag & drop workbench at 127.0.0.1:8765
```

Channel configuration lives in three layers (`channels.json` -> `channels.local.json` -> env vars);
see [docs/channels.md](docs/channels.md). Nothing in the repo needs a token to boot.

### Channels

| Channel | Acquisition | Coordinate space |
| --- | --- | --- |
| **MinerU** | batch upload API (signed OSS PUT); falls back to driving the desktop app over CDP | anisotropic pixel space, per-source calibration (`1034x1012` desktop, `1017x1008` API) |
| **dots.ocr** | Gradio 5 instance: `/config` -> upload -> warm-up -> inference -> result zip | normalised 0-1 per page (no calibration) |
| **PaddleOCR-VL** | async cloud jobs | normalised 0-1 per page (no calibration) |

Already have engine output on disk? Drop it in `inbox/<doc>__<channel>/` and the pipeline uses it
instead of calling anything.

## Design notes (the parts that took the longest)

- **One coordinate space, declared in config.** Every engine reports boxes in its own space, sometimes
  anisotropic. The four numbers `src_w / src_h / x_off / y_off` are data, not code, and are re-read on
  every call so you can re-calibrate without restarting the service. Recipe:
  [docs/calibration.md](docs/calibration.md).
- **Calibration key is written next to the data.** The same `mineru` folder may come from the desktop app
  or the cloud API (about 1.6% different geometry). A marker file records which one, so the viewer and
  the merger can never disagree about the same bytes.
- **Cache-first, then API, then desktop.** Re-running a document is free; forcing a re-fetch means
  deleting one cache directory.
- **Upstream weirdness is treated as upstream.** Truncated page JSON, double-encoded payloads, renamed
  keys between engine versions: the adapters parse defensively and *say so in the log*, instead of
  silently producing empty blocks.
- **U+FFFD is a metric, not cosmetics.** It is counted per channel and surfaced in the overview, because
  it is the cheapest text-integrity signal you get without ground truth.

## Status and limitations

- Research / engineering tool, not a hosted service. The three cloud channels are third-party services:
  their endpoints and request formats can change at any time, and their terms apply to you.
- Only the MinerU desktop fallback is Windows-specific; the pipeline itself is platform-neutral.
- The reference-entry checks in `regress.py` are Korean/Chinese-centric heuristics for scholarly PDFs.
- No live-service tests are included (they would need credentials and quota). CI compiles every module
  and runs the offline demo.

## Repository layout

```
pipeline.py        render -> fetch -> adapt -> viewers -> index
webapp.py app.js   drag & drop workbench (stdlib HTTP server + channel config panel)
merge_out.py       per-page merge across channels
unify.py           cross-engine grouping -> unified/ + summary metrics
analyze.py         cross-document metrics + attribution report
regress.py         reference-entry quality checks (truncation, authors, page joins)
caption_audit.py   figure / table caption audit
mineru_drive.py    CDP driver for the MinerU desktop app (fallback)
channels.json      calibration + model defaults (no secrets)
examples/          make_demo.py -> offline demo; 13625974/ -> real checked-in run
docs/              architecture, calibration, channels
```

## License

MIT — see [LICENSE](LICENSE). The one checked-in run (`examples/13625974/`) contains a published
Korean journal article used purely to demonstrate the output format; its copyright stays with the
authors and the journal. Run the pipeline on files you have the right to process.
