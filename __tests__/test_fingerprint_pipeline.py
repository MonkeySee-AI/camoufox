from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import sys
import types
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "pythonlib" / "rotunda"


@dataclass(frozen=True)
class FakeNavigator:
    userAgent: str
    platform: str = "MacIntel"
    oscpu: Optional[str] = None


@dataclass(frozen=True)
class FakeScreen:
    width: int = 1500
    height: int = 970
    availWidth: int = 1490
    availHeight: int = 940
    availLeft: int = 0
    availTop: int = 0
    colorDepth: int = 24
    pixelDepth: int = 24
    outerHeight: int = 920
    outerWidth: int = 1400
    innerHeight: int = 880
    innerWidth: int = 1360
    screenX: int = 24
    screenY: int = 0
    pageXOffset: int = 0
    pageYOffset: int = 0
    clientWidth: int = 1360
    clientHeight: int = 880
    hasHDR: bool = False
    devicePixelRatio: float = 2.0


@dataclass(frozen=True)
class FakeFingerprint:
    navigator: FakeNavigator
    screen: FakeScreen


def _install_dependency_shims() -> None:
    if "rotunda.geo" not in sys.modules:
        geo_package = types.ModuleType("rotunda.geo")
        geo_package.__path__ = []
        sys.modules["rotunda.geo"] = geo_package

    if "orjson" not in sys.modules:
        orjson = types.ModuleType("orjson")
        orjson.JSONEncodeError = TypeError
        orjson.dumps = lambda value: json.dumps(value).encode("utf-8")
        orjson.loads = lambda value: json.loads(
            value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value
        )
        sys.modules["orjson"] = orjson

    if "browserforge.fingerprints" not in sys.modules:
        browserforge = types.ModuleType("browserforge")
        fingerprints = types.ModuleType("browserforge.fingerprints")

        @dataclass(frozen=True)
        class Screen:
            max_width: int = 0
            max_height: int = 0

        @dataclass
        class ScreenFingerprint:
            width: int = 0
            height: int = 0
            availWidth: int = 0
            availHeight: int = 0
            availLeft: int = 0
            availTop: int = 0
            colorDepth: int = 24
            pixelDepth: int = 24
            outerHeight: int = 0
            outerWidth: int = 0
            innerHeight: int = 0
            innerWidth: int = 0
            screenX: int = 0
            screenY: int = 0
            pageXOffset: int = 0
            pageYOffset: int = 0
            clientWidth: int = 0
            clientHeight: int = 0
            hasHDR: bool = False
            devicePixelRatio: float = 1.0

        @dataclass
        class Fingerprint:
            navigator: Any
            screen: Any

        class FingerprintGenerator:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def generate(self, **kwargs: Any) -> Fingerprint:
                raise AssertionError("Tests should stub FingerprintGenerator.generate()")

        fingerprints.Screen = Screen
        fingerprints.ScreenFingerprint = ScreenFingerprint
        fingerprints.Fingerprint = Fingerprint
        fingerprints.FingerprintGenerator = FingerprintGenerator

        browserforge.fingerprints = fingerprints
        sys.modules["browserforge"] = browserforge
        sys.modules["browserforge.fingerprints"] = fingerprints

    if "screeninfo" not in sys.modules:
        screeninfo = types.ModuleType("screeninfo")
        screeninfo.get_monitors = lambda: []
        sys.modules["screeninfo"] = screeninfo

    if "ua_parser" not in sys.modules:
        ua_parser = types.ModuleType("ua_parser")

        def parse_os(user_agent: str) -> Dict[str, str]:
            if "Macintosh" in user_agent:
                return {"family": "Mac OS X"}
            if "Windows" in user_agent:
                return {"family": "Windows"}
            return {"family": "Linux"}

        def parse_user_agent(user_agent: str) -> Dict[str, str]:
            return {"family": "Firefox" if "Firefox/" in user_agent else "Unknown"}

        ua_parser.user_agent_parser = types.SimpleNamespace(
            ParseOS=parse_os,
            ParseUserAgent=parse_user_agent,
        )
        sys.modules["ua_parser"] = ua_parser

    if "rotunda.pkgman" not in sys.modules:
        pkgman = types.ModuleType("rotunda.pkgman")

        def load_yaml(filename: str) -> Dict[str, Any]:
            import yaml

            with open(PACKAGE_ROOT / "assets" / filename, "r", encoding="utf-8") as handle:
                return yaml.safe_load(handle)

        pkgman.OS_NAME = "mac"
        pkgman.load_yaml = load_yaml
        pkgman.get_path = lambda file: f"/tmp/{file}"
        pkgman.installed_verstr = lambda: "150.0.1-beta.25"
        pkgman.launch_path = lambda browser_path=None: "/tmp/rotunda"
        sys.modules["rotunda.pkgman"] = pkgman

    if "rotunda._warnings" not in sys.modules:
        warnings_module = types.ModuleType("rotunda._warnings")

        class LeakWarning(RuntimeWarning):
            @staticmethod
            def warn(warning_key: str, i_know_what_im_doing: Optional[bool] = None) -> None:
                return None

        warnings_module.LeakWarning = LeakWarning
        sys.modules["rotunda._warnings"] = warnings_module

    if "rotunda.addons" not in sys.modules:
        addons = types.ModuleType("rotunda.addons")

        class DefaultAddons(Enum):
            UBO = "ubo"

        addons.DefaultAddons = DefaultAddons
        addons.add_default_addons = lambda addons_list, exclude_list=None: None
        addons.confirm_paths = lambda paths: None
        sys.modules["rotunda.addons"] = addons

    if "rotunda.geo.geolocation" not in sys.modules:
        geolocation = types.ModuleType("rotunda.geo.geolocation")
        geolocation.geoip_allowed = lambda: None
        geolocation.get_geolocation = lambda ip, geoip_db=None: types.SimpleNamespace(as_config=lambda: {})
        sys.modules["rotunda.geo.geolocation"] = geolocation
        sys.modules["rotunda.geo"].geolocation = geolocation

    if "rotunda.geo.ip" not in sys.modules:
        ip = types.ModuleType("rotunda.geo.ip")

        @dataclass(frozen=True)
        class Proxy:
            server: str
            username: str = ""
            password: str = ""

            def as_string(self) -> str:
                return self.server

        ip.Proxy = Proxy
        ip.public_ip = lambda proxy=None: "1.2.3.4"
        ip.valid_ipv4 = lambda value: "." in value
        ip.valid_ipv6 = lambda value: ":" in value
        ip.validate_ip = lambda value: None
        sys.modules["rotunda.geo.ip"] = ip
        sys.modules["rotunda.geo"].ip = ip

    if "rotunda.geo.locales" not in sys.modules:
        locales = types.ModuleType("rotunda.geo.locales")

        @dataclass(frozen=True)
        class Locale:
            language: str
            region: str
            script: Optional[str] = None

            @property
            def as_string(self) -> str:
                return f"{self.language}-{self.region}"

        def normalize_locale(locale: str) -> Locale:
            language, region = locale.split("-", 1)
            return Locale(language=language, region=region, script=None)

        locales.Locale = Locale
        locales.normalize_locale = normalize_locale
        locales.handle_locale = lambda locale, ignore_region=False: normalize_locale(locale)
        locales.handle_locales = lambda locales_arg, config: None
        locales.Geolocation = types.SimpleNamespace
        locales.SELECTOR = types.SimpleNamespace(
            from_region=lambda region: normalize_locale(f"en-{region}"),
            from_language=lambda language: normalize_locale(f"{language}-US"),
        )
        sys.modules["rotunda.geo.locales"] = locales
        sys.modules["rotunda.geo"].locales = locales

    if "rotunda.virtdisplay" not in sys.modules:
        virtdisplay = types.ModuleType("rotunda.virtdisplay")

        class VirtualDisplay:
            def kill(self) -> None:
                return None

        virtdisplay.VirtualDisplay = VirtualDisplay
        sys.modules["rotunda.virtdisplay"] = virtdisplay


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def modules() -> tuple[Any, Any, Any]:
    _install_dependency_shims()

    package = types.ModuleType("rotunda")
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["rotunda"] = package

    assets = _load_module("rotunda.assets", PACKAGE_ROOT / "assets" / "__init__.py")
    fingerprints = _load_module("rotunda.fingerprints", PACKAGE_ROOT / "fingerprints.py")
    utils = _load_module("rotunda.utils", PACKAGE_ROOT / "utils.py")
    return assets, fingerprints, utils


