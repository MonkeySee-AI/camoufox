# Rotunda Tests

Ensures that Playwright functionality is not broken.

---

This directory is based on the original Playwright-Python [tests](https://github.com/microsoft/playwright-python/tree/main/tests).

It has been modified to skip tests that use the following features:

- Injecting JavaScript into the page or writing to DOM. Rotunda's `page.evaluate` only supports reading values, not executing within the page context.
- Overriding the User-Agent.
- Any tests specific to Chromium or Webkit.

---

# Usage

### Setting up the environment

From the repo root, sync the shared `pythonlib`-backed `uv` environment:

```bash
uv sync --group dev --group playwright-tests --locked
uv run --group dev --group playwright-tests python -m playwright install firefox ffmpeg
```

### Running the tests

Run the Playwright async suite directly through `uv`:

```bash
ROTUNDA_EXECUTABLE_PATH=/path/to/rotunda-bin \
  uv run --group dev --group playwright-tests \
  pytest --integration --headless __tests__/playwright/async/
```

For a headful run:

```bash
ROTUNDA_EXECUTABLE_PATH=/path/to/rotunda-bin \
  uv run --group dev --group playwright-tests \
  pytest --integration __tests__/playwright/async/
```

Or through the repo `Makefile`:

```bash
make tests headful=true
```

---
