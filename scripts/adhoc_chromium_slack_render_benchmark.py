#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "playwright>=1.51",
#   "rotunda[geoip]",
# ]
# ///

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from rotunda.pkgman import launch_path
from rotunda.sync_api import NewBrowser

DEFAULT_URL = "https://slack.com/signin#/signin"
FIELD_SELECTOR = (
    "input:not([type=hidden]):not([disabled]),"
    "textarea:not([disabled]),"
    "select:not([disabled]),"
    "[contenteditable='true']"
)
CACHE_PREFS = {
    "browser.cache.disk.enable": True,
    "browser.cache.disk.smart_size.enabled": True,
    "browser.cache.disk_cache_ssl": True,
    "browser.cache.memory.enable": True,
    "browser.sessionhistory.max_entries": 10,
    "browser.sessionhistory.max_total_viewers": -1,
}


INIT_SCRIPT = f"""
(() => {{
  const selector = {FIELD_SELECTOR!r};
  const state = window.__rotundaSlackBench = {{
    firstVisibleFieldMs: null,
    longTasks: [],
    paints: {{}},
  }};

  function isVisible(el) {{
    const style = window.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none")
      return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }}

  function scanFields() {{
    if (state.firstVisibleFieldMs !== null)
      return;
    for (const el of document.querySelectorAll(selector)) {{
      if (isVisible(el)) {{
        state.firstVisibleFieldMs = performance.now();
        return;
      }}
    }}
  }}

  new MutationObserver(scanFields).observe(document, {{
    attributes: true,
    childList: true,
    subtree: true,
  }});
  document.addEventListener("DOMContentLoaded", scanFields, true);
  window.addEventListener("load", scanFields, true);
  requestAnimationFrame(scanFields);

  try {{
    new PerformanceObserver(list => {{
      for (const entry of list.getEntries())
        state.paints[entry.name] = entry.startTime;
    }}).observe({{ type: "paint", buffered: true }});
  }} catch (e) {{}}

  try {{
    new PerformanceObserver(list => {{
      for (const entry of list.getEntries()) {{
        state.longTasks.push({{
          startTime: entry.startTime,
          duration: entry.duration,
          name: entry.name,
        }});
      }}
    }}).observe({{ type: "longtask", buffered: true }});
  }} catch (e) {{}}
}})();
"""


