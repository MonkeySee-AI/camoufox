/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ElementVideoStream.h"

#include <algorithm>
#include <cmath>

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
#  include "ElementVideoStreamMac.h"
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

Result<RefPtr<layers::Image>, MediaResult> RenderFrame(
    gfx::CrossProcessPaint::ResolvedFragmentMap&& aFragments,
    const gfx::IntSize& aOutputSize, gfx::IntRect& aContentRect
#ifdef XP_MACOSX
    ,
    RefPtr<MacIOSurface>& aIOSurface
#endif
) {
  RefPtr<gfx::RecordedDependentSurface> root = aFragments.Get(TabId(0));
  if (!root || root->mSize.IsEmpty()) {
    return Err(StreamError("Element paint did not produce a root surface"_ns));
  }

  const double canvasScale = std::min(
      {1.0, 1280.0 / aOutputSize.width, 720.0 / aOutputSize.height});
  const gfx::IntSize logicalSize(
      std::max(2, static_cast<int32_t>(aOutputSize.width * canvasScale)),
      std::max(2, static_cast<int32_t>(aOutputSize.height * canvasScale)));
  const double fitScale = std::min(
      {1.0, static_cast<double>(logicalSize.width) / root->mSize.width,
       static_cast<double>(logicalSize.height) / root->mSize.height});
  const double scale = fitScale * std::min(2.0, 1.0 / canvasScale);
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

  RefPtr<gfx::DrawTarget> outputTarget;
#ifdef XP_MACOSX
  const gfx::IntSize renderSize = contentSize;
  RefPtr<MacIOSurface> staging = MacIOSurface::CreateIOSurface(
      renderSize.width, renderSize.height, false);
  if (!staging || !staging->Lock(false)) {
    return Err(StreamError("Could not lock an IOSurface video frame"_ns));
  }
  auto unlock = MakeScopeExit([&] { staging->Unlock(false); });
  outputTarget =
      staging->GetAsDrawTargetLocked(gfx::BackendType::SKIA);
#else
  const gfx::IntSize& renderSize = aOutputSize;
  outputTarget =
      gfxPlatform::GetPlatform()->CreateOffscreenContentDrawTarget(
          aOutputSize, gfx::SurfaceFormat::B8G8R8X8);
#endif
  if (!outputTarget || !outputTarget->IsValid()) {
    return Err(StreamError("Could not allocate the video frame surface"_ns));
  }

  outputTarget->FillRect(
      gfx::Rect(gfx::Point(), gfx::Size(renderSize)),
      gfx::ColorPattern(gfx::DeviceColor(1, 1, 1, 1)));
#ifdef XP_MACOSX
  const gfx::Point offset(0, 0);
  const double paintScale = scale;
#else
  const gfx::Point offset((renderSize.width - fitted.width) / 2,
                          (renderSize.height - fitted.height) / 2);
  const double paintScale = scale;
#endif
  gfx::Matrix fit = gfx::Matrix::Scaling(paintScale, paintScale).PostTranslate(
      offset.x, offset.y);
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

#ifdef XP_MACOSX
  unlock.release();
  staging->Unlock(false);
  if (!aIOSurface || aIOSurface->GetSize() != aOutputSize) {
    aIOSurface = MacIOSurface::CreateBiPlanarSurface(
        aOutputSize,
        gfx::IntSize(aOutputSize.width / 2, aOutputSize.height / 2),
        gfx::ChromaSubsampling::HALF_WIDTH_AND_HEIGHT,
        gfx::YUVColorSpace::BT709, gfx::TransferFunction::BT709,
        gfx::ColorRange::LIMITED, gfx::ColorDepth::COLOR_8);
  }
  if (!aIOSurface) {
    return Err(StreamError("Could not allocate the output IOSurface"_ns));
  }
  if (!CompositeElementVideoSurface(
          staging, aIOSurface,
          gfx::IntRect(gfx::IntPoint(), staging->GetSize()), aContentRect)) {
    return Err(StreamError("Could not composite the IOSurface video frame"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(aIOSurface.get()));
#else
  RefPtr<gfx::SourceSurface> output = outputTarget->Snapshot();
  if (!output) {
    return Err(StreamError("Could not snapshot the video frame"_ns));
  }
  return RefPtr<layers::Image>(new layers::SourceSurfaceImage(output));
#endif
}

Result<RefPtr<layers::Image>, MediaResult> RenderSurface(
    gfx::DataSourceSurface* aSource, const gfx::IntSize& aOutputSize,
    gfx::IntRect& aContentRect
#ifdef XP_MACOSX
    ,
    RefPtr<MacIOSurface>& aIOSurface
#endif
) {
  if (!aSource || aSource->GetSize().IsEmpty()) {
    return Err(StreamError("Viewport capture did not produce a surface"_ns));
  }
  aContentRect = gfx::IntRect(gfx::IntPoint(), aOutputSize);
#ifdef XP_MACOSX
  const gfx::IntSize sourceSize = aSource->GetSize();
  RefPtr<MacIOSurface> staging = MacIOSurface::CreateIOSurface(
      sourceSize.width, sourceSize.height, false);
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
  if (!aIOSurface || aIOSurface->GetSize() != aOutputSize) {
    aIOSurface = MacIOSurface::CreateBiPlanarSurface(
        aOutputSize,
        gfx::IntSize(aOutputSize.width / 2, aOutputSize.height / 2),
        gfx::ChromaSubsampling::HALF_WIDTH_AND_HEIGHT,
        gfx::YUVColorSpace::BT709, gfx::TransferFunction::BT709,
        gfx::ColorRange::LIMITED, gfx::ColorDepth::COLOR_8);
  }
  if (!aIOSurface ||
      !CompositeElementVideoSurface(
          staging, aIOSurface,
          gfx::IntRect(gfx::IntPoint(), staging->GetSize()), aContentRect)) {
    return Err(StreamError("Could not composite the viewport IOSurface"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(aIOSurface.get()));
#else
  RefPtr<gfx::DrawTarget> target =
      gfxPlatform::GetPlatform()->CreateOffscreenContentDrawTarget(
          aOutputSize, gfx::SurfaceFormat::B8G8R8X8);
  if (!target || !target->IsValid()) {
    return Err(StreamError("Could not allocate a viewport video frame"_ns));
  }
  target->DrawSurface(aSource,
                      gfx::Rect(gfx::Point(), gfx::Size(aOutputSize)),
                      gfx::Rect(gfx::Point(), gfx::Size(aSource->GetSize())));
  RefPtr<gfx::SourceSurface> output = target->Snapshot();
  if (!output) {
    return Err(StreamError("Could not snapshot the viewport video frame"_ns));
  }
  return RefPtr<layers::Image>(new layers::SourceSurfaceImage(output));
#endif
}

#ifdef XP_MACOSX
Result<RefPtr<layers::Image>, MediaResult> RenderIOSurface(
    const RefPtr<MacIOSurface>& aSource, const gfx::IntRect& aSourceRect,
    const gfx::IntSize& aOutputSize, gfx::IntRect& aContentRect,
    RefPtr<MacIOSurface>& aIOSurface) {
  if (!aSource || aSourceRect.IsEmpty() ||
      !gfx::IntRect(gfx::IntPoint(), aSource->GetSize())
           .Contains(aSourceRect)) {
    return Err(
        StreamError("Viewport capture produced an invalid IOSurface"_ns));
  }
  aContentRect = gfx::IntRect(gfx::IntPoint(), aOutputSize);
  if (!aIOSurface || aIOSurface->GetSize() != aOutputSize) {
    aIOSurface = MacIOSurface::CreateBiPlanarSurface(
        aOutputSize,
        gfx::IntSize(aOutputSize.width / 2, aOutputSize.height / 2),
        gfx::ChromaSubsampling::HALF_WIDTH_AND_HEIGHT,
        gfx::YUVColorSpace::BT709, gfx::TransferFunction::BT709,
        gfx::ColorRange::LIMITED, gfx::ColorDepth::COLOR_8);
  }
  if (!aIOSurface ||
      !CompositeElementVideoSurface(aSource, aIOSurface, aSourceRect,
                                    aContentRect, true)) {
    return Err(StreamError("Could not composite the viewport IOSurface"_ns));
  }
  return RefPtr<layers::Image>(new layers::MacIOSurfaceImage(aIOSurface.get()));
}
#endif

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
  if (!aOptions.mH265) {
    specific.emplace<H264Specific>(H264_PROFILE_BASE, level,
                                   H264BitStreamFormat::ANNEXB);
  }
#ifdef XP_MACOSX
  const ImageBitmapFormat format = ImageBitmapFormat::YUV420SP_NV12;
#else
  const ImageBitmapFormat format = ImageBitmapFormat::BGRA32;
#endif
  EncoderConfig config(
      aOptions.mH265 ? CodecType::H265 : CodecType::H264,
      gfx::IntSize(aOptions.mWidth, aOptions.mHeight), Usage::Realtime,
      EncoderConfig::SampleFormat(format),
      aOptions.mFramesPerSecond, aOptions.mFramesPerSecond,
      aOptions.mBitsPerSecond, 0, 0, BitrateMode::Variable,
      HardwarePreference::None, ScalabilityMode::None,
      specific);
  return stream->mEncoder->Configure(config)->Then(
      GetCurrentSerialEventTarget(), __func__,
      [stream](bool) {
        stream->mPipelined = stream->mEncoder->SupportsPipelinedEncode();
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
  RefPtr<EncodePromise::Private> promise =
      MakeRefPtr<EncodePromise::Private>(__func__);
  PendingEncode encode;
  encode.mFragments = MakeUnique<gfx::CrossProcessPaint::ResolvedFragmentMap>(
      std::move(aFragments));
  encode.mFrameIndex = aFrameIndex;
  encode.mPromise = promise;
  if (mPipelined) {
    EncodeNow(std::move(encode));
    return promise;
  }
  if (mEncoding) {
    if (mPendingEncode) {
      mPendingEncode->mPromise->Resolve(nsCString(), __func__);
    }
    mPendingEncode = MakeUnique<PendingEncode>(std::move(encode));
  } else {
    EncodeNow(std::move(encode));
  }
  return promise;
}

RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeSurface(
    RefPtr<gfx::DataSourceSurface> aSurface, uint64_t aFrameIndex) {
  MOZ_ASSERT(!mShutdown);
  RefPtr<EncodePromise::Private> promise =
      MakeRefPtr<EncodePromise::Private>(__func__);
  PendingEncode encode;
  encode.mSurface = std::move(aSurface);
  encode.mFrameIndex = aFrameIndex;
  encode.mPromise = promise;
  if (mPipelined) {
    EncodeNow(std::move(encode));
    return promise;
  }
  if (mEncoding) {
    if (mPendingEncode) {
      mPendingEncode->mPromise->Resolve(nsCString(), __func__);
    }
    mPendingEncode = MakeUnique<PendingEncode>(std::move(encode));
  } else {
    EncodeNow(std::move(encode));
  }
  return promise;
}

#ifdef XP_MACOSX
RefPtr<ElementVideoStream::EncodePromise> ElementVideoStream::EncodeIOSurface(
    RefPtr<MacIOSurface> aSurface, const gfx::IntRect& aSourceRect,
    uint64_t aFrameIndex) {
  MOZ_ASSERT(!mShutdown);
  RefPtr<EncodePromise::Private> promise =
      MakeRefPtr<EncodePromise::Private>(__func__);
  PendingEncode encode;
  encode.mIOSurface = std::move(aSurface);
  encode.mSourceRect = aSourceRect;
  encode.mFrameIndex = aFrameIndex;
  encode.mPromise = promise;
  if (mPipelined) {
    EncodeNow(std::move(encode));
    return promise;
  }
  if (mEncoding) {
    if (mPendingEncode) {
      mPendingEncode->mPromise->Resolve(nsCString(), __func__);
    }
    mPendingEncode = MakeUnique<PendingEncode>(std::move(encode));
  } else {
    EncodeNow(std::move(encode));
  }
  return promise;
}
#endif

void ElementVideoStream::EncodeNow(PendingEncode&& aEncode) {
  MOZ_ASSERT(mPipelined || !mEncoding);
  MOZ_ASSERT(!mShutdown);
  mEncoding = !mPipelined;
  const gfx::IntSize size(mOptions.mWidth, mOptions.mHeight);
  gfx::IntRect contentRect;
#ifdef XP_MACOSX
  RefPtr<MacIOSurface> frameSurface;
  RefPtr<MacIOSurface>& surface = mPipelined ? frameSurface : mIOSurface;
#endif
  Result<RefPtr<layers::Image>, MediaResult> image =
#ifdef XP_MACOSX
      aEncode.mIOSurface
          ? RenderIOSurface(aEncode.mIOSurface, aEncode.mSourceRect, size,
                            contentRect, surface)
      :
#endif
      aEncode.mSurface
      ? RenderSurface(aEncode.mSurface.get(), size, contentRect
#ifdef XP_MACOSX
                      , surface
#endif
        )
      : RenderFrame(std::move(*aEncode.mFragments), size, contentRect
#ifdef XP_MACOSX
                    , surface
#endif
        );
  if (image.isErr()) {
    aEncode.mPromise->Reject(image.unwrapErr(), __func__);
    if (!mPipelined) {
      EncodeDone();
    }
    return;
  }

  const int64_t duration = USECS_PER_S / mOptions.mFramesPerSecond;
  const int64_t timestamp =
      AssertedCast<int64_t>(aEncode.mFrameIndex) * duration;
  const media::TimeUnit time = media::TimeUnit::FromMicroseconds(timestamp);
  RefPtr<VideoData> frame = VideoData::CreateFromImage(
      size, 0, time, media::TimeUnit::FromMicroseconds(duration),
      image.unwrap(),
      aEncode.mFrameIndex % mOptions.mFramesPerSecond == 0, time);
  RefPtr<MediaDataEncoder::EncodePromise> encode = mPipelined
      ? mEncoder->EncodePipelined(frame)
      : mEncoder->Encode(nsTArray<RefPtr<MediaData>>{frame});
  encode->Then(
      GetCurrentSerialEventTarget(), __func__,
      [self = RefPtr{this}, size, contentRect, promise = aEncode.mPromise](
          MediaDataEncoder::EncodedData&& aFrames) {
        nsCString packet;
        for (const RefPtr<MediaRawData>& encoded : aFrames) {
          AppendEncodedFrame(packet, *encoded, size, contentRect);
        }
        promise->Resolve(std::move(packet), __func__);
        if (!self->mPipelined) {
          self->EncodeDone();
        }
      },
      [self = RefPtr{this}, promise = aEncode.mPromise](
          const MediaResult& aError) {
        promise->Reject(aError, __func__);
        if (!self->mPipelined) {
          self->EncodeDone();
        }
      });
}

void ElementVideoStream::EncodeDone() {
  MOZ_ASSERT(mEncoding);
  mEncoding = false;
  if (!mShutdown && mPendingEncode) {
    UniquePtr<PendingEncode> next = std::move(mPendingEncode);
    EncodeNow(std::move(*next));
  }
}

void ElementVideoStream::Shutdown() {
  if (mShutdown) {
    return;
  }
  mShutdown = true;
  if (mPendingEncode) {
    mPendingEncode->mPromise->Reject(
        StreamError("Element video stream stopped"_ns), __func__);
    mPendingEncode = nullptr;
  }
  (void)mEncoder->Shutdown();
}

}  // namespace mozilla::dom
