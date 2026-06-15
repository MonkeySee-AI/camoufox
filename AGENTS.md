# Agent Notes

## Testing Split For Driver And Browser Hooks

When a change extends an external driver, protocol layer, browser runtime hook, or
other boundary where unit tests can accidentally verify only our own mock setup,
split coverage by what each layer can prove:

- Unit tests should cover registry behavior, install timing, and actionable error
  messages when a hook is missing.
- Node or driver-structural tests should be compatibility sentinels only, such as
  proving a patched Playwright dispatcher/schema still exists after dependency
  changes.
- Integration tests should own the real behavioral guarantee. For anti-leak or
  isolation work, use an actual page that instruments the relevant page-visible
  functions on load, then assert the new path does not trip those instruments.

Do not treat broad mock-based plumbing tests as proof of a browser-visible
privacy, isolation, or stealth property.
