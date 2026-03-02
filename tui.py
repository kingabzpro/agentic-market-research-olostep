from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents import RunConfig, Runner

from script import (
    LOGGER,
    MODEL_NAME,
    build_agents,
    olostep_answer,
    parse_answer_result,
    parse_json_object,
    run_async,
    scrape_three_sources,
    unique_http_urls,
)

OUTPUT_DIR = Path("output")

ANSI = {
    "reset": "\033[0m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "magenta": "\033[95m",
    "bold": "\033[1m",
}


def ctext(text: str, color: str) -> str:
    return f"{ANSI.get(color, '')}{text}{ANSI['reset']}"


def safe_print(text: Any = "") -> None:
    message = str(text)
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        sys.stdout.buffer.write((message + "\n").encode(encoding, errors="replace"))
        sys.stdout.flush()


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:60] or "topic"


def parse_quick_answer(raw_answer: dict[str, Any]) -> dict[str, Any]:
    result = raw_answer.get("result")
    if isinstance(result, str):
        parsed_result = parse_json_object(result)
        nested_text = parsed_result.get("result")
        if isinstance(nested_text, str) and nested_text.strip():
            quick_text = nested_text.strip()
        else:
            quick_text = result.strip()
        return {
            "quick_text": quick_text,
            "json_content": {},
            "sources": [],
        }

    result_obj = result if isinstance(result, dict) else {}
    json_content = parse_json_object(result_obj.get("json_content"))
    sources = result_obj.get("sources", [])
    sources = sources if isinstance(sources, list) else []
    sources = unique_http_urls(sources)

    quick_text = ""
    for key in ("summary", "result", "answer", "text"):
        value = result_obj.get(key)
        if isinstance(value, str) and value.strip():
            quick_text = value.strip()
            break
    if not quick_text and json_content:
        for key in ("summary", "result", "answer", "text"):
            value = json_content.get(key)
            if isinstance(value, str) and value.strip():
                quick_text = value.strip()
                break
    if not quick_text and json_content:
        quick_text = json.dumps(json_content, ensure_ascii=False)

    return {
        "quick_text": quick_text,
        "json_content": json_content,
        "sources": sources,
    }


def print_banner() -> None:
    safe_print(ctext("\n=== Agentic Market Research TUI ===", "bold"))
    safe_print(ctext("Quick Answer -> Optional Deep Research (signals/trends/brief)\n", "cyan"))


def ask_topic() -> str:
    while True:
        topic = input(ctext("Enter research topic: ", "green")).strip()
        if topic:
            return topic
        safe_print(ctext("Topic cannot be empty.", "yellow"))


def ask_deep_research() -> bool:
    while True:
        value = input(ctext("Run deep research now? [y/n]: ", "green")).strip().lower()
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        safe_print(ctext("Please type y or n.", "yellow"))


def ask_output_selection() -> dict[str, bool]:
    safe_print(ctext("\nChoose outputs (comma-separated):", "magenta"))
    safe_print("  1) Deep research answer")
    safe_print("  2) Signals")
    safe_print("  3) Trends")
    safe_print("  4) Research brief")
    safe_print("  Example: 1,2,4")

    while True:
        raw = input(ctext("Selection: ", "green")).strip().lower()
        if not raw:
            raw = "1,2,3,4"
        if raw == "all":
            raw = "1,2,3,4"

        picks = {part.strip() for part in raw.split(",") if part.strip()}
        if picks.issubset({"1", "2", "3", "4"}):
            needs = {
                "deep_answer": "1" in picks,
                "signals": "2" in picks,
                "trends": "3" in picks,
                "brief": "4" in picks,
            }
            # Apply dependencies.
            if needs["brief"]:
                needs["trends"] = True
            if needs["trends"]:
                needs["signals"] = True
            if needs["signals"]:
                needs["deep_answer"] = True
            return needs

        safe_print(ctext("Invalid input. Use numbers 1-4, comma-separated.", "yellow"))


