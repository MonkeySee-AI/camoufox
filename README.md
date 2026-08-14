# Rotunda

![Rotunda Logo](https://raw.githubusercontent.com/MonkeySee-AI/rotunda/main/docs/media/header.png)

Giving your agents the power to browse the web is like giving them superpowers. You can automate almost anything. Rotunda is a browser built for agents from the ground up. Sick of seeing more captchas in Claude than when you open Chrome and do it yourself? Try Rotunda.

<p align="center">
  <a href="https://rotunda.sh/#demo">
    <img src="docs/media/amazon-demo.png" alt="Rotunda Amazon demo" width="480" />
  </a>
  <br />
  <em><a href="https://rotunda.sh/#demo">Check out the demo!</a></em>
</p>

## Getting started

Install Rotunda into your Python project with `uv`, then fetch the latest Rotunda browser build:

```bash
uv add rotunda
uv run rotunda fetch
```

`rotunda fetch` syncs the available browser releases and installs the latest build for the active channel.

Then use Rotunda from Playwright by swapping in the Rotunda launch helper and creating contexts with `NewContext`:

```python
from playwright.sync_api import sync_playwright
from rotunda import NewBrowser, NewContext


with sync_playwright() as playwright:
    browser = NewBrowser(playwright, headless=False)
    context = NewContext(browser)
    page = context.new_page()

    page.goto("https://pierce.dev", wait_until="domcontentloaded")
    first_article_text = page.locator("main article article").first.inner_text()
    print(first_article_text)

    browser.close()
```

Default add-ons are opt-in because they are a page-dependent tradeoff. uBlock
takes extra processing time, but it can still be net faster on pages with lots
of ads or trackers. For SaaS sites with minimal or no ads, keep default add-ons
off so Rotunda does not spend time processing unnecessary extension rules.
Pass `NewBrowser(playwright, default_addons=True)` when that tradeoff is useful.

### Isolated eval

For custom DOM reads that should avoid page monkeypatches, Rotunda exposes
Playwright's isolated utility context:

```python
from rotunda import evaluate_in_utility

title = evaluate_in_utility(page, "() => document.title")
```

Async code can use `async_evaluate_in_utility(page, expression, arg=None)`.
Import `rotunda` before starting Playwright so Rotunda can install the driver
preload; `Rotunda(...)` / `AsyncRotunda(...)` do this automatically.

## Agent

You can also drive Rotunda directly from the command line with `uvx`, without adding it to a project first. The agent commands keep browser profiles, daemon sessions, and short resource indexes under `~/.rotunda`, so later `uvx rotunda ...` calls can attach to the same profile.

For the daemon, resource-index, heartbeat, and singleton process model behind these commands, see [Agent CLI Architecture](docs/agent-cli-architecture.md).

For agent clients that support installable skills, see the [Rotunda skill](skills/rotunda/SKILL.md) for a compact operating guide.

First install the active browser build and create a profile:

```bash
uvx rotunda fetch
uvx rotunda agent new-profile --name agent-demo
```

Create a browser context by passing the profile name to `new-context`, then navigate the printed page index. The page number below is an example; use the index printed by your commands:

```bash
uvx rotunda agent new-context agent-demo
uvx rotunda agent navigate 3 https://pierce.dev
```

Describe the page to get element refs:

```bash
uvx rotunda agent describe 3
```

Use those refs directly for actions. You do not need to pass the page index once a ref has been described:

```bash
uvx rotunda agent click <ref>
uvx rotunda agent hover <ref>
uvx rotunda agent info <select-ref>
uvx rotunda agent select <select-ref> "option-value"
uvx rotunda agent fill <input-ref> "replacement text"
uvx rotunda agent type <input-ref> "additional text"
uvx rotunda agent press <input-ref> Enter
uvx rotunda agent scroll down
uvx rotunda agent check <checkbox-ref>
```

`info` prints the full attributes, state, bounds, and select options for one element. `select` chooses dropdown options by value by default; use `--by label` or `--by index` when that is more convenient. `fill` replaces the field contents, while `type` appends at the focused cursor position. Both use Rotunda's humanized text input path, and mouse actions use Rotunda's path prediction when humanization is enabled.

After an action, the CLI reports whether the page had a full refresh or mostly stayed the same. Same-page updates print a compact `+`/`-` element delta; run `describe` again when you want the full current DOM.

The agent CLI also includes broader browser primitives for less form-like tasks:

```bash
uvx rotunda agent pages
uvx rotunda agent screenshot 3 --full-page
uvx rotunda agent wait 3 --for text "Done"
uvx rotunda agent back 3
uvx rotunda agent forward 3
uvx rotunda agent reload 3
uvx rotunda agent extract 3 --format markdown
uvx rotunda agent upload <file-input-ref> ./document.pdf
uvx rotunda agent downloads
uvx rotunda agent save-download <download-ref> ./download.bin
uvx rotunda agent dialog 3 accept
uvx rotunda agent close-page 3
```

`screenshot` can capture the viewport, full page, or one described element with `--element <ref>`; when no path is provided, it writes a randomly named PNG under the system temp directory and prints the absolute filepath. `wait` supports load states, URL patterns, visible text, selectors, and fixed timeouts. `extract` can return text, HTML, markdown, links, or form metadata. `dialog` arms how the next browser dialog on a page should be handled; unarmed dialogs are dismissed and recorded so the browser does not hang.

Stop the profile daemon when you are done:

```bash
uvx rotunda agent stop 1
```

## Additional reading

- [Remote Juggler](docs/remote-juggler.md): launch Rotunda with a fixed Juggler endpoint and connect from another local process.
- [Video Streaming](docs/video-streaming.md): stream native H.264 or H.265 viewport and element video to your own client, with a complete WebRTC example.
- [Live Screencast Stream](docs/live-screencast-stream.md): stream Rotunda browser frames over HLS for QuickTime or VLC.
- [Agent CLI Architecture](docs/agent-cli-architecture.md): understand the daemon, resource-index, heartbeat, and singleton process model behind `uvx rotunda agent`.

## On stealth browsing

Web automation is incredible. Unfortunately for us, so many people have abused the automation powers of browsers in the past (ticket scalpers, shoe resellers) that sites have poured billions into detecting anything that's not a human. If you run Chrome over CDP with Playwright you'll know what I'm talking about. You get recaptchas, refusals to login, or subtle changes in behavior.

"Stealth" plugins advertise that they're able to evade these detections. But all stealth plugins are flawed. They often rely on overriding Javascript properties to return fake values that simulate another browser. Fingerprinters will check if these function implementations are native or non-native. Non-native never happens in the wild so you're flagged as a bot. Other plugins will fork Chromium and patch code that do the same things on the backend, so you'll be unable to detect them by sniffing Javascript state. Fingerprinters then use browser accessories like the canvas or audio drivers to detect anomalies with known devices. And so you're flagged as a bot. And on and on.

This cat and mouse game has been around since the beginning of the web. As fingerprinting has switched from adhoc to statistical, the burden has shifted dramatically to the stealth implementers. Our view at Rotunda is it's _impossible to compellingly lie about your browser fingerprint_. In the law of large numbers, and the surface area of APIs that browsers have to support, there's some way to detect that you're anomalous. The sites only need one thing wrong to prove that you're faking your whole identity. You need to patch every surface area, simulate the subtleties of every GPU driver, and honestly it's just not a game worth playing.

Instead Rotunda focuses on providing a browser that looks fully human, without lying about its underlying identity. We _want_ to look like it's actually running on your laptop - and instead focus on making sure no automation signatures can be detected. This includes making sure that Playwright can't be detected as the driver controlling your screen, and that any cursor movements tween as if you're moving a mouse, and that keyboard clicks have some occasional errors. Instead of lying about your fingerprint it's better to fib: tell them what GPU and audio drivers you're running on, but lie about some specifics like accessible fonts or extensions or screen size. It's not out of the ordinary for 10 M1 chips to be browsing their site at the same time - but it is impossible for a Linux GPU to be claiming its macos.

This results in a browser that's not suitable for crawling. For public sites you should be automating that in the cloud anyway via [Browserbase](https://browserbase.com/), [Kernel](https://kernel.sh/), or [ScrapingBee](https://www.scrapingbee.com/). But it's _very_ suitable when you're delegating tasks to your Agents. It's like having a fleet of interns that are doing useful work on your home network.

## Fingerprint blocked?

You're a lot less likely to get flagged as a bot with our host-passthrough approach. But that doesn't mean it's impossible. First we recommend you open the same site in Chrome/Firefox and see if you still start seeing flags. If you do it might be because of your IP reputation.

If other browsers work fine and you suspect it's at the Rotunda level, run the same site with our debugging handlers. This echos the calls that the site makes into the Javascript VM, the return values from those calls, console output, and outgoing page requests sent to their servers. 99.99% of the time these payloads reveal that the site picked up on something anomalous. The only thing they don't really cover is the TCP handshake, but we're using the authentic Firefox protocol for that anyway.

```sh
export ROTUNDA_DEBUG_DUMP_DIR=/tmp/rotunda-fingerprint-debug
export ROTUNDA_DEBUG_DUMP=manifest,network,console,vm,returns
export ROTUNDA_VM_ACCESS_SAMPLE_RATE=10

python your_repro_script.py
zip -r rotunda-fingerprint-debug.zip "$ROTUNDA_DEBUG_DUMP_DIR"
```

Attach `rotunda-fingerprint-debug.zip` to a GitHub Issue with the site URL, what you expected to happen, and what the site reported instead. The dump includes request/response bodies, so review it before sharing and do not set `ROTUNDA_DEBUG_DUMP_RAW=1` unless a maintainer asks for it.

## Agent Skill

Rotunda includes an installable skill for agents that support the `skills` CLI. Install it into your current project with:

```bash
npx skills add MonkeySee-AI/rotunda --skill rotunda
```

To install it for all supported local agents without prompts, use:

```bash
npx skills add MonkeySee-AI/rotunda --skill rotunda --agent '*' -y
```

The skill lives at [skills/rotunda/SKILL.md](skills/rotunda/SKILL.md) and gives agents a compact operating guide for `uvx rotunda agent ...` browser workflows.

## Want to help?

There are a ton of ways to get involved. Check the Issues for any good getting started tickets and chime that you're interested in helping out. Also hit me up on [X](https://x.com/piercefreeman) or subscribe to my [newsletter](https://pierce.dev/media) if you want to chat about agents and support the development.

## Credits

This repository builds on daijro's original Firefox patching work, which laid the foundation - via much trial and error - for the browser patching techniques used here. They made the case for using Firefox because Juggler is isolated from the browser context (unlike CDP).

Their main focus, however, is on stealth whereas ours is on automation. We want to give your Agents access to a browser that works almost identically to your daily driver.

[camoufox](https://github.com/daijro/camoufox)

## FAQ

Can't I just control Chrome with computer vision?

You certainly can try! Computer vision isn't a perfect answer here because it's so slow, fills up your context window, and doesn't allow your agent to see any content that's not in the viewport. It's much more convenient to grab the current DOM and parse it into an LLM friendly representation of the page. But grabbing this representation opens you up to the same question of Playwright/CDP control that we were trying to avoid.

Launching in most cloud VMs to use computer vision also risks leaking state about the underlying host. Most use the same stealth plugins that are pretty easy to detect, which means you're going to eventually get flagged if you use them naturally.

Plus computer vision sometimes makes it hard to click around some websites because direct click events are hard to translate cleanly (see reports of Claude being unable to select dropdowns from form lists).
