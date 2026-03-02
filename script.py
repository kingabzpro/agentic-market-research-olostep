from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from agents import Agent, RunConfig, Runner, function_tool, set_default_openai_client
from openai import AsyncOpenAI

INITIAL_TASK = (
    "Research current trends in AI agent tools used by SMB marketing teams. "
    "Focus on recurring use cases, positioning, and common feature patterns."
)
MODEL_NAME = "gpt-5.2"
OLOSTEP_BASE_URL = os.getenv("OLOSTEP_BASE_URL", "https://api.olostep.com").rstrip("/")
OUTPUT_DIR = Path("output")
OUTPUT_MD_PATH = OUTPUT_DIR / "agents_sdk_style_market_research_top3_brief.md"
OUTPUT_JSON_PATH = OUTPUT_DIR / "agents_sdk_style_market_research_top3_result.json"


def _enable_ansi_colors_on_windows() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        # Color output is optional.
        pass


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[92m",
        logging.WARNING: "\033[93m",
        logging.ERROR: "\033[91m",
        logging.CRITICAL: "\033[95m",
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool) -> None:
        super().__init__("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if self.use_color:
            color = self.COLORS.get(record.levelno, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def get_logger() -> logging.Logger:
    _enable_ansi_colors_on_windows()
    logger = logging.getLogger("market_research_top3")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter(use_color=sys.stdout.isatty()))
    logger.addHandler(handler)
    return logger


LOGGER = get_logger()


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}

    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def unique_http_urls(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for item in items:
        candidate = str(item).strip()
        if not candidate.startswith(("http://", "https://")):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls


def run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def compact_text(value: Any, limit: int = 7000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def ensure_env() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing.")
    if not os.getenv("OLOSTEP_API_KEY"):
        raise RuntimeError("OLOSTEP_API_KEY is missing.")


ensure_env()
set_default_openai_client(AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]))

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Authorization": f"Bearer {os.environ['OLOSTEP_API_KEY']}",
        "Content-Type": "application/json",
    }
)

TRANSIENT_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
OLOSTEP_TIMEOUT_SECONDS = 90
OLOSTEP_MAX_RETRIES = 4


def _retry_delay(attempt: int) -> float:
    # Exponential backoff with jitter.
    return min(8.0, (2 ** (attempt - 1)) + random.random())


