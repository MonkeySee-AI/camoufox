from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from random import randrange
from typing import Any, ClassVar, cast

from browserforge.fingerprints import (
    Fingerprint,
    FingerprintGenerator,
    ScreenFingerprint,
)

from .._generated_profile import (
    AudioProfile,
    ColorGamut,
    DoNotTrack,
    DynamicRange,
    FontsProfile,
    NavigatorProfile,
    RotundaProfile,
    ScreenProfile,
    VideoDynamicRange,
    WindowProfile,
)
from .common import LINUX, MACOS, WINDOWS, HostTargetOS, TargetOS
from .hosts import (
    HostFingerprintAdapter,
    current_host_target_os,
    get_host_adapter,
    normalize_target_os,
)

_DEFAULT_BROWSER_CHROME_HEIGHT = 28


@dataclass(frozen=True)
class CompiledScreen:
    width: int | None
    height: int | None
    color_depth: int | None
    avail_width: int | None = None
    avail_height: int | None = None
    avail_left: int | None = None
    avail_top: int | None = None
    device_pixel_ratio: float | None = None


@dataclass(frozen=True)
class FirefoxFingerprintCompiler:
    """
    Compile Firefox-oriented fingerprint sources into the runtime payload Rotunda uses.

    This class owns the translation boundary between upstream fingerprint inputs and
    Rotunda's internal browser profile model. It accepts either BrowserForge
    fingerprints or caller-supplied preset dictionaries, extracts the subset of
    Firefox-relevant fields we actually honor, and produces three coupled outputs:
    a normalized `RotundaProfile`, Playwright context options derived from that
    profile, and the per-context init script values that must stay in sync with it.

    It does not own host discovery or OS-specific realism policy. Those concerns
    stay in `HostFingerprintAdapter` implementations, which finalize the compiled
    profile after the raw field mapping step. Public orchestration such as
    argument validation and explicit locale/timezone overrides stays in
    `rotunda.fingerprints`.

    Instances are cached per host target OS because both the BrowserForge
    generator configuration and the host adapter are OS-bound.
    """

    target_os: HostTargetOS
    host: HostFingerprintAdapter
    generator: FingerprintGenerator

    _cached: ClassVar[dict[HostTargetOS, FirefoxFingerprintCompiler]] = {}

    @classmethod
    def current(cls, target_os: Any | None = None) -> FirefoxFingerprintCompiler:
        normalized = normalize_target_os(target_os)
        cached = cls._cached.get(normalized)
        if cached is None:
            cached = cls(
                target_os=normalized,
                host=get_host_adapter(normalized),
                generator=FingerprintGenerator(browser="firefox", os=(normalized,)),
            )
            cls._cached[normalized] = cached
        return cached

    def generate(self, window: tuple[int, int] | None = None, **config: Any) -> Fingerprint:
        config["os"] = normalize_target_os(config.get("os") or self.target_os)
        fingerprint = self.generator.generate(**config)
        self.host.adjust_generated_screen(fingerprint.screen)
        if window:
            _apply_window_override(fingerprint, *window)
        return fingerprint

    def compile_browserforge(
        self,
        fingerprint: Fingerprint,
        ff_version: str | None,
    ) -> RotundaProfile:
        normalize_target_os(browserforge_target_os(fingerprint))
        source = asdict(fingerprint)
        navigator = source.get("navigator", {})
        screen = asdict(fingerprint.screen)

        profile = RotundaProfile(
            navigator=_navigator_from_browserforge(navigator, ff_version),
            screen=_screen_from_mapping(screen),
            window=_window_from_mapping(screen),
        )
        _copy_screen_offsets(profile, fingerprint.screen)
        self.host.finalize_config(profile)
        return profile

    def compile_preset(self, preset: dict[str, Any], ff_version: str | None) -> RotundaProfile:
        normalize_target_os(preset_target_os(preset))

        navigator = preset.get("navigator", {})
        screen = preset.get("screen", {})
        fonts = preset.get("fonts", {})
        audio = preset.get("audio", {})

        navigator_profile = NavigatorProfile()
        screen_profile = ScreenProfile()
        window_profile = WindowProfile()
        profile = RotundaProfile(
            navigator=navigator_profile,
            screen=screen_profile,
            window=window_profile,
        )

        user_agent = navigator.get("userAgent")
        if isinstance(user_agent, str):
            patched_user_agent = _patch_firefox_version(user_agent, ff_version)
            navigator_profile.user_agent = patched_user_agent
            navigator_profile.app_version = _derive_app_version(patched_user_agent)

        platform_value = navigator.get("platform")
        if isinstance(platform_value, str):
            navigator_profile.platform = platform_value
        oscpu_value = navigator.get("oscpu")
        if isinstance(oscpu_value, str):
            navigator_profile.oscpu = oscpu_value
        if isinstance(preset.get("timezone"), str):
            profile.timezone = preset["timezone"]

        width = _non_negative_value(screen.get("width"))
        if isinstance(width, int):
            screen_profile.width = width

        height = _non_negative_value(screen.get("height"))
        if isinstance(height, int):
            screen_profile.height = height

        avail_width = _non_negative_value(screen.get("availWidth"))
        if isinstance(avail_width, int):
            screen_profile.avail_width = avail_width

        avail_height = _non_negative_value(screen.get("availHeight"))
        if isinstance(avail_height, int):
            screen_profile.avail_height = avail_height

        avail_left = _non_negative_value(screen.get("availLeft"))
        if isinstance(avail_left, int):
            screen_profile.avail_left = avail_left

        avail_top = _non_negative_value(screen.get("availTop"))
        if isinstance(avail_top, int):
            screen_profile.avail_top = avail_top

        color_depth = _non_negative_value(screen.get("colorDepth"))
        if isinstance(color_depth, int):
            screen_profile.color_depth = color_depth

        pixel_depth = _non_negative_value(screen.get("pixelDepth"))
        if isinstance(pixel_depth, int):
            screen_profile.pixel_depth = pixel_depth

        color_gamut = _color_gamut_value(screen.get("colorGamut"))
        if color_gamut is not None:
            screen_profile.color_gamut = color_gamut

        dynamic_range = _dynamic_range_value(screen.get("dynamicRange"))
        if dynamic_range is not None:
            screen_profile.dynamic_range = dynamic_range

        video_dynamic_range = _video_dynamic_range_value(screen.get("videoDynamicRange"))
        if video_dynamic_range is not None:
            screen_profile.video_dynamic_range = video_dynamic_range

        device_pixel_ratio = screen.get("devicePixelRatio")
        if isinstance(device_pixel_ratio, int | float):
            window_profile.device_pixel_ratio = float(device_pixel_ratio)

        if isinstance(fonts, dict):
            spacing_seed = _non_negative_value(fonts.get("spacingSeed"))
            if isinstance(spacing_seed, int):
                profile.fonts = FontsProfile.model_validate({"spacingSeed": spacing_seed})

        if isinstance(audio, dict):
            audio_seed = _non_negative_value(audio.get("seed"))
            if isinstance(audio_seed, int):
                profile.audio = AudioProfile(seed=audio_seed)

        self.host.finalize_config(profile)
        return profile

    def screen_from_browserforge(
        self,
        fingerprint: Fingerprint,
        config: RotundaProfile,
    ) -> CompiledScreen:
        screen = asdict(fingerprint.screen)
        return _compiled_screen_from_profile(config, screen)

    def screen_from_preset(
        self,
        preset: dict[str, Any],
        config: RotundaProfile,
    ) -> CompiledScreen:
        screen = preset.get("screen", {})
        return _compiled_screen_from_profile(config, screen)

    def build_context_options(
        self,
        config: RotundaProfile,
        screen: CompiledScreen,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}

        user_agent = config.navigator.user_agent if config.navigator else None
        if isinstance(user_agent, str):
            options["user_agent"] = user_agent

        window = config.window
        viewport_width = (
            window.inner_width
            if window and isinstance(window.inner_width, int) and window.inner_width > 0
            else screen.width
        )
        viewport_height = (
            window.inner_height
            if window and isinstance(window.inner_height, int) and window.inner_height > 0
            else (
                max(screen.height - _DEFAULT_BROWSER_CHROME_HEIGHT, 600)
                if screen.height
                else None
            )
        )

        if viewport_width and viewport_height:
            options["viewport"] = {
                "width": viewport_width,
                "height": viewport_height,
            }

        if screen.device_pixel_ratio:
            options["device_scale_factor"] = screen.device_pixel_ratio

        timezone = config.timezone
        if isinstance(timezone, str):
            options["timezone_id"] = timezone

        language = config.navigator.language if config.navigator else None
        if isinstance(language, str):
            options["locale"] = language

        return options

    def build_init_script(
        self,
        config: RotundaProfile,
        screen: CompiledScreen,
        webrtc_ip: str | None,
    ) -> str:
        values = {
            "audioFingerprintSeed": config.audio.seed if config.audio else None,
            "fontList": config.fonts.families if config.fonts else None,
            "fontSpacingSeed": config.fonts.spacing_seed if config.fonts else None,
            "hardwareConcurrency": (
                config.navigator.hardware_concurrency if config.navigator else None
            ),
            "navigatorOscpu": config.navigator.oscpu if config.navigator else None,
            "navigatorPlatform": config.navigator.platform if config.navigator else None,
            "navigatorUserAgent": config.navigator.user_agent if config.navigator else None,
            "screenColorDepth": screen.color_depth,
            "screenAvailHeight": screen.avail_height,
            "screenAvailLeft": screen.avail_left,
            "screenAvailTop": screen.avail_top,
            "screenAvailWidth": screen.avail_width,
            "screenHeight": screen.height,
            "screenWidth": screen.width,
            "speechVoices": config.voices.items if config.voices else None,
            "timezone": config.timezone,
            "webrtcIP": webrtc_ip or "",
        }

        lines = ["(function() {", "  var w = window;"]
        for key, setter in (
            ("fontSpacingSeed", "setFontSpacingSeed"),
            ("audioFingerprintSeed", "setAudioFingerprintSeed"),
            ("navigatorPlatform", "setNavigatorPlatform"),
            ("navigatorOscpu", "setNavigatorOscpu"),
            ("hardwareConcurrency", "setNavigatorHardwareConcurrency"),
            ("navigatorUserAgent", "setNavigatorUserAgent"),
        ):
            value = values.get(key)
            if value is not None:
                lines.append(
                    f'  if (typeof w.{setter} === "function") w.{setter}({json.dumps(value)});'
                )

        if values["screenWidth"] and values["screenHeight"]:
            window = config.window
            if (
                window
                and isinstance(window.outer_width, int)
                and window.outer_width > 0
                and isinstance(window.outer_height, int)
                and window.outer_height > 0
                and isinstance(window.inner_width, int)
                and window.inner_width > 0
                and isinstance(window.inner_height, int)
                and window.inner_height > 0
            ):
                screen_x = window.screen_x if isinstance(window.screen_x, int) else 0
                screen_y = window.screen_y if isinstance(window.screen_y, int) else 0
                lines.append(
                    "  if (typeof w.setWindowDimensions === \"function\") "
                    f"w.setWindowDimensions({window.outer_width}, {window.outer_height}, "
                    f"{window.inner_width}, {window.inner_height}, "
                    f"{max(screen_x, 0)}, {max(screen_y, 0)});"
                )
            if values["screenAvailWidth"] and values["screenAvailHeight"]:
                avail_left = values["screenAvailLeft"]
                avail_top = values["screenAvailTop"]
                lines.append(
                    "  if (typeof w.setScreenAvailableRect === \"function\") "
                    f"w.setScreenAvailableRect({values['screenAvailWidth']}, "
                    f"{values['screenAvailHeight']}, "
                    f"{max(avail_left if isinstance(avail_left, int) else 0, 0)}, "
                    f"{max(avail_top if isinstance(avail_top, int) else 0, 0)});"
                )
            lines.append(
                "  if (typeof w.setScreenDimensions === \"function\") "
                f"w.setScreenDimensions({values['screenWidth']}, {values['screenHeight']});"
            )
            if values["screenColorDepth"] is not None:
                lines.append(
                    "  if (typeof w.setScreenColorDepth === \"function\") "
                    f"w.setScreenColorDepth({values['screenColorDepth']});"
                )

        if values["timezone"]:
            lines.append(
                "  if (typeof w.setTimezone === \"function\") "
                f"w.setTimezone({json.dumps(values['timezone'])});"
            )

        lines.append(
            "  if (typeof w.setWebRTCIPv4 === \"function\") "
            f"w.setWebRTCIPv4({json.dumps(values['webrtcIP'])});"
        )

        fonts = values["fontList"]
        if isinstance(fonts, list) and fonts:
            lines.append(
                "  if (typeof w.setFontList === \"function\") "
                f"w.setFontList({json.dumps(','.join(fonts))});"
            )

        voices = values["speechVoices"]
        if isinstance(voices, list) and voices:
            voice_names = [voice if isinstance(voice, str) else voice.name for voice in voices]
            lines.append(
                "  if (typeof w.setSpeechVoices === \"function\") "
                f"w.setSpeechVoices({json.dumps(','.join(voice_names))});"
            )

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