@pytest.fixture
def fake_host(modules: tuple[Any, Any, Any]) -> Any:
    _ = modules
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    voices = importlib.import_module("rotunda.fingerprinting.voices")
    return host_macos.MacOSHostAdapter(
        architecture="arm64",
        gpu_vendor="apple",
        gpu_family="apple_m_series",
        bundled_fonts=("Helvetica Neue", "PingFang SC"),
        extra_fonts=("Fira Code", "IBM Plex Sans", "JetBrains Mono"),
        bundled_voices=(voices.Voice("Alex", bundled=True), voices.Voice("Samantha", bundled=True)),
        extra_voices=(voices.Voice("Moira (Enhanced)"), voices.Voice("Karen (Premium)")),
    )


@pytest.fixture
def fake_linux_host(modules: tuple[Any, Any, Any]) -> Any:
    _ = modules
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")
    voices = importlib.import_module("rotunda.fingerprinting.voices")
    return host_linux.LinuxHostAdapter(
        architecture="x86_64",
        gpu_vendor="intel",
        gpu_family="intel_iris",
        bundled_fonts=("Arimo", "Cousine"),
        extra_fonts=("Fira Sans", "IBM Plex Sans", "JetBrains Mono"),
        bundled_voices=(voices.Voice("English", bundled=True), voices.Voice("German", bundled=True)),
        extra_voices=(),
    )


@pytest.fixture
def fake_fingerprint() -> FakeFingerprint:
    return FakeFingerprint(
        navigator=FakeNavigator(
            userAgent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) "
                "Gecko/20100101 Firefox/145.0"
            )
        ),
        screen=FakeScreen(),
    )


