#!/usr/bin/env python3
"""Launch headed Rotunda with an exact runtime profile JSON file."""

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from rotunda.utils import (
    launch_options,
    persistent_context_options,
    runtime_profile_init_script,
    validate_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="Saved Rotunda runtime profile JSON")
    parser.add_argument("url", nargs="?", default="about:blank")
    parser.add_argument(
        "--executable-path",
        default=os.getenv("ROTUNDA_EXECUTABLE_PATH"),
        help="Local Rotunda executable (defaults to ROTUNDA_EXECUTABLE_PATH or the active install)",
    )
    parser.add_argument(
        "--vm-log",
        type=Path,
        help="Log native JS VM accesses and disable JIT tiers so the logger sees every access",
    )
    parser.add_argument(
        "--offline-telemetry",
        type=Path,
        help="Run a local telemetry.js with browser networking disabled",
    )
    parser.add_argument(
        "--disable-content-sandbox",
        action="store_true",
        help="Let content processes write VM logs; use only with the offline replay",
    )
    parser.add_argument("--user-data-dir", type=Path)
    args = parser.parse_args()

    if args.disable_content_sandbox and not args.offline_telemetry:
        parser.error(
            "--disable-content-sandbox is only allowed with --offline-telemetry"
        )
    profile_path = args.profile.expanduser().resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    validate_config(profile)

    env = os.environ.copy()
    if args.disable_content_sandbox:
        env["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
    firefox_user_prefs = {}
    if args.vm_log:
        vm_log = args.vm_log.expanduser().resolve()
        vm_log.parent.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "ROTUNDA_VM_ACCESS_LOG": "1",
                "ROTUNDA_VM_ACCESS_LOG_FILE": str(vm_log),
            }
        )
        for name in (
            "ROTUNDA_VM_ACCESS_BUFFERED",
            "ROTUNDA_VM_ACCESS_REALM",
            "ROTUNDA_VM_ACCESS_RETURNS",
            "ROTUNDA_VM_ACCESS_VALUE_STRINGS",
        ):
            env.setdefault(name, "1")
        firefox_user_prefs = {
            "javascript.options.blinterp": False,
            "javascript.options.baselinejit": False,
            "javascript.options.ion": False,
        }

    with sync_playwright() as playwright:
        options = launch_options(
            config=profile,
            executable_path=args.executable_path,
            env=env,
            firefox_user_prefs=firefox_user_prefs,
            headless=False,
            i_know_what_im_doing=True,
        )
        if args.offline_telemetry:
            options["proxy"] = {"server": "http://127.0.0.1:9"}
        # Reuse the caller's file verbatim instead of the launcher's generated copy.
        options["env"]["ROTUNDA_CONFIG_PATH"] = str(profile_path)
        user_data_dir = (
            args.user_data_dir.expanduser().resolve() if args.user_data_dir else ""
        )
        context = playwright.firefox.launch_persistent_context(
            user_data_dir, **persistent_context_options(options)
        )
        context.add_init_script(runtime_profile_init_script(profile))
        page = context.pages[0] if context.pages else context.new_page()
        blocked_requests = []
        if args.offline_telemetry:
            source = (
                args.offline_telemetry.expanduser()
                .resolve()
                .read_text(encoding="utf-8")
            )
            telemetry_url = "https://elements.stytch.com/telemetry.js"

            def route_request(route) -> None:
                if route.request.url == telemetry_url:
                    route.fulfill(content_type="text/javascript", body=source)
                else:
                    blocked_requests.append(route.request.url)
                    route.abort()

            context.route("**/*", route_request)
            page.add_script_tag(url=telemetry_url)
            result = page.evaluate(
                """async () => GetTelemetryID({
                    publicToken: "public-token-local-offline",
                    submitURL: "https://network.invalid"
                })"""
            )
            print(f"Telemetry result: {result}")
            print(f"Blocked requests: {json.dumps(blocked_requests)}")
        elif args.url != "about:blank":
            page.goto(args.url, wait_until="domcontentloaded")

        print(f"Profile: {profile_path}")
        print(f"Firefox: {options['executable_path']}")
        if not args.offline_telemetry:
            input("Press Enter to close Rotunda... ")
        context.close()


if __name__ == "__main__":
    main()
