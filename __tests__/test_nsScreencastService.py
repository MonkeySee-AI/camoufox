from pathlib import Path


ROOT = Path(__file__).parents[1]
SERVICE = ROOT / "additions/juggler/screencast/nsScreencastService.cpp"
IDL = ROOT / "additions/juggler/screencast/nsIScreencastService.idl"
HANDLER = ROOT / "additions/juggler/protocol/PageHandler.js"
REGISTRY = ROOT / "additions/juggler/TargetRegistry.js"
SNAPSHOT_PATCH = ROOT / "patches/webrender-snapshot-stride.patch"
IOSURFACE_PATCH = ROOT / "patches/native-viewport-iosurface.patch"


def test_native_viewport_stream_reuses_webrender_snapshot() -> None:
    # This compatibility sentinel keeps native viewport video on Gecko's
    # compositor readback and shared encoder instead of OS window capture.
    source = SERVICE.read_text()

    assert "BeginTransactionWithTarget" in source
    assert "EndEmptyTransaction" in source
    assert "CreateWindowCapturer" in source  # Legacy JPEG/WebM only.
    assert "if (!nativeVideo)" in source
    assert "ElementVideoStream::Create(options)" in source
    assert "EncodeSurface" in source
    assert "kMaxNativeFramesInFlight = 8" in source
    assert "TYPE_REPEATING_PRECISE_CAN_SKIP" in source


def test_headed_macos_viewport_uses_compositor_iosurface() -> None:
    # Headed macOS must route the native layer tree through a GPU IOSurface;
    # the browser integration owns the pixel and cross-process guarantees.
    service = SERVICE.read_text()
    patch = IOSURFACE_PATCH.read_text()

    assert "CreateWindowVideoSnapshotter" in service
    assert "GetWindowIOSurface(captureSize)" in service
    assert "EncodeIOSurface" in service
    assert "CompositorThread()->Dispatch" in service
    assert "SharedSurface_IOSurface::Create" in patch
    assert "RenderSnapshot(aWindowSize, mIOSurfaceSnapshot->mFb->mFB" in patch


def test_webrender_readback_keeps_buffer_stride() -> None:
    # This private compatibility sentinel protects the row-alignment fix; real
    # browser coverage below owns the no-shearing behavioral guarantee.
    source = SNAPSHOT_PATCH.read_text()

    assert "Readback produces tightly packed pixels" in source
    assert "Expand bottom-up in place" in source


def test_selectorless_video_routes_around_element_paint() -> None:
    # Browser integration owns pixel behavior; this only proves the Juggler
    # routing and generated XPCOM argument contract stay wired together.
    handler = HANDLER.read_text()
    registry = REGISTRY.read_text()
    idl = IDL.read_text()

    assert "if (options.objectId || options.frameId)" in handler
    assert "return await this._pageTarget.startScreencast(options)" in handler
    assert "nativeVideo" in idl
    assert "video, fps, bitrate, codec" in registry
    assert "devicePixelRatio * viewport.width" in registry
    assert "devicePixelRatio * viewport.height" in registry


def test_native_video_is_gated_to_macos_at_the_juggler_boundary() -> None:
    # This sentinel protects the backend gate shared by selector and viewport
    # video so direct Juggler clients cannot bypass Playwright's host check.
    handler = HANDLER.read_text()

    assert "options.video && AppConstants.platform !== 'macosx'" in handler
    assert "Linux and Microsoft Windows are not supported yet" in handler