def preset_target_os(preset: dict[str, Any]) -> TargetOS:
    navigator = preset.get("navigator", {})
    return infer_target_os(
        navigator.get("platform"),
        navigator.get("oscpu"),
        navigator.get("userAgent"),
    )


def browserforge_target_os(fingerprint: Fingerprint) -> TargetOS:
    navigator = fingerprint.navigator
    return infer_target_os(
        navigator.platform,
        navigator.oscpu,
        navigator.userAgent,
    )


def infer_target_os(*values: Any) -> TargetOS:
    for value in values:
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if "linux" in lowered or "x11" in lowered:
            return LINUX
        if "mac" in lowered or "darwin" in lowered:
            return MACOS
        if "windows" in lowered or lowered.startswith("win"):
            return WINDOWS
    return current_host_target_os()


def _as_optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _as_optional_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _extract_device_pixel_ratio(screen: dict[str, Any]) -> float | None:
    for key in ("devicePixelRatio", "pixelRatio"):
        value = _as_optional_float(screen.get(key))
        if value is not None:
            return value
    return None


def _navigator_from_browserforge(
    navigator: dict[str, Any],
    ff_version: str | None,
) -> NavigatorProfile:
    profile = NavigatorProfile()
    user_agent = navigator.get("userAgent")
    if isinstance(user_agent, str):
        profile.user_agent = _patch_firefox_version(user_agent, ff_version)
        profile.app_version = _derive_app_version(profile.user_agent)

    do_not_track = _normalize_do_not_track(navigator.get("doNotTrack"))
    if do_not_track is not None:
        profile.do_not_track = do_not_track

    app_code_name = navigator.get("appCodeName")
    if app_code_name is not None:
        profile.app_code_name = app_code_name

    app_name = navigator.get("appName")
    if app_name is not None:
        profile.app_name = app_name

    oscpu = navigator.get("oscpu")
    if oscpu is not None:
        profile.oscpu = oscpu

    platform_value = navigator.get("platform")
    if platform_value is not None:
        profile.platform = platform_value

    hardware_concurrency = navigator.get("hardwareConcurrency")
    if hardware_concurrency is not None:
        profile.hardware_concurrency = hardware_concurrency

    product = navigator.get("product")
    if product is not None:
        profile.product = product

    max_touch_points = navigator.get("maxTouchPoints")
    if max_touch_points is not None:
        profile.max_touch_points = max_touch_points

    extra = navigator.get("extraProperties", {})
    if isinstance(extra, dict) and isinstance(extra.get("globalPrivacyControl"), bool):
        profile.global_privacy_control = extra["globalPrivacyControl"]

    return profile