def main() -> None:
    args = parse_args()
    output = Path(args.output) if args.output else None
    summary_output = Path(args.summary_json) if args.summary_json else None

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    start_record = {
        "browser": args.browser,
        "cache_override": args.cache_override,
        "default_addons": args.default_addons,
        "event": "benchmark_start",
        "headless": args.headless,
        "iterations": args.iterations,
        "reuse_context": args.reuse_context,
        "ts": utc_now(),
        "url": args.url,
        "viewport": [args.viewport_width, args.viewport_height],
        "wait_until": args.wait_until,
        "warmup_pages": args.warmup_pages,
    }
    records.append(start_record)
    write_jsonl(output, start_record)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, args)
        try:
            launch_record = {
                "browser_type": args.browser,
                "duration_ms": round((time.perf_counter() - launch_browser.start_time) * 1000, 1),
                "event": "browser_launched",
                "ts": utc_now(),
            }
            records.append(launch_record)
            write_jsonl(output, launch_record)

            if args.reuse_context:
                context = new_context(browser, args)
                try:
                    run_warmups(context, args, output, records)
                    for iteration in range(1, args.iterations + 1):
                        record = run_iteration(context, args, iteration)
                        records.append(record)
                        write_jsonl(output, record)
                        print_iteration(record)
                finally:
                    context.close()
            else:
                for iteration in range(1, args.iterations + 1):
                    context = new_context(browser, args)
                    try:
                        run_warmups(context, args, output, records)
                        record = run_iteration(context, args, iteration)
                        records.append(record)
                        write_jsonl(output, record)
                        print_iteration(record)
                    finally:
                        context.close()
        finally:
            browser.close()

    summary = build_summary(records, args)
    if summary_output:
        summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary["summary"], indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Slack sign-in first-field render timing across Firefox/Rotunda."
    )
    parser.add_argument(
        "--browser",
        choices=("firefox", "chromium", "rotunda", "rotunda-raw"),
        default="rotunda",
        help=(
            "firefox/chromium use Playwright browsers; rotunda uses Rotunda launch_options; "
            "rotunda-raw launches the installed Rotunda executable directly."
        ),
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warmup-pages", type=int, default=1)
    parser.add_argument("--reuse-context", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cache-override", action="store_true")
    parser.add_argument("--default-addons", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--wait-until", default="commit")
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--settle-ms", type=int, default=200)
    parser.add_argument("--viewport-width", type=int, default=1440)
    parser.add_argument("--viewport-height", type=int, default=960)
    parser.add_argument("--output")
    parser.add_argument("--summary-json")
    return parser.parse_args()


def launch_browser(playwright: Playwright, args: argparse.Namespace) -> Browser:
    launch_browser.start_time = time.perf_counter()
    prefs = CACHE_PREFS if args.cache_override else None
    if args.browser == "chromium":
        return playwright.chromium.launch(headless=args.headless)
    if args.browser == "firefox":
        kwargs: dict[str, Any] = {"headless": args.headless}
        if prefs:
            kwargs["firefox_user_prefs"] = prefs
        return playwright.firefox.launch(**kwargs)
    if args.browser == "rotunda-raw":
        kwargs = {
            "executable_path": launch_path(),
            "headless": args.headless,
        }
        if prefs:
            kwargs["firefox_user_prefs"] = prefs
        return playwright.firefox.launch(**kwargs)
    return NewBrowser(
        playwright,
        headless=args.headless,
        enable_cache=args.cache_override,
        default_addons=args.default_addons,
    )


launch_browser.start_time = 0.0


def new_context(browser: Browser, args: argparse.Namespace) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": args.viewport_width, "height": args.viewport_height}
    )
    context.add_init_script(INIT_SCRIPT)
    return context


def run_warmups(
    context: BrowserContext,
    args: argparse.Namespace,
    output: Path | None,
    records: list[dict[str, Any]],
) -> None:
    for warmup_index in range(1, args.warmup_pages + 1):
        page = context.new_page()
        started = time.perf_counter()
        try:
            page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout_ms)
            wait_for_first_field(page, args.timeout_ms)
        finally:
            page.close()
        record = {
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "event": "warmup_done",
            "ts": utc_now(),
            "warmup_index": warmup_index,
        }
        records.append(record)
        write_jsonl(output, record)


def run_iteration(
    context: BrowserContext, args: argparse.Namespace, iteration: int
) -> dict[str, Any]:
    page = context.new_page()
    started = time.perf_counter()
    try:
        page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout_ms)
        goto_ms = elapsed_ms(started)
        wait_for_first_field(page, args.timeout_ms)
        first_field_ms = elapsed_ms(started)
        page.wait_for_timeout(args.settle_ms)
        perf = collect_performance(page)
        status = "ok"
        error = None
    except Exception as exc:
        goto_ms = None
        first_field_ms = None
        perf = {}
        status = "error"
        error = repr(exc)
    finally:
        page.close()

    navigation = perf.get("navigation") or {}
    fcp = (perf.get("paints") or {}).get("first-contentful-paint")
    field_observer_first = perf.get("field_observer_first_ms")
    dcl = navigation.get("dom_content_loaded_ms")
    response_end = navigation.get("response_end_ms")
    return {
        "derived": {
            "field_observer_first_ms": round_number(field_observer_first),
            "first_contentful_paint_ms": round_number(fcp),
            "main_document_network_ms": round_number(response_end),
            "playwright_first_field_ms": first_field_ms,
            "post_response_to_dom_content_loaded_ms": round_delta(dcl, response_end),
        },
        "error": error,
        "event": "iteration_summary",
        "first_field_ms": first_field_ms,
        "goto_ms": goto_ms,
        "iteration": iteration,
        "perf": perf,
        "status": status,
        "total_ms": elapsed_ms(started),
        "ts": utc_now(),
    }


