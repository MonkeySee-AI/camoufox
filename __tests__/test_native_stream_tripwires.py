"""Tripwires for the native video stream's external seams.

These greps intentionally cover only contracts owned by someone else — private
Gecko APIs used by our patches and the platform gate that non-Playwright
Juggler clients hit. Behavior is owned by the executable suites:
__tests__/playwright/async/test_element_stream*.py and
__tests__/driver_hooks/test_element_screencast.py. Internal code shape is
deliberately not pinned here; refactors should not fight this file.
"""

from pathlib import Path

ROOT = Path(__file__).parents[1]
HANDLER = ROOT / "browserbuild/additions/juggler/protocol/PageHandler.js"
MOZ_BUILD = ROOT / "browserbuild/additions/juggler/screencast/moz.build"
ELEMENT_PATCH = ROOT / "browserbuild/patches/native-element-snapshot.patch"
IOSURFACE_PATCH = ROOT / "browserbuild/patches/native-viewport-iosurface.patch"
STRIDE_PATCH = ROOT / "browserbuild/patches/webrender-snapshot-stride.patch"


def test_native_video_is_gated_to_macos_at_the_juggler_boundary() -> None:
    # Direct Juggler clients bypass Playwright's host check, so the backend
    # gate must stay even if the driver patch also gates.
    handler = HANDLER.read_text()

    assert "AppConstants.platform !== 'macosx'" in handler
    assert "Linux and Microsoft Windows are not supported yet" in handler


def test_gecko_patches_keep_their_private_api_seams() -> None:
    # Warns when a Gecko upgrade moves the private surfaces our patches rely
    # on: the AppleVTEncoder IOSurface handoff, the compositor snapshot
    # export, and the WebRender readback stride fix.
    element_patch = ELEMENT_PATCH.read_text()
    iosurface_patch = IOSURFACE_PATCH.read_text()
    stride_patch = STRIDE_PATCH.read_text()

    assert "CVPixelBufferCreateWithIOSurface" in element_patch
    assert "AsMacIOSurfaceImage" in element_patch
    assert "SharedSurface_IOSurface::Create" in iosurface_patch
    assert "Readback produces tightly packed pixels" in stride_patch


def test_build_contract_includes_shared_media_encoder_headers() -> None:
    # The additions overlay is built on every target, so these private include
    # paths are an explicit compatibility sentinel for Gecko upgrades.
    source = MOZ_BUILD.read_text()

    assert "'/dom/media/platforms'" in source
    assert "'/dom/media/webcodecs'" in source
