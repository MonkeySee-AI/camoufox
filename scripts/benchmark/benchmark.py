#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "click>=8.1",
#   "playwright",
#   "rotunda[geoip]",
#   "tabulate",
# ]
# ///

import subprocess
import time

import click
from rotunda.pkgman import launch_path
from rotunda.sync_api import Rotunda
from rotunda.virtdisplay import VirtualDisplay
from playwright.sync_api import sync_playwright
from tabulate import tabulate

# URLs to benchmark
urls = ["about:blank", "https://google.com", "https://yahoo.com"]


def get_firefox_memory(name):
    """Get the total memory usage of all processes named 'firefox'."""
    try:
        result = subprocess.run(["ps", "-C", name, "-o", "rss="], capture_output=True, text=True)
        memory_kb = sum(int(line.strip()) for line in result.stdout.splitlines())
        memory_mb = memory_kb / 1024  # Convert KB to MB
        return memory_mb
    except Exception as e:
        print(f"Error getting Firefox memory: {e}")
        return 0


def get_average_memory(name, duration):
    """Monitor memory usage for Firefox over a duration (seconds) and return the average."""
    memory_samples = []
    for n in range(duration):
        memory_samples.append(get_firefox_memory(name))
        # print(f"> Mem ({n}sec): {memory_samples[-1]} MB")
        time.sleep(1)
    return sum(memory_samples) / len(memory_samples) if memory_samples else 0


def run_playwright(mode, browser_name):
    headless = mode == "headless"
    memory_usage = []
    # Set up virtual display
    virt = VirtualDisplay()
    env = {"DISPLAY": virt.get()}

    if browser_name == "rotunda-ubo":
        rotunda = Rotunda(headless=headless, env=env, default_addons=True)
        browser = rotunda.start()
    elif browser_name == "firefox":
        playwright = sync_playwright().start()
        browser = playwright.firefox.launch(headless=headless, env=env)
    elif browser_name == "rotunda":
        playwright = sync_playwright().start()
        browser = playwright.firefox.launch(
            headless=headless, env=env, executable_path=launch_path()
        )

    for url in urls:
        page = browser.new_page()
        page.goto(url)
        time.sleep(5)  # Allow the page to load
        process_name = "rotunda-bin" if browser_name.startswith("rotunda") else "firefox"
        memory = get_average_memory(name=process_name, duration=10)
        memory_usage.append((url, memory))
        page.close()

    browser.close()

    return memory_usage


@click.command()
@click.option(
    "--mode",
    type=click.Choice(["headless", "headful"]),
    required=True,
    help="Mode to run the browser in.",
)
@click.option(
    "--browser",
    "browser_name",
    type=click.Choice(["firefox", "rotunda", "rotunda-ubo"]),
    required=True,
    help="Browser to use for the benchmark.",
)
def main(mode: str, browser_name: str) -> None:
    """Run a browser memory benchmark."""
    # Run the benchmark
    results = run_playwright(mode, browser_name)

    # Format results as a table
    print(f"\n=== MEMORY RESULTS FOR {browser_name.upper()} ===")
    table = [["URL", "Memory Usage (MB)"]] + results
    print(tabulate(table, headers="firstrow", tablefmt="grid"))


if __name__ == "__main__":
    main()
