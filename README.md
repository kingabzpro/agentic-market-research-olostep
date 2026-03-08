# Agentic Market Research & Trend Analysis with Olostep

General-purpose market research workflow built with Olostep + OpenAI Agents SDK.

This project helps you go from a plain-language research topic to:
- quick web-grounded snapshot
- structured market signals
- trend analysis
- concise technical research brief

<img width="1847" height="857" alt="image" src="https://github.com/user-attachments/assets/0ecc4ad7-fe2d-49df-8e22-8ec28152ce33" />

It is domain-agnostic. SMB prompts in examples are only sample inputs.

## What You Get
- `app.py`: interactive Gradio web app
- `tui.py`: terminal workflow
- `script.py`: one-shot pipeline using a default task
- `output/`: exported markdown and json results
- `cache/`: cached quick snapshots and scraped pages

## Core Workflow
1. Query Olostep Answer API for a quick snapshot.
2. Parse returned content and source URLs.
3. Select top sources.
4. Scrape source pages with Olostep Scrape API.
5. Run Agents SDK stages for:
   - signal extraction
   - trend analysis
   - brief generation
6. Save markdown + json artifacts.

## Agents SDK Stages
Defined in `script.py`:
- `research_agent`: answer + source selection + scraping flow
- `extraction_agent`: signal extraction from summary + scraped context
- `trend_agent`: trend synthesis from extracted signals
- `brief_agent`: concise technical brief generation

## Project Layout
- `app.py` - Gradio UX, cache reuse, parallel scrape execution, file exports
- `tui.py` - CLI flow for quick answer and optional deep stages
- `script.py` - shared pipeline logic, agent/tool definitions, retries, parsing
- `notebook.ipynb` - notebook variant
- `requirements.txt` - pinned dependencies

## Requirements
- Python `3.13` (tested)

Install:

```bash
pip install -r requirements.txt
```

Optional virtual environment:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables
Create your API keys first:
- OpenAI: https://platform.openai.com/api-keys
- Olostep: https://app.olostep.com/

Recommended: copy `.env.example` to `.env`, then fill in your keys.

```bash
cp .env.example .env
nano .env
```


If you prefer setting variables directly in your shell for the current session:


```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_BASE_URL="https://api.openai.com/v1"  # optional
export OLOSTEP_API_KEY="..."
export OLOSTEP_BASE_URL="https://api.olostep.com"  # optional
```

Optional runtime overrides for `script.py`:

```bash
INITIAL_TASK="Research current trends in AI agent tools used by SMB marketing teams..."
MODEL_NAME="gpt-5.2"
OUTPUT_DIR="output"
OUTPUT_MD_PATH="output/agents_sdk_style_market_research_top3_brief.md"
OUTPUT_JSON_PATH="output/agents_sdk_style_market_research_top3_result.json"
```

## Run

Web app:

```bash
python app.py
```

Terminal app:

```bash
python tui.py
```

One-shot script:

```bash
python script.py
```

## Example Topics
- "Generative AI copilots for growth-stage SaaS go-to-market teams"
- "Agentic workflows in healthcare revenue-cycle operations"
- "AI automation landscape for developer support and incident response"

## Web App Flow
1. Enter a topic and run **Quick Snapshot**.
2. Run **Signals**, **Trends**, or **Brief** tabs as needed.
3. Download generated markdown/json files for each stage.

## Outputs
- Web app stage files (timestamped):
  - `<timestamp>_<topic>_signals.md/.json`
  - `<timestamp>_<topic>_trends.md/.json`
  - `<timestamp>_<topic>_brief.md/.json`
- Script outputs:
  - `output/agents_sdk_style_market_research_top3_brief.md`
  - `output/agents_sdk_style_market_research_top3_result.json`

## Caching
- Quick snapshot cache: `cache/quick_snapshot/`
- Scraped page cache: `cache/scrape_pages/`

The app reuses cached payloads to reduce repeated API calls and latency.

## Reliability Notes
- Olostep calls in `script.py` include retries for transient statuses (`408`, `429`, `5xx`) and network timeouts.
- If upstream APIs return HTTP `504`, retry the same request after a short wait.

## Tech Stack
- OpenAI Python SDK
- OpenAI Agents SDK (`Agent`, `Runner`, `function_tool`)
- Requests
- Gradio

## References
- OpenAI Agents SDK docs: https://openai.github.io/openai-agents-python/
- OpenAI Python SDK docs: https://github.com/openai/openai-python
- Gradio Blocks docs: https://www.gradio.app/docs/gradio/blocks
- Olostep API docs: https://docs.olostep.com/
- GitHub README guidance: https://docs.github.com/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
