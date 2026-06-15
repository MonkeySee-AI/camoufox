# Agent Notes

## Testing Split

Split coverage by what each layer can genuinely prove. A test that only verifies
our own mock setup is useful for plumbing, but it should not stand in for the
real behavioral guarantee.

- Unit tests should cover local branching, data shaping, registry behavior,
  install timing, argument serialization, and actionable error messages.
- Structural compatibility tests should be explicit sentinels for dependency
  internals or generated interfaces. They prove that a private schema, method,
  file layout, or generated contract still exists after dependency changes; they
  do not prove product behavior.
- Integration tests should own behavior that depends on real external systems,
  browsers, drivers, protocols, filesystems, networks, subprocesses, or other
  runtime boundaries. For isolation, privacy, or anti-leak guarantees, use a real
  target that instruments the observable surface and assert the new path does
  not trip those instruments.

For unit and structural tests, prefer the `test_{original_filename}.py`
counterpart for the implementation file under test, for example
`playwright_driver_hooks.py` -> `test_playwright_driver_hooks.py`. Integration
tests may be named around the workflow or behavior they cover.

Do not treat broad mock-based plumbing tests as proof of a user-visible,
browser-visible, privacy, isolation, security, or stealth property.