def request_olostep(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{OLOSTEP_BASE_URL}{path}"
    last_error: Exception | None = None

    for attempt in range(1, OLOSTEP_MAX_RETRIES + 1):
        try:
            response = SESSION.post(url, json=payload, timeout=OLOSTEP_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            LOGGER.warning(
                "Olostep request network/timeout error on attempt %s/%s: %s",
                attempt,
                OLOSTEP_MAX_RETRIES,
                exc,
            )
            if attempt < OLOSTEP_MAX_RETRIES:
                time.sleep(_retry_delay(attempt))
                continue
            raise RuntimeError(f"Olostep request failed after retries: {exc}") from exc

        if response.status_code in TRANSIENT_HTTP_STATUS:
            detail = response.text[:300]
            last_error = requests.HTTPError(
                f"Transient Olostep HTTP {response.status_code}: {detail}",
                response=response,
            )
            LOGGER.warning(
                "Olostep transient HTTP %s on attempt %s/%s for %s",
                response.status_code,
                attempt,
                OLOSTEP_MAX_RETRIES,
                path,
            )
            if attempt < OLOSTEP_MAX_RETRIES:
                time.sleep(_retry_delay(attempt))
                continue
            response.raise_for_status()

        if response.status_code >= 400:
            # Non-transient client/server error: fail fast.
            response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Failed to decode Olostep JSON response: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Olostep response type: {type(data)}")
        return data

    raise RuntimeError(f"Olostep request failed after retries: {last_error}")


def olostep_answer(task: str) -> dict[str, Any]:
    return request_olostep("/v1/answers", {"task": task})


def olostep_scrape(url: str) -> dict[str, Any]:
    payload = {"url_to_scrape": url, "formats": ["markdown", "text"]}
    return request_olostep("/v1/scrapes", payload)


@function_tool
def olostep_answer_tool(task: str) -> dict[str, Any]:
    """Call Olostep Answers and return the raw response JSON."""
    return olostep_answer(task)


@function_tool
def olostep_scrape_tool(url: str) -> dict[str, Any]:
    """Call Olostep Scrapes for a single URL and return the raw response JSON."""
    return olostep_scrape(url)


def parse_answer_result(raw_answer: dict[str, Any]) -> dict[str, Any]:
    result = raw_answer.get("result") if isinstance(raw_answer.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), str) else ""

    json_content = parse_json_object(result.get("json_content"))

    sources_raw = result.get("sources", [])
    sources: list[str] = []
    if isinstance(sources_raw, list):
        for item in sources_raw:
            if isinstance(item, str):
                sources.append(item)
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                sources.append(item["url"])

    json_urls = json_content.get("urls", [])
    json_urls = json_urls if isinstance(json_urls, list) else []
    top3_sources = unique_http_urls(sources + json_urls)[:3]

    return {
        "answer_summary": summary,
        "answer_json_content": json_content,
        "answer_sources": unique_http_urls(sources),
        "top3_sources": top3_sources,
    }


def scrape_three_sources(top3_sources: list[str]) -> list[dict[str, Any]]:
    scraped_pages: list[dict[str, Any]] = []
    for idx, url in enumerate(top3_sources, start=1):
        LOGGER.info("Scraping source %s/%s: %s", idx, len(top3_sources), url)
        raw_scrape = olostep_scrape(url)
        scrape_result = raw_scrape.get("result") if isinstance(raw_scrape.get("result"), dict) else {}
        metadata = scrape_result.get("metadata") if isinstance(scrape_result.get("metadata"), dict) else {}
        scraped_pages.append(
            {
                "url": url,
                "title": str(metadata.get("title") or ""),
                "content": compact_text(
                    scrape_result.get("markdown_content") or scrape_result.get("text_content")
                ),
            }
        )
    return scraped_pages


def build_agents() -> tuple[Agent, Agent, Agent, Agent]:
    research_agent = Agent(
        name="research_agent",
        model=MODEL_NAME,
        tools=[olostep_answer_tool, olostep_scrape_tool],
        instructions=(
            "Always keep INITIAL_TASK central.\n"
            "Run this exact flow:\n"
            "1) Call olostep_answer_tool once with INITIAL_TASK.\n"
            "2) Parse result.json_content and result.sources.\n"
            "3) Select top 3 unique URLs (prefer result.sources order).\n"
            "4) Scrape those top 3 URLs with olostep_scrape_tool.\n"
            "Return strict JSON only with keys: initial_task, answer_summary, "
            "answer_json_content, answer_sources, top3_sources, scraped_pages."
        ),
    )

    extraction_agent = Agent(
        name="extraction_agent",
        model=MODEL_NAME,
        instructions=(
            "Always include INITIAL_TASK context.\n"
            "Extract concrete market signals from provided summary + scraped context only.\n"
            "Return strict JSON with: signals (list of objects).\n"
            "Each signal object: topic, use_case, positioning_pattern, feature_pattern, evidence, source_url."
        ),
    )

    trend_agent = Agent(
        name="trend_agent",
        model=MODEL_NAME,
        instructions=(
            "Always include INITIAL_TASK context.\n"
            "Analyze recurring patterns from extracted signals.\n"
            "Return strict JSON with: trends (list) and summary (string).\n"
            "Each trend object: trend, why_now, supporting_signals, source_urls, confidence_0_to_1."
        ),
    )

    brief_agent = Agent(
        name="brief_agent",
        model=MODEL_NAME,
        instructions=(
            "Always include INITIAL_TASK context.\n"
            "Write a concise technical research brief in markdown.\n"
            "Use sections: Executive Summary, Top Trends, Recurring Use Cases, "
            "Positioning Patterns, Feature Patterns, Sources.\n"
            "Be specific and evidence-linked, but compact."
        ),
    )

    return research_agent, extraction_agent, trend_agent, brief_agent


def run_pipeline() -> dict[str, Any]:
    LOGGER.info("Starting pipeline for initial task.")
    research_agent, extraction_agent, trend_agent, brief_agent = build_agents()

    LOGGER.info("Stage 1: Running research agent (Answer + Top3 + Scrapes).")
    research_prompt = (
        f"INITIAL_TASK:\n{INITIAL_TASK}\n\n"
        "Use tools to complete the flow exactly and return strict JSON only."
    )
    research_run = run_async(
        Runner.run(
            research_agent,
            input=research_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    research_payload = parse_json_object(research_run.final_output)

    if not research_payload.get("top3_sources"):
        LOGGER.warning("Research agent output incomplete. Using deterministic fallback.")
        parsed_answer = parse_answer_result(olostep_answer(INITIAL_TASK))
        top3_sources = parsed_answer["top3_sources"]
        scraped_pages = scrape_three_sources(top3_sources)
        research_payload = {
            "initial_task": INITIAL_TASK,
            "answer_summary": parsed_answer["answer_summary"],
            "answer_json_content": parsed_answer["answer_json_content"],
            "answer_sources": parsed_answer["answer_sources"],
            "top3_sources": top3_sources,
            "scraped_pages": scraped_pages,
        }
    else:
        top3_sources = research_payload.get("top3_sources", [])
        top3_sources = top3_sources if isinstance(top3_sources, list) else []
        top3_sources = unique_http_urls(top3_sources)[:3]
        research_payload["top3_sources"] = top3_sources
        if len(top3_sources) < 3:
            LOGGER.warning("Only %s source(s) available for scraping.", len(top3_sources))

        scraped_pages = research_payload.get("scraped_pages", [])
        scraped_pages = scraped_pages if isinstance(scraped_pages, list) else []
        if len(scraped_pages) < len(top3_sources):
            LOGGER.info("Stage 1b: Completing missing scrape results explicitly.")
            scraped_pages = scrape_three_sources(top3_sources)
            research_payload["scraped_pages"] = scraped_pages

    LOGGER.info("Selected top sources: %s", json.dumps(research_payload.get("top3_sources", [])))

    LOGGER.info("Stage 2: Running extraction agent.")
    extraction_prompt = (
        f"INITIAL_TASK:\n{INITIAL_TASK}\n\n"
        "Extract signals from this research package. Return strict JSON only.\n\n"
        f"RESEARCH_PACKAGE:\n{json.dumps(research_payload, ensure_ascii=False)}"
    )
    extraction_run = run_async(
        Runner.run(
            extraction_agent,
            input=extraction_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    extraction_payload = parse_json_object(extraction_run.final_output)
    signals = extraction_payload.get("signals", [])
    signals = signals if isinstance(signals, list) else []
    LOGGER.info("Signals extracted: %s", len(signals))

    LOGGER.info("Stage 3: Running trend agent.")
    trend_prompt = (
        f"INITIAL_TASK:\n{INITIAL_TASK}\n\n"
        "Run trend analysis from the extracted signals. Return strict JSON only.\n\n"
        f"SIGNALS_JSON:\n{json.dumps(signals, ensure_ascii=False)}"
    )
    trend_run = run_async(
        Runner.run(
            trend_agent,
            input=trend_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    trend_payload = parse_json_object(trend_run.final_output)
    trends = trend_payload.get("trends", [])
    trends = trends if isinstance(trends, list) else []
    LOGGER.info("Trends identified: %s", len(trends))

    LOGGER.info("Stage 4: Running brief agent.")
    brief_prompt = (
        f"INITIAL_TASK:\n{INITIAL_TASK}\n\n"
        "Generate the final concise technical research brief in markdown.\n\n"
        "ANSWER_SUMMARY_AND_CONTEXT:\n"
        f"{json.dumps({'answer_summary': research_payload.get('answer_summary', ''), 'top3_sources': research_payload.get('top3_sources', []), 'scraped_pages': research_payload.get('scraped_pages', [])}, ensure_ascii=False)}\n\n"
        f"EXTRACTED_SIGNALS:\n{json.dumps(signals, ensure_ascii=False)}\n\n"
        f"TREND_ANALYSIS:\n{json.dumps(trends, ensure_ascii=False)}"
    )
    brief_run = run_async(
        Runner.run(
            brief_agent,
            input=brief_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    final_brief = str(brief_run.final_output).strip()

    return {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "initial_task": INITIAL_TASK,
        "model": MODEL_NAME,
        "top3_sources": research_payload.get("top3_sources", []),
        "research_payload": research_payload,
        "signals": signals,
        "trends": trends,
        "brief_markdown": final_brief,
    }


def save_outputs(result: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD_PATH.write_text(result.get("brief_markdown", "").strip() + "\n", encoding="utf-8")
    OUTPUT_JSON_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Saved markdown brief: %s", OUTPUT_MD_PATH)
    LOGGER.info("Saved JSON result: %s", OUTPUT_JSON_PATH)


def main() -> None:
    try:
        result = run_pipeline()
        save_outputs(result)
        LOGGER.info("Pipeline completed successfully.")
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
