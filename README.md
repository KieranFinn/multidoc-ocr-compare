# multidoc-ocr-compare

**[English](README.md) · [中文](README.zh-CN.md)**

> This is the **English** README. The Chinese version is [README.zh-CN.md](README.zh-CN.md).

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

---

## Where do I start?

Pick the row that matches what you actually want; each row works on its own.

| You want to… | Command | Needs an account? |
| --- | --- | --- |
| **see what the output looks like** | open [`examples/13625974/index.html`](examples/13625974/) after cloning — or click the rendered links below | no |
| **check it runs on your machine** | [Quick start (offline)](#quick-start-offline-no-account-no-network) — synthetic PDF, mock engine outputs | no |
| **run your own PDF** | [Run it on your own PDFs](#run-it-on-your-own-pdfss) — one engine is enough to start | only for the engines you enable |
| **get the structured fields** (title / authors / references …) | [Structured metadata](#structured-metadata-optional-runs-after-unify) | no (rules first; LLM optional) |

There is **no single setup step that has to succeed before anything else works** — you can start with
zero credentials, add engines one at a time, and stop wherever the output is good enough.

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
| `viewer_compare.html` | the 1:3 view: four columns per page (image + three engines), colour-coded, boxes on hover; repeated citations of the same sentence are grouped into one row with every highlight and reference link; reference/footnote-row hover selects the matching member block where possible; citation-row hover uses its unified block; both show a cropped original-page preview; clicking a metadata row (overview or per-page band) scrolls to the page and flashes the source block on the original scan |
| `merged/` (+ `merged.md`) | per-page merged blocks with provenance, built by `merge_out.py` |
| `unified/` | merge groups across engines + `_unified_summary.json` (coverage, one_to_one, orphan_rate), built by `unify.py` |
| `metadata.json` + `_metadata_report.html` | structured fields (title, authors, affiliation, abstract, keywords, reference entries) with per-field confidence + page anchors, built by `metadata.py` |
| `out_llm_trial/fds_extract/ner_lite/<doc>/` | entity extraction (funding / software / dataset) written by `ner_lite.py` **outside** `out/`: `ner_lite.json`, `ner_lite_audit.json`, `_audit.html`; picked up by `viewer_compare.html` when present |
| `metadata_audit.json` | diagnostic sidecar written by the same stages: per-field basis/review comments, reference style, L1 checks (`ref_validation`). **Not** part of `metadata.json` — readers that look for validation data must read this file |

## Quick start (offline, no account, no network)

```bash
pip install -r requirements.txt
python _attic/examples/make_demo.py                    # synthetic PDF + three mock engine outputs, into _attic/examples/demo/
python pipeline.py _attic/examples/demo/samples/demo_kr.pdf \
    --mineru _attic/examples/demo/inbox/demo_kr__mineru \
    --dots   _attic/examples/demo/inbox/demo_kr__dots \
    --paddle _attic/examples/demo/inbox/demo_kr__paddle \
    --channels _attic/examples/demo/channels.json \
    --out _attic/examples/demo/out/demo_kr
python -W error::ResourceWarning -m unittest -v test_pipeline.py
python -m http.server -d _attic/examples/demo/out/demo_kr  # then open /index.html
```

`--channels` swaps the whole config for `_attic/examples/demo/channels.json`: MinerU declares the actual
521×688 page-pixel space, dots/Paddle use normalized coordinates, and every cloud channel is disabled.
The synthetic document deliberately includes an outlier reading, an empty block and a Paddle-only page
number (one intentional orphan). CI runs this flow without tokens or network.

> The unit tests cover the unified-freshness / data-preservation contract and the viewer's citation and
> metadata rendering — they do **not** cover the metadata heuristics (reference segmentation, author
> zone, keyword panel). Those are validated by diffing the checked-in example, see
> [Status and limitations](#status-and-limitations).

## Run it on your own PDFs

### Step 0 — can I run with only one engine?

**Yes.** Every step below is per-channel: with one channel enabled you get `viewer_<channel>.html`
plus the overview; the 1:3 viewer and `merged/` need two or more, and `unified/` (which the metadata
layer reads) needs the engines to agree on at least some content. Channels you did not provide are
labelled "not provided" in the report instead of failing the run.

### Step 1 — pick the engines you actually have

| Channel | What it needs | Account / cost | Notes |
| --- | --- | --- | --- |
| **dots.ocr** | a Gradio 5 endpoint; the default is the public Xiaohongshu instance `https://dotsocr.xiaohongshu.com` | **none — no token field** | the quickest way to get a first real run; override `dots_api.base` only if you self-host |
| **PaddleOCR-VL** | `token` in `channels.local.json` | key from the AI Studio service (`paddleocr.aistudio-app.com`) | async job, normalized coordinates, no calibration |
| **MinerU** | `token` in `channels.local.json` | key from mineru.net | batch signed upload; falls back to driving the desktop app over CDP (Windows) |

```bash
cp channels.local.example.json channels.local.json   # fill in only the tokens you have; gitignored
python pipeline.py mypaper.pdf                       # uses every channel that has a token (or a cache / inbox entry)
python webapp.py                                     # or: drag & drop workbench at 127.0.0.1:8765
```

A channel with no token, no cache and nothing in `inbox/` is simply skipped — you do not have to
disable it explicitly. Starting with `dots` alone (it needs no token) is the shortest path to a first
real run; add `paddle` / `mineru` tokens later and the next document picks them up automatically.

Full request flow per channel (endpoints, polling, quirks) is in
[docs/channels.md](docs/channels.md); calibration is in [docs/calibration.md](docs/calibration.md).

### Step 2 — or bring engine output you already have

If you have already run an engine somewhere (its web UI, a previous batch, a colleague's export), you
do **not** need any API key: drop the output into `inbox/` and the pipeline uses it instead of calling
anything.

```
inbox/<doc>__<channel>/     <doc> = the PDF file name without .pdf
```

| Channel | What to put in the directory |
| --- | --- |
| `<doc>__mineru` | the directory the MinerU run produced, containing `*_content_list_v2.json` (and `layout.json` if you have it — it marks OCR vs non-OCR runs) |
| `<doc>__dots` | per-page `page_0.json`, `page_1.json`, … (list of blocks with `bbox` + text) |
| `<doc>__paddle` | per-page `page_N.json` as above, **or** a single Paddle JSON export `*.json` |

Matching is **strict by document name**: the directory must start with `<doc>`, and a typo means
"no channel found" rather than "use the closest one" (that cross-document fallback used to paint one
document's blocks onto another). Without the suffix marker the pipeline also guesses by content
(`*_content_list_v2.json` → mineru, `page_N.json` → dots), but the explicit suffix is always safer.

### Step 3 — read the result

```
out/<doc>/index.html            start here
out/<doc>/viewer_compare.html   1:3 comparison
```

## Tests

```bash
pip install -r requirements.txt      # test_pipeline imports pipeline -> pipeline_io -> pypdfium2/PIL
python -W error::ResourceWarning -m unittest -v test_pipeline.py
python ner_lite.py selfcheck         # offline: 7 synthetic blocks + a mock model, no network
```

10 cases, no network, no tokens. They pin the contract that matters most when editing the merge layer
("a stale `unified/` is preserved on disk but never drawn"), the viewer's citation/metadata
rendering, and the entity-locating invariants (folded-coordinate mapping, rightward extension,
cross-block segments, biblio-footnote annotation). `ner_lite.py selfcheck` adds the entity gate's
own cases. CI runs both on Python 3.10 and 3.12.

## Configuration

Three layers, later wins (shallow merge per section):

1. `channels.json` — committed baseline: calibration, model names, timeouts, switches. **No tokens.**
2. `channels.local.json` — your private overrides (gitignored). Write only the keys you change.
3. env vars `MINERU_API_TOKEN` / `PADDLE_API_TOKEN` — override those two tokens (handy in CI).

`python pipeline.py --channels <file>` replaces the baseline entirely and **stops consulting layers 2
and 3**, so an offline/demo run can never pick up a real token from your machine. Every call re-reads
the merged config, so editing calibration or a token takes effect on the next document without a
restart. Details: [docs/channels.md](docs/channels.md).

## Design notes (the parts that took the longest)

- **One coordinate space, declared in config.** Every engine reports boxes in its own space, sometimes
  anisotropic. The four numbers `src_w / src_h / x_off / y_off` are data, not code, and are re-read on
  every call so you can re-calibrate without restarting the service. Recipe:
  [docs/calibration.md](docs/calibration.md).
- **Calibration key is written next to the data.** The same `mineru` folder may come from the desktop app
  or the cloud API (about 1.6% different geometry). A marker file records which one, so the viewer and
  the merger can never disagree about the same bytes.
- **Cache-first, then API, then desktop.** Re-running a document is free; forcing a re-fetch means
  deleting one cache directory (`out/<doc>/raw/<channel>_api`).
- **Upstream weirdness is treated as upstream.** Truncated page JSON, double-encoded payloads, renamed
  keys between engine versions: the adapters parse defensively and *say so in the log*, instead of
  silently producing empty blocks.
- **U+FFFD is a metric, not cosmetics.** It is counted per channel and surfaced in the overview, because
  it is the cheapest text-integrity signal you get without ground truth.

## Structured metadata (optional, runs after unify)

The comparison chain above tells you *who read what*. `metadata.py` turns the surviving evidence into
*citable fields* — title (Korean + English), authors, affiliations, abstract, bilingual
keywords (`keywords` Korean / `keywords_en` English, each the first matching panel in page
order), and reference entries — each with a confidence level and a page anchor back into the 1:3 viewer.

```bash
python unify.py <doc>                      # produces out/<doc>/unified/ (pipeline.py already does this; run it standalone for existing merged/ data)
python metadata.py <doc> --stage m3        # cross-page join: glue a paragraph split by the page break
python metadata.py <doc> --stage refs      # reference entries -> a list of {authors, year, title, vol, no, pages}
python metadata.py <doc> --stage frefs     # all numbered footnotes (references and notes) -> footnote_refs
python metadata.py <doc> --stage front     # title / authors / affiliations (Korean + romanised)
python metadata.py <doc> --stage abs       # Korean + English abstracts
python metadata.py <doc> --stage issue     # journal / year / volume / issue / pages + keywords
python metadata.py <doc> --stage cspan     # in-text citation sentences
python metadata.py <doc> --stage report    # -> _metadata_report.html
```

Rules first, model second. Every field is parsed by deterministic heuristics (layout-class aware
segmentation for Korean five-part citations and `In:` book chapters, superscript-set matching for
author footnotes, per-entry style detection for `[n]` / `N.` / author-year). Product fields carry
`layout` (citation-form class), `container_title` / `publisher` / `editor` (CSL-named; `source`
remains the raw container string for back-compat — downstream should read `container_title` as the
container, not `source`). An optional LLM pass (`--stage llm`) only *asks about fields the
regexes failed or scored low*; it never rewrites a high-confidence value. It is off by default
(`llm.enabled: false` in `channels.json`) and reads its key from the `KCI_LLM_KEY` environment
variable — no secret is ever written to disk.

```bash
python metadata.py <doc> --viewer          # rebuild viewer_compare.html from existing merged/ + unified/
```

> `--stage m3` writes back into `unified/page_N.json`; re-running unify throws those markers away.
> The order is: pipeline (which already runs unify) → metadata
> (m3 → refs → frefs → front → abs → issue → cspan → report). If unify fails or is disabled, old
> unified data is preserved on disk but not drawn into the new viewer. `--viewer` redraws only.

## Entity extraction (`ner_lite.py`, optional, runs after unify)

The metadata stages above recover *bibliographic* fields. `ner_lite.py` recovers the three entity
types that citation parsers do not model — **funding** (funder / programme / grant id), **software**,
and **dataset** — with a contract that is deliberately narrower than a classic NER pipeline:

```bash
# put endpoint/model into channels.json -> llm, and the key in KCI_LLM_KEY (never on disk)
python ner_lite.py selfcheck               # offline gate check, no network
python ner_lite.py 13625974                # one document
python ner_lite.py all                     # every doc under out/ that has unified/
```

| Stage | What it does | Why |
| --- | --- | --- |
| Nominate | one prompt for three open-vocabulary kinds; the model returns only `[{kind, name}]` | no `uid`, no relation, no second pass — nothing the model writes is trusted as a coordinate |
| Locate | the name is folded (whitespace/BOM/NFC-insensitive) and must appear in the batch text, otherwise the nomination is **dropped** (`anchor_miss`) | the model can hallucinate a name; it cannot make the string appear in the source |
| Emit | the value is always the **original slice**, enumerated over the whole document (`segments` when an occurrence crosses a block, the tighter overlap wins) | every output can be verified with a bare `slice in block_text` |
| Annotate | rightward literal extension (version number / parenthesised grant id) is marked `extended`; `multi_block`, `multi_occurrence`, `cross_block`, `mention_in_biblio_footnote` | each flag answers a question a reviewer would otherwise ask by hand |

The gate is a structural guarantee of *one* thing: **the string occurs where the record says it does**. It
is not a guarantee that the entity *belongs* to that sentence (a truncated name that is a substring of a
neighbouring reading still passes), and a dropped nomination is invisible in the output — read
`dropped_anchor_miss` / `dropped_parse_fail` / `errors_network` in `ner_lite_audit.json` before quoting
counts. Batching is ~8,000 characters per call, batches never split a block, and an occurrence that spans
a batch seam is still located — because location is a whole-document search, not a batch search.

Outputs go to `out_llm_trial/fds_extract/ner_lite/<doc>/` (`ner_lite.json`, `ner_lite_audit.json`,
`_audit.html`). `viewer_compare.html` shows them as an extra column when the file is present and simply
omits the column when it is not.

## Status and limitations

- Research / engineering tool, not a hosted service. The three cloud channels are third-party services:
  their endpoints and request formats can change at any time, and their terms apply to you.
- `dots_api.base` defaults to a public community instance; if it disappears, point it at your own
  Gradio 5 deployment (or use `inbox/` with output you already have).
- **Accuracy is not uniform.** The reference-entry and front-matter heuristics are tuned on a 13-document
  Korean/Chinese journal corpus and were validated by diffing the checked-in example. Known weak spots
  (all reproducible on that corpus) are: citation styles where the year is preceded only by a period
  ("Author. 1999. Title."), all-Hanzi author lines, `N.`-style footnote markers, documents whose
  reference list shares its last page with an abstract in the other column, and title/author zones on
  layouts where the abstract begins without a recognisable lead-in. Treat `metadata.json` as a
  high-precision draft: check the confidence level on each field (and cross-check against
  `viewer_compare.html`) before citing.
- **Entity extraction is recall-limited, not precision-limited.** On the 13-document corpus `ner_lite.py`
  returns 57 records (35 dataset / 15 software / 7 funding) with 61/61 nominations admitted and zero
  network or parse failures — but its recovery against hand-checked `verified.json` is uneven: software
  6/7, funding 3/4 (the miss is a split record: funder and programme land as two names instead of one),
  dataset 0/0 (35 false positives, mostly titles of old books and ICC reports containing `史` / `報告書`).
  Every one of the 35 dataset records is still *verifiable* — each `slice` is verbatim in the source —
  which is exactly the difference between "located" and "correct".
- Only the MinerU desktop fallback is Windows-specific; `pipeline.py`, `webapp.py`, `metadata.py`
  and the views are platform-neutral. `start.bat` / `start.sh` are convenience wrappers.
- No live-service tests are included (they would need credentials and quota). CI compiles every module,
  runs the unit tests, `ner_lite.py selfcheck` and the offline demo; it does not call an LLM.

## Repository layout

```
pipeline.py         main orchestration + compatibility re-exports
pipeline_io.py      render -> fetch -> archive -> calibrate -> adapt
viewer.py           single/compare viewers + md2html + metadata panels
webapp.py app.js dashboard.css   drag & drop workbench (job overview + pipeline/channel status + config panel)
merge_out.py        per-page merge across channels
unify.py            cross-engine grouping -> unified/ + summary metrics (frozen)
metadata.py         structured metadata CLI (11-line shell; implementation lives in metapy/)
ner_lite.py         entity extraction (funding / software / dataset): LLM nominates, code locates
regress.py          reference-entry quality checks (truncation, authors, page joins)
test_pipeline.py    unified freshness, data-preservation and viewer regression tests
mineru_drive.py     CDP driver for the MinerU desktop app (fallback)
requirements.txt    render/CDP runtime dependencies
channels.json       calibration + model defaults (no secrets)
channels.local.example.json   copy to channels.local.json and fill in your tokens
docs/               channels.md · calibration.md · architecture.md · REVIEW.md · RULES.md
examples/           13625974/ (one real run, linked from the front page)
uploads/ inbox/     input PDFs + cached/external channel output (gitignored)
out/                pipeline products; out_llm_trial/ holds entity-extraction data (gitignored)
metapy/             __init__ (the CLI) · common / m3(frozen) / issue / cspan / refs / front /
                    abs / llmrep / dual
_attic/             retired code, kept for reference: analyze.py · caption_audit.py ·
                    examples/ (make_demo.py + the synthetic offline demo used by CI) · docs/ copies
```

## License

MIT — see [LICENSE](LICENSE). The one checked-in run (`examples/13625974/`) contains a published
Korean journal article used purely to demonstrate the output format; its copyright stays with the
authors and the journal. Run the pipeline on files you have the right to process.
