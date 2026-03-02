# Agentic Market Research (Olostep + OpenAI Agents SDK)

Compact market-research project for SMB AI-agent trends.

It provides three runnable entry points:
- `app.py`: Gradio web app (Quick Snapshot -> Signals -> Trends -> Brief)
- `tui.py`: terminal workflow
- `script.py`: one-shot pipeline for the default `INITIAL_TASK`

The workflow uses:
- Olostep Answer API for initial web-grounded snapshot
- Olostep Scrape API for top source pages
- OpenAI Agents SDK (`Agent`, `Runner`, `function_tool`) for extraction/trend/brief steps

## What It Does
- Runs a quick market snapshot from a user topic.
- Selects top sources and reuses them for deeper analysis.
- Extracts structured market signals.
- Produces trend analysis from those signals.
- Generates a concise technical research brief.
- Saves Markdown + JSON outputs in `output/`.
- Caches quick answers and scraped pages in `cache/` to avoid repeated scraping.

## Project Structure
- `app.py` - Gradio UX, caching, tab orchestration, export files
- `tui.py` - CLI workflow with optional deep analysis stages
- `script.py` - reusable pipeline core, Olostep client calls, agent definitions
- `notebook.ipynb` - notebook variant
- `cache/` - local cache for quick snapshots and scraped pages
- `output/` - generated markdown/json artifacts

## Requirements
Tested with Python `3.13`.

Install from pinned dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables
Set these before running:

```bash
OPENAI_API_KEY=...
OLOSTEP_API_KEY=...
# Optional
OLOSTEP_BASE_URL=https://api.olostep.com
```

## Run

Web app:

```bash
python app.py
```

TUI:

```bash
python tui.py
```

One-shot default pipeline:

```bash
python script.py
```

## Output Files
- Web app stage outputs: timestamped files in `output/`
  - `<timestamp>_<topic>_signals.md/.json`
  - `<timestamp>_<topic>_trends.md/.json`
  - `<timestamp>_<topic>_brief.md/.json`
- Script output:
  - `output/agents_sdk_style_market_research_top3_brief.md`
  - `output/agents_sdk_style_market_research_top3_result.json`

## Notes
- No pandas-based processing is used in the core pipeline.
- If an API call times out (e.g., HTTP 504), retrying usually resolves it.
