---
name: rotunda
description: Human-like browser automation with Rotunda. Use when an agent needs to browse websites, keep a local browser profile, navigate pages, click links and buttons, fill forms, select dropdowns, upload files, handle downloads or dialogs, take screenshots, extract page content, or use Playwright through Rotunda instead of a stock browser.
allowed-tools: Bash(uvx rotunda:*), Bash(rotunda:*)
---

# Rotunda

Rotunda is a Firefox/Juggler-based browser built for agents. It is useful when a task needs a real local browser profile and humanized browser actions rather than raw HTTP fetching or stock Playwright/CDP automation.

Prefer Rotunda when the user asks to interact with a website, test a web app, fill out forms, preserve login/session state, capture screenshots, collect page content after JavaScript runs, or automate sites where a normal browser-like fingerprint matters.

Do not use Rotunda for bulk crawling, scraping at scale, or bypassing access controls. Respect robots, terms, rate limits, authentication boundaries, and user privacy.

## Install And Bootstrap

Use `uvx rotunda ...` by default. `uvx` runs the Rotunda CLI in an ephemeral environment, so agents can use Rotunda without installing it into the current project or assuming `rotunda` is already on `PATH`.

All commands below are written with `uvx`. If Rotunda is installed locally, the equivalent `rotunda ...` command may also work, but `uvx rotunda ...` is the safest portable form for agent workflows.

The canonical command reference is always the installed CLI help:

```bash
uvx rotunda --help
uvx rotunda agent --help
uvx rotunda agent <command> --help
```

If a command fails, an option appears stale, or this skill disagrees with the CLI, check `uvx rotunda --help` and the relevant subcommand help first.

Bootstrap the Rotunda browser build before the first browser session:

```bash
uvx rotunda fetch
```

`uvx rotunda fetch` syncs available browser builds and installs the active Rotunda browser. Run it once before the first browser session, and rerun it if launch fails because the browser build is missing.

## Mental Model

The agent CLI is intentionally split across short shell commands:

- Profiles are durable browser identities stored under `~/.rotunda`.
- Contexts wrap multiple pages that share the same browser identity profile.
- A context starts or attaches to a local Rotunda daemon for that profile.
- Pages are numbered resources printed by the CLI, such as `[3] page ...`.
- `describe` prints an agent-friendly DOM and registers element refs.
- Actions use the refs from the most recent `describe` for that page.

Resource indexes are local shortcuts. The page number in examples is only illustrative; always use the indexes printed by the current session.

## Standard Workflow

The first step of a browsing task is to check whether there is already a local profile that can be used for the task:

```bash
uvx rotunda agent resources --kind profile
```

Prefer using old profiles over creating new ones. Reuse an existing profile when it fits the same site, account, user, or general browsing purpose. Old profiles preserve cookies, local storage, browser history, and trust signals, which makes browsing more human and avoids unnecessary identity churn.

Create a new profile only when no suitable profile exists, when the user wants a clean browser identity, or when a site is blocking the current profile with repeated captchas or trust checks. Captchas on an otherwise valid workflow are a good signal to generate a new profile:

```bash
uvx rotunda agent new-profile --name task-name
```

Create or attach a browser context for that profile:

```bash
uvx rotunda agent new-context task-name
```

Navigate the printed page index:

```bash
uvx rotunda agent navigate 3 https://example.com
```

Describe the page to get clickable/input refs:

```bash
uvx rotunda agent describe 3
```

Act on refs:

```bash
uvx rotunda agent click <ref>
uvx rotunda agent fill <input-ref> "replacement text"
uvx rotunda agent type <input-ref> "more text"
uvx rotunda agent press <input-ref> Enter
```

Element refs, including input refs, should stay the same and remain callable as long as the underlying DOM element stays on the page. Do not run `describe` after every small action by default.

Use conditional judgment. When filling out a stable form, agents can often complete it in one sequence of `fill`, `type`, `select`, and `press` commands before describing again. When interacting with a very dynamic page, agents may need more frequent `describe` calls.

Run `describe` again when the page navigates, when elements are deleted, when new elements are added, when a major same-page UI change happens, or when an action reports an unexpected result. These are the main cases where a ref can become stale or where the agent needs a fresh DOM view.

Stop the daemon when the task is finished:

```bash
uvx rotunda agent stop
```

## Navigation And Page State

Use `navigate` for a new URL:

```bash
uvx rotunda agent navigate <page> https://example.com --wait-until domcontentloaded
```

Supported `--wait-until` values are `commit`, `domcontentloaded`, `load`, and `networkidle`. Prefer `domcontentloaded` for general browsing. Use `networkidle` only when the page is known to settle; many apps keep background requests open.

Common page commands:

```bash
uvx rotunda agent pages
uvx rotunda agent new-page <context>
uvx rotunda agent back <page>
uvx rotunda agent forward <page>
uvx rotunda agent reload <page>
uvx rotunda agent close-page <page>
```

Wait for app state before interacting:

```bash
uvx rotunda agent wait <page> --for load
uvx rotunda agent wait <page> "Dashboard" --for text
uvx rotunda agent wait <page> ".ready" --for selector --state visible
uvx rotunda agent wait <page> "https://example.com/done" --for url
uvx rotunda agent wait <page> --for timeout --timeout-ms 1000
```