def _screen_from_mapping(screen: dict[str, Any]) -> ScreenProfile:
    profile = ScreenProfile()
    avail_left = _non_negative_value(screen.get("availLeft"))
    if avail_left is not None:
        profile.avail_left = avail_left

    avail_top = _non_negative_value(screen.get("availTop"))
    if avail_top is not None:
        profile.avail_top = avail_top

    avail_width = _non_negative_value(screen.get("availWidth"))
    if avail_width is not None:
        profile.avail_width = avail_width

    avail_height = _non_negative_value(screen.get("availHeight"))
    if avail_height is not None:
        profile.avail_height = avail_height

    height = _non_negative_value(screen.get("height"))
    if height is not None:
        profile.height = height

    width = _non_negative_value(screen.get("width"))
    if width is not None:
        profile.width = width

    color_depth = _non_negative_value(screen.get("colorDepth"))
    if color_depth is not None:
        profile.color_depth = color_depth

    pixel_depth = _non_negative_value(screen.get("pixelDepth"))
    if pixel_depth is not None:
        profile.pixel_depth = pixel_depth

    color_gamut = _color_gamut_value(screen.get("colorGamut"))
    if color_gamut is not None:
        profile.color_gamut = color_gamut

    dynamic_range = _dynamic_range_value(screen.get("dynamicRange"))
    if dynamic_range is not None:
        profile.dynamic_range = dynamic_range

    video_dynamic_range = _video_dynamic_range_value(screen.get("videoDynamicRange"))
    if video_dynamic_range is not None:
        profile.video_dynamic_range = video_dynamic_range

    page_x_offset = screen.get("pageXOffset")
    if page_x_offset is not None:
        profile.page_x_offset = page_x_offset

    page_y_offset = screen.get("pageYOffset")
    if page_y_offset is not None:
        profile.page_y_offset = page_y_offset
    return profile


