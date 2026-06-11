from __future__ import annotations

import asyncio
import inspect
import json
import time
from contextlib import suppress
from pathlib import Path

import pytest
from click.testing import CliRunner
from rotunda import __main__ as cli_module
from rotunda.agent import daemon as daemon_module
from rotunda.agent.client import AgentClient, AgentClientError
from rotunda.__main__ import cli
from rotunda.agent import store as store_module
from rotunda.agent.daemon import (
    AgentDaemon,
    AgentHTTPServer,
)
from rotunda.agent.store import AgentStore


def isolate_agent_store(tmp_path, monkeypatch) -> None:
    def ensure_dirs() -> None:
        for name in ("profiles", "sessions", "logs"):
            (tmp_path / name).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(store_module, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(store_module, "RESOURCES_FILE", tmp_path / "resources.json")
    monkeypatch.setattr(store_module, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(store_module, "AUTH_FILE", tmp_path / "auth.json")
    monkeypatch.setattr(store_module, "DAEMON_FILE", tmp_path / "daemon.json")
    monkeypatch.setattr(store_module, "ensure_agent_dirs", ensure_dirs)


def test_agent_http_server_accepts_concurrent_requests_for_async_playwright_api() -> None:
    assert inspect.iscoroutinefunction(AgentHTTPServer.handle_client)
    assert inspect.iscoroutinefunction(AgentHTTPServer.serve_forever)


async def test_agent_http_timeout_cancels_page_locked_extract(monkeypatch) -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = HangingTextPage(events)

    async def fake_describe_page(page_id: str, max_items: int = 200) -> dict:
        return {
            "page": {"id": page_id, "url": "https://example.test", "title": ""},
            "text": "described",
            "items": [],
            "max_items": max_items,
        }

    daemon._describe_page_unlocked = fake_describe_page
    monkeypatch.setitem(daemon_module.AGENT_ROUTE_TIMEOUT_SECONDS, "/extract", 0.05)

    server = await daemon_module._bind_agent_server(
        daemon=daemon,
        token="token",
        instance_id="daemon_test",
        port_base=0,
        port_count=1,
    )
    client = AgentClient(
        {
            "profile_id": "prof_1",
            "host": daemon_module.AGENT_HOST,
            "port": int(server.server_address[1]),
            "token": "token",
        }
    )
    try:
        with pytest.raises(AgentClientError, match=r"/extract.*timed out"):
            await asyncio.to_thread(client.post, "/extract", {"page_id": "page_1", "format": "text"})

        result = await asyncio.to_thread(client.post, "/describe", {"page_id": "page_1", "max_items": 5})
    finally:
        await server.close()

    assert result["text"] == "described"
    assert result["max_items"] == 5
    assert ("inner_text_start", "body", 15_000) in events
    assert ("inner_text_cancelled", "body") in events


async def test_agent_http_client_disconnect_cancels_page_locked_extract() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = HangingTextPage(events)

    async def fake_describe_page(page_id: str, max_items: int = 200) -> dict:
        return {
            "page": {"id": page_id, "url": "https://example.test", "title": ""},
            "text": "described",
            "items": [],
            "max_items": max_items,
        }

    daemon._describe_page_unlocked = fake_describe_page
    server = await daemon_module._bind_agent_server(
        daemon=daemon,
        token="token",
        instance_id="daemon_test",
        port_base=0,
        port_count=1,
    )
    client = AgentClient(
        {
            "profile_id": "prof_1",
            "host": daemon_module.AGENT_HOST,
            "port": int(server.server_address[1]),
            "token": "token",
        }
    )
    try:
        _reader, writer = await asyncio.open_connection(daemon_module.AGENT_HOST, int(server.server_address[1]))
        body = b'{"page_id":"page_1","format":"text"}'
        writer.write(
            b"\r\n".join(
                [
                    b"POST /extract HTTP/1.1",
                    b"Host: 127.0.0.1",
                    b"Authorization: Bearer token",
                    b"Content-Type: application/json",
                    f"Content-Length: {len(body)}".encode("ascii"),
                    b"",
                    b"",
                ]
            )
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

        for _ in range(100):
            if ("inner_text_cancelled", "body") in events:
                break
            await asyncio.sleep(0.01)

        result = await asyncio.to_thread(client.post, "/describe", {"page_id": "page_1", "max_items": 5})
    finally:
        await server.close()

    assert result["text"] == "described"
    assert ("inner_text_start", "body", 15_000) in events
    assert ("inner_text_cancelled", "body") in events


async def test_agent_async_daemon_lifecycle_serves_identity_ping_shutdown(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    profile = store.create_profile(name="async-daemon")
    ready_file = tmp_path / "logs" / "ready.json"
    session_file = store.session_path(profile["id"])

    task = asyncio.create_task(
        daemon_module._run_daemon_async(
            profile_id=profile["id"],
            token="token",
            ready_file=ready_file,
            session_file=session_file,
            port_base=0,
            port_count=1,
        )
    )
    try:
        for _ in range(100):
            if ready_file.exists():
                break
            await asyncio.sleep(0.01)
        assert ready_file.exists()

        ready = json.loads(ready_file.read_text(encoding="utf-8"))
        assert ready["ok"] is True
        client = AgentClient(ready["session"])

        identity = await asyncio.to_thread(client.get, "/identity")
        assert identity["service"] == "rotunda-agent"
        ping = await asyncio.to_thread(client.get, "/ping")
        assert ping["profile_id"] == profile["id"]
        stopped = await asyncio.to_thread(client.post, "/shutdown")
        assert stopped["stopping"] is True
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def test_agent_profile_defaults_to_headed(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)

    profile = AgentStore().create_profile(name="headed")

    assert profile["headless"] is False
    assert profile["humanize"] is True


def test_agent_launch_env_keeps_headed_macos_windows_in_back(monkeypatch) -> None:
    monkeypatch.setattr(daemon_module.sys, "platform", "darwin")

    env = daemon_module._agent_launch_env(headless=False, source_env={"PATH": "/bin"})

    assert env["PATH"] == "/bin"
    assert env[daemon_module.MACOS_BACKGROUND_WINDOWS_ENV] == "1"


def test_agent_launch_env_respects_macos_background_window_override(monkeypatch) -> None:
    monkeypatch.setattr(daemon_module.sys, "platform", "darwin")

    env = daemon_module._agent_launch_env(
        headless=False,
        source_env={daemon_module.MACOS_BACKGROUND_WINDOWS_ENV: "0"},
    )

    assert env[daemon_module.MACOS_BACKGROUND_WINDOWS_ENV] == "0"


def test_agent_launch_env_does_not_background_headless_or_non_macos(monkeypatch) -> None:
    monkeypatch.setattr(daemon_module.sys, "platform", "darwin")
    mac_headless = daemon_module._agent_launch_env(headless=True, source_env={})

    monkeypatch.setattr(daemon_module.sys, "platform", "linux")
    linux_headed = daemon_module._agent_launch_env(headless=False, source_env={})

    assert daemon_module.MACOS_BACKGROUND_WINDOWS_ENV not in mac_headless
    assert daemon_module.MACOS_BACKGROUND_WINDOWS_ENV not in linux_headed


def test_agent_store_resolves_profile_by_name(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    old_profile = store.create_profile(name="pierce-dev-click-smoke")
    store.register(kind="profile", id=old_profile["id"], label=old_profile["name"])
    new_profile = store.create_profile(name="pierce-dev-click-smoke")
    new_resource = store.register(kind="profile", id=new_profile["id"], label=new_profile["name"])

    resolved = store.resolve("pierce-dev-click-smoke", kind="profile")

    assert resolved.id == new_resource.id


def test_agent_store_can_refresh_page_element_resources(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    page = store.register(kind="page", id="page_1", profile_id="prof_1", parent_id="ctx_1")
    store.register(kind="element", id="old", profile_id="prof_1", parent_id=page.id)
    store.register(kind="element", id="other", profile_id="prof_1", parent_id="page_2")

    store.remove_children(page.id, kind="element")

    resources = store.list_resources()
    assert [resource.id for resource in resources] == ["page_1", "other"]


def test_agent_store_prunes_stale_runtime_state_but_keeps_profiles(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    profile = store.create_profile(name="demo")
    profile_resource = store.register(kind="profile", id=profile["id"], label=profile["name"])
    store.register(
        kind="context",
        id="ctx_1",
        profile_id=profile["id"],
        parent_id=profile["id"],
        runtime_id="daemon_old",
    )
    store.register(kind="page", id="page_1", profile_id=profile["id"], parent_id="ctx_1", runtime_id="daemon_old")
    store.save_session(
        profile["id"],
        {
            "profile_id": profile["id"],
            "host": "127.0.0.1",
            "port": 1,
            "token": "token",
            "instance_id": "daemon_old",
            "update_tick": time.time() - 120,
        },
    )
    store.save_daemon_record(
        {
            "service": "rotunda-agent",
            "profile_id": profile["id"],
            "host": "127.0.0.1",
            "port": 1,
            "instance_id": "daemon_old",
            "update_tick": time.time() - 120,
        }
    )

    pruned_store = AgentStore()

    assert [(resource.kind, resource.id) for resource in pruned_store.list_resources()] == [
        ("profile", profile_resource.id)
    ]
    assert not store.session_path(profile["id"]).exists()
    assert not store_module.DAEMON_FILE.exists()


def test_agent_store_keeps_runtime_state_for_fresh_heartbeat(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    profile = store.create_profile(name="demo")
    store.register(kind="profile", id=profile["id"], label=profile["name"])
    store.register(
        kind="context",
        id="ctx_1",
        profile_id=profile["id"],
        parent_id=profile["id"],
        runtime_id="daemon_live",
    )
    store.save_session(
        profile["id"],
        {
            "profile_id": profile["id"],
            "host": "127.0.0.1",
            "port": 1,
            "token": "token",
            "instance_id": "daemon_live",
            "update_tick": time.time(),
        },
    )
    store.save_daemon_record(
        {
            "service": "rotunda-agent",
            "profile_id": profile["id"],
            "host": "127.0.0.1",
            "port": 1,
            "instance_id": "daemon_live",
            "update_tick": time.time(),
        }
    )

    fresh_store = AgentStore()

    assert [resource.kind for resource in fresh_store.list_resources()] == ["profile", "context"]
    assert store.session_path(profile["id"]).exists()


def test_agent_click_fill_and_type_accept_global_element_refs(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    page = store.register(kind="page", id="page_1", profile_id="prof_1", parent_id="ctx_1")
    store.register(kind="element", id="button_ref", profile_id="prof_1", parent_id=page.id)

    click_page, click_ref = cli_module._agent_target_from_click_args(store, ("button_ref",))
    fill_page, fill_ref, text = cli_module._agent_target_from_text_args(
        store,
        ("button_ref", "hello"),
        command_name="fill",
    )

    assert click_page.id == page.id
    assert click_ref == "button_ref"
    assert fill_page.id == page.id
    assert fill_ref == "button_ref"
    assert text == "hello"

    type_page, type_ref, typed_text = cli_module._agent_target_from_text_args(
        store,
        ("button_ref", "typed"),
        command_name="type",
    )

    assert type_page.id == page.id
    assert type_ref == "button_ref"
    assert typed_text == "typed"

    select_page, select_ref, values = cli_module._agent_target_from_values_args(
        store,
        ("button_ref", "one", "two"),
        command_name="select",
    )

    assert select_page.id == page.id
    assert select_ref == "button_ref"
    assert values == ["one", "two"]


class FakeKeyboard:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events

    async def insert_text(self, text: str) -> None:
        self.events.append(("insert_text", text))

    async def press(self, key: str) -> None:
        self.events.append(("keyboard_press", key))


class FakeMouse:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events

    async def down(self) -> None:
        self.events.append(("mouse_down",))

    async def up(self) -> None:
        self.events.append(("mouse_up",))


class FakePage:
    def __init__(self, events: list[tuple]) -> None:
        self.keyboard = FakeKeyboard(events)
        self.mouse = FakeMouse(events)
        self.events = events
        self.url = "https://example.test"

    async def title(self) -> str:
        return "Example"

    async def screenshot(self, *, path: str, full_page: bool, timeout: int) -> None:
        self.events.append(("page_screenshot", path, full_page, timeout))

    async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.events.append(("wait_for_load_state", state, timeout))

    async def wait_for_timeout(self, timeout: int) -> None:
        self.events.append(("wait_for_timeout", timeout))

    async def wait_for_url(self, value: str, *, timeout: int) -> None:
        self.events.append(("wait_for_url", value, timeout))

    async def evaluate(self, script: str, arg=None):
        self.events.append(("page_evaluate", script, arg))
        if "blocks.join" in script:
            return "# Example"
        if "querySelectorAll(\"a[href]\")" in script:
            return [{"text": "Home", "href": "https://example.test", "title": "", "target": ""}]
        if "document.forms" in script:
            return [{"index": 0, "fields": []}]
        return {"ok": True}

    async def content(self) -> str:
        self.events.append(("content",))
        return "<html></html>"

    def locator(self, selector: str):
        self.events.append(("page_locator", selector))

        class BodyLocator:
            @property
            def first(self):
                return self

            async def inner_text(self, *, timeout: int) -> str:
                return "Example text"

            async def wait_for(self, *, state: str, timeout: int) -> None:
                events.append(("locator_wait_for", selector, state, timeout))

        events = self.events
        return BodyLocator()

    def get_by_text(self, text: str):
        self.events.append(("get_by_text", text))

        class TextLocator:
            @property
            def first(self):
                return self

            async def wait_for(self, *, state: str, timeout: int) -> None:
                events.append(("text_wait_for", text, state, timeout))

        events = self.events
        return TextLocator()


class HangingTextPage(FakePage):
    def locator(self, selector: str):
        self.events.append(("page_locator", selector))

        class BodyLocator:
            async def inner_text(self, *, timeout: int) -> str:
                events.append(("inner_text_start", selector, timeout))
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    events.append(("inner_text_cancelled", selector))
                    raise

        events = self.events
        return BodyLocator()


class FakeLocator:
    def __init__(self, events: list[tuple]) -> None:
        self.events = events

    async def click(self, *, timeout: int) -> None:
        self.events.append(("click", timeout))

    async def press(
        self,
        key: str,
        *,
        timeout: int | None = None,
        no_wait_after: bool | None = None,
    ) -> None:
        if no_wait_after is None:
            self.events.append(("press", key, timeout))
        else:
            self.events.append(("press", key, timeout, no_wait_after))

    async def evaluate(self, script: str, arg=None) -> dict:
        self.events.append(("evaluate", script, arg))
        return {
            "tag": "select",
            "role": "",
            "name": "Country",
            "text": "United States Canada",
            "attributes": {"id": "country"},
            "state": {"visible": True, "disabled": False},
            "value": "us",
            "bounds": {"x": 1, "y": 2, "width": 100, "height": 20},
            "selectedIndex": 0,
            "selectedValues": ["us"],
            "options": [
                {
                    "index": 0,
                    "value": "us",
                    "label": "United States",
                    "text": "United States",
                    "selected": True,
                    "disabled": False,
                },
                {
                    "index": 1,
                    "value": "ca",
                    "label": "Canada",
                    "text": "Canada",
                    "selected": False,
                    "disabled": False,
                },
            ],
            "outerHTML": '<select id="country"></select>',
        }

    async def select_option(
        self,
        value: str | list[str] | None = None,
        *,
        index: int | list[int] | None = None,
        label: str | list[str] | None = None,
        timeout: int,
    ) -> list[str]:
        self.events.append(("select_option", value, index, label, timeout))
        if value is not None:
            return [value] if isinstance(value, str) else value
        if label is not None:
            return [label] if isinstance(label, str) else label
        if index is not None:
            indexes = [index] if isinstance(index, int) else index
            return [str(item) for item in indexes]
        return []

    async def hover(self, *, timeout: int) -> None:
        self.events.append(("hover", timeout))

    async def drag_to(self, target, *, timeout: int) -> None:
        self.events.append(("drag_to", target, timeout))

    async def check(self, *, timeout: int) -> None:
        self.events.append(("check", timeout))

    async def uncheck(self, *, timeout: int) -> None:
        self.events.append(("uncheck", timeout))

    async def set_input_files(self, paths: list[str], *, timeout: int) -> None:
        self.events.append(("set_input_files", paths, timeout))

    async def screenshot(self, *, path: str, timeout: int) -> None:
        self.events.append(("locator_screenshot", path, timeout))


class SubmitNavigationWaitLocator(FakeLocator):
    async def press(
        self,
        key: str,
        *,
        timeout: int | None = None,
        no_wait_after: bool | None = None,
    ) -> None:
        await super().press(key, timeout=timeout, no_wait_after=no_wait_after)
        if key == "Enter" and no_wait_after is not True:
            await asyncio.Event().wait()


class FakeSerializer:
    def __init__(self, locator: FakeLocator) -> None:
        self.locator = locator

    def resolve_locator(self, page: FakePage, ref: str) -> FakeLocator:
        return self.locator

    async def async_resolve_locator(self, page: FakePage, ref: str) -> FakeLocator:
        return self.locator

    def get_reference(self, ref: str):
        class Element:
            frame_index = 0
            frame_url = "https://example.test"
            frame_name = ""

        class Reference:
            element = Element()

        return Reference()


async def _fake_describe_page(page_id: str, max_items: int = 200) -> dict:
    return {"page": {"id": page_id}, "max_items": max_items}


async def test_agent_click_uses_playwright_click_path_for_juggler_humanization() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    await daemon.click("page_1", "button_ref")

    action_events = [event for event in events if event[0] != "wait_for_load_state"]
    assert action_events == [
        ("click", 15_000),
    ]


async def test_agent_fill_uses_playwright_click_and_rotunda_insert_text_path() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    await daemon.fill_text("page_1", "input_ref", "hello", submit=True)

    action_events = [event for event in events if event[0] != "wait_for_load_state"]
    assert action_events == [
        ("click", 15_000),
        ("press", "ControlOrMeta+A", 15_000),
        ("press", "Backspace", 15_000),
        ("insert_text", "hello"),
        ("press", "Enter", 15_000, True),
    ]


async def test_agent_fill_submit_does_not_wait_for_playwright_navigation_after_enter() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(SubmitNavigationWaitLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    await asyncio.wait_for(daemon.fill_text("page_1", "input_ref", "hello", submit=True), timeout=1)

    assert ("press", "Enter", 15_000, True) in events


async def test_agent_type_uses_playwright_click_and_rotunda_insert_text_path() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    await daemon.type_text("page_1", "input_ref", "hello", submit=True)

    action_events = [event for event in events if event[0] != "wait_for_load_state"]
    assert action_events == [
        ("click", 15_000),
        ("insert_text", "hello"),
        ("press", "Enter", 15_000, True),
    ]


async def test_agent_info_includes_select_options() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))

    async def fake_page_payload(page_id, page):
        return {"id": page_id, "url": "https://example.test"}

    daemon._page_payload = fake_page_payload

    result = await daemon.element_info("page_1", "select_ref")

    assert result["info"]["options"][1]["value"] == "ca"
    assert "value='ca'" in result["text"]
    assert "selectedValues: [\"us\"]" in result["text"]


async def test_agent_select_can_match_by_value_label_or_index() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    by_value = await daemon.select_options("page_1", "select_ref", ["ca"])
    by_label = await daemon.select_options("page_1", "select_ref", ["Canada"], by="label")
    by_index = await daemon.select_options("page_1", "select_ref", ["1"], by="index")

    assert by_value["selected"] == ["ca"]
    assert by_label["selected"] == ["Canada"]
    assert by_index["selected"] == ["1"]
    action_events = [event for event in events if event[0] != "wait_for_load_state"]
    assert action_events == [
        ("select_option", "ca", None, None, 15_000),
        ("select_option", None, None, "Canada", 15_000),
        ("select_option", None, 1, None, 15_000),
    ]


async def test_agent_press_hover_drag_check_scroll_and_upload_primitives() -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))
    daemon._describe_page_unlocked = _fake_describe_page

    await daemon.press_key("page_1", "Enter", ref="button_ref")
    await daemon.press_key("page_1", "Escape")
    await daemon.hover("page_1", "button_ref")
    await daemon.drag("page_1", "source_ref", "target_ref")
    await daemon.set_checked("page_1", "checkbox_ref", checked=True)
    await daemon.set_checked("page_1", "checkbox_ref", checked=False)
    await daemon.scroll("page_1", direction="down", amount=300, ref="panel_ref")
    await daemon.scroll("page_1", direction="up", amount=200)
    await daemon.upload_files("page_1", "file_ref", ["/tmp/example.txt"])

    assert ("press", "Enter", 15_000) in events
    assert ("keyboard_press", "Escape") in events
    assert ("hover", 15_000) in events
    assert any(event[0] == "drag_to" for event in events)
    assert ("check", 15_000) in events
    assert ("uncheck", 15_000) in events
    assert any(event[0] == "evaluate" for event in events)
    assert any(event[0] == "page_evaluate" for event in events)
    assert ("set_input_files", ["/tmp/example.txt"], 15_000) in events


async def test_agent_screenshot_wait_extract_download_and_dialog_primitives(tmp_path) -> None:
    events: list[tuple] = []
    daemon = AgentDaemon({"id": "prof_1"})
    daemon.pages["page_1"] = FakePage(events)
    daemon.page_serializers["page_1"] = FakeSerializer(FakeLocator(events))

    page_path = tmp_path / "page.png"
    element_path = tmp_path / "element.png"
    await daemon.screenshot("page_1", str(page_path), full_page=True)
    await daemon.screenshot("page_1", str(element_path), ref="button_ref")
    await daemon.wait_for("page_1", target="selector", value="main", state="visible", timeout_ms=123)
    await daemon.wait_for("page_1", target="text", value="Done", timeout_ms=456)
    await daemon.wait_for("page_1", target="url", value="**/done", timeout_ms=789)
    await daemon.wait_for("page_1", target="timeout", timeout_ms=50)

    assert ("page_screenshot", str(page_path), True, 30_000) in events
    assert ("locator_screenshot", str(element_path), 15_000) in events
    assert ("locator_wait_for", "main", "visible", 123) in events
    assert ("text_wait_for", "Done", "visible", 456) in events
    assert ("wait_for_url", "**/done", 789) in events
    assert ("wait_for_timeout", 50) in events

    assert (await daemon.extract("page_1", "text"))["text"] == "Example text"
    assert (await daemon.extract("page_1", "html"))["text"] == "<html></html>"
    assert "Home" in (await daemon.extract("page_1", "links"))["text"]
    assert "fields" in (await daemon.extract("page_1", "forms"))["text"]
    assert (await daemon.extract("page_1", "markdown"))["text"] == "# Example"

    class FakeDownload:
        url = "https://example.test/file.txt"
        suggested_filename = "file.txt"

        def __init__(self) -> None:
            self.saved = ""

        async def path(self) -> str:
            return "/tmp/browser-download"

        async def save_as(self, path: str) -> None:
            self.saved = path

    download = FakeDownload()
    daemon._record_download("page_1", download)
    download_id = next(iter(daemon.downloads))
    assert (await daemon.list_downloads())["downloads"][0]["suggested_filename"] == "file.txt"
    saved = await daemon.save_download(download_id, str(tmp_path / "file.txt"))
    assert saved["download"]["saved_as"] == str(tmp_path / "file.txt")
    assert download.saved == str(tmp_path / "file.txt")

    class FakeDialog:
        type = "prompt"
        message = "Name?"
        default_value = ""

        def __init__(self) -> None:
            self.accepted = None
            self.dismissed = False

        async def accept(self, prompt_text: str | None = None) -> None:
            self.accepted = prompt_text

        async def dismiss(self) -> None:
            self.dismissed = True

    await daemon.dialog("page_1", "fill", text="Pierce")
    dialog = FakeDialog()
    await daemon._handle_dialog("page_1", dialog)
    assert dialog.accepted == "Pierce"
    assert (await daemon.dialog("page_1", "list"))["dialogs"][0]["action"] == "fill"


def test_agent_target_parsers_support_page_level_and_global_refs(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    page = store.register(kind="page", id="page_1", profile_id="prof_1", parent_id="ctx_1")
    store.register(kind="element", id="input_ref", profile_id="prof_1", parent_id=page.id)
    store.register(kind="element", id="target_ref", profile_id="prof_1", parent_id=page.id)

    page_resource, ref, key = cli_module._agent_target_from_key_args(
        store,
        ("input_ref", "Enter"),
        command_name="press",
    )
    assert page_resource.id == "page_1"
    assert ref == "input_ref"
    assert key == "Enter"

    page_resource, ref, direction = cli_module._agent_target_from_direction_args(
        store,
        ("page_1", "down"),
        command_name="scroll",
    )
    assert page_resource.id == "page_1"
    assert ref is None
    assert direction == "down"

    page_resource, source_ref, target_ref = cli_module._agent_target_from_drag_args(
        store,
        ("input_ref", "target_ref"),
    )
    assert page_resource.id == "page_1"
    assert source_ref == "input_ref"
    assert target_ref == "target_ref"


def test_agent_screenshot_defaults_to_temp_absolute_path(tmp_path, monkeypatch) -> None:
    isolate_agent_store(tmp_path, monkeypatch)
    store = AgentStore()
    store.save_daemon_record(
        {
            "service": "rotunda-agent",
            "profile_id": "prof_1",
            "host": "127.0.0.1",
            "port": 1,
            "instance_id": "daemon_live",
            "update_tick": time.time(),
        }
    )
    page = store.register(
        kind="page",
        id="page_1",
        profile_id="prof_1",
        parent_id="ctx_1",
        runtime_id="daemon_live",
    )
    requests: list[tuple[str, dict]] = []

    class FakeClient:
        def post(self, path: str, payload: dict | None = None) -> dict:
            requests.append((path, payload or {}))
            return {
                "page": {"id": page.id, "url": "https://example.test", "title": ""},
                "path": payload["path"],
            }

    monkeypatch.setattr(cli_module, "_agent_client", lambda store, profile_id: FakeClient())
    monkeypatch.setattr(cli_module.tempfile, "gettempdir", lambda: str(tmp_path))

    result = CliRunner().invoke(cli, ["agent", "screenshot", str(page.idx)])

    assert result.exit_code == 0
    payload = requests[0][1]
    screenshot_path = payload["path"]
    assert Path(screenshot_path).is_absolute()
    assert Path(screenshot_path).parent == tmp_path
    assert Path(screenshot_path).name.startswith("rotunda-agent-screenshot-")
    assert Path(screenshot_path).suffix == ".png"
    assert f"screenshot: {screenshot_path}" in result.output


def test_agent_help_exposes_describe_not_list() -> None:
    result = CliRunner().invoke(cli, ["agent", "--help"])

    assert result.exit_code == 0
    assert "describe" in result.output
    assert "screenshot" in result.output
    assert "wait" in result.output
    assert "press" in result.output
    assert "scroll" in result.output
    assert "upload" in result.output
    assert "extract" in result.output
    assert "fill" in result.output
    assert "info" in result.output
    assert "select" in result.output
    assert "type" in result.output
    assert "navigate PAGE URL" in result.output
    assert "click [PAGE] REF" in result.output
    assert "screenshot PAGE [PATH]" in result.output
    assert "upload [PAGE] REF PATH..." in result.output
    assert " list " not in result.output
    assert " enter " not in result.output


def test_agent_action_help_explains_positional_arguments() -> None:
    for command in ("click", "info", "fill", "select", "type"):
        result = CliRunner().invoke(cli, ["agent", command, "--help"])

        assert result.exit_code == 0
        assert "Arguments:" in result.output
        assert "REF" in result.output
        assert "PAGE" in result.output
