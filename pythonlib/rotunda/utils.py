from __future__ import annotations

import configparser
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from os import environ
from os.path import abspath
from pathlib import Path
from pprint import pprint
from random import randint, randrange
from typing import Any, Literal, TypeAlias, cast

import orjson
from browserforge.fingerprints import Fingerprint, Screen
from pydantic import ValidationError
from screeninfo import get_monitors
from ua_parser import user_agent_parser

from ._generated_profile import (
    AudioProfile,
    FontsProfile,
    GeolocationProfile,
    HistoryProfile,
    HumanizeProfile,
    LocaleProfile,
    RotundaProfile,
    WebRtcProfile,
    WindowProfile,
)
from ._warnings import LeakWarning
from .addons import DefaultAddons, add_default_addons, confirm_paths
from .debug_dump import configure_launch_debug_dump
from .exceptions import InvalidOS, InvalidPropertyType, NonFirefoxFingerprint
from .fingerprinting import normalize_target_os
from .fingerprinting.common import HostTargetOS
from .fingerprints import (
    from_browserforge,
    from_preset,
    generate_fingerprint,
    is_generated_fingerprint,
)
from .geo.geolocation import geoip_allowed, get_geolocation
from .geo.ip import Proxy, public_ip, valid_ipv4, valid_ipv6
from .geo.locales import handle_locale
from .pkgman import OS_NAME, get_path, installed_verstr, launch_path
from .settings import ROTUNDA_MACOS_BACKGROUND_WINDOWS, RotundaSettings
from .virtdisplay import VirtualDisplay

ListOrString: TypeAlias = tuple[str, ...] | list[str] | str

CACHE_PREFS = {
    "browser.cache.disk.smart_size.enabled": True,
    "browser.cache.disk_cache_ssl": True,
    "browser.cache.memory.enable": True,
    "browser.sessionhistory.max_entries": 10,
    "browser.sessionhistory.max_total_viewers": -1,
}

MACOS_BACKGROUND_WINDOWS_ENV = ROTUNDA_MACOS_BACKGROUND_WINDOWS

STEALTH_PREFS = {
    # Playwright/Juggler attaches pages as Debugger debuggees. Without this,
    # Firefox exposes async stack frames to page Error.stack values, which lets
    # fingerprinting scripts infer a developer-tools/debugger state.
    "javascript.options.asyncstack": False,
}