def wait_for_first_field(page: Page, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        selector => {
          for (const el of document.querySelectorAll(selector)) {
            const style = window.getComputedStyle(el);
            if (style.visibility === "hidden" || style.display === "none")
              continue;
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0)
              return true;
          }
          return false;
        }
        """,
        arg=FIELD_SELECTOR,
        timeout=timeout_ms,
    )


def collect_performance(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const bench = window.__rotundaSlackBench || {};
          const nav = performance.getEntriesByType("navigation")[0];
          const navigation = nav ? {
            connect_ms: nav.connectEnd - nav.connectStart,
            decoded_body_size: nav.decodedBodySize,
            dom_content_loaded_ms: nav.domContentLoadedEventEnd,
            dom_interactive_ms: nav.domInteractive,
            encoded_body_size: nav.encodedBodySize,
            fetch_start_ms: nav.fetchStart,
            load_event_ms: nav.loadEventEnd,
            protocol: nav.nextHopProtocol,
            redirect_count: nav.redirectCount,
            request_start_ms: nav.requestStart,
            request_to_first_byte_ms: nav.responseStart - nav.requestStart,
            response_download_ms: nav.responseEnd - nav.responseStart,
            response_end_ms: nav.responseEnd,
            response_start_ms: nav.responseStart,
            transfer_size: nav.transferSize,
            type: nav.type,
            url: nav.name,
          } : {};
          const resources = performance.getEntriesByType("resource").map(entry => ({
            decoded_body_size: entry.decodedBodySize || 0,
            duration_ms: entry.duration,
            encoded_body_size: entry.encodedBodySize || 0,
            initiator_type: entry.initiatorType,
            name: entry.name,
            response_end_ms: entry.responseEnd,
            start_ms: entry.startTime,
            transfer_size: entry.transferSize || 0,
          }));
          const totalsByType = {};
          for (const entry of resources) {
            const key = entry.initiator_type || "other";
            totalsByType[key] ||= {
              count: 0,
              duration_sum_ms: 0,
              encoded_body_size: 0,
              max_response_end_ms: 0,
              transfer_size: 0,
            };
            totalsByType[key].count += 1;
            totalsByType[key].duration_sum_ms += entry.duration_ms;
            totalsByType[key].encoded_body_size += entry.encoded_body_size;
            totalsByType[key].max_response_end_ms = Math.max(
              totalsByType[key].max_response_end_ms,
              entry.response_end_ms
            );
            totalsByType[key].transfer_size += entry.transfer_size;
          }
          const slowest = resources
            .slice()
            .sort((a, b) => b.duration_ms - a.duration_ms)
            .slice(0, 12)
            .map(entry => {
              let host = "";
              try { host = new URL(entry.name).host; } catch (e) {}
              return { ...entry, host, url: entry.name };
            });
          const fields = Array.from(document.querySelectorAll(
            "input:not([type=hidden]),textarea,select,[contenteditable='true']"
          )).filter(el => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== "hidden" && style.display !== "none" &&
              rect.width > 0 && rect.height > 0;
          });
          return {
            body_text_length: document.body ? document.body.innerText.length : 0,
            dom: {
              node_count: document.querySelectorAll("*").length,
              script_count: document.scripts.length,
              stylesheet_count: document.styleSheets.length,
              visible_field_count: fields.length,
            },
            field_observer_first_ms: bench.firstVisibleFieldMs,
            long_tasks: {
              count: (bench.longTasks || []).length,
              total_ms: (bench.longTasks || []).reduce((sum, item) => sum + item.duration, 0),
              slowest: (bench.longTasks || [])
                .slice()
                .sort((a, b) => b.duration - a.duration)
                .slice(0, 5),
            },
            navigation,
            now_ms: performance.now(),
            paints: bench.paints || {},
            ready_state: document.readyState,
            resources: {
              count: resources.length,
              slowest,
              totals_by_type: totalsByType,
              transfer_size: resources.reduce((sum, entry) => sum + entry.transfer_size, 0),
            },
            title: document.title,
            url: location.href,
            visible_field_count: fields.length,
          };
        }
        """
    )