def _window_from_mapping(screen: dict[str, Any]) -> WindowProfile:
    profile = WindowProfile()
    outer_height = _positive_int_value(screen.get("outerHeight"))
    if outer_height is not None:
        profile.outer_height = outer_height

    outer_width = _positive_int_value(screen.get("outerWidth"))
    if outer_width is not None:
        profile.outer_width = outer_width

    inner_height = _positive_int_value(screen.get("innerHeight"))
    if inner_height is not None:
        profile.inner_height = inner_height
    elif profile.outer_height:
        profile.inner_height = max(profile.outer_height - _DEFAULT_BROWSER_CHROME_HEIGHT, 1)
    else:
        height = _positive_int_value(screen.get("height"))
        if height is not None:
            profile.inner_height = max(height - _DEFAULT_BROWSER_CHROME_HEIGHT, 1)

    inner_width = _positive_int_value(screen.get("innerWidth"))
    if inner_width is not None:
        profile.inner_width = inner_width
    elif profile.outer_width:
        profile.inner_width = profile.outer_width
    else:
        width = _positive_int_value(screen.get("width"))
        if width is not None:
            profile.inner_width = width

    if profile.outer_height is not None and profile.inner_height is not None:
        profile.inner_height = min(profile.inner_height, profile.outer_height)
    if profile.outer_width is not None and profile.inner_width is not None:
        profile.inner_width = min(profile.inner_width, profile.outer_width)

    screen_x = screen.get("screenX")
    if screen_x is not None:
        profile.screen_x = screen_x

    screen_y = screen.get("screenY")
    if screen_y is not None:
        profile.screen_y = screen_y

    profile.device_pixel_ratio = _extract_device_pixel_ratio(screen)
    return profile


