/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ElementVideoStream.h"

#include <algorithm>

#include "EncoderConfig.h"
#include "MediaData.h"
#include "gfxPlatform.h"
#include "mozilla/ScopeExit.h"
#include "mozilla/dom/EncoderAgent.h"
#include "mozilla/gfx/2D.h"
#include "mozilla/gfx/InlineTranslator.h"
#include "ImageContainer.h"
#include "nsThreadUtils.h"

#ifdef XP_MACOSX
#  include "mozilla/gfx/MacIOSurface.h"
#  include "MacIOSurfaceImage.h"
#endif

namespace mozilla::dom {

namespace {

MediaResult StreamError(const nsACString& aMessage) {
  return MediaResult(NS_ERROR_DOM_MEDIA_FATAL_ERR, aMessage);
}

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
                        const gfx::IntSize& aSize) {
  aPacket.Append("RSE1", 4);
  aPacket.Append(aFrame.mKeyframe ? '\x01' : '\x00');
  AppendUint32(aPacket, AssertedCast<uint32_t>(aFrame.Size()));
  AppendUint64(aPacket,
               AssertedCast<uint64_t>(aFrame.mTime.ToMicroseconds()));
  AppendUint32(
      aPacket,
      AssertedCast<uint32_t>(aFrame.mDuration.ToMicroseconds()));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aSize.width));
  AppendUint32(aPacket, AssertedCast<uint32_t>(aSize.height));
  aPacket.Append(reinterpret_cast<const char*>(aFrame.Data()), aFrame.Size());
}

Result<RefPtr<layers::Image>, MediaResult> RenderFrame(
    gfx::CrossProcessPaint::ResolvedFragmentMap&& aFragments,
    const gfx::IntSize& aOutputSize
#ifdef XP_MACOSX
    ,
    RefPtr<MacIOSurface>& aIOSurface
#endif
) {
  RefPtr<gfx::RecordedDependentSurface> root = aFragments.Get(TabId(0));
  if (!root || root->mSize.IsEmpty()) {
    return Err(StreamError("Element paint did not produce a root surface"_ns));
  }

  RefPtr<gfx::DrawTarget> outputTarget;
#ifdef XP_MACOSX
  if (!aIOSurface) {
    aIOSurface = MacIOSurface::CreateIOSurface(
        aOutputSize.width, aOutputSize.height, false);
  }
  if (!aIOSurface || !aIOSurface->Lock(false)) {
    return Err(StreamError("Could not lock an IOSurface video frame"_ns));
  }
  auto unlock = MakeScopeExit([&] { aIOSurface->Unlock(false); });
  outputTarget =
      aIOSurface->GetAsDrawTargetLocked(gfx::BackendType::SKIA);
#else
  outputTarget =
      gfxPlatform::GetPlatform()->CreateOffscreenContentDrawTarget(
          aOutputSize, gfx::SurfaceFormat::B8G8R8X8);
#endif
  if (!outputTarget || !outputTarget->IsValid()) {
    return Err(StreamError("Could not allocate the video frame surface"_ns));
  }

  outputTarget->FillRect(
      gfx::Rect(gfx::Point(), gfx::Size(aOutputSize)),
      gfx::ColorPattern(gfx::DeviceColor(0, 0, 0, 1)));
  const double scale =
      std::min(static_cast<double>(aOutputSize.width) / root->mSize.width,
               static_cast<double>(aOutputSize.height) / root->mSize.height);
  const gfx::Size fitted(root->mSize.width * scale,
                         root->mSize.height * scale);
  gfx::Matrix fit = gfx::Matrix::Scaling(scale, scale).PostTranslate(
      (aOutputSize.width - fitted.width) / 2,
      (aOutputSize.height - fitted.height) / 2);
  gfx::InlineTranslator translator(outputTarget, nullptr);
  translator.SetReferenceDrawTargetTransform(fit);
  translator.SetDependentSurfaces(&aFragments);
  if (!translator.TranslateRecording(
          reinterpret_cast<char*>(root->mRecording.mData),
          root->mRecording.mLen)) {
    return Err(StreamError("Could not replay the element paint"_ns));
  }
  outputTarget->Flush();

#ifdef XP_MACOSX
  // ponytail: encode calls are serialized, so one IOSurface is enough until
  // the encoder supports multiple frames in flight.
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(aIOSurface.get()));
#else
  RefPtr<gfx::SourceSurface> output = outputTarget->Snapshot();
  if (!output) {
    return Err(StreamError("Could not snapshot the video frame"_ns));
  }
  return RefPtr<layers::Image>(new layers::SourceSurfaceImage(output));
#endif
}

}  // namespace

ElementVideoStream::ElementVideoStream(const Options& aOptions)
    : mOptions(aOptions), mEncoder(MakeRefPtr<EncoderAgent>(sNextId++)) {}

ElementVideoStream::~ElementVideoStream() { MOZ_ASSERT(mShutdown); }

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
  specific.emplace<H264Specific>(H264_PROFILE_BASE, level,
                                 H264BitStreamFormat::ANNEXB);
  EncoderConfig config(
      CodecType::H264,
      gfx::IntSize(aOptions.mWidth, aOptions.mHeight), Usage::Realtime,
      EncoderConfig::SampleFormat(ImageBitmapFormat::BGRA32),
      aOptions.mFramesPerSecond, aOptions.mFramesPerSecond,
      aOptions.mBitsPerSecond, 0, 0, BitrateMode::Variable,
      HardwarePreference::None, ScalabilityMode::None,
      specific);
  return stream->mEncoder->Configure(config)->Then(
      GetCurrentSerialEventTarget(), __func__,
      [stream](bool) {
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
  MOZ_ASSERT(!mShutdown);
  const gfx::IntSize size(mOptions.mWidth, mOptions.mHeight);
  auto image = RenderFrame(std::move(aFragments), size
#ifdef XP_MACOSX
                           ,
                           mIOSurface
#endif
  );
  if (image.isErr()) {
    return EncodePromise::CreateAndReject(image.unwrapErr(), __func__);
  }

  const int64_t duration = USECS_PER_S / mOptions.mFramesPerSecond;
  const int64_t timestamp = AssertedCast<int64_t>(aFrameIndex) * duration;
  const media::TimeUnit time = media::TimeUnit::FromMicroseconds(timestamp);
  RefPtr<VideoData> frame = VideoData::CreateFromImage(
      size, 0, time, media::TimeUnit::FromMicroseconds(duration),
      image.unwrap(), aFrameIndex % mOptions.mFramesPerSecond == 0, time);
  nsTArray<RefPtr<MediaData>> frames{frame};
  return mEncoder->Encode(std::move(frames))->Then(
      GetCurrentSerialEventTarget(), __func__,
      [size](MediaDataEncoder::EncodedData&& aFrames) {
        nsCString packet;
        for (const RefPtr<MediaRawData>& encoded : aFrames) {
          AppendEncodedFrame(packet, *encoded, size);
        }
        return EncodePromise::CreateAndResolve(std::move(packet), __func__);
      },
      [](const MediaResult& aError) {
        return EncodePromise::CreateAndReject(aError, __func__);
      });
}

void ElementVideoStream::Shutdown() {
  if (mShutdown) {
    return;
  }
  mShutdown = true;
  (void)mEncoder->Shutdown();
}

}  // namespace mozilla::dom