@pytest.fixture(autouse=True)
def stable_environment(
    modules: tuple[Any, Any, Any],
    fake_host: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fingerprints, utils = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")

    monkeypatch.setattr(hosts.sys, "platform", "darwin")
    monkeypatch.setattr(host_macos.MacOSHostAdapter, "_cached", fake_host)
    monkeypatch.setattr(host_linux.LinuxHostAdapter, "_cached", None)
    monkeypatch.setattr(hosts, "_sample_extras", lambda items: list(items[:2]))
    monkeypatch.setattr(fingerprints.FirefoxFingerprintCompiler, "_cached", {})

    monkeypatch.setattr(utils, "OS_NAME", "mac")
    monkeypatch.setattr(utils, "installed_verstr", lambda: "150.0.1-beta.25")
    monkeypatch.setattr(utils, "launch_path", lambda browser_path=None: "/tmp/rotunda")
    monkeypatch.setattr(
        utils,
        "get_path",
        lambda file: f"/tmp/{file}",
    )
    monkeypatch.setattr(utils, "add_default_addons", lambda addons, exclude: None)
    monkeypatch.setattr(utils, "confirm_paths", lambda addons: None)


@pytest.fixture
def fake_linux_fingerprint() -> FakeFingerprint:
    return FakeFingerprint(
        navigator=FakeNavigator(
            userAgent="Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0",
            platform="Linux x86_64",
            oscpu="Linux x86_64",
        ),
        screen=FakeScreen(
            width=1536,
            height=864,
            availWidth=1536,
            availHeight=864,
            outerHeight=832,
            outerWidth=1536,
            innerHeight=800,
            innerWidth=1504,
            devicePixelRatio=1.25,
        ),
    )


def test_from_browserforge_compiles_host_compatible_config(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
) -> None:
    _, fingerprints, _ = modules
    config = fingerprints.from_browserforge(fake_fingerprint, ff_version="150")

    assert config.navigator.user_agent.endswith("Firefox/150.0")
    assert config.navigator.app_version.startswith("5.0 (Macintosh; Intel Mac OS X 10.15")
    assert config.navigator.platform == "MacIntel"
    assert config.navigator.oscpu == "Intel Mac OS X 10.15"
    assert config.screen.width == 1500
    assert config.screen.height == 970
    assert config.screen.avail_width == 1490
    assert config.screen.avail_height == 940
    assert config.fonts.families == ["Helvetica Neue", "PingFang SC", "Fira Code", "IBM Plex Sans"]
    assert config.voices.items == ["Alex", "Samantha", "Moira (Enhanced)", "Karen (Premium)"]
    assert config.fonts.spacing_seed == 0
    assert isinstance(config.audio.seed, int)
    assert not hasattr(config, "canvas")


def test_macos_font_probe_uses_defaults_and_samples_local_extras(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    fonts = importlib.import_module("rotunda.fingerprinting.fonts")
    cache = _macos_inventory_cache(host_macos, tmp_path)

    discovered = (
        fonts.Font(
            "Helvetica Neue",
            path="/System/Library/Fonts/HelveticaNeue.ttc",
            is_system=True,
        ),
        fonts.Font(
            "PingFang SC",
            path="/System/Library/Fonts/PingFang.ttc",
            is_system=True,
        ),
        fonts.Font(
            "Noto Sans Gunjala Gondi Regular",
            path="/System/Library/Fonts/Supplemental/NotoSansGunjalaGondi-Regular.otf",
            is_system=True,
        ),
        fonts.Font("Cambria Math", path="/Library/Fonts/cambria.ttc", is_system=False),
        fonts.Font("Arimo", path="/Library/Fonts/Arimo.ttf", is_system=False),
        fonts.Font("Roboto", path="/Users/test/Library/Fonts/Roboto.ttf", is_system=False),
        fonts.Font(
            "Ubuntu Mono derivative Powerline",
            path="/Users/test/Library/Fonts/UbuntuMono.ttf",
            is_system=False,
        ),
        fonts.Font("MS Outlook", path="/Library/Fonts/MS Outlook.ttf", is_system=False),
        fonts.Font("OpenSymbol", path="/Library/Fonts/OpenSymbol.ttf", is_system=False),
        fonts.Font("Fira Code", path="/Library/Fonts/FiraCode.ttf", is_system=False),
    )

    monkeypatch.setattr(
        host_macos.MacOSHostAdapter,
        "_discover_installed_fonts",
        classmethod(lambda cls: discovered),
    )
    monkeypatch.setattr(
        host_macos.MacOSHostAdapter,
        "_discover_installed_voices",
        classmethod(lambda cls: ()),
    )
    monkeypatch.setattr(host_macos, "_probe_gpu_family", lambda: ("apple", "apple_m_series"))
    monkeypatch.setattr(host_macos, "_host_inventory_cache", lambda: cache)
    monkeypatch.setattr(hosts, "_sample_extras", lambda items: list(items))

    adapter = host_macos.MacOSHostAdapter._probe()
    sampled = adapter.sample_fonts()

    assert "Helvetica Neue" in adapter.bundled_fonts
    assert "PingFang SC" in adapter.bundled_fonts
    assert "Noto Sans Gunjala Gondi Regular" in adapter.bundled_fonts
    assert "PT Serif" in adapter.font_allowlist_aliases
    assert "STIXGeneral" in adapter.font_allowlist_aliases
    assert "PT Serif" in sampled
    assert "STIXGeneral" in sampled
    assert "Fira Code" in sampled
    assert "Cambria Math" not in sampled
    assert "Arimo" not in sampled
    assert "Roboto" not in sampled
    assert "Ubuntu Mono derivative Powerline" not in sampled
    assert "MS Outlook" not in sampled
    assert "OpenSymbol" not in sampled


def test_macos_host_probe_uses_fresh_inventory_cache(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    cache = _macos_inventory_cache(host_macos, tmp_path)
    cache.write(
        host_macos.MacOSHostInventory(
            architecture="arm64",
            gpu_vendor="apple",
            gpu_family="apple_m_series",
            fonts=[
                host_macos.CachedFont(family="Helvetica Neue", is_system=True),
                host_macos.CachedFont(family="Fira Code", is_system=False),
            ],
            voices=[
                host_macos.CachedVoice(name="Alex", bundled=True),
                host_macos.CachedVoice(name="Moira (Enhanced)", bundled=False),
            ],
        )
    )

    def unexpected_probe() -> None:
        raise AssertionError("fresh inventory cache should avoid host probes")

    monkeypatch.setattr(host_macos, "_host_inventory_cache", lambda: cache)
    monkeypatch.setattr(
        host_macos.MacOSHostAdapter,
        "_discover_installed_fonts",
        classmethod(lambda cls: unexpected_probe()),
    )
    monkeypatch.setattr(
        host_macos.MacOSHostAdapter,
        "_discover_installed_voices",
        classmethod(lambda cls: unexpected_probe()),
    )
    monkeypatch.setattr(host_macos, "_probe_gpu_family", unexpected_probe)
    monkeypatch.setattr(hosts, "_sample_extras", lambda items: list(items))

    adapter = host_macos.MacOSHostAdapter._probe()

    assert adapter.architecture == "arm64"
    assert adapter.gpu_vendor == "apple"
    assert adapter.gpu_family == "apple_m_series"
    assert "Helvetica Neue" in adapter.bundled_fonts
    assert "Fira Code" in adapter.sample_fonts()
    assert adapter.sample_voices() == ["Alex", "Moira (Enhanced)"]


def test_macos_font_probe_prefers_fast_appkit_inventory(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = modules
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")

    class FakeFontManager:
        def availableFontFamilies(self) -> list[str]:
            return ["Helvetica Neue", "Fira Code", "Helvetica Neue", ""]

    class FakeNSFontManager:
        @staticmethod
        def sharedFontManager() -> FakeFontManager:
            return FakeFontManager()

    fake_appkit = types.SimpleNamespace(NSFontManager=FakeNSFontManager)
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    monkeypatch.setattr(
        host_macos,
        "run_host_text",
        lambda *args: (_ for _ in ()).throw(AssertionError("system_profiler should not run")),
    )

    discovered = host_macos.MacOSHostAdapter._discover_installed_fonts()

    assert [font.family for font in discovered] == ["Helvetica Neue", "Fira Code"]
    assert discovered[0].is_system is True
    assert discovered[1].is_system is False


def _macos_inventory_cache(host_macos: Any, tmp_path: Path) -> Any:
    return host_macos.PydanticDiskCache(
        tmp_path / "macos-host-inventory.json",
        payload_model=host_macos.MacOSHostInventory,
        max_age_seconds=host_macos._HOST_INVENTORY_CACHE_MAX_AGE_SECONDS,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS AppKit")
def test_macos_appkit_font_probe_returns_real_families(
    modules: tuple[Any, Any, Any],
) -> None:
    _ = modules
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")

    discovered = host_macos._discover_fonts_with_appkit()
    families = {font.family for font in discovered}

    assert families
    assert {"Arial", "Helvetica"}.intersection(families)


def test_macos_font_blocklist_keeps_legitimate_mac_families(
    modules: tuple[Any, Any, Any],
) -> None:
    _ = modules
    common = importlib.import_module("rotunda.fingerprinting.common")
    fonts = importlib.import_module("rotunda.fingerprinting.fonts")

    assert fonts.is_blocked_family_for_target_os("Segoe Fluent Icons", common.MACOS)
    assert fonts.is_blocked_family_for_target_os("Ubuntu Mono derivative Powerline", common.MACOS)
    assert fonts.is_blocked_family_for_target_os("Roboto Mono for Powerline", common.MACOS)
    assert fonts.is_blocked_family_for_target_os("MS Outlook", common.MACOS)
    assert not fonts.is_blocked_family_for_target_os("Monaco", common.MACOS)
    assert not fonts.is_blocked_family_for_target_os("Noto Sans Gunjala Gondi Regular", common.MACOS)


def test_from_preset_keeps_explicit_preset_path_host_safe(modules: tuple[Any, Any, Any]) -> None:
    _, fingerprints, _ = modules
    preset = {
        "navigator": {
            "platform": "MacIntel",
            "userAgent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) "
                "Gecko/20100101 Firefox/145.0"
            ),
        },
        "screen": {
            "width": 1720,
            "height": 1100,
            "availWidth": 1700,
            "availHeight": 1060,
            "colorDepth": 24,
            "pixelDepth": 24,
            "devicePixelRatio": 2.0,
        },
        "timezone": "America/New_York",
    }

    config = fingerprints.from_preset(preset, ff_version="150")

    assert config.navigator.user_agent.endswith("Firefox/150.0")
    assert config.navigator.oscpu == "Intel Mac OS X 10.15"
    assert config.screen.width == 1720
    assert config.screen.height == 1100
    assert config.screen.avail_width == 1700
    assert config.screen.avail_height == 1060
    assert config.window.device_pixel_ratio == 2.0
    assert config.timezone == "America/New_York"
    assert config.fonts.families == ["Helvetica Neue", "PingFang SC", "Fira Code", "IBM Plex Sans"]
    assert config.voices.items == ["Alex", "Samantha", "Moira (Enhanced)", "Karen (Premium)"]


def test_from_preset_preserves_explicit_font_spacing_seed(
    modules: tuple[Any, Any, Any],
) -> None:
    _, fingerprints, _ = modules
    preset = {
        "navigator": {
            "platform": "MacIntel",
            "userAgent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) "
                "Gecko/20100101 Firefox/145.0"
            ),
        },
        "screen": {"width": 1720, "height": 1100},
        "fonts": {"spacingSeed": 123456},
    }

    config = fingerprints.from_preset(preset, ff_version="150")

    assert config.fonts.spacing_seed == 123456


def test_generate_context_fingerprint_strips_webgl_but_keeps_native_canvas(
    modules: tuple[Any, Any, Any],
) -> None:
    _, fingerprints, _ = modules
    preset = {
        "navigator": {
            "platform": "MacIntel",
            "userAgent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) "
                "Gecko/20100101 Firefox/145.0"
            ),
        },
        "screen": {"width": 1720, "height": 1100},
        "webGl": {"vendor": "Synthetic Vendor", "renderer": "Synthetic Renderer"},
        "webGl2": {"vendor": "Synthetic Vendor", "renderer": "Synthetic Renderer"},
    }

    result = fingerprints.generate_context_fingerprint(preset=preset, ff_version="150")
    config = result["config"]
    init_script = result["init_script"]
    payload = config.model_dump(by_alias=True, exclude_none=True, mode="json")

    assert not hasattr(config, "web_gl")
    assert not hasattr(config, "web_gl2")
    assert "webGl" not in payload
    assert "webGl2" not in payload
    assert config.fonts.spacing_seed == 0
    assert isinstance(config.audio.seed, int)
    assert not hasattr(config, "canvas")
    assert 'if (typeof w.setWebGLVendor === "function") w.setWebGLVendor' not in init_script
    assert 'if (typeof w.setWebGLRenderer === "function") w.setWebGLRenderer' not in init_script
    assert 'if (typeof w.setAudioFingerprintSeed === "function")' in init_script
    assert "setCanvasSeed" not in init_script
    assert 'if (typeof w.setFontSpacingSeed === "function")' in init_script
    assert "delete w.setWebRTCIPv6" in init_script


def test_generate_context_fingerprint_emits_debug_logs(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, fingerprints, _ = modules
    monkeypatch.setattr(fingerprints, "generate_fingerprint", lambda **_: fake_fingerprint)

    result = fingerprints.generate_context_fingerprint(debug=True)
    output = capsys.readouterr().out

    assert "[rotunda:fingerprint] Preparing fingerprinted browser context." in output
    assert "[rotunda:fingerprint] Generating BrowserForge Firefox skeleton." in output
    assert "[rotunda:fingerprint] Context options ready:" in output
    assert result["context_options"]["user_agent"].endswith("Firefox/145.0")


def test_runtime_profile_init_script_cleans_up_every_injected_setter(
    modules: tuple[Any, Any, Any],
) -> None:
    _, _, utils = modules

    script = utils.runtime_profile_init_script({})

    assert "delete w.setFontSpacingSeed" in script
    assert "delete w.setSpeechVoices" in script
    assert "delete w.setWebRTCIPv4" in script
    assert "delete w.setWebRTCIPv6" in script


def test_generate_context_fingerprint_reuses_supplied_browserforge_fingerprint(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fingerprints, _ = modules

    def _unexpected_generate(**_: Any) -> Any:
        raise AssertionError("generate_fingerprint should not be called when a fingerprint is supplied")

    monkeypatch.setattr(fingerprints, "generate_fingerprint", _unexpected_generate)

    result = fingerprints.generate_context_fingerprint(fingerprint=fake_fingerprint, ff_version="150")

    assert result["config"].navigator.user_agent.endswith("Firefox/150.0")
    assert result["context_options"]["viewport"] == {"width": 1360, "height": 880}


def test_derives_major_firefox_version_from_playwright_browser(
    modules: tuple[Any, Any, Any],
) -> None:
    _, fingerprints, _ = modules

    class PropertyBrowser:
        version = "150.0.1"

    class MethodBrowser:
        def version(self) -> str:
            return "Rotunda 150.0.1-beta.25"

    assert fingerprints._derive_browser_major_version(PropertyBrowser()) == "150"
    assert fingerprints._derive_browser_major_version(MethodBrowser()) == "150"
    assert fingerprints._derive_browser_major_version(types.SimpleNamespace(version=None)) is None


def test_new_context_uses_browser_version_when_ff_version_is_omitted(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = modules
    sync_api = importlib.import_module("rotunda.sync_api")
    captured: dict[str, Any] = {}

    class FakeContext:
        def add_init_script(self, script: str) -> None:
            captured["init_script"] = script

    class FakeBrowser:
        version = "150.0.1"

        def new_context(self, **opts: Any) -> FakeContext:
            captured["context_options"] = opts
            return FakeContext()

    def _fake_generate_context_fingerprint(**kwargs: Any) -> dict[str, Any]:
        captured["fingerprint_kwargs"] = kwargs
        return {"context_options": {}, "init_script": "/* init */"}

    monkeypatch.setattr(sync_api, "generate_context_fingerprint", _fake_generate_context_fingerprint)

    context = sync_api.NewContext(FakeBrowser(), fingerprint=fake_fingerprint)

    assert isinstance(context, FakeContext)
    assert captured["fingerprint_kwargs"]["ff_version"] == "150"
    assert captured["init_script"] == "/* init */"


def test_zero_browserforge_inner_window_dimensions_are_repaired(
    modules: tuple[Any, Any, Any],
) -> None:
    _, fingerprints, _ = modules
    fingerprint = FakeFingerprint(
        navigator=FakeNavigator(
            userAgent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:145.0) "
                "Gecko/20100101 Firefox/145.0"
            ),
        ),
        screen=FakeScreen(
            width=1440,
            height=900,
            availWidth=1440,
            availHeight=900,
            outerWidth=1440,
            outerHeight=900,
            innerWidth=0,
            innerHeight=0,
        ),
    )

    result = fingerprints.generate_context_fingerprint(fingerprint=fingerprint, ff_version="150")
    config = result["config"]

    assert config.window.inner_width == 1440
    assert config.window.inner_height == 872
    assert result["context_options"]["viewport"] == {"width": 1440, "height": 872}


def test_launch_options_does_not_warn_for_rotunda_generated_fingerprint(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fingerprints, utils = modules
    monkeypatch.setattr(
        fingerprints.FirefoxFingerprintCompiler.current().generator,
        "generate",
        lambda **_: fake_fingerprint,
    )

    generated_fingerprint = fingerprints.generate_fingerprint()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        utils.launch_options(fingerprint=generated_fingerprint, env={"TEST_ENV": "1"}, headless=True)

    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]


def test_launch_options_generates_full_config_payload(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(
        config=None,
        env={"TEST_ENV": "1"},
        headless=True,
        locale="en-US",
    )
    payload = _decode_rotunda_config(options["env"])

    assert options["executable_path"] == "/tmp/rotunda"
    assert options["headless"] is True
    assert options["env"]["TEST_ENV"] == "1"
    assert options["firefox_user_prefs"]["javascript.options.asyncstack"] is False
    assert payload["navigator"]["userAgent"].endswith("Firefox/150.0")
    assert payload["navigator"]["language"] == "en-US"
    assert payload["locale"]["language"] == "en"
    assert payload["locale"]["region"] == "US"
    assert payload["fonts"]["families"] == [
        "Helvetica Neue",
        "PingFang SC",
        "Fira Code",
        "IBM Plex Sans",
    ]
    assert payload["voices"]["items"] == ["Alex", "Samantha", "Moira (Enhanced)", "Karen (Premium)"]
    assert "webGl" not in payload
    assert 1 <= payload["window"]["history"]["length"] <= 5


def test_launch_options_does_not_install_default_addons_by_default(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    default_addon_calls = []
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)
    monkeypatch.setattr(
        utils,
        "add_default_addons",
        lambda addons, exclude: default_addon_calls.append((list(addons), exclude)),
    )

    options = utils.launch_options(env={"TEST_ENV": "1"}, headless=True)
    payload = _decode_rotunda_config(options["env"])

    assert default_addon_calls == []
    assert "addons" not in payload


def test_launch_options_can_opt_into_default_addons(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    default_path = "/tmp/rotunda-default-ubo"
    default_addon_calls = []
    confirmed_addons = []

    def add_default_addons(addons: list[str], exclude: list[Any] | None) -> None:
        default_addon_calls.append(exclude)
        addons.append(default_path)

    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)
    monkeypatch.setattr(utils, "add_default_addons", add_default_addons)
    monkeypatch.setattr(utils, "confirm_paths", lambda addons: confirmed_addons.append(list(addons)))

    options = utils.launch_options(
        default_addons=True,
        env={"TEST_ENV": "1"},
        exclude_addons=[utils.DefaultAddons.UBO],
        headless=True,
    )
    payload = _decode_rotunda_config(options["env"])

    assert default_addon_calls == [[utils.DefaultAddons.UBO]]
    assert confirmed_addons == [[default_path]]
    assert payload["addons"] == [default_path]


def test_launch_options_sets_headless_firefox_viewport_env(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(env={"TEST_ENV": "1"}, headless=True)

    assert options["env"]["MOZ_HEADLESS_WIDTH"] == str(fake_fingerprint.screen.innerWidth)
    assert options["env"]["MOZ_HEADLESS_HEIGHT"] == str(fake_fingerprint.screen.innerHeight)
    assert options["args"][-2:] == [
        "--window-size",
        f"{fake_fingerprint.screen.innerWidth},{fake_fingerprint.screen.innerHeight}",
    ]


def test_persistent_context_options_sets_viewport_from_runtime_profile(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(env={"TEST_ENV": "1"}, headless=True)
    context_options = utils.persistent_context_options(options)

    assert "viewport" not in options
    assert context_options["viewport"] == {
        "width": fake_fingerprint.screen.innerWidth,
        "height": fake_fingerprint.screen.innerHeight,
    }
    assert context_options["env"] == options["env"]


def test_persistent_context_options_respects_explicit_viewport(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(env={"TEST_ENV": "1"}, headless=True)
    context_options = utils.persistent_context_options(
        {**options, "viewport": {"width": 320, "height": 240}}
    )

    assert context_options["viewport"] == {"width": 320, "height": 240}


def test_persistent_context_options_drops_macos_foreground_for_background_windows(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils.sys, "platform", "darwin")

    context_options = utils.persistent_context_options(
        {
            "args": [],
            "env": {utils.MACOS_BACKGROUND_WINDOWS_ENV: "1"},
            "headless": False,
        }
    )

    assert context_options["ignore_default_args"] == ["-foreground"]


def test_persistent_context_options_keeps_macos_foreground_for_normal_launches(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils.sys, "platform", "darwin")

    context_options = utils.persistent_context_options(
        {
            "args": [],
            "env": {"TEST_ENV": "1"},
            "headless": False,
        }
    )

    assert "ignore_default_args" not in context_options


def test_launch_options_applies_navigator_tracking_signal_prefs(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        options = utils.launch_options(
            config={
                "navigator": {
                    "doNotTrack": "1",
                    "globalPrivacyControl": True,
                }
            },
            env={"TEST_ENV": "1"},
            headless=True,
        )

    prefs = options["firefox_user_prefs"]
    payload = _decode_rotunda_config(options["env"])

    assert prefs["privacy.donottrackheader.enabled"] is True
    assert prefs["privacy.donottrackheader.value"] == 1
    assert prefs["privacy.globalprivacycontrol.functionality.enabled"] is True
    assert prefs["privacy.globalprivacycontrol.enabled"] is True
    assert payload["navigator"]["doNotTrack"] == "1"
    assert payload["navigator"]["globalPrivacyControl"] is True
    assert not [
        warning for warning in caught if "Pydantic serializer warnings" in str(warning.message)
    ]


def test_launch_options_allows_explicit_asyncstack_override(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(
        env={"TEST_ENV": "1"},
        firefox_user_prefs={"javascript.options.asyncstack": True},
        headless=True,
    )

    assert options["firefox_user_prefs"]["javascript.options.asyncstack"] is True


def test_launch_options_enables_debug_dump_env_and_manifest(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    options = utils.launch_options(
        env={
            "ROTUNDA_DEBUG_DUMP_DIR": str(tmp_path),
            "ROTUNDA_DEBUG_DUMP": "manifest,returns",
            "ROTUNDA_VM_ACCESS_SAMPLE_RATE": "10",
            "ROTUNDA_VM_ACCESS_VALUE_STRINGS": "1",
        },
        headless=True,
    )

    assert options["env"]["ROTUNDA_VM_ACCESS_LOG"] == "1"
    assert options["env"]["ROTUNDA_VM_ACCESS_LOG_FILE"] == str(tmp_path / "vm-access.log")
    assert options["env"]["ROTUNDA_VM_ACCESS_BUFFERED"] == "1"
    assert options["env"]["ROTUNDA_VM_ACCESS_REALM"] == "1"
    assert options["env"]["ROTUNDA_VM_ACCESS_RETURNS"] == "1"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["launch"]["executable_path"] == "/tmp/rotunda"
    assert manifest["launch"]["config"]["navigator"]["userAgent"].endswith("Firefox/150.0")
    assert manifest["launch"]["xul"]["app_bundle"]["exists"] is False
    assert manifest["launch"]["env"]["ROTUNDA_VM_ACCESS_SAMPLE_RATE"] == "10"
    assert manifest["launch"]["env"]["ROTUNDA_VM_ACCESS_VALUE_STRINGS"] == "1"


def test_new_context_installs_debug_dump_hooks(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ = modules
    sync_api = importlib.import_module("rotunda.sync_api")

    class FakeContext:
        def __init__(self) -> None:
            self.handlers: dict[str, list[Any]] = {}
            self.init_scripts: list[str] = []
            self.pages: list[Any] = []

        def on(self, event: str, callback: Any) -> None:
            self.handlers.setdefault(event, []).append(callback)

        def add_init_script(self, script: str) -> None:
            self.init_scripts.append(script)

    class FakeBrowser:
        version = "150.0.1"
        _rotunda_debug_dump_env = {
            "ROTUNDA_DEBUG_DUMP_DIR": str(tmp_path),
            "ROTUNDA_DEBUG_DUMP": "manifest,network,console,vm,returns",
            "ROTUNDA_DEBUG_DUMP_MAX_BODY": "12",
        }

        def __init__(self) -> None:
            self.context = FakeContext()

        def new_context(self, **opts: Any) -> FakeContext:
            self.context_options = opts
            return self.context

    class FakeFrame:
        url = "https://example.test/frame"

    class FakeResponse:
        url = "https://example.test/api"
        status = 200
        status_text = "OK"
        headers = {"content-type": "application/json", "set-cookie": "secret=1"}

        def body(self) -> bytes:
            return b'{"access_token=supersecret"}'

    class FakeRequest:
        url = "https://example.test/api"
        method = "POST"
        resource_type = "xhr"
        headers = {"cookie": "session=secret", "x-test": "ok"}
        post_data = "api_key=supersecret"
        timing = {"startTime": 1}
        frame = FakeFrame()

        def is_navigation_request(self) -> bool:
            return False

        def response(self) -> FakeResponse:
            return FakeResponse()

    class FakePage:
        url = "https://example.test/"

        def __init__(self) -> None:
            self.handlers: dict[str, list[Any]] = {}

        def on(self, event: str, callback: Any) -> None:
            self.handlers.setdefault(event, []).append(callback)

    class FakeMessage:
        type = "debug"
        text = "debug message"
        location = {"url": "https://example.test/script.js", "lineNumber": 1}
        args: list[Any] = []

    monkeypatch.setattr(
        sync_api,
        "generate_context_fingerprint",
        lambda **_: {
            "context_options": {"user_agent": "ua"},
            "init_script": "/* fingerprint */",
            "config": None,
        },
    )

    browser = FakeBrowser()
    context = sync_api.NewContext(browser, fingerprint=fake_fingerprint)

    assert context.init_scripts[0] == "/* fingerprint */"
    assert {"request", "requestfinished", "requestfailed", "page"}.issubset(context.handlers)

    request = FakeRequest()
    context.handlers["request"][0](request)
    context.handlers["requestfinished"][0](request)

    page = FakePage()
    context.handlers["page"][0](page)
    page.handlers["console"][0](FakeMessage())

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["context"]["browser_version"] == "150.0.1"
    assert manifest["context"]["context_options"]["user_agent"] == "ua"

    network_lines = [
        json.loads(line) for line in (tmp_path / "network.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert network_lines[0]["headers"]["cookie"] == "<redacted>"
    assert network_lines[0]["post_data"]["text"] == "api_key=<redacted>"
    assert network_lines[1]["response"]["headers"]["set-cookie"] == "<redacted>"
    assert network_lines[1]["response"]["body"]["truncated"] is True

    console_lines = [
        json.loads(line) for line in (tmp_path / "console.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert console_lines[0]["text"] == "debug message"


def test_launch_options_rejects_webgl_profile_override(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)

    with pytest.raises(utils.InvalidPropertyType):
        utils.launch_options(
            config={"webGl": {"vendor": "spoofed"}},
            env={"TEST_ENV": "1"},
            headless=True,
        )


def test_from_browserforge_compiles_linux_host_compatible_config(
    modules: tuple[Any, Any, Any],
    fake_linux_host: Any,
    fake_linux_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fingerprints, utils = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")

    monkeypatch.setattr(hosts.sys, "platform", "linux")
    monkeypatch.setattr(host_macos.MacOSHostAdapter, "_cached", None)
    monkeypatch.setattr(host_linux.LinuxHostAdapter, "_cached", fake_linux_host)
    monkeypatch.setattr(fingerprints.FirefoxFingerprintCompiler, "_cached", {})
    monkeypatch.setattr(utils, "OS_NAME", "lin")

    config = fingerprints.from_browserforge(fake_linux_fingerprint, ff_version="150")

    assert config.navigator.user_agent.endswith("Firefox/150.0")
    assert config.navigator.app_version.startswith("5.0 (X11; Linux x86_64")
    assert config.navigator.platform == "Linux x86_64"
    assert config.navigator.oscpu == "Linux x86_64"
    assert config.screen.width == 1536
    assert config.screen.height == 864
    assert config.fonts.families == ["Arimo", "Cousine", "Fira Sans", "IBM Plex Sans"]
    assert config.voices.items == ["English", "German"]


def test_linux_font_probe_combines_defaults_local_and_bundled_extras(
    modules: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")
    fonts = importlib.import_module("rotunda.fingerprinting.fonts")

    installed = (
        fonts.Font("Arimo", path="/usr/share/fonts/arimo.ttf", is_system=True),
        fonts.Font("Cambria Math", path="/usr/share/fonts/cambria.ttc", is_system=True),
        fonts.Font("Fira Sans", path="/home/user/.local/share/fonts/fira.ttf", is_system=False),
    )
    bundled = (
        fonts.Font("Cousine", path="/opt/rotunda/fonts/linux/Cousine.ttf", is_system=True),
        fonts.Font(
            "Noto Color Emoji",
            path="/opt/rotunda/fonts/linux/NotoColorEmoji.ttf",
            is_system=True,
        ),
        fonts.Font("Roboto", path="/opt/rotunda/fonts/linux/Roboto.ttf", is_system=True),
        fonts.Font("Segoe UI", path="/opt/rotunda/fonts/linux/segoeui.ttf", is_system=True),
    )

    monkeypatch.setattr(hosts.sys, "platform", "linux")
    monkeypatch.setattr(
        host_linux.LinuxHostAdapter,
        "_discover_installed_fonts",
        classmethod(lambda cls: installed),
    )
    monkeypatch.setattr(
        host_linux.LinuxHostAdapter,
        "_discover_installed_voices",
        classmethod(lambda cls: ()),
    )
    monkeypatch.setattr(host_linux, "_discover_bundled_runtime_fonts", lambda: bundled)
    monkeypatch.setattr(host_linux, "_probe_gpu_family", lambda: ("intel", "intel_iris"))
    monkeypatch.setattr(hosts, "_sample_extras", lambda items: list(items))

    adapter = host_linux.LinuxHostAdapter._probe()
    sampled = adapter.sample_fonts()

    assert adapter.bundled_fonts == ("Arimo", "Cousine", "Noto Color Emoji")
    assert "Fira Sans" in sampled
    assert "Roboto" in sampled
    assert "Cambria Math" not in sampled
    assert "Segoe UI" not in sampled


def test_generate_fingerprint_dedupes_repeated_linux_screens(
    modules: tuple[Any, Any, Any],
    fake_linux_host: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fingerprints, _ = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")

    monkeypatch.setattr(hosts.sys, "platform", "linux")
    monkeypatch.setattr(host_macos.MacOSHostAdapter, "_cached", None)
    monkeypatch.setattr(host_linux.LinuxHostAdapter, "_cached", fake_linux_host)
    monkeypatch.setattr(fingerprints.FirefoxFingerprintCompiler, "_cached", {})

    def _fake_linux_fingerprint() -> Any:
        return types.SimpleNamespace(
            navigator=types.SimpleNamespace(
                userAgent=(
                    "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) "
                    "Gecko/20100101 Firefox/145.0"
                ),
                platform="Linux x86_64",
                oscpu="Linux x86_64",
            ),
            screen=types.SimpleNamespace(
                width=1536,
                height=864,
                availWidth=1536,
                availHeight=864,
                availTop=0,
                availLeft=0,
                colorDepth=24,
                pixelDepth=24,
                outerHeight=832,
                outerWidth=1536,
                innerHeight=800,
                innerWidth=1504,
                pageXOffset=0,
                pageYOffset=0,
                screenX=0,
                clientWidth=1504,
                clientHeight=800,
                hasHDR=False,
                devicePixelRatio=1.25,
            ),
        )

    compiler = fingerprints.FirefoxFingerprintCompiler.current("linux")
    monkeypatch.setattr(compiler.generator, "generate", lambda **_: _fake_linux_fingerprint())

    pairs = []
    for _ in range(5):
        fingerprint = fingerprints.generate_fingerprint(os="linux")
        pairs.append((fingerprint.screen.width, fingerprint.screen.height))

    assert pairs[0] == (1536, 864)
    assert len(set(pairs)) == len(pairs)


def test_linux_runtime_bundle_font_scan_exposes_expected_markers(
    modules: tuple[Any, Any, Any],
) -> None:
    _ = modules
    if shutil.which("fc-scan") is None:
        pytest.skip("fc-scan is required to inspect bundled Linux fonts.")

    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")
    discovered = host_linux._discover_bundled_runtime_fonts()
    families = {font.family for font in discovered}

    assert {"Arimo", "Cousine", "Tinos", "Twemoji Mozilla"} <= families


def test_launch_options_defaults_to_linux_host_target(
    modules: tuple[Any, Any, Any],
    fake_linux_host: Any,
    fake_linux_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, fingerprints, utils = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")

    monkeypatch.setattr(hosts.sys, "platform", "linux")
    monkeypatch.setattr(host_macos.MacOSHostAdapter, "_cached", None)
    monkeypatch.setattr(host_linux.LinuxHostAdapter, "_cached", fake_linux_host)
    monkeypatch.setattr(fingerprints.FirefoxFingerprintCompiler, "_cached", {})
    monkeypatch.setattr(utils, "OS_NAME", "lin")
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_linux_fingerprint)
    fontconfig_root = tmp_path / "fontconfigs" / "linux"
    fontconfig_root.mkdir(parents=True)
    (fontconfig_root / "fonts.conf").write_text('<fontconfig><dir prefix="cwd">fonts</dir></fontconfig>')
    monkeypatch.setattr(utils, "get_path", lambda file: str(tmp_path / file))

    options = utils.launch_options(env={"TEST_ENV": "1"}, headless=True)
    payload = _decode_rotunda_config(options["env"])

    assert utils._normalize_requested_os(None) == "linux"
    assert payload["navigator"]["platform"] == "Linux x86_64"
    assert payload["navigator"]["oscpu"] == "Linux x86_64"
    assert payload["fonts"]["families"] == ["Arimo", "Cousine", "Fira Sans", "IBM Plex Sans"]


def test_launch_options_rejects_literal_readme_placeholder_path(
    modules: tuple[Any, Any, Any],
) -> None:
    _, _, utils = modules

    with pytest.raises(FileNotFoundError, match="README placeholder was used literally"):
        utils.launch_options(
            executable_path="/tmp/rotunda-<version>-<release>/Rotunda.app/Contents/MacOS/rotunda"
        )


def test_launch_options_reads_version_from_macos_bundle(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)
    monkeypatch.setattr(utils, "installed_verstr", lambda: "142.0.0-beta.1")

    executable_path = tmp_path / "Rotunda.app" / "Contents" / "MacOS" / "rotunda"
    executable_path.parent.mkdir(parents=True)
    executable_path.write_text("", encoding="utf-8")

    resources = executable_path.parent.parent / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "application.ini").write_text(
        "[App]\nVersion=150.0.1-beta.25\n",
        encoding="utf-8",
    )

    options = utils.launch_options(executable_path=executable_path, env={"TEST_ENV": "1"}, headless=True)
    payload = _decode_rotunda_config(options["env"])

    assert options["executable_path"] == str(executable_path)
    assert payload["navigator"]["userAgent"].endswith("Firefox/150.0")


def test_launch_options_reads_version_from_linux_bundle_browser_dir(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)
    monkeypatch.setattr(
        utils,
        "installed_verstr",
        lambda: (_ for _ in ()).throw(AssertionError("should not use installed version")),
    )

    executable_path = tmp_path / "rotunda-bin"
    executable_path.write_text("", encoding="utf-8")
    browser_dir = executable_path.parent / "browser"
    browser_dir.mkdir()
    (browser_dir / "application.ini").write_text(
        "[App]\nVersion=150.0.1-beta.25\n",
        encoding="utf-8",
    )

    options = utils.launch_options(executable_path=executable_path, env={"TEST_ENV": "1"}, headless=True)
    payload = _decode_rotunda_config(options["env"])

    assert options["executable_path"] == str(executable_path)
    assert payload["navigator"]["userAgent"].endswith("Firefox/150.0")


def test_launch_options_uses_explicit_linux_bundle_font_resources(
    modules: tuple[Any, Any, Any],
    fake_linux_host: Any,
    fake_linux_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, utils = modules
    hosts = importlib.import_module("rotunda.fingerprinting.hosts")
    host_macos = importlib.import_module("rotunda.fingerprinting.host_macos")
    host_linux = importlib.import_module("rotunda.fingerprinting.host_linux")

    monkeypatch.setattr(hosts.sys, "platform", "linux")
    monkeypatch.setattr(host_macos.MacOSHostAdapter, "_cached", None)
    monkeypatch.setattr(host_linux.LinuxHostAdapter, "_cached", fake_linux_host)
    monkeypatch.setattr(utils, "OS_NAME", "lin")
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_linux_fingerprint)
    monkeypatch.setattr(
        utils,
        "get_path",
        lambda file: (_ for _ in ()).throw(AssertionError(f"should not resolve {file}")),
    )

    executable_path = tmp_path / "rotunda-bin"
    executable_path.write_text("", encoding="utf-8")
    fontconfig_root = tmp_path / "fontconfig" / "linux"
    fontconfig_root.mkdir(parents=True)
    (fontconfig_root / "fonts.conf").write_text(
        '<fontconfig><dir prefix="cwd">fonts</dir></fontconfig>',
        encoding="utf-8",
    )
    (tmp_path / "fonts" / "linux").mkdir(parents=True)

    options = utils.launch_options(
        executable_path=executable_path,
        env={"TEST_ENV": "1"},
        headless=True,
    )
    runtime_fontconfig = Path(options["env"]["FONTCONFIG_FILE"])

    assert options["executable_path"] == str(executable_path)
    assert f"<dir>{tmp_path / 'fonts'}</dir>" in runtime_fontconfig.read_text(encoding="utf-8")


def test_launch_options_does_not_fetch_installed_version_for_explicit_executable(
    modules: tuple[Any, Any, Any],
    fake_fingerprint: FakeFingerprint,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, _, utils = modules
    monkeypatch.setattr(utils, "generate_fingerprint", lambda **_: fake_fingerprint)
    monkeypatch.setattr(
        utils,
        "installed_verstr",
        lambda: (_ for _ in ()).throw(AssertionError("should not use installed version")),
    )

    executable_path = tmp_path / "rotunda-bin"
    executable_path.write_text("", encoding="utf-8")

    options = utils.launch_options(executable_path=executable_path, env={"TEST_ENV": "1"}, headless=True)
    payload = _decode_rotunda_config(options["env"])

    assert options["executable_path"] == str(executable_path)
    assert payload["navigator"]["userAgent"].endswith("Firefox/145.0")


def test_get_asset_by_name_returns_packaged_path(modules: tuple[Any, Any, Any]) -> None:
    assets, _, _ = modules

    asset_path = assets.get_asset_by_name("launchServer.js")

    assert asset_path.name == "launchServer.js"
    assert asset_path.is_file()
    assert asset_path.parent == PACKAGE_ROOT / "assets"


def _decode_rotunda_config(env: Dict[str, Any]) -> Dict[str, Any]:
    with open(env["ROTUNDA_CONFIG_PATH"], encoding="utf-8") as handle:
        return json.load(handle)