def _compiled_screen_from_profile(
    config: RotundaProfile,
    source_screen: dict[str, Any],
) -> CompiledScreen:
    return CompiledScreen(
        width=(config.screen.width if config.screen else None)
        or _as_optional_int(source_screen.get("width")),
        height=(config.screen.height if config.screen else None)
        or _as_optional_int(source_screen.get("height")),
        color_depth=(config.screen.color_depth if config.screen else None)
        or _as_optional_int(source_screen.get("colorDepth")),
        avail_width=(config.screen.avail_width if config.screen else None)
        or _as_optional_int(source_screen.get("availWidth")),
        avail_height=(config.screen.avail_height if config.screen else None)
        or _as_optional_int(source_screen.get("availHeight")),
        avail_left=(config.screen.avail_left if config.screen else None)
        or _as_optional_int(source_screen.get("availLeft")),
        avail_top=(config.screen.avail_top if config.screen else None)
        or _as_optional_int(source_screen.get("availTop")),
        device_pixel_ratio=(config.window.device_pixel_ratio if config.window else None)
        or _extract_device_pixel_ratio(source_screen),
    )


def _positive_int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    value = _non_negative_value(value)
    if isinstance(value, int) and value > 0:
        return value
    return None


def _copy_screen_offsets(config: RotundaProfile, screen: ScreenFingerprint) -> None:
    config.window = config.window or WindowProfile()
    if config.window.screen_x is None:
        config.window.screen_x = max(screen.screenX, 0)
    if config.window.screen_y is not None:
        return

    screen_x = screen.screenX
    if screen_x in range(-50, 51):
        config.window.screen_y = max(screen_x, 0)
        return

    avail_height = screen.availHeight - screen.outerHeight
    if avail_height <= 0:
        config.window.screen_y = 0
    else:
        config.window.screen_y = randrange(0, avail_height)  # nosec


