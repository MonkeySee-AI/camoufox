from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CalledProcessError
from typing import ClassVar

from browserforge.fingerprints import ScreenFingerprint
from typing_extensions import Self

from .._generated_profile import NavigatorProfile, RotundaProfile
from .common import LINUX, HostTargetOS
from .fonts import (
    Font,
    default_families_for_target_os,
    essential_families_for_target_os,
)
from .hosts import (
    HostFingerprintAdapter,
    dedupe,
    normalize_architecture,
    normalize_gpu_family,
    normalize_gpu_vendor,
    normalize_target_os,
    run_host_text,
)
from .voices import Voice, dedupe_voices, voice_definitions_for_target_os

_LINUX_FONT_SUFFIXES = {".otf", ".ttc", ".ttf"}
_LINUX_BASELINE_FONTS = essential_families_for_target_os(LINUX)
_COMMON_LINUX_SCREEN_SIZES: tuple[tuple[int, int], ...] = (
    (1280, 800),
    (1280, 1024),
    (1366, 768),
    (1400, 900),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1680, 1050),
    (1920, 1080),
    (1920, 1200),
    (2048, 1152),
    (2560, 1440),
)


@dataclass(frozen=True)
class LinuxHostAdapter(HostFingerprintAdapter):
    _cached: ClassVar[Self | None] = None
    _issued_screen_pairs: set[tuple[int, int]] = field(
        default_factory=set,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def target_os(self) -> HostTargetOS:
        return LINUX

    @classmethod
    def _probe(cls) -> Self:
        normalize_target_os(LINUX)

        discovered_fonts = cls._discover_installed_fonts()
        bundled_runtime_fonts = _discover_bundled_runtime_fonts()
        discovered_voices = cls._discover_installed_voices()
        matched_catalog_voices = cls._filter_locally_available_voices(
            list(voice_definitions_for_target_os(LINUX)),
            discovered_voices,
        )
        matched_catalog_voice_names = {voice.name for voice in matched_catalog_voices}
        gpu_vendor, gpu_family = _probe_gpu_family()

        default_font_families = {
            family.casefold() for family in default_families_for_target_os(LINUX)
        }
        bundled_fonts: list[str] = []
        extra_fonts: list[str] = []

        for font in (*discovered_fonts, *bundled_runtime_fonts):
            if font.family.casefold() in default_font_families:
                bundled_fonts.append(font.family)
            else:
                extra_fonts.append(font.family)

        bundled_voices = [voice for voice in matched_catalog_voices if voice.bundled]
        extra_voices = [voice for voice in matched_catalog_voices if not voice.bundled]
        for voice in discovered_voices:
            if voice.name in matched_catalog_voice_names:
                continue
            if voice.bundled:
                bundled_voices.append(voice)
            else:
                extra_voices.append(voice)

        return cls(
            architecture=normalize_architecture(platform.machine()),
            gpu_vendor=gpu_vendor,
            gpu_family=gpu_family,
            bundled_fonts=dedupe(bundled_fonts),
            extra_fonts=dedupe(extra_fonts),
            bundled_voices=dedupe_voices(bundled_voices),
            extra_voices=dedupe_voices(extra_voices),
        )

    @classmethod
    def _discover_installed_fonts(cls) -> tuple[Font, ...]:
        try:
            output = run_host_text("fc-list", "--format", "%{family}\t%{file}\n")
        except (CalledProcessError, FileNotFoundError):
            return ()
        records: list[Font] = []
        seen: set[str] = set()

        for line in output.splitlines():
            try:
                families_part, font_path = line.rsplit("\t", 1)
            except ValueError:
                continue

            is_system = _is_system_font(font_path)
            for family in [part.strip() for part in families_part.split(",") if part.strip()]:
                if family in seen:
                    continue
                seen.add(family)
                records.append(Font(family=family, path=font_path, is_system=is_system))

        return tuple(records)

    @classmethod
    def _discover_installed_voices(cls) -> tuple[Voice, ...]:
        for command in (("espeak-ng", "--voices"), ("espeak", "--voices")):
            try:
                output = run_host_text(*command)
            except (CalledProcessError, FileNotFoundError):
                continue
            voices = _parse_espeak_voices(output)
            if voices:
                return tuple(voices)
        return ()

    def adjust_generated_screen(self, screen: ScreenFingerprint) -> None:
        """
        Expand Linux screen variety when BrowserForge repeats the same panels.

        BrowserForge's Linux Firefox dataset currently clusters around a very
        small set of resolutions. Real Linux desktops are more varied, so
        repeated per-context profiles inside a single run look artificial and
        fail the build-tester's cross-profile uniqueness checks.

        The first generated size is preserved exactly. When that same size
        appears again, we remap it onto the nearest unused entry in a curated
        Linux desktop pool, then keep `screen`, `avail*`, and browser-chrome
        geometry aligned. This keeps the output realistic without inventing
        implausible aspect ratios.
        """
        width = screen.width
        height = screen.height
        if not isinstance(width, int) or not isinstance(height, int):
            return

        target_width, target_height = self._select_screen_pair(width, height)
        if (target_width, target_height) == (width, height):
            return

        screen.width = target_width
        screen.height = target_height
        screen.availWidth = target_width
        screen.availHeight = target_height

        outer_width = screen.outerWidth
        inner_width = screen.innerWidth
        if isinstance(outer_width, int):
            width_delta = outer_width - inner_width if isinstance(inner_width, int) else 0
            screen.outerWidth = min(outer_width, target_width)
            if isinstance(inner_width, int):
                screen.innerWidth = max(screen.outerWidth - width_delta, 0)

        outer_height = screen.outerHeight
        inner_height = screen.innerHeight
        if isinstance(outer_height, int):
            height_delta = outer_height - inner_height if isinstance(inner_height, int) else 0
            screen.outerHeight = min(outer_height, target_height)
            if isinstance(inner_height, int):
                screen.innerHeight = max(screen.outerHeight - height_delta, 0)

    def ensure_platform(self, config: RotundaProfile) -> None:
        if not config.navigator:
            config.navigator = NavigatorProfile()
        if not config.navigator.platform:
            config.navigator.platform = f"Linux {self.architecture}"

    def ensure_oscpu(self, config: RotundaProfile) -> None:
        if not config.navigator:
            config.navigator = NavigatorProfile()
        if config.navigator.oscpu:
            return
        platform_value = config.navigator.platform or f"Linux {self.architecture}"
        if "Linux" in platform_value:
            config.navigator.oscpu = platform_value

    def _select_screen_pair(self, width: int, height: int) -> tuple[int, int]:
        pair = (width, height)
        if pair not in self._issued_screen_pairs:
            self._issued_screen_pairs.add(pair)
            return pair

        candidates = [
            candidate
            for candidate in _COMMON_LINUX_SCREEN_SIZES
            if candidate not in self._issued_screen_pairs
        ]
        if not candidates:
            return pair

        selected = min(
            candidates,
            key=lambda candidate: abs(candidate[0] - width) + abs(candidate[1] - height),
        )
        self._issued_screen_pairs.add(selected)
        return selected


def _probe_gpu_family() -> tuple[str | None, str | None]:
    for probe in (_probe_gpu_from_lspci, _probe_gpu_from_glxinfo):
        try:
            gpu = probe()
        except (CalledProcessError, FileNotFoundError):
            continue
        if gpu != (None, None):
            return gpu
    return None, None


def _probe_gpu_from_lspci() -> tuple[str | None, str | None]:
    output = run_host_text("lspci")
    for line in output.splitlines():
        if not re.search(r"(VGA compatible controller|3D controller|Display controller)", line):
            continue
        renderer = line.split(":", 2)[-1].strip()
        vendor = normalize_gpu_vendor(renderer)
        family = normalize_gpu_family(renderer)
        return vendor, family
    return None, None


def _probe_gpu_from_glxinfo() -> tuple[str | None, str | None]:
    output = run_host_text("glxinfo", "-B")
    vendor = None
    renderer = ""
    for line in output.splitlines():
        if "OpenGL vendor string:" in line:
            vendor = normalize_gpu_vendor(line.split(":", 1)[1].strip())
        elif "OpenGL renderer string:" in line:
            renderer = line.split(":", 1)[1].strip()
    if renderer:
        return vendor or normalize_gpu_vendor(renderer), normalize_gpu_family(renderer)
    return None, None


def _parse_espeak_voices(output: str) -> list[Voice]:
    voices: list[Voice] = []
    seen: set[str] = set()
    for line in output.splitlines():
        line = line.rstrip()
        if not line or line.startswith("Pty "):
            continue
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=4)
        if len(parts) < 4:
            continue
        name = parts[3].strip()
        if name in seen:
            continue
        seen.add(name)
        voices.append(Voice(name=name, bundled=True))
    return voices