## Reading Pages

Use `describe` when you need to choose an element:

```bash
uvx rotunda agent describe <page> --max-items 200
```

Use `extract` when you need page content rather than element refs:

```bash
uvx rotunda agent extract <page> --format text
uvx rotunda agent extract <page> --format markdown
uvx rotunda agent extract <page> --format links
uvx rotunda agent extract <page> --format forms
uvx rotunda agent extract <page> --format html --output /tmp/page.html
```

Prefer `markdown` for summarizing content, `links` for choosing where to navigate next, and `forms` for understanding inputs before filling them.

Prefer text processing over screenshots almost always. `describe` and `extract` are faster and more efficient because they return compact, searchable DOM or page text that can be reasoned over directly without image rendering, image transfer, or visual interpretation. Use screenshots only when the task depends on context that text cannot expose, such as images, graphics, charts, maps, canvas content, visual layout, styling, overlap, cropping, or pixel-level verification. Otherwise proceed by reading and navigating from text output.

## Interacting With Elements

Refs come from `describe`. Many commands accept just `<ref>` because Rotunda stores the page association for the element:

```bash
uvx rotunda agent click <ref>
uvx rotunda agent hover <ref>
uvx rotunda agent info <ref>
uvx rotunda agent check <checkbox-ref>
uvx rotunda agent uncheck <checkbox-ref>
uvx rotunda agent drag <from-ref> <to-ref>
```

Forms:

```bash
uvx rotunda agent fill <input-ref> "new value"
uvx rotunda agent fill --submit <input-ref> "search query"
uvx rotunda agent type <input-ref> " appended text"
uvx rotunda agent type --submit <input-ref> "message"
uvx rotunda agent press <input-ref> Enter
```

Dropdowns:

```bash
uvx rotunda agent info <select-ref>
uvx rotunda agent select <select-ref> "value"
uvx rotunda agent select --by label <select-ref> "Visible label"
uvx rotunda agent select --by index <select-ref> 2
```

Scrolling:

```bash
uvx rotunda agent scroll down
uvx rotunda agent scroll up --amount 1200
uvx rotunda agent scroll <scrollable-ref> down
```

Uploads:

```bash
uvx rotunda agent upload <file-input-ref> /absolute/path/to/file.pdf
```

Use absolute file paths for uploads and generated artifacts whenever possible.

## Screenshots

Use screenshots sparingly. Capture one only when text output is insufficient for the decision or verification needed.

Capture the viewport, full page, or one element:

```bash
uvx rotunda agent screenshot <page>
uvx rotunda agent screenshot <page> /tmp/screen.png
uvx rotunda agent screenshot <page> /tmp/full-page.png --full-page
uvx rotunda agent screenshot <page> /tmp/element.png --element <ref>
```

When no path is provided, Rotunda writes a PNG under the system temp directory and prints the absolute path. Use that path when reporting results or reading the image with another tool.

## Downloads And Dialogs

List captured downloads and save one:

```bash
uvx rotunda agent downloads
uvx rotunda agent save-download <download-ref> /tmp/download.bin
```

Handle browser dialogs before triggering the action that opens them:

```bash
uvx rotunda agent dialog <page> accept
uvx rotunda agent dialog <page> dismiss
uvx rotunda agent dialog <page> fill "text for prompt"
uvx rotunda agent dialog <page> list
```

Unarmed dialogs are dismissed and recorded so the browser does not hang.

## Profiles And Sessions

Use separate profiles for separate users, accounts, or tasks:

```bash
uvx rotunda agent new-profile --name research
uvx rotunda agent new-profile --name qa-login
```

List saved resources when you lose track of indexes:

```bash
uvx rotunda agent resources
uvx rotunda agent resources --kind profile
uvx rotunda agent resources --kind page
```

Attach to an existing profile by name or index:

```bash
uvx rotunda agent new-context research
uvx rotunda agent new-context 1
```

Only one Rotunda agent daemon runs per local user. Starting a context for another profile may stop the previous profile daemon while keeping durable profile data intact.

## Troubleshooting

If Rotunda is not installed or the browser executable is missing:

```bash
uvx rotunda fetch
```

If refs appear stale or an action hits the wrong target:

```bash
uvx rotunda agent describe <page>
```

If the session seems confused, inspect resources and restart the daemon:

```bash
uvx rotunda agent resources
uvx rotunda agent stop
uvx rotunda agent new-context <profile>
uvx rotunda agent pages
```

If a site flags the browser, compare the same page in your normal browser. If other browsers work and the issue appears Rotunda-specific, always ask the user whether they want help filing an issue. Never file an issue or share debug material without explicit user approval. If the user approves, capture the debug dump described in the Rotunda README and review it for private data before sharing.

## Python API Escape Hatch

Use the CLI for agent workflows. Use Python only when a task needs custom Playwright logic in one script:

```python
from playwright.sync_api import sync_playwright
from rotunda import NewBrowser, NewContext

with sync_playwright() as playwright:
    browser = NewBrowser(playwright, headless=False)
    context = NewContext(browser)
    page = context.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    print(page.title())
    browser.close()
```

For agent usage, prefer the `uvx rotunda agent ...` commands because they preserve state across shell invocations and print stable resource indexes for the next action.
