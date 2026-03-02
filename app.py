from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gradio as gr
import requests

from script import MODEL_NAME, build_agents, compact_text, olostep_scrape, unique_http_urls
from tui import (
    olostep_answer,
    parse_quick_answer,
    run_brief_generation,
    run_signal_extraction,
    run_trend_analysis,
    slugify,
)

APP_TITLE = "Agentic Market Research Studio"
OUTPUT_DIR = Path("output")
CACHE_DIR = Path("cache")
QUICK_CACHE_DIR = CACHE_DIR / "quick_snapshot"
SCRAPE_CACHE_DIR = CACHE_DIR / "scrape_pages"
APP_THEME = gr.themes.Ocean()
FORCE_LIGHT_JS = """
() => {
  try {
    localStorage.setItem("theme", "light");
    document.documentElement.classList.remove("dark");
  } catch (e) {}
}
"""


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cache_file(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _cache_load(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = _cache_file(cache_dir, key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_save(cache_dir: Path, key: str, payload: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file(cache_dir, key)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cached_quick_snapshot_raw(topic: str) -> dict[str, Any]:
    normalized = " ".join(topic.strip().lower().split())
    key = _stable_hash(normalized)
    cached = _cache_load(QUICK_CACHE_DIR, key)
    if isinstance(cached, dict):
        return cached

    raw = olostep_answer(topic)
    if isinstance(raw, dict):
        _cache_save(QUICK_CACHE_DIR, key, raw)
    return raw


def _source_rows(sources: list[str]) -> list[list[str]]:
    return [[str(i + 1), src] for i, src in enumerate(sources)]


def _quick_markdown(topic: str, quick: dict[str, Any]) -> str:
    text = str(quick.get("quick_text") or "").strip()
    return f"## Quick Answer: {topic}\n\n{text or '_No quick summary text returned._'}\n"


def _try_parse_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_records(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        parsed = _try_parse_dict(item)
        if isinstance(parsed, dict):
            normalized.append(parsed)
            continue
        if isinstance(item, dict):
            normalized.append(item)
            continue
        text = str(item or "").strip()
        if text:
            normalized.append({"raw_text": text})
    return normalized


def _signals_markdown(signals: list[dict[str, Any]]) -> str:
    lines = ["## Signals", ""]
    normalized = _normalize_records(list(signals))
    if not normalized:
        lines.append("_No signals generated._")
        return "\n".join(lines) + "\n"

    for idx, signal in enumerate(normalized, start=1):
        topic = str(signal.get("topic") or signal.get("title") or f"Signal {idx}").strip()
        use_case = str(signal.get("use_case") or "").strip()
        positioning = str(signal.get("positioning_pattern") or "").strip()
        feature = str(signal.get("feature_pattern") or "").strip()
        evidence = str(signal.get("evidence") or "").strip()
        source_url = str(signal.get("source_url") or "").strip()
        raw_text = str(signal.get("raw_text") or "").strip()

        lines.append(f"### {idx}. {topic}")
        if use_case:
            lines.append(f"- **Use case:** {use_case}")
        if positioning:
            lines.append(f"- **Positioning pattern:** {positioning}")
        if feature:
            lines.append(f"- **Feature pattern:** {feature}")
        if evidence:
            lines.append(f"- **Evidence:** {evidence}")
        if source_url.startswith(("http://", "https://")):
            lines.append(f"- **Source:** [{source_url}]({source_url})")
        elif source_url:
            lines.append(f"- **Source:** {source_url}")
        if raw_text:
            lines.append(raw_text)
        lines.append("")
    return "\n".join(lines) + "\n"


def _trends_markdown(trends: list[dict[str, Any]], summary: str = "") -> str:
    lines = ["## Trends", ""]
    if summary.strip():
        lines.append(summary.strip())
        lines.append("")
    normalized = _normalize_records(list(trends))
    if not normalized:
        lines.append("_No trends generated._")
        return "\n".join(lines) + "\n"

    for idx, trend in enumerate(normalized, start=1):
        trend_title = str(trend.get("trend") or trend.get("title") or f"Trend {idx}").strip()
        why_now = str(trend.get("why_now") or "").strip()
        confidence = trend.get("confidence_0_to_1")
        raw_text = str(trend.get("raw_text") or "").strip()

        lines.append(f"### {idx}. {trend_title}")
        if why_now:
            lines.append(f"- **Why now:** {why_now}")

        supporting_signals = trend.get("supporting_signals", [])
        if isinstance(supporting_signals, list) and supporting_signals:
            joined = "; ".join([str(s).strip() for s in supporting_signals if str(s).strip()])
            if joined:
                lines.append(f"- **Supporting signals:** {joined}")

        source_urls = trend.get("source_urls", [])
        if isinstance(source_urls, list) and source_urls:
            clean_urls = [str(url).strip() for url in source_urls if str(url).strip()]
            if clean_urls:
                linked = ", ".join(
                    [f"[{url}]({url})" if url.startswith(("http://", "https://")) else url for url in clean_urls]
                )
                lines.append(f"- **Sources:** {linked}")

        if isinstance(confidence, (int, float)):
            lines.append(f"- **Confidence:** {round(float(confidence), 2)}")
        elif str(confidence).strip():
            lines.append(f"- **Confidence:** {confidence}")

        if raw_text:
            lines.append(raw_text)
        lines.append("")
    return "\n".join(lines) + "\n"


def _unwrap_text_block(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _pick_markdown_from_object(payload: dict[str, Any]) -> str:
    for key in ("brief_markdown", "markdown", "brief", "result", "answer", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _strip_leaked_initial_task(text: str, topic: str = "") -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        return ""

    first = lines[0].strip().upper().rstrip(":")
    if first != "INITIAL_TASK":
        return text.strip()

    lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)

    if lines:
        first_content = lines[0].strip()
        if topic and first_content.lower() == topic.strip().lower():
            lines.pop(0)
        elif not first_content.startswith("#") and len(first_content) <= 220:
            lines.pop(0)

    while lines and not lines[0].strip():
        lines.pop(0)

    return "\n".join(lines).strip()


def _brief_markdown(brief_text: str, topic: str = "") -> str:
    text = str(brief_text or "").strip()
    if not text:
        return "_No brief generated._"

    parsed = _try_parse_dict(text)
    if isinstance(parsed, dict):
        text = _pick_markdown_from_object(parsed) or text

    text = _unwrap_text_block(text)
    text = _strip_leaked_initial_task(text, topic=topic)
    return text if text else "_No brief generated._"


def _status_block(title: str, body: str) -> str:
    return (
        f"<div class='status-card'>"
        f"<div class='status-title'>{title}</div>"
        f"<div class='status-body'>{body}</div>"
        f"</div>"
    )


def _raise_user_api_error(exc: Exception, stage: str) -> None:
    message = str(exc)
    if "504" in message:
        raise gr.Error(f"{stage} timed out (HTTP 504). Please retry in a few seconds.") from exc
    if "429" in message:
        raise gr.Error(f"{stage} hit rate limits (HTTP 429). Please retry shortly.") from exc
    raise gr.Error(f"{stage} failed: {message}") from exc


def _save_section_outputs(
    topic: str,
    section: str,
    markdown_text: str,
    json_payload: dict[str, Any],
) -> tuple[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = slugify(topic)
    md_path = OUTPUT_DIR / f"{timestamp}_{slug}_{section}.md"
    json_path = OUTPUT_DIR / f"{timestamp}_{slug}_{section}.json"
    md_path.write_text(markdown_text.strip() + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(json_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(md_path), str(json_path)


def _extract_candidate_urls(quick: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    sources = quick.get("sources", [])
    if isinstance(sources, list):
        urls.extend([str(item) for item in sources])

    json_content = quick.get("json_content", {})
    if isinstance(json_content, dict):
        raw_urls = json_content.get("urls", [])
        if isinstance(raw_urls, list):
            for item in raw_urls:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict) and isinstance(item.get("url"), str):
                    urls.append(item["url"])

    return unique_http_urls(urls)[:3]


def _scrape_sources_parallel(urls: list[str]) -> list[dict[str, Any]]:
    if not urls:
        return []

    pages_by_url: dict[str, dict[str, Any]] = {}
    missing_urls: list[str] = []

    # Reuse cached page content first.
    for url in urls:
        key = _stable_hash(url.strip())
        cached = _cache_load(SCRAPE_CACHE_DIR, key)
        if isinstance(cached, dict) and isinstance(cached.get("url"), str):
            pages_by_url[url] = cached
        else:
            missing_urls.append(url)

    if not missing_urls:
        return [pages_by_url[url] for url in urls if url in pages_by_url]

    max_workers = min(4, len(missing_urls))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(olostep_scrape, url): url for url in missing_urls}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                raw = future.result()
                result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
                metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                page_payload = {
                    "url": url,
                    "title": str(metadata.get("title") or ""),
                    "content": compact_text(result.get("markdown_content") or result.get("text_content")),
                }
                pages_by_url[url] = page_payload
                _cache_save(SCRAPE_CACHE_DIR, _stable_hash(url.strip()), page_payload)
            except Exception:
                pass
    return [pages_by_url[url] for url in urls if url in pages_by_url]


def _ensure_deep_research(topic: str, state: dict[str, Any]) -> dict[str, Any]:
    existing = state.get("deep_research")
    if isinstance(existing, dict) and existing:
        return existing

    quick = state.get("quick_answer", {})
    quick = quick if isinstance(quick, dict) else {}
    candidate_urls = _extract_candidate_urls(quick)
    if not candidate_urls:
        raise gr.Error("Quick Snapshot returned no usable sources for deep research. Try a different query.")

    scraped_pages = _scrape_sources_parallel(candidate_urls)
    deep = {
        "initial_task": topic,
        "answer_summary": str(quick.get("quick_text") or ""),
        "answer_json_content": quick.get("json_content", {}) if isinstance(quick.get("json_content"), dict) else {},
        "answer_sources": quick.get("sources", []) if isinstance(quick.get("sources"), list) else [],
        "top3_sources": candidate_urls,
        "scraped_pages": scraped_pages,
    }
    state["deep_research"] = deep
    return deep


def _require_quick_snapshot(topic: str, session: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(session or {})
    if not topic.strip():
        raise gr.Error("Please enter a research topic.")
    if "quick_answer" not in state:
        raise gr.Error("Run Quick Snapshot first.")
    return state


def run_quick_answer(topic: str, session: dict[str, Any] | None) -> tuple[Any, ...]:
    cleaned = topic.strip()
    if not cleaned:
        raise gr.Error("Please enter a research topic.")

    try:
        raw = _cached_quick_snapshot_raw(cleaned)
    except (requests.RequestException, RuntimeError) as exc:
        _raise_user_api_error(exc, "Quick snapshot")

    quick = parse_quick_answer(raw)
    sources = quick.get("sources", [])
    sources = sources if isinstance(sources, list) else []

    state: dict[str, Any] = dict(session or {})
    state.update(
        {
            "topic": cleaned,
            "quick_answer": quick,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    state.pop("deep_research", None)
    state.pop("signals", None)
    state.pop("trends", None)
    state.pop("trend_summary", None)
    state.pop("brief_markdown", None)

    status = _status_block(
        "Quick snapshot complete",
        f"Found {len(sources)} source(s). Signals/Trends/Brief tabs are now unlocked.",
    )

    return (
        state,
        status,
        _quick_markdown(cleaned, quick),
        _source_rows(sources[:12]),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        "_Signals not generated yet._",
        None,
        None,
        "_Trends not generated yet._",
        None,
        None,
        "_Brief not generated yet._",
        None,
        None,
    )


def run_signals_option(topic: str, session: dict[str, Any] | None) -> tuple[dict[str, Any], str, str, str, str]:
    state = _require_quick_snapshot(topic, session)
    deep = _ensure_deep_research(topic, state)

    _, extraction_agent, _, _ = build_agents()
    try:
        signals = run_signal_extraction(topic, deep, extraction_agent)
    except (requests.RequestException, RuntimeError) as exc:
        _raise_user_api_error(exc, "Signals generation")

    state["signals"] = signals
    markdown = _signals_markdown(signals)
    md_path, json_path = _save_section_outputs(topic, "signals", markdown, {"signals": signals})
    status = _status_block("Signals complete", f"Saved: {Path(md_path).name} and {Path(json_path).name}")
    return state, status, markdown, md_path, json_path


def run_trends_option(topic: str, session: dict[str, Any] | None) -> tuple[dict[str, Any], str, str, str, str]:
    state = _require_quick_snapshot(topic, session)
    signals = state.get("signals")
    if not isinstance(signals, list) or not signals:
        deep = _ensure_deep_research(topic, state)
        _, extraction_agent, _, _ = build_agents()
        try:
            signals = run_signal_extraction(topic, deep, extraction_agent)
        except (requests.RequestException, RuntimeError) as exc:
            _raise_user_api_error(exc, "Signals generation")
        state["signals"] = signals

    _, _, trend_agent, _ = build_agents()
    try:
        trend_result = run_trend_analysis(topic, signals, trend_agent)
    except (requests.RequestException, RuntimeError) as exc:
        _raise_user_api_error(exc, "Trend analysis")

    trends = trend_result.get("trends", [])
    trends = trends if isinstance(trends, list) else []
    summary = str(trend_result.get("summary") or "")
    state["trends"] = trends
    state["trend_summary"] = summary

    markdown = _trends_markdown(trends, summary)
    payload = {"summary": summary, "trends": trends}
    md_path, json_path = _save_section_outputs(topic, "trends", markdown, payload)
    status = _status_block("Trends complete", f"Saved: {Path(md_path).name} and {Path(json_path).name}")
    return state, status, markdown, md_path, json_path


def run_brief_option(topic: str, session: dict[str, Any] | None) -> tuple[dict[str, Any], str, str, str, str]:
    state = _require_quick_snapshot(topic, session)
    deep = state.get("deep_research")
    signals = state.get("signals")
    trends = state.get("trends")

    if not isinstance(deep, dict) or not deep:
        deep = _ensure_deep_research(topic, state)
    if not isinstance(signals, list) or not signals:
        _, extraction_agent, _, _ = build_agents()
        try:
            signals = run_signal_extraction(topic, deep, extraction_agent)
        except (requests.RequestException, RuntimeError) as exc:
            _raise_user_api_error(exc, "Signals generation")
        state["signals"] = signals
    if not isinstance(trends, list) or not trends:
        _, _, trend_agent, _ = build_agents()
        try:
            trend_result = run_trend_analysis(topic, signals, trend_agent)
        except (requests.RequestException, RuntimeError) as exc:
            _raise_user_api_error(exc, "Trend analysis")
        trends = trend_result.get("trends", [])
        trends = trends if isinstance(trends, list) else []
        state["trends"] = trends

    _, _, _, brief_agent = build_agents()
    try:
        brief = run_brief_generation(topic, deep, signals, trends, brief_agent)
    except (requests.RequestException, RuntimeError) as exc:
        _raise_user_api_error(exc, "Brief generation")

    state["brief_markdown"] = brief
    markdown = _brief_markdown(brief, topic=topic)
    payload = {"brief_markdown": brief}
    md_path, json_path = _save_section_outputs(topic, "brief", markdown, payload)
    status = _status_block("Brief complete", f"Saved: {Path(md_path).name} and {Path(json_path).name}")
    return state, status, markdown, md_path, json_path


def reset_session() -> tuple[Any, ...]:
    return (
        {},
        _status_block("Ready", "Enter a topic and run Quick Snapshot."),
        "",
        [],
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        "_Signals not generated yet._",
        None,
        None,
        "_Trends not generated yet._",
        None,
        None,
        "_Brief not generated yet._",
        None,
        None,
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title=APP_TITLE) as demo:
        session_state = gr.State({})

        gr.Markdown(
            """
<div id="hero">
  <h1 style="margin:0;">Agentic Market Research Studio</h1>
  <p style="margin:.5rem 0 0 0;">
    Generate a fast market snapshot from Olostep Answer, then produce structured signals,
    trend analysis, and a concise technical brief with cached parallel source scraping.
  </p>
</div>
""",
        )

        with gr.Row():
            with gr.Column(scale=3):
                topic = gr.Textbox(
                    label="Research Topic",
                    placeholder="e.g. AI agents for SMB lifecycle email marketing in 2026",
                    lines=2,
                    elem_id="topic_box",
                )
            with gr.Column(scale=1, min_width=180):
                quick_btn = gr.Button("Quick Snapshot", variant="primary")
                reset_btn = gr.Button("Reset", variant="secondary")

        gr.Examples(
            examples=[
                ["AI agents for SMB email and social marketing automation"],
                ["B2B SMB CRM copilots and automation trends"],
                ["AI-native campaign orchestration for local businesses"],
            ],
            inputs=topic,
        )

        status_html = gr.HTML(_status_block("Ready", "Enter a topic and run Quick Snapshot."))

        with gr.Tabs():
            with gr.Tab("Quick Snapshot", id="quick_tab"):
                quick_md = gr.Markdown()
                quick_sources = gr.Dataframe(
                    headers=["#", "Source URL"],
                    datatype=["str", "str"],
                    label="Sources",
                    wrap=True,
                )

            with gr.Tab("Signals", id="signals_tab", visible=False) as signals_tab:
                run_signals_btn = gr.Button("Run Signals", variant="primary")
                signals_md = gr.Markdown("_Signals not generated yet._")
                signals_md_file = gr.File(label="Signals Markdown File")
                signals_json_file = gr.File(label="Signals JSON File")

            with gr.Tab("Trends", id="trends_tab", visible=False) as trends_tab:
                run_trends_btn = gr.Button("Run Trends", variant="primary")
                trends_md = gr.Markdown("_Trends not generated yet._")
                trends_md_file = gr.File(label="Trends Markdown File")
                trends_json_file = gr.File(label="Trends JSON File")

            with gr.Tab("Brief", id="brief_tab", visible=False) as brief_tab:
                run_brief_btn = gr.Button("Run Brief", variant="primary")
                brief_md = gr.Markdown("_Brief not generated yet._")
                brief_md_file = gr.File(label="Brief Markdown File")
                brief_json_file = gr.File(label="Brief JSON File")

        quick_btn.click(
            fn=run_quick_answer,
            inputs=[topic, session_state],
            outputs=[
                session_state,
                status_html,
                quick_md,
                quick_sources,
                signals_tab,
                trends_tab,
                brief_tab,
                signals_md,
                signals_md_file,
                signals_json_file,
                trends_md,
                trends_md_file,
                trends_json_file,
                brief_md,
                brief_md_file,
                brief_json_file,
            ],
        )

        run_signals_btn.click(
            fn=run_signals_option,
            inputs=[topic, session_state],
            outputs=[session_state, status_html, signals_md, signals_md_file, signals_json_file],
        )

        run_trends_btn.click(
            fn=run_trends_option,
            inputs=[topic, session_state],
            outputs=[session_state, status_html, trends_md, trends_md_file, trends_json_file],
        )

        run_brief_btn.click(
            fn=run_brief_option,
            inputs=[topic, session_state],
            outputs=[session_state, status_html, brief_md, brief_md_file, brief_json_file],
        )

        reset_btn.click(
            fn=reset_session,
            inputs=[],
            outputs=[
                session_state,
                status_html,
                quick_md,
                quick_sources,
                signals_tab,
                trends_tab,
                brief_tab,
                signals_md,
                signals_md_file,
                signals_json_file,
                trends_md,
                trends_md_file,
                trends_json_file,
                brief_md,
                brief_md_file,
                brief_json_file,
            ],
        )

    return demo


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    QUICK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SCRAPE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    app = build_app()
    app.launch(theme=APP_THEME, js=FORCE_LIGHT_JS)


if __name__ == "__main__":
    main()
