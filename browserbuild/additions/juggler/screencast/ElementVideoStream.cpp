/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ElementVideoStream.h"

#include <algorithm>
#include <cmath>

#include <atomic>

#include "EncoderConfig.h"
#include "MediaData.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/dom/EncoderAgent.h"
#include "mozilla/gfx/2D.h"
#include "mozilla/gfx/InlineTranslator.h"
#include "ImageContainer.h"
#include "nsThreadUtils.h"

#ifdef XP_MACOSX
#  include "ElementVideoStreamMac.h"
#  include "mozilla/gfx/MacIOSurface.h"
#  include "MacIOSurfaceImage.h"
#endif

namespace mozilla::dom {

namespace {

static std::atomic<uint32_t> sNextStreamId{1};

MediaResult StreamError(const nsACString& aMessage) {
  return MediaResult(NS_ERROR_DOM_MEDIA_FATAL_ERR, aMessage);
}

#ifdef XP_MACOSX

// The output canvas is modeled as an up-to-kMaxRasterScale× HiDPI raster of a
// kLogicalWidth×kLogicalHeight logical viewport: elements are fitted in
// logical space, then rastered at the canvas's pixel density. Clients do not
// re-derive these constants; they present the region the RSE2 crop rectangle
// describes (see scripts/stream-selector-webrtc.py).
constexpr double kLogicalWidth = 1280.0;
constexpr double kLogicalHeight = 720.0;
constexpr double kMaxRasterScale = 2.0;

void AppendUint32(nsCString& aPacket, uint32_t aValue) {
  for (int shift = 24; shift >= 0; shift -= 8) {
    aPacket.Append(static_cast<char>(aValue >> shift));
  }
}

void AppendUint64(nsCString& aPacket, uint64_t aValue) {
  AppendUint32(aPacket, static_cast<uint32_t>(aValue >> 32));
  AppendUint32(aPacket, static_cast<uint32_t>(aValue));
}

void AppendEncodedFrame(nsCString& aPacket, const MediaRawData& aFrame,
                        const gfx::IntSize& aSize,
                        const gfx::IntRect& aContentRect) {
  aPacket.Append("RSE2", 4);
  aPacket.Append(aFrame.mKeyframe ? '\x01' : '\x00');
  AppendUint32(aPacket, AssertedCast<uint32_t>(aFrame.Size()));
  AppendUint64(aPacket,
               AssertedCast<uint64_t>(aFrame.mTime.ToMicroseconds()));
  AppendUint32(
      aPacket,
      AssertedCast<uint32_t>(aFrame.mDuration.ToMicroseconds()));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aSize.width));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aSize.height));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aContentRect.x));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aContentRect.y));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aContentRect.width));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aContentRect.height));
  aPacket.Append(reinterpret_cast<const char*>(aFrame.Data()), aFrame.Size());
}

RefPtr<MacIOSurface> CreateOutputSurface(const gfx::IntSize& aOutputSize) {
  return MacIOSurface::CreateBiPlanarSurface(
      aOutputSize,
      gfx::IntSize(aOutputSize.width / 2, aOutputSize.height / 2),
      gfx::ChromaSubsampling::HALF_WIDTH_AND_HEIGHT,
      gfx::YUVColorSpace::BT709, gfx::TransferFunction::BT709,
      gfx::ColorRange::LIMITED, gfx::ColorDepth::COLOR_8,
      MacIOSurface::AllowAlpha::No);
}