def build_summary(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    iterations = [record for record in records if record.get("event") == "iteration_summary"]
    statuses = Counter(str(record.get("status")) for record in iterations)
    summary = {
        "iterations": len(iterations),
        "statuses": dict(statuses),
        "first_field_ms": summarize([record.get("first_field_ms") for record in iterations]),
        "field_observer_first_ms": summarize(
            [record.get("derived", {}).get("field_observer_first_ms") for record in iterations]
        ),
        "dom_content_loaded_ms": summarize(
            [
                (record.get("perf", {}).get("navigation") or {}).get("dom_content_loaded_ms")
                for record in iterations
            ]
        ),
        "first_contentful_paint_ms": summarize(
            [record.get("derived", {}).get("first_contentful_paint_ms") for record in iterations]
        ),
        "goto_ms": summarize([record.get("goto_ms") for record in iterations]),
        "main_document_network_ms": summarize(
            [record.get("derived", {}).get("main_document_network_ms") for record in iterations]
        ),
        "post_response_to_dom_content_loaded_ms": summarize(
            [
                record.get("derived", {}).get("post_response_to_dom_content_loaded_ms")
                for record in iterations
            ]
        ),
        "total_ms": summarize([record.get("total_ms") for record in iterations]),
        "slowest_cached_resource_ms": summarize(slowest_cached_resource_ms(iterations)),
    }
    return {
        "benchmark": "Slack sign-in first visible field render",
        "browser": args.browser,
        "cache_override": args.cache_override,
        "default_addons": args.default_addons,
        "generated_at": utc_now(),
        "summary": summary,
        "url": args.url,
    }


def slowest_cached_resource_ms(iterations: list[dict[str, Any]]) -> list[float]:
    values = []
    for record in iterations:
        resources = (record.get("perf", {}).get("resources") or {}).get("slowest") or []
        cached = [
            item.get("duration_ms")
            for item in resources
            if item.get("transfer_size") == 0
            and item.get("initiator_type") in {"script", "link", "css", "stylesheet"}
        ]
        values.append(max((float(value) for value in cached if is_number(value)), default=None))
    return values


def summarize(values: list[Any]) -> dict[str, Any]:
    numbers = [float(value) for value in values if is_number(value)]
    if not numbers:
        return {"count": 0}
    numbers.sort()
    return {
        "count": len(numbers),
        "max": round(numbers[-1], 1),
        "min": round(numbers[0], 1),
        "p50": round(statistics.median(numbers), 1),
        "p90": round(percentile(numbers, 0.9), 1),
    }


def percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    index = min(max(int(round((len(values) - 1) * quantile)), 0), len(values) - 1)
    return values[index]


def print_iteration(record: dict[str, Any]) -> None:
    derived = record.get("derived", {})
    print(
        "iteration={iteration} status={status} first_field={first_field_ms} "
        "dcl={dcl} fcp={fcp} main_doc_net={main_doc_net} post_resp_to_dcl={post_resp}"
        .format(
            iteration=record.get("iteration"),
            status=record.get("status"),
            first_field_ms=record.get("first_field_ms"),
            dcl=round_number((record.get("perf", {}).get("navigation") or {}).get("dom_content_loaded_ms")),
            fcp=derived.get("first_contentful_paint_ms"),
            main_doc_net=derived.get("main_document_network_ms"),
            post_resp=derived.get("post_response_to_dom_content_loaded_ms"),
        )
    )


def write_jsonl(path: Path | None, record: dict[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def round_delta(left: Any, right: Any) -> float | None:
    if not is_number(left) or not is_number(right):
        return None
    return round(float(left) - float(right), 1)


def round_number(value: Any) -> float | None:
    if not is_number(value):
        return None
    return round(float(value), 1)


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
