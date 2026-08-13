from pathlib import Path


ROOT = Path(__file__).parents[1]
STREAM_CPP = ROOT / "additions/juggler/screencast/ElementVideoStream.cpp"
STREAM_MAC = ROOT / "additions/juggler/screencast/ElementVideoStreamMac.mm"
MOZ_BUILD = ROOT / "additions/juggler/screencast/moz.build"
NATIVE_PATCH = ROOT / "patches/native-element-snapshot.patch"


def test_shared_stream_keeps_platform_encoders_behind_gecko_contract() -> None:
    # This sentinel protects the architecture boundary: the stream owns one
    # layers::Image contract and Gecko chooses the platform encoder.
    source = STREAM_CPP.read_text()

    assert "EncoderAgent" in source
    assert "SourceSurfaceImage" in source
    assert "VideoToolbox" not in source
    assert "Media Foundation" not in source
    assert "mPendingEncode" in source
    assert "mPendingEncode->mPromise->Resolve(nsCString()" in source
    assert "EncodeSurface" in source
    assert "CreateBiPlanarSurface" in source
    assert "YUV420SP_NV12" in source
    assert "RenderIOSurface" in source
    assert "EncodeIOSurface" in source
    assert "startTaskToRender" in STREAM_MAC.read_text()
    assert "waitUntilCompletedAndReturnError" in STREAM_MAC.read_text()
    assert "imageByCompositingOverImage" in STREAM_MAC.read_text()
    assert "aDestinationRect.width / aSourceRect.width" in STREAM_MAC.read_text()
    assert "sourceSize.height - aSourceRect.YMost()" in STREAM_MAC.read_text()


def test_build_contract_includes_shared_media_encoder_headers() -> None:
    # The additions overlay is built on every target, so these private include
    # paths are an explicit compatibility sentinel for Gecko upgrades.
    source = MOZ_BUILD.read_text()

    assert "'ElementVideoStream.cpp'" in source
    assert "'ElementVideoStreamMac.mm'" in source
    assert "'/dom/media/platforms'" in source
    assert "'/dom/media/webcodecs'" in source


def test_macos_patch_keeps_iosurface_to_videotoolbox_handoff() -> None:
    # Product behavior is covered by the browser integration; this only warns
    # when the private AppleVTEncoder surface seam changes upstream.
    source = NATIVE_PATCH.read_text()

    assert "encodeElementVideoFrame(Element element" in source
    assert "CVPixelBufferCreateWithIOSurface" in source
    assert "AsMacIOSurfaceImage" in source