Result<RefPtr<layers::Image>, MediaResult> RenderFrame(
    gfx::CrossProcessPaint::ResolvedFragmentMap&& aFragments,
    const gfx::IntSize& aOutputSize, gfx::IntRect& aContentRect) {
  // Selector capture cannot reuse the whole-window compositor snapshot without
  // exposing pixels outside the selected element. It therefore replays the
  // element paint into a new surface. This is isolation-first and CPU-bound;
  // high-resolution selector capture needs a compositor subtree export.
  RefPtr<gfx::RecordedDependentSurface> root = aFragments.Get(TabId(0));
  if (!root || root->mSize.IsEmpty()) {
    return Err(StreamError("Element paint did not produce a root surface"_ns));
  }

  const double canvasScale = std::min(
      {1.0, kLogicalWidth / aOutputSize.width,
       kLogicalHeight / aOutputSize.height});
  const gfx::IntSize logicalSize(
      std::max(2, static_cast<int32_t>(aOutputSize.width * canvasScale)),
      std::max(2, static_cast<int32_t>(aOutputSize.height * canvasScale)));
  const double fitScale = std::min(
      {1.0, static_cast<double>(logicalSize.width) / root->mSize.width,
       static_cast<double>(logicalSize.height) / root->mSize.height});
  // 1/canvasScale == max(1, w/kLogicalWidth, h/kLogicalHeight), so this is the
  // canvas's HiDPI raster density capped at kMaxRasterScale.
  const double rasterScale = std::min(kMaxRasterScale, 1.0 / canvasScale);
  const double scale = fitScale * rasterScale;
  const gfx::Size fitted(root->mSize.width * scale,
                         root->mSize.height * scale);
  const gfx::IntSize contentSize(
      std::min(aOutputSize.width,
               std::max(2, static_cast<int32_t>(std::ceil(fitted.width)))),
      std::min(aOutputSize.height,
               std::max(2, static_cast<int32_t>(std::ceil(fitted.height)))));
  aContentRect = gfx::IntRect((aOutputSize.width - contentSize.width) / 2,
                              (aOutputSize.height - contentSize.height) / 2,
                              contentSize.width, contentSize.height);

  RefPtr<MacIOSurface> staging = MacIOSurface::CreateIOSurface(
      contentSize.width, contentSize.height, MacIOSurface::AllowAlpha::No);
  if (!staging || !staging->Lock(false)) {
    return Err(StreamError("Could not lock an IOSurface video frame"_ns));
  }
  auto unlock = MakeScopeExit([&] { staging->Unlock(false); });
  RefPtr<gfx::DrawTarget> outputTarget =
      staging->GetAsDrawTargetLocked(gfx::BackendType::SKIA);
  if (!outputTarget || !outputTarget->IsValid()) {
    return Err(StreamError("Could not allocate the video frame surface"_ns));
  }

  outputTarget->FillRect(
      gfx::Rect(gfx::Point(), gfx::Size(contentSize)),
      gfx::ColorPattern(gfx::DeviceColor(1, 1, 1, 1)));
  gfx::Matrix fit = gfx::Matrix::Scaling(scale, scale);
  outputTarget->SetTransform(fit);
  gfx::InlineTranslator translator(outputTarget, nullptr);
  translator.SetReferenceDrawTargetTransform(fit);
  translator.SetDependentSurfaces(&aFragments);
  if (!translator.TranslateRecording(
          reinterpret_cast<char*>(root->mRecording.mData),
          root->mRecording.mLen)) {
    return Err(StreamError("Could not replay the element paint"_ns));
  }
  outputTarget->Flush();
  unlock.release();
  staging->Unlock(false);

  RefPtr<MacIOSurface> output = CreateOutputSurface(aOutputSize);
  if (!output) {
    return Err(StreamError("Could not allocate the output IOSurface"_ns));
  }
  if (!CompositeElementVideoSurface(
          staging, output,
          gfx::IntRect(gfx::IntPoint(), staging->GetSize()), aContentRect)) {
    return Err(StreamError("Could not composite the IOSurface video frame"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(output.get()));
}

Result<RefPtr<layers::Image>, MediaResult> RenderSurface(
    gfx::DataSourceSurface* aSource, const gfx::IntSize& aOutputSize,
    gfx::IntRect& aContentRect) {
  if (!aSource || aSource->GetSize().IsEmpty()) {
    return Err(StreamError("Viewport capture did not produce a surface"_ns));
  }
  aContentRect = gfx::IntRect(gfx::IntPoint(), aOutputSize);
  // The source arrived as CPU pixels (the headless viewport fallback). Stage it
  // in an IOSurface so Core Image can produce the NV12 surface expected by the
  // platform encoder; headed capture enters through RenderIOSurface instead.
  const gfx::IntSize sourceSize = aSource->GetSize();
  RefPtr<MacIOSurface> staging = MacIOSurface::CreateIOSurface(
      sourceSize.width, sourceSize.height, MacIOSurface::AllowAlpha::No);
  if (!staging || !staging->Lock(false)) {
    return Err(StreamError("Could not lock a viewport IOSurface"_ns));
  }
  auto unlock = MakeScopeExit([&] { staging->Unlock(false); });
  RefPtr<gfx::DrawTarget> target =
      staging->GetAsDrawTargetLocked(gfx::BackendType::SKIA);
  if (!target || !target->IsValid()) {
    return Err(StreamError("Could not allocate a viewport surface"_ns));
  }
  target->DrawSurface(aSource, gfx::Rect(gfx::Point(), gfx::Size(sourceSize)),
                      gfx::Rect(gfx::Point(), gfx::Size(sourceSize)));
  target->Flush();
  unlock.release();
  staging->Unlock(false);

  RefPtr<MacIOSurface> output = CreateOutputSurface(aOutputSize);
  if (!output ||
      !CompositeElementVideoSurface(
          staging, output,
          gfx::IntRect(gfx::IntPoint(), staging->GetSize()), aContentRect)) {
    return Err(StreamError("Could not composite the viewport IOSurface"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(output.get()));
}

Result<RefPtr<layers::Image>, MediaResult> RenderIOSurface(
    const RefPtr<MacIOSurface>& aSource, const gfx::IntRect& aSourceRect,
    const gfx::IntSize& aOutputSize, gfx::IntRect& aContentRect) {
  if (!aSource || aSourceRect.IsEmpty() ||
      !gfx::IntRect(gfx::IntPoint(), aSource->GetSize())
           .Contains(aSourceRect)) {
    return Err(
        StreamError("Viewport capture produced an invalid IOSurface"_ns));
  }
  aContentRect = gfx::IntRect(gfx::IntPoint(), aOutputSize);
  RefPtr<MacIOSurface> output = CreateOutputSurface(aOutputSize);
  if (!output ||
      !CompositeElementVideoSurface(aSource, output, aSourceRect,
                                    aContentRect, true)) {
    return Err(StreamError("Could not composite the viewport IOSurface"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(output.get()));
}

#endif  // XP_MACOSX

}  // namespace

ElementVideoStream::ElementVideoStream(const Options& aOptions)
    : mOptions(aOptions), mEncoder(MakeRefPtr<EncoderAgent>(sNextStreamId++)) {}

ElementVideoStream::~ElementVideoStream() { MOZ_ASSERT(mShutdown); }

#ifdef XP_MACOSX

/* static */
RefPtr<ElementVideoStream::CreatePromise> ElementVideoStream::Create(
    const Options& aOptions) {
  RefPtr<ElementVideoStream> stream = new ElementVideoStream(aOptions);
  const uint64_t pixels =
      static_cast<uint64_t>(aOptions.mWidth) * aOptions.mHeight;
  const H264_LEVEL level = pixels > 1920 * 1080
                               ? H264_LEVEL::H264_LEVEL_5_2
                           : pixels > 1280 * 720
                               ? H264_LEVEL::H264_LEVEL_4_2
                               : H264_LEVEL::H264_LEVEL_3_2;
  EncoderConfig::CodecSpecific specific{void_t{}};
  if (!aOptions.mH265) {
    specific.emplace<H264Specific>(H264_PROFILE_BASE, level,
                                   H264BitStreamFormat::ANNEXB);
  }
  EncoderConfig config(
      aOptions.mH265 ? CodecType::H265 : CodecType::H264,
      gfx::IntSize(aOptions.mWidth, aOptions.mHeight), Usage::Realtime,
      EncoderConfig::SampleFormat(ImageBitmapFormat::YUV420SP_NV12),
      aOptions.mFramesPerSecond, aOptions.mFramesPerSecond,
      aOptions.mBitsPerSecond, 0, 0, BitrateMode::Variable,
      HardwarePreference::None, ScalabilityMode::None,
      specific);
  return stream->mEncoder->Configure(config)->Then(
      GetCurrentSerialEventTarget(), __func__,
      [stream](bool) {
        if (!stream->mEncoder->SupportsPipelinedEncode()) {
          stream->Shutdown();
          return CreatePromise::CreateAndReject(
              StreamError(
                  "The selected platform encoder cannot pipeline encodes"_ns),
              __func__);
        }
        return CreatePromise::CreateAndResolve(stream, __func__);
      },
      [stream](const MediaResult& aError) {
        stream->Shutdown();
        return CreatePromise::CreateAndReject(aError, __func__);
      });
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::Encode(
    gfx::CrossProcessPaint::ResolvedFragmentMap&& aFragments,
    uint64_t aFrameIndex) {
  Frame frame;
  frame.mFragments = MakeUnique<gfx::CrossProcessPaint::ResolvedFragmentMap>(
      std::move(aFragments));
  frame.mFrameIndex = aFrameIndex;
  return EncodeFrame(std::move(frame));
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeSurface(
    RefPtr<gfx::DataSourceSurface> aSurface, uint64_t aFrameIndex) {
  Frame frame;
  frame.mSurface = std::move(aSurface);
  frame.mFrameIndex = aFrameIndex;
  return EncodeFrame(std::move(frame));
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeIOSurface(
    RefPtr<MacIOSurface> aSurface, const gfx::IntRect& aSourceRect,
    uint64_t aFrameIndex) {
  Frame frame;
  frame.mIOSurface = std::move(aSurface);
  frame.mSourceRect = aSourceRect;
  frame.mFrameIndex = aFrameIndex;
  return EncodeFrame(std::move(frame));
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeFrame(
    Frame&& aFrame) {
  MOZ_ASSERT(!mShutdown);
  const gfx::IntSize size(mOptions.mWidth, mOptions.mHeight);
  gfx::IntRect contentRect;
  // Each frame renders into a fresh output IOSurface: the asynchronous
  // platform encode retains its input, so pipelined frames need distinct
  // surfaces. Introduce a bounded pool only if allocation shows in profiles.
  Result<RefPtr<layers::Image>, MediaResult> image =
      aFrame.mIOSurface
          ? RenderIOSurface(aFrame.mIOSurface, aFrame.mSourceRect, size,
                            contentRect)
      : aFrame.mSurface
          ? RenderSurface(aFrame.mSurface.get(), size, contentRect)
          : RenderFrame(std::move(*aFrame.mFragments), size, contentRect);
  if (image.isErr()) {
    return EncodePromise::CreateAndReject(image.unwrapErr(), __func__);
  }

  const int64_t duration = USECS_PER_S / mOptions.mFramesPerSecond;
  const int64_t timestamp =
      AssertedCast<int64_t>(aFrame.mFrameIndex) * duration;
  const media::TimeUnit time = media::TimeUnit::FromMicroseconds(timestamp);
  // Roughly one keyframe per second. Frame indices may have gaps (skipped
  // capture ticks), so track the next threshold instead of testing
  // divisibility, which a gap could jump over entirely.
  const bool keyframe = aFrame.mFrameIndex >= mNextKeyframeIndex;
  if (keyframe) {
    mNextKeyframeIndex = aFrame.mFrameIndex + mOptions.mFramesPerSecond;
  }
  RefPtr<VideoData> frame = VideoData::CreateFromImage(
      size, 0, time, media::TimeUnit::FromMicroseconds(duration),
      image.unwrap(), keyframe, time);
  return mEncoder->EncodePipelined(frame)->Then(
      GetCurrentSerialEventTarget(), __func__,
      [size, contentRect](MediaDataEncoder::EncodedData&& aFrames) {
        nsCString packet;
        for (const RefPtr<MediaRawData>& encoded : aFrames) {
          AppendEncodedFrame(packet, *encoded, size, contentRect);
        }
        return EncodePromise::CreateAndResolve(std::move(packet), __func__);
      },
      [](const MediaResult& aError) {
        return EncodePromise::CreateAndReject(aError, __func__);
      });
}

#else  // XP_MACOSX

// Native video streaming is macOS-only; the Playwright driver and the Juggler
// protocol layer both gate it, and these stubs give direct callers a clean
// rejection instead of a CPU pipeline that cannot meet the realtime target.

/* static */
RefPtr<ElementVideoStream::CreatePromise> ElementVideoStream::Create(
    const Options&) {
  return CreatePromise::CreateAndReject(
      StreamError("Native video streaming requires macOS"_ns), __func__);
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::Encode(
    gfx::CrossProcessPaint::ResolvedFragmentMap&&, uint64_t) {
  return EncodePromise::CreateAndReject(
      StreamError("Native video streaming requires macOS"_ns), __func__);
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeSurface(
    RefPtr<gfx::DataSourceSurface>, uint64_t) {
  return EncodePromise::CreateAndReject(
      StreamError("Native video streaming requires macOS"_ns), __func__);
}

#endif  // XP_MACOSX

void ElementVideoStream::Shutdown() {
  if (mShutdown) {
    return;
  }
  mShutdown = true;
  (void)mEncoder->Shutdown();
}

}  // namespace mozilla::dom