def launch_options(
    *,
    config: RotundaProfile | dict[str, Any] | None = None,
    os: ListOrString | None = None,
    block_images: bool | None = None,
    block_webrtc: bool | None = None,
    block_webgl: bool | None = None,
    disable_coop: bool | None = None,
    geoip: str | bool | None = None,
    geoip_db: str | None = None,
    humanize: bool | float | None = None,
    locale: str | list[str] | None = None,
    addons: list[str] | None = None,
    fonts: list[str] | None = None,
    custom_fonts_only: bool | None = None,
    default_addons: bool | None = None,
    exclude_addons: list[DefaultAddons] | None = None,
    screen: Screen | None = None,
    window: tuple[int, int] | None = None,
    fingerprint: Fingerprint | None = None,
    fingerprint_preset: dict[str, Any] | None = None,
    ff_version: int | None = None,
    headless: bool | None = None,
    main_world_eval: bool | None = None,
    executable_path: str | Path | None = None,
    browser: str | None = None,
    firefox_user_prefs: dict[str, Any] | None = None,
    proxy: dict[str, str] | None = None,
    enable_cache: bool | None = None,
    args: list[str] | None = None,
    env: dict[str, str | float | bool] | None = None,
    i_know_what_im_doing: bool | None = None,
    debug: bool | None = None,
    virtual_display: str | None = None,
    **launch_options: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the Playwright launch options for Rotunda.

    The active fingerprint path is intentionally small:
    BrowserForge supplies the Firefox skeleton, and Rotunda only layers in
    host-compatible values for the current OS on top of it.

    `fingerprint_preset` is now an explicit preset dictionary only. Rotunda no
    longer ships or samples a bundled preset corpus through this API.
    """
    return LaunchOptionBuilder(
        config=config,
        requested_os=os,
        block_images=block_images,
        block_webrtc=block_webrtc,
        block_webgl=block_webgl,
        disable_coop=disable_coop,
        geoip=geoip,
        geoip_db=geoip_db,
        humanize=humanize,
        locale=locale,
        addons=addons,
        fonts=fonts,
        custom_fonts_only=custom_fonts_only,
        default_addons=default_addons,
        exclude_addons=exclude_addons,
        screen=screen,
        window=window,
        fingerprint=fingerprint,
        fingerprint_preset=fingerprint_preset,
        ff_version=ff_version,
        headless=headless,
        main_world_eval=main_world_eval,
        executable_path=executable_path,
        browser=browser,
        firefox_user_prefs=firefox_user_prefs,
        proxy=proxy,
        enable_cache=enable_cache,
        args=args,
        env=env,
        i_know_what_im_doing=i_know_what_im_doing,
        debug=debug,
        virtual_display=virtual_display,
        extra_launch_options=launch_options,
    ).build()


def runtime_profile_init_script(config: RotundaProfile | dict[str, Any]) -> str:
    """
    Build the page init script that transfers a launch runtime profile into
    Rotunda's per-context spoofing storage.
    """
    profile = _coerce_profile(config)
    lines = ["(function() {", "  var w = window;"]

    def call(setter: str, *values: Any) -> None:
        args = ", ".join(json.dumps(value) for value in values)
        lines.append(f'  if (typeof w.{setter} === "function") w.{setter}({args});')

    navigator = profile.navigator
    if navigator is not None:
        if navigator.platform:
            call("setNavigatorPlatform", navigator.platform)
        if navigator.oscpu:
            call("setNavigatorOscpu", navigator.oscpu)
        if isinstance(navigator.hardware_concurrency, int) and navigator.hardware_concurrency > 0:
            call("setNavigatorHardwareConcurrency", navigator.hardware_concurrency)
        if navigator.user_agent:
            call("setNavigatorUserAgent", navigator.user_agent)

    window = profile.window
    if (
        window is not None
        and isinstance(window.outer_width, int)
        and window.outer_width > 0
        and isinstance(window.outer_height, int)
        and window.outer_height > 0
        and isinstance(window.inner_width, int)
        and window.inner_width > 0
        and isinstance(window.inner_height, int)
        and window.inner_height > 0
    ):
        call(
            "setWindowDimensions",
            window.outer_width,
            window.outer_height,
            window.inner_width,
            window.inner_height,
            window.screen_x if isinstance(window.screen_x, int) else 0,
            window.screen_y if isinstance(window.screen_y, int) else 0,
        )

    screen = profile.screen
    if screen is not None:
        if (
            isinstance(screen.avail_width, int)
            and screen.avail_width > 0
            and isinstance(screen.avail_height, int)
            and screen.avail_height > 0
        ):
            call(
                "setScreenAvailableRect",
                screen.avail_width,
                screen.avail_height,
                screen.avail_left if isinstance(screen.avail_left, int) else 0,
                screen.avail_top if isinstance(screen.avail_top, int) else 0,
            )

        if (
            isinstance(screen.width, int)
            and screen.width > 0
            and isinstance(screen.height, int)
            and screen.height > 0
        ):
            call("setScreenDimensions", screen.width, screen.height)

        if isinstance(screen.color_depth, int) and screen.color_depth > 0:
            call("setScreenColorDepth", screen.color_depth)

    if profile.timezone:
        call("setTimezone", profile.timezone)

    for setter in (
        "setFontSpacingSeed",
        "setAudioFingerprintSeed",
        "setTimezone",
        "setScreenDimensions",
        "setScreenAvailableRect",
        "setScreenColorDepth",
        "setWindowDimensions",
        "setNavigatorPlatform",
        "setNavigatorOscpu",
        "setNavigatorHardwareConcurrency",
        "setNavigatorUserAgent",
        "setFontList",
        "setSpeechVoices",
        "setWebRTCIPv4",
        "setWebRTCIPv6",
    ):
        lines.append(f"  try {{ w.{setter} = undefined; delete w.{setter}; }} catch (e) {{}}")

    lines.append("})();")
    return "\n".join(lines)


def persistent_context_options(options: Mapping[str, Any]) -> dict[str, Any]:
    """
    Adapt Rotunda launch options for Playwright persistent contexts.

    `launch_options()` must remain valid for `firefox.launch()`, which does not
    accept context-only keys such as `viewport`. Persistent contexts do accept
    those keys, and Linux headless layout uses that viewport instead of Firefox
    process window flags.

    On macOS, we only suppress Playwright's default `-foreground` argument when
    the launch env explicitly opts into Rotunda background windows. Normal headed
    persistent-context launches keep the platform default of becoming frontmost.
    """
    result = dict(options)
    if "viewport" not in result and result.get("no_viewport") is not True:
        viewport = _viewport_from_launch_options(result)
        if viewport is not None:
            result["viewport"] = viewport
    if _should_keep_macos_windows_in_back(result):
        _ignore_default_arg(result, "-foreground")
    return result


def _should_keep_macos_windows_in_back(options: Mapping[str, Any]) -> bool:
    if sys.platform != "darwin" or options.get("headless") is True:
        return False
    env = options.get("env")
    return (
        isinstance(env, Mapping)
        and RotundaSettings.from_env(env).macos_background_windows is True
    )


def _ignore_default_arg(options: dict[str, Any], arg: str) -> None:
    ignored = options.get("ignore_default_args")
    if ignored is True:
        return
    ignored_args = list(ignored) if isinstance(ignored, list | tuple) else []
    if arg not in ignored_args:
        ignored_args.append(arg)
    options["ignore_default_args"] = ignored_args


async def async_attach_vd(
    browser: Any,
    virtual_display: VirtualDisplay | None = None,
) -> Any:
    """
    Attach a virtual display lifecycle to an async Playwright browser/context.

    The returned object is the same Playwright instance with `.close()`
    wrapped so that the `VirtualDisplay` is always torn down.
    """
    if not virtual_display:
        return browser

    close = browser.close

    async def wrapped_close(*args: Any, **kwargs: Any) -> None:
        try:
            await close(*args, **kwargs)
        finally:
            virtual_display.kill()

    browser.close = wrapped_close
    browser._virtual_display = virtual_display
    return browser


def sync_attach_vd(
    browser: Any,
    virtual_display: VirtualDisplay | None = None,
) -> Any:
    """
    Attach a virtual display lifecycle to a sync Playwright browser/context.

    The returned object is the same Playwright instance with `.close()`
    wrapped so that the `VirtualDisplay` is always torn down.
    """
    if not virtual_display:
        return browser

    close = browser.close

    def wrapped_close(*args: Any, **kwargs: Any) -> None:
        try:
            close(*args, **kwargs)
        finally:
            virtual_display.kill()

    browser.close = wrapped_close
    browser._virtual_display = virtual_display
    return browser


def get_env_vars(
    config: RotundaProfile | dict[str, Any],
    user_agent_os: str,
    *,
    executable_path: str | Path | None = None,
) -> dict[str, str | float | bool]:
    """
    Validate and serialize a config map into a runtime profile JSON file.

    The browser bootstrap code reads `ROTUNDA_CONFIG_PATH` before Firefox starts.
    On Linux, `executable_path` lets packaged browser artifacts supply their own
    fontconfig and font resources without requiring an installed Rotunda bundle.
    """
    profile = _build_runtime_profile(config)

    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="rotunda-profile-",
        suffix=".json",
        delete=False,
    ) as handle:
        handle.write(orjson.dumps(profile))
        config_path = handle.name

    env_vars: dict[str, str | float | bool] = {"ROTUNDA_CONFIG_PATH": config_path}

    if OS_NAME == "lin":
        fontconfig_path, fonts_path = _resolve_linux_font_resources(
            user_agent_os,
            Path(executable_path) if executable_path is not None else None,
        )
        env_vars["FONTCONFIG_FILE"] = _generate_fontconfig(fontconfig_path, fonts_path)

    return env_vars


def validate_config(config: RotundaProfile | dict[str, Any], path: Path | None = None) -> None:
    """
    Validate a profile payload against the generated Rotunda profile schema.

    `path` is accepted for backward compatibility with older callers.
    """
    _ = path
    _build_runtime_profile(config)


def _build_runtime_profile(config: RotundaProfile | dict[str, Any]) -> dict[str, Any]:
    try:
        profile_data = (
            config.model_dump(by_alias=True, exclude_none=True, mode="json")
            if isinstance(config, RotundaProfile)
            else config
        )
        profile = RotundaProfile.model_validate(profile_data)
    except ValidationError as exc:
        raise InvalidPropertyType(str(exc)) from exc
    return profile.model_dump(by_alias=True, exclude_none=True, mode="json")


@dataclass
class LaunchOptionBuilder:
    config: RotundaProfile | dict[str, Any] | None
    requested_os: ListOrString | None
    block_images: bool | None
    block_webrtc: bool | None
    block_webgl: bool | None
    disable_coop: bool | None
    geoip: str | bool | None
    geoip_db: str | None
    humanize: bool | float | None
    locale: str | list[str] | None
    addons: list[str] | None
    fonts: list[str] | None
    custom_fonts_only: bool | None
    default_addons: bool | None
    exclude_addons: list[DefaultAddons] | None
    screen: Screen | None
    window: tuple[int, int] | None
    fingerprint: Fingerprint | None
    fingerprint_preset: dict[str, Any] | None
    ff_version: int | None
    headless: bool | None
    main_world_eval: bool | None
    executable_path: str | Path | None
    browser: str | None
    firefox_user_prefs: dict[str, Any] | None
    proxy: dict[str, str] | None
    enable_cache: bool | None
    args: list[str] | None
    env: dict[str, str | float | bool] | None
    i_know_what_im_doing: bool | None
    debug: bool | None
    virtual_display: str | None
    extra_launch_options: dict[str, Any] = field(default_factory=dict)

    _manual_fonts: list[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        original_config = _coerce_profile(self.config)
        configured_fonts = original_config.fonts.families if original_config.fonts else None
        self._manual_fonts = _unique_strings(
            [*_string_list(configured_fonts), *(self.fonts or [])]
        )

        self.config = original_config
        self.addons = list(self.addons or [])
        self.args = list(self.args or [])
        self.firefox_user_prefs = dict(self.firefox_user_prefs or {})
        self.fonts = list(self.fonts or [])
        self.env = dict(self.env or cast(dict[str, str | float | bool], dict(environ)))
        self.extra_launch_options = dict(self.extra_launch_options)

        if self.headless is None:
            self.headless = False
        if self.custom_fonts_only is None:
            self.custom_fonts_only = False
        if self.default_addons is None:
            self.default_addons = False
        if self.i_know_what_im_doing is None:
            self.i_know_what_im_doing = False

        if isinstance(self.executable_path, str):
            self.executable_path = Path(abspath(self.executable_path))

    def _profile(self) -> RotundaProfile:
        config = self.config
        assert config is not None
        assert isinstance(config, RotundaProfile)
        return config

    def _addons_list(self) -> list[str]:
        addons = self.addons
        assert addons is not None
        return addons

    def _launch_args(self) -> list[str]:
        args = self.args
        assert args is not None
        return args

    def _env_map(self) -> dict[str, str | float | bool]:
        env = self.env
        assert env is not None
        return env

    def _firefox_prefs(self) -> dict[str, Any]:
        prefs = self.firefox_user_prefs
        assert prefs is not None
        return prefs

    def build(self) -> dict[str, Any]:
        config = self._profile()
        env = self._env_map()
        args = self._launch_args()
        firefox_user_prefs = self._firefox_prefs()

        if self.virtual_display:
            env["DISPLAY"] = self.virtual_display

        if not self.i_know_what_im_doing:
            _warn_manual_config(config)

        resolved_executable_path = self._resolve_executable_path()
        resolved_executable = Path(resolved_executable_path)
        _debug_log(self.debug, f"Using executable path: {resolved_executable}")

        target_os = _normalize_requested_os(self.requested_os)
        _debug_log(self.debug, f"Target fingerprint OS: {target_os}")
        self._configure_addons()

        ff_version_str = self._resolve_firefox_version(resolved_executable)
        _debug_log(self.debug, f"Resolved Firefox version: {ff_version_str}")
        generated_config = self._build_generated_config(target_os, ff_version_str)
        config = _merge_profile_missing(config, generated_config)
        self.config = config

        self._apply_fonts()
        self._apply_launch_defaults()
        self._apply_geoip()
        self._apply_locale()
        self._apply_humanize()
        self._apply_firefox_preferences()
        self._apply_headless_viewport_env()

        if self.debug:
            print("[DEBUG] Config:")
            pprint(config)

        _debug_log(self.debug, "Validating generated config against Rotunda profile schema.")
        validate_config(config, path=resolved_executable)

        runtime_env = {
            **env,
            **get_env_vars(
                config,
                _user_agent_os(config),
                executable_path=(
                    resolved_executable
                    if self.executable_path is not None or self.browser is not None
                    else None
                ),
            ),
        }
        configure_launch_debug_dump(
            runtime_env,
            executable_path=resolved_executable_path,
            firefox_user_prefs=firefox_user_prefs,
            config=config,
        )

        result = {
            "args": args,
            "env": runtime_env,
            "executable_path": resolved_executable_path,
            "firefox_user_prefs": firefox_user_prefs,
            "headless": self.headless,
            **self.extra_launch_options,
        }
        if self.proxy is not None:
            result["proxy"] = self.proxy
        return result

    def _configure_addons(self) -> None:
        addons = self._addons_list()
        config = self._profile()

        if self.default_addons:
            add_default_addons(addons, self.exclude_addons)
        if not addons:
            return

        confirm_paths(addons)
        config.addons = addons

    def _resolve_firefox_version(self, executable_path: Path) -> str | None:
        if self.ff_version is None:
            bundle_version = _load_bundle_version(executable_path)
            if bundle_version:
                return bundle_version.split(".", 1)[0]
            if self.executable_path is not None:
                return None
            return installed_verstr().split(".", 1)[0]

        LeakWarning.warn("ff_version", self.i_know_what_im_doing)
        return str(self.ff_version)

    def _build_generated_config(
        self, target_os: HostTargetOS, ff_version: str | None
    ) -> RotundaProfile:
        env = self._env_map()

        if self.fingerprint is not None:
            if not self.i_know_what_im_doing:
                _check_custom_fingerprint(self.fingerprint)
            _debug_log(self.debug, "Using caller-supplied BrowserForge fingerprint.")
            return from_browserforge(self.fingerprint, ff_version)

        preset = self._resolve_preset()
        if preset is not None:
            _debug_log(self.debug, "Using caller-supplied fingerprint preset.")
            return from_preset(preset, ff_version)

        _debug_log(self.debug, "Generating BrowserForge fingerprint for browser launch.")
        fingerprint = generate_fingerprint(
            os=target_os,
            screen=self.screen or _screen_constraints(bool(self.headless) or "DISPLAY" in env),
            window=self.window,
            debug=bool(self.debug),
        )
        return from_browserforge(fingerprint, ff_version)

    def _resolve_preset(self) -> dict[str, Any] | None:
        if self.fingerprint_preset is None:
            return None
        if isinstance(self.fingerprint_preset, bool):
            raise TypeError(
                "fingerprint_preset no longer accepts booleans. Pass an explicit preset dict or omit the argument."
            )
        return self.fingerprint_preset

    def _apply_fonts(self) -> None:
        config = self._profile()
        firefox_user_prefs = self._firefox_prefs()

        if self.custom_fonts_only:
            firefox_user_prefs["gfx.bundled-fonts.activate"] = 0
            if not self._manual_fonts:
                raise ValueError("No custom fonts were passed, but `custom_fonts_only` is enabled.")
            config.fonts = config.fonts or FontsProfile()
            config.fonts.families = self._manual_fonts
            return

        if self._manual_fonts:
            config.fonts = config.fonts or FontsProfile()
            config.fonts.families = _unique_strings(
                [*_string_list(config.fonts.families), *self._manual_fonts]
            )

    def _apply_launch_defaults(self) -> None:
        config = self._profile()

        config.window = config.window or WindowProfile()
        config.window.history = config.window.history or HistoryProfile()
        if config.window.history.length is None:
            config.window.history.length = randrange(1, 6)  # nosec

        config.fonts = config.fonts or FontsProfile()
        if config.fonts.spacing_seed is None:
            config.fonts.spacing_seed = 0

        config.audio = config.audio or AudioProfile()
        if config.audio.seed is None:
            config.audio.seed = randint(1, 4_294_967_295)  # nosec

    def _apply_geoip(self) -> None:
        config = self._profile()
        firefox_user_prefs = self._firefox_prefs()

        if not self.geoip:
            if (
                self.proxy
                and "localhost" not in self.proxy.get("server", "")
                and config.geolocation is None
            ):
                LeakWarning.warn("proxy_without_geoip")
            return

        geoip_allowed()
        ip_address = self.geoip
        if ip_address is True:
            ip_address = public_ip(Proxy(**self.proxy).as_string()) if self.proxy else public_ip()

        assert isinstance(ip_address, str)

        if not self.block_webrtc:
            if valid_ipv4(ip_address):
                config.webrtc = config.webrtc or WebRtcProfile()
                if config.webrtc.ipv4 is None:
                    config.webrtc.ipv4 = ip_address
                firefox_user_prefs["network.dns.disableIPv6"] = True
            elif valid_ipv6(ip_address):
                config.webrtc = config.webrtc or WebRtcProfile()
                if config.webrtc.ipv6 is None:
                    config.webrtc.ipv6 = ip_address

        geolocation = get_geolocation(ip_address, geoip_db=self.geoip_db)
        config.geolocation = GeolocationProfile(
            latitude=geolocation.latitude,
            longitude=geolocation.longitude,
            accuracy=geolocation.accuracy,
        )
        if config.timezone is None:
            config.timezone = geolocation.timezone
        config.locale = config.locale or LocaleProfile()
        if config.locale.language is None:
            config.locale.language = geolocation.locale.language
        if config.locale.region is None:
            config.locale.region = geolocation.locale.region
        if config.locale.script is None:
            config.locale.script = geolocation.locale.script

    def _apply_locale(self) -> None:
        if self.locale:
            locales = (
                [loc.strip() for loc in self.locale.split(",")]
                if isinstance(self.locale, str)
                else list(self.locale)
            )
            intl_locale = handle_locale(locales[0])
            config = self._profile()
            config.locale = config.locale or LocaleProfile()
            config.locale.language = intl_locale.language
            config.locale.region = intl_locale.region
            config.locale.script = intl_locale.script
            if len(locales) > 1:
                config.locale.all = _join_unique(
                    handle_locale(locale, ignore_region=True).as_string for locale in locales
                )
            if config.navigator is not None:
                config.navigator.language = intl_locale.as_string

    def _apply_humanize(self) -> None:
        config = self._profile()

        if self.humanize:
            config.humanize = config.humanize or HumanizeProfile()
            if config.humanize.enabled is None:
                config.humanize.enabled = True
            if isinstance(self.humanize, int | float) and config.humanize.max_time is None:
                config.humanize.max_time = self.humanize

        if self.main_world_eval and config.allow_main_world is None:
            config.allow_main_world = True

    def _apply_firefox_preferences(self) -> None:
        config = self._profile()
        firefox_user_prefs = self._firefox_prefs()

        _merge_missing(firefox_user_prefs, STEALTH_PREFS)

        do_not_track = config.navigator.do_not_track if config.navigator else None
        if hasattr(do_not_track, "value"):
            do_not_track = do_not_track.value
        if do_not_track == "1":
            firefox_user_prefs["privacy.donottrackheader.enabled"] = True
            firefox_user_prefs["privacy.donottrackheader.value"] = 1

        global_privacy_control = (
            config.navigator.global_privacy_control if config.navigator else None
        )
        if isinstance(global_privacy_control, bool):
            firefox_user_prefs["privacy.globalprivacycontrol.functionality.enabled"] = True
            firefox_user_prefs["privacy.globalprivacycontrol.enabled"] = global_privacy_control
            if not global_privacy_control:
                firefox_user_prefs["privacy.globalprivacycontrol.pbmode.enabled"] = False

        if self.block_images:
            LeakWarning.warn("block_images", self.i_know_what_im_doing)
            firefox_user_prefs["permissions.default.image"] = 2

        if self.block_webrtc:
            firefox_user_prefs["media.peerconnection.enabled"] = False

        if self.disable_coop:
            LeakWarning.warn("disable_coop", self.i_know_what_im_doing)
            firefox_user_prefs["browser.tabs.remote.useCrossOriginOpenerPolicy"] = False

        allow_webgl = self.extra_launch_options.pop("allow_webgl", True)
        if self.block_webgl or allow_webgl is False:
            firefox_user_prefs["webgl.disabled"] = True
            LeakWarning.warn("block_webgl", self.i_know_what_im_doing)

        if self.enable_cache:
            _merge_missing(firefox_user_prefs, CACHE_PREFS)

    def _apply_headless_viewport_env(self) -> None:
        if not self.headless:
            return

        config = self._profile()
        window = config.window
        if (
            window is None
            or not isinstance(window.inner_width, int)
            or window.inner_width <= 0
            or not isinstance(window.inner_height, int)
            or window.inner_height <= 0
        ):
            return

        env = self._env_map()
        env["MOZ_HEADLESS_WIDTH"] = str(window.inner_width)
        env["MOZ_HEADLESS_HEIGHT"] = str(window.inner_height)

        args = self._launch_args()
        _append_arg_value(args, "--window-size", f"{window.inner_width},{window.inner_height}")

    def _resolve_executable_path(self) -> str:
        if self.executable_path is not None:
            executable_path = Path(self.executable_path)
            if not executable_path.exists():
                placeholder_hint = ""
                executable_path_str = str(executable_path)
                if "<version>" in executable_path_str or "<release>" in executable_path_str:
                    placeholder_hint = (
                        " The README placeholder was used literally. Run `source upstream.sh` first, "
                        "then build the path with `$version` and `$release`."
                    )
                raise FileNotFoundError(
                    f"Rotunda executable does not exist: {executable_path}.{placeholder_hint}"
                )
            return str(executable_path)

        if self.browser:
            from .multiversion import find_installed_version

            browser_path = find_installed_version(self.browser)
            if not browser_path:
                raise ValueError(
                    f"Browser version '{self.browser}' not found. Run `rotunda list` to see installed versions."
                )
            return launch_path(browser_path)

        return launch_path()


def _generate_fontconfig(
    fontconfig_path: str | Path,
    fonts_dir: str | Path | None = None,
) -> str:
    import hashlib

    if fonts_dir is None:
        fonts_dir = get_path("fonts")
    fontconfig_path = Path(fontconfig_path)
    fonts_dir = Path(fonts_dir)
    fonts_conf_src = fontconfig_path / "fonts.conf"

    with fonts_conf_src.open(encoding="utf-8") as handle:
        conf_content = handle.read()

    conf_content = conf_content.replace(
        '<dir prefix="cwd">fonts</dir>',
        f"<dir>{fonts_dir}</dir>",
    )

    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "rotunda", "fontconfig")
    os.makedirs(cache_dir, exist_ok=True)

    content_hash = hashlib.sha256(conf_content.encode()).hexdigest()[:12]
    runtime_conf = os.path.join(cache_dir, f"fonts-{content_hash}.conf")
    if not os.path.exists(runtime_conf):
        with open(runtime_conf, "w", encoding="utf-8") as handle:
            handle.write(conf_content)

    return runtime_conf


def _normalize_requested_os(requested_os: ListOrString | None) -> HostTargetOS:
    values = requested_os
    if requested_os is not None:
        values = [requested_os] if isinstance(requested_os, str) else list(requested_os)
        _check_valid_os(cast(ListOrString, values))
    return normalize_target_os(values)


def _user_agent_os(config: RotundaProfile) -> Literal["mac", "win", "lin"]:
    user_agent = config.navigator.user_agent if config.navigator else None
    if user_agent:
        return _determine_ua_os(user_agent)
    return OS_NAME


def _determine_ua_os(user_agent: str) -> Literal["mac", "win", "lin"]:
    family = user_agent_parser.ParseOS(user_agent).get("family")
    if not family:
        raise ValueError("Could not determine OS from user agent")
    if family.startswith("Mac"):
        return "mac"
    if family.startswith("Windows"):
        return "win"
    return "lin"


def _screen_constraints(headless: bool | None = None) -> Screen | None:
    if headless is False:
        return None

    try:
        monitors = get_monitors()
    except Exception:
        return None
    if not monitors:
        return None

    monitor = max(monitors, key=lambda item: item.width * item.height)
    return Screen(max_width=monitor.width, max_height=monitor.height)


def _check_custom_fingerprint(fingerprint: Fingerprint) -> None:
    if is_generated_fingerprint(fingerprint):
        return

    browser_name = user_agent_parser.ParseUserAgent(fingerprint.navigator.userAgent).get(
        "family",
        "Non-Firefox",
    )
    if browser_name != "Firefox":
        raise NonFirefoxFingerprint(
            f'"{browser_name}" fingerprints are not supported in Rotunda. '
            "Using fingerprints from a browser other than Firefox will lead to detection. "
            "If this is intentional, pass `i_know_what_im_doing=True`."
        )
    LeakWarning.warn("custom_fingerprint", False)


def _check_valid_os(os_value: ListOrString) -> None:
    if not isinstance(os_value, str):
        for entry in os_value:
            _check_valid_os(entry)
        return

    if not os_value.islower():
        raise InvalidOS(f"OS values must be lowercase: '{os_value}'")
    if os_value not in ("windows", "macos", "linux"):
        raise InvalidOS(f"Rotunda does not support the OS: '{os_value}'")


def _merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        target.setdefault(key, value)


def _coerce_profile(config: RotundaProfile | dict[str, Any] | None) -> RotundaProfile:
    try:
        if isinstance(config, RotundaProfile):
            profile_data = config.model_dump(by_alias=True, exclude_none=True, mode="json")
        else:
            profile_data = config or {}
        return RotundaProfile.model_validate(profile_data)
    except ValidationError as exc:
        raise InvalidPropertyType(str(exc)) from exc


def _merge_profile_missing(target: RotundaProfile, source: RotundaProfile) -> RotundaProfile:
    target_data = target.model_dump(by_alias=True, exclude_none=True, mode="json")
    source_data = source.model_dump(by_alias=True, exclude_none=True, mode="json")
    _merge_missing_nested(target_data, source_data)
    return RotundaProfile.model_validate(target_data)


def _merge_missing_nested(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge_missing_nested(current, value)
        elif key not in target:
            target[key] = value


def _viewport_from_launch_options(options: Mapping[str, Any]) -> dict[str, int] | None:
    env = options.get("env")
    if not isinstance(env, Mapping):
        return None

    config_path = env.get("ROTUNDA_CONFIG_PATH")
    if not isinstance(config_path, str):
        return None

    try:
        profile = RotundaProfile.model_validate(
            json.loads(Path(config_path).read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValidationError):
        return None

    window = profile.window
    if (
        window is None
        or not isinstance(window.inner_width, int)
        or window.inner_width <= 0
        or not isinstance(window.inner_height, int)
        or window.inner_height <= 0
    ):
        return None

    return {"width": window.inner_width, "height": window.inner_height}


def _warn_manual_config(config: RotundaProfile) -> None:
    if (
        config.locale is not None
        or (config.navigator is not None and (config.navigator.language or config.navigator.languages))
        or (config.headers is not None and config.headers.accept_language)
    ):
        LeakWarning.warn("locale", False)
    if config.geolocation is not None or config.timezone is not None:
        LeakWarning.warn("geolocation", False)
    if config.headers is not None and config.headers.user_agent:
        LeakWarning.warn("header-ua", False)
    if _profile_section_set(config.navigator):
        LeakWarning.warn("navigator", False)
    if (
        _profile_section_set(config.screen)
        or _profile_section_set(config.window)
        or _profile_section_set(config.document)
    ):
        LeakWarning.warn("viewport", False)


def _profile_section_set(section: Any) -> bool:
    if section is None:
        return False
    if hasattr(section, "model_dump"):
        return bool(section.model_dump(exclude_none=True))
    return True


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [item for item in value if isinstance(item, str)]
    return []


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _append_arg_value(args: list[str], name: str, value: int | str) -> None:
    if any(arg == name or arg.startswith(f"{name}=") for arg in args):
        return
    args.extend([name, str(value)])


def _join_unique(values: Iterable[str]) -> str:
    return ", ".join(_unique_strings(values))


def _bundle_resource_roots(executable_path: Path) -> list[Path]:
    candidates = [
        executable_path.parent,
        executable_path.parent / "browser",
    ]
    if executable_path.parent.name == "MacOS" and executable_path.parent.parent.name == "Contents":
        candidates.insert(0, executable_path.parent.parent / "Resources")

    return _dedupe_paths(candidates)


def _source_bundle_root() -> Path:
    return Path(__file__).resolve().parents[2] / "browserbuild" / "bundle"


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _resolve_linux_font_resources(
    user_agent_os: str,
    executable_path: Path | None,
) -> tuple[Path, Path | None]:
    directory_map = {"lin": "linux", "mac": "macos", "win": "windows"}
    directory = directory_map[user_agent_os]

    if executable_path is not None:
        roots = [*_bundle_resource_roots(executable_path), _source_bundle_root()]
        if result := _find_linux_font_resources(roots, directory):
            return result

        raise FileNotFoundError(
            "fonts.conf not found in the Rotunda bundle for "
            f"{executable_path}. Expected fontconfig/{directory}/fonts.conf "
            "and fonts/ beside the executable or in the source bundle."
        )

    checked: list[str] = []
    for root_name in ("fontconfig", "fontconfigs"):
        fontconfig_path = Path(get_path(os.path.join(root_name, directory)))
        checked.append(str(fontconfig_path))
        if (fontconfig_path / "fonts.conf").exists():
            return fontconfig_path, Path(get_path("fonts"))

    raise FileNotFoundError(
        f"fonts.conf not found in {', '.join(checked)}. The Rotunda bundle is incomplete."
    )


def _find_linux_font_resources(
    roots: Iterable[Path],
    directory: str,
) -> tuple[Path, Path] | None:
    for root in _dedupe_paths(roots):
        fonts_path = root / "fonts"
        if not fonts_path.exists():
            continue
        for root_name in ("fontconfig", "fontconfigs"):
            fontconfig_path = root / root_name / directory
            if (fontconfig_path / "fonts.conf").exists():
                return fontconfig_path, fonts_path
    return None


def _resolve_bundle_resource(executable_path: Path, filename: str) -> Path:
    candidates = [root / filename for root in _bundle_resource_roots(executable_path)]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _load_bundle_version(executable_path: Path) -> str | None:
    application_ini = _resolve_bundle_resource(executable_path, "application.ini")
    if not application_ini.exists():
        return None

    parser = configparser.ConfigParser()
    parser.read(application_ini, encoding="utf-8")
    return parser.get("App", "Version", fallback=None)


def _debug_log(enabled: bool | None, message: str) -> None:
    if enabled:
        print(f"[rotunda:launch] {message}")