def run_deep_answer(initial_task: str, research_agent: Any) -> dict[str, Any]:
    LOGGER.info("Stage: Deep research answer.")
    research_prompt = (
        f"INITIAL_TASK:\n{initial_task}\n\n"
        "Use tools to complete the flow exactly and return strict JSON only."
    )
    run_result = run_async(
        Runner.run(
            research_agent,
            input=research_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    research_payload = parse_json_object(run_result.final_output)

    if not research_payload.get("top3_sources"):
        LOGGER.warning("Research agent output incomplete. Using deterministic fallback.")
        parsed = parse_answer_result(olostep_answer(initial_task))
        top3_sources = parsed["top3_sources"]
        research_payload = {
            "initial_task": initial_task,
            "answer_summary": parsed["answer_summary"],
            "answer_json_content": parsed["answer_json_content"],
            "answer_sources": parsed["answer_sources"],
            "top3_sources": top3_sources,
            "scraped_pages": scrape_three_sources(top3_sources),
        }
    else:
        top3 = research_payload.get("top3_sources", [])
        top3 = top3 if isinstance(top3, list) else []
        top3 = unique_http_urls(top3)[:3]
        research_payload["top3_sources"] = top3

        scraped_pages = research_payload.get("scraped_pages", [])
        scraped_pages = scraped_pages if isinstance(scraped_pages, list) else []
        if len(scraped_pages) < len(top3):
            LOGGER.info("Completing missing scrape results explicitly.")
            research_payload["scraped_pages"] = scrape_three_sources(top3)

    LOGGER.info("Top 3 sources: %s", json.dumps(research_payload.get("top3_sources", [])))
    return research_payload


def run_signal_extraction(initial_task: str, research_payload: dict[str, Any], extraction_agent: Any) -> list[dict[str, Any]]:
    LOGGER.info("Stage: Signal extraction.")
    extraction_prompt = (
        f"INITIAL_TASK:\n{initial_task}\n\n"
        "Extract signals from this research package. Return strict JSON only.\n\n"
        f"RESEARCH_PACKAGE:\n{json.dumps(research_payload, ensure_ascii=False)}"
    )
    run_result = run_async(
        Runner.run(
            extraction_agent,
            input=extraction_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    payload = parse_json_object(run_result.final_output)
    signals = payload.get("signals", [])
    signals = signals if isinstance(signals, list) else []
    LOGGER.info("Signals extracted: %s", len(signals))
    return signals


def run_trend_analysis(initial_task: str, signals: list[dict[str, Any]], trend_agent: Any) -> dict[str, Any]:
    LOGGER.info("Stage: Trend analysis.")
    trend_prompt = (
        f"INITIAL_TASK:\n{initial_task}\n\n"
        "Run trend analysis from the extracted signals. Return strict JSON only.\n\n"
        f"SIGNALS_JSON:\n{json.dumps(signals, ensure_ascii=False)}"
    )
    run_result = run_async(
        Runner.run(
            trend_agent,
            input=trend_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    payload = parse_json_object(run_result.final_output)
    trends = payload.get("trends", [])
    trends = trends if isinstance(trends, list) else []
    LOGGER.info("Trends identified: %s", len(trends))
    return {"summary": payload.get("summary", ""), "trends": trends}


def run_brief_generation(
    initial_task: str,
    research_payload: dict[str, Any],
    signals: list[dict[str, Any]],
    trends: list[dict[str, Any]],
    brief_agent: Any,
) -> str:
    LOGGER.info("Stage: Research brief generation.")
    brief_prompt = (
        f"INITIAL_TASK:\n{initial_task}\n\n"
        "Generate the final concise technical research brief in markdown.\n\n"
        "ANSWER_SUMMARY_AND_CONTEXT:\n"
        f"{json.dumps({'answer_summary': research_payload.get('answer_summary', ''), 'top3_sources': research_payload.get('top3_sources', []), 'scraped_pages': research_payload.get('scraped_pages', [])}, ensure_ascii=False)}\n\n"
        f"EXTRACTED_SIGNALS:\n{json.dumps(signals, ensure_ascii=False)}\n\n"
        f"TREND_ANALYSIS:\n{json.dumps(trends, ensure_ascii=False)}"
    )
    run_result = run_async(
        Runner.run(
            brief_agent,
            input=brief_prompt,
            run_config=RunConfig(model=MODEL_NAME),
        )
    )
    return str(run_result.final_output).strip()


def build_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Market Research Report: {result['topic']}")
    lines.append("")
    lines.append(f"- Generated at: {result['generated_at']}")
    lines.append(f"- Model: {result['model']}")
    lines.append("")

    quick = result.get("quick_answer", {})
    lines.append("## Quick Answer")
    lines.append("")
    quick_text = str(quick.get("quick_text") or "").strip()
    lines.append(quick_text or "_No quick summary text returned._")
    lines.append("")

    quick_sources = quick.get("sources", [])
    quick_sources = quick_sources if isinstance(quick_sources, list) else []
    if quick_sources:
        lines.append("### Quick Answer Sources")
        lines.append("")
        for src in quick_sources[:10]:
            lines.append(f"- {src}")
        lines.append("")

    deep = result.get("deep_research", {})
    if isinstance(deep, dict) and deep:
        lines.append("## Deep Research Answer")
        lines.append("")
        lines.append(str(deep.get("answer_summary") or "_No deep summary provided._"))
        lines.append("")
        top3 = deep.get("top3_sources", [])
        top3 = top3 if isinstance(top3, list) else []
        if top3:
            lines.append("### Top 3 Sources")
            lines.append("")
            for src in top3:
                lines.append(f"- {src}")
            lines.append("")

    signals = result.get("signals", [])
    signals = signals if isinstance(signals, list) else []
    if signals:
        lines.append("## Signals")
        lines.append("")
        for signal in signals[:10]:
            lines.append(f"- {json.dumps(signal, ensure_ascii=False)}")
        lines.append("")

    trends = result.get("trends", [])
    trends = trends if isinstance(trends, list) else []
    if trends:
        lines.append("## Trends")
        lines.append("")
        for trend in trends[:10]:
            lines.append(f"- {json.dumps(trend, ensure_ascii=False)}")
        lines.append("")

    brief = str(result.get("brief_markdown") or "").strip()
    if brief:
        lines.append("## Research Brief")
        lines.append("")
        lines.append(brief)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def save_outputs(topic: str, result: dict[str, Any]) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = slugify(topic)

    md_path = OUTPUT_DIR / f"{timestamp}_{slug}_research.md"
    json_path = OUTPUT_DIR / f"{timestamp}_{slug}_research.json"

    md_path.write_text(build_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> None:
    print_banner()
    topic = ask_topic()

    LOGGER.info("Running quick answer for topic: %s", topic)
    quick_raw = olostep_answer(topic)
    quick = parse_quick_answer(quick_raw)
    safe_print(ctext("\nQuick answer:", "bold"))
    safe_print(quick.get("quick_text") or ctext("(No summary text returned.)", "yellow"))
    sources = quick.get("sources", [])
    sources = sources if isinstance(sources, list) else []
    if sources:
        safe_print(ctext("\nQuick answer sources:", "magenta"))
        for idx, src in enumerate(sources[:5], start=1):
            safe_print(f"  {idx}. {src}")

    deep = {}
    signals: list[dict[str, Any]] = []
    trends: list[dict[str, Any]] = []
    brief_markdown = ""

    if ask_deep_research():
        selected = ask_output_selection()
        research_agent, extraction_agent, trend_agent, brief_agent = build_agents()
        initial_task = topic

        if selected["deep_answer"]:
            deep = run_deep_answer(initial_task, research_agent)
        if selected["signals"]:
            signals = run_signal_extraction(initial_task, deep, extraction_agent)
        if selected["trends"]:
            trend_result = run_trend_analysis(initial_task, signals, trend_agent)
            trends = trend_result.get("trends", [])
            trends = trends if isinstance(trends, list) else []
        if selected["brief"]:
            brief_markdown = run_brief_generation(initial_task, deep, signals, trends, brief_agent)
    else:
        LOGGER.info("Deep research skipped by user.")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "model": MODEL_NAME,
        "quick_answer": quick,
        "deep_research": deep,
        "signals": signals,
        "trends": trends,
        "brief_markdown": brief_markdown,
    }

    md_path, json_path = save_outputs(topic, result)
    safe_print(ctext("\nSaved outputs:", "bold"))
    safe_print(f"- {md_path}")
    safe_print(f"- {json_path}")
    safe_print(ctext("\nDone.", "green"))


if __name__ == "__main__":
    main()