def _is_system_font(font_path: str) -> bool:
    home = str(Path.home())
    user_prefixes = (
        f"{home}/.fonts",
        f"{home}/.local/share/fonts",
    )
    return not any(font_path.startswith(prefix) for prefix in user_prefixes)


def _is_baseline_font(family: str) -> bool:
    return family in _LINUX_BASELINE_FONTS


def _discover_bundled_runtime_fonts() -> tuple[Font, ...]:
    for font_dir in _runtime_font_dir_candidates():
        if not font_dir.is_dir():
            continue

        font_paths = [
            path
            for path in sorted(font_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in _LINUX_FONT_SUFFIXES
        ]
        if not font_paths:
            continue

        try:
            output = run_host_text(
                "fc-scan",
                "--format",
                "%{family}\t%{file}\n",
                *(str(path) for path in font_paths),
            )
        except (CalledProcessError, FileNotFoundError):
            continue
        discovered = _parse_font_scan_output(output)
        if discovered:
            return discovered
    return ()


def _runtime_font_dir_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = [
        Path(__file__).resolve().parents[3] / "browserbuild" / "bundle" / "fonts" / "linux"
    ]

    try:
        from ..pkgman import OS_NAME, rotunda_path

        install_root = rotunda_path(download_if_missing=False)
        if OS_NAME == "mac":
            candidates.append(
                install_root / "Rotunda.app" / "Contents" / "Resources" / "fonts" / "linux"
            )
        else:
            candidates.append(install_root / "fonts" / "linux")
    except Exception:
        pass

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def _parse_font_scan_output(output: str) -> tuple[Font, ...]:
    records: list[Font] = []
    seen: set[str] = set()

    for line in output.splitlines():
        try:
            families_part, font_path = line.rsplit("\t", 1)
        except ValueError:
            continue

        is_system = _is_system_font(font_path)
        for family in [part.strip() for part in families_part.split(",") if part.strip()]:
            if family in seen:
                continue
            seen.add(family)
            records.append(Font(family=family, path=font_path, is_system=is_system))

    return tuple(records)
