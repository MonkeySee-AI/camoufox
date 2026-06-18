# Benchmarks

This document records one local agent-control benchmark against the Acme Mock
Console example app. It is a small workflow benchmark, not a universal measure
of browser speed.

## Environment

- Date: 2026-06-18
- App: local FastAPI/Jinja Acme Mock Console
- URL: `http://127.0.0.1:8000`
- Server command: `python -m uvicorn main:app --host 127.0.0.1 --port 8000`
- Server mode: no reload watcher

The app kept state in memory. Each agent used unique labels so its created
project and contact message could be identified after all runs.

## Results

| Surface | Result | Observed duration | Tool/call count | Screenshots |
| --- | ---: | ---: | ---: | ---: |
| Codex integrated browser | Pass | ~2m 26s | 12 browser batches | 0 |
| Rotunda CLI | Pass | ~3m 40s | 33 `uvx rotunda agent` commands | 0 |
| Computer Use | Pass | ~8m 13s | 69 UI/tool calls | 0 |

The integrated browser was fastest because it used direct browser/DOM control
and did not include humanized mouse or keyboard timing. Rotunda CLI used the
agent-facing command surface and kept human-like input behavior for actions
such as `fill`, `select`, and `click`, so per-action latency is expected.

Computer Use was slow in this run because Chrome produced blank paints and
stale accessibility/form state. The agent switched to Safari and completed the
workflow there.

## Shared Workflow

Each agent was instructed to complete the same browser-visible workflow:

1. Load the Dashboard and verify heading/navigation plus stat labels or
   dashboard text.
2. Go to Projects and create a project with status `paused` and progress `47`.
   Do not delete records.
3. Go to Contact and submit a message with topic `Bug report` and the subscribe
   checkbox checked.
4. Go to Components, open and close the modal, fire the toast, and expand the
   disclosure.

The per-agent data labels were:

| Surface | Project | Owner/name | Email |
| --- | --- | --- | --- |
| Rotunda CLI | `Rotunda Timed Project` | `Rotunda Timed Agent` | `rotunda-timed@example.com` |
| Codex integrated browser | `Browser Timed Project` | `Browser Timed Agent` | `browser-timed@example.com` |
| Computer Use | `Computer Timed Project` | `Computer Timed Agent` | `computer-timed@example.com` |

## Agent Instructions

### Rotunda CLI

The Rotunda agent was instructed to use only `uvx rotunda agent ...` commands.
It was explicitly told not to use stock Playwright, the Codex integrated
browser, Computer Use, raw HTTP assertions for app behavior, or screenshots by
default.

The screenshot rule was:

> Do not sanity check with screenshots if regular Rotunda CLI calls suffice.
> Prefer `navigate`, `describe`, `extract --format text|markdown|forms|html`,
> `info`, URL flags, and action output/diffs. Only take a screenshot if a normal
> CLI assertion is inconclusive or fails, and report why.

Observed Rotunda details:

- Pass on all workflow items.
- Used zero screenshots.
- Used 33 Rotunda CLI commands.
- Had one avoidable retry: `select --by label ... "Paused"` timed out because
  the option label/value was lowercase `paused`; selecting `paused` succeeded.
- Some same-page UI changes, such as toast and disclosure, returned
  `page: stayed the same`, so text/HTML extraction was used to verify state.

The largest Rotunda command costs in this run were:

| Command shape | Time |
| --- | ---: |
| failed `select --by label "Paused"` | 15.45s |
| fill contact message | 6.04s |
| fill email | 4.67s |
| fill project name | 4.21s |
| fill owner | 4.00s |

### Codex Integrated Browser

The integrated-browser agent was instructed to use only the Codex integrated
Browser skill/API. It was told not to use Rotunda, Computer Use, raw HTTP
assertions for app behavior, or screenshots unless DOM/browser state was
inconclusive.

Observed integrated-browser details:

- Pass on all workflow items.
- Used zero screenshots.
- Used 12 browser batches.
- Checked browser logs and found no console errors.
- Reported no retries or failures.

### Computer Use

The Computer Use agent was instructed to use only UI interaction tools against
a local browser app. It was told not to use Rotunda, integrated-browser DOM
APIs, raw HTTP assertions for app behavior, or file edits.

Observed Computer Use details:

- Pass on all workflow items.
- Used zero screenshots.
- Used about 69 UI/tool calls total.
- Chrome was unreliable in this run: repeated blank paints, inconsistent modal
  visibility, and stale accessibility/form state caused duplicated owner text.
- The agent switched to Safari, where the app completed successfully.
- One minor Safari retry corrected the progress field from `047` to `47`.

## Interpretation

This benchmark shows the expected tradeoff between direct DOM control and
human-like browser control.

The Codex integrated browser is fastest for local DOM-heavy applications
because it can interact directly with the page without human input timing.
Rotunda CLI is slower because it intentionally routes interactions through
humanized browser actions and uses short CLI invocations that preserve a local
browser profile and page resource indexes. Computer Use is the least stable
surface for this specific benchmark because it depends on the host browser UI
and accessibility state.

For future Rotunda timing runs, keep screenshots out of the success path unless
the CLI-visible state is inconclusive. Also prefer known option values, such as
`paused`, over guessed labels, such as `Paused`, when selecting dropdowns.
