from pathlib import Path


ROOT = Path(__file__).parents[1]
SERVICE = ROOT / "additions/juggler/screencast/nsScreencastService.cpp"
IDL = ROOT / "additions/juggler/screencast/nsIScreencastService.idl"
HANDLER = ROOT / "additions/juggler/protocol/PageHandler.js"
REGISTRY = ROOT / "additions/juggler/TargetRegistry.js"
SNAPSHOT_PATCH = ROOT / "patches/webrender-snapshot-stride.patch"
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