def _apply_window_override(fingerprint: Fingerprint, outer_width: int, outer_height: int) -> None:
    screen = fingerprint.screen
    screen.screenX += max((screen.width - outer_width) // 2, 0)
    if screen.innerWidth:
        screen.innerWidth = max(outer_width - screen.outerWidth + screen.innerWidth, 0)
    if screen.innerHeight:
        screen.innerHeight = max(outer_height - screen.outerHeight + screen.innerHeight, 0)
    screen.outerWidth = outer_width
    screen.outerHeight = outer_height
    cast(Any, screen).screenY = max((screen.height - outer_height) // 2, 0)


def _patch_firefox_version(value: str, ff_version: str | None) -> str:
    if not ff_version:
        return value

    value = re.sub(r"Firefox/\d+\.0", f"Firefox/{ff_version}.0", value)
    return re.sub(r"rv:\d+\.0", f"rv:{ff_version}.0", value)


def _derive_app_version(user_agent: str) -> str:
    match = re.search(r"\(([^)]+)\)", user_agent)
    if not match:
        return "5.0"
    return f"5.0 ({match.group(1)})"


def _normalize_do_not_track(value: Any) -> DoNotTrack | None:
    if isinstance(value, DoNotTrack):
        return value
    if value in {"0", "1", "unspecified"}:
        return DoNotTrack(value)
    return None


def _color_gamut_value(value: Any) -> ColorGamut | None:
    if isinstance(value, ColorGamut):
        return value
    if isinstance(value, str):
        try:
            return ColorGamut(value)
        except ValueError:
            return None
    return None


def _dynamic_range_value(value: Any) -> DynamicRange | None:
    if isinstance(value, DynamicRange):
        return value
    if isinstance(value, str):
        try:
            return DynamicRange(value)
        except ValueError:
            return None
    return None


def _video_dynamic_range_value(value: Any) -> VideoDynamicRange | None:
    if isinstance(value, VideoDynamicRange):
        return value
    if isinstance(value, str):
        try:
            return VideoDynamicRange(value)
        except ValueError:
            return None
    return None


def _non_negative_value(value: Any) -> Any:
    if isinstance(value, int):
        return max(value, 0)
    return value
