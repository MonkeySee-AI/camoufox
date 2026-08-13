/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_ElementVideoStream_h
#define mozilla_dom_ElementVideoStream_h

#include "MediaResult.h"
#include "mozilla/MozPromise.h"
#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "mozilla/gfx/2D.h"
#include "mozilla/gfx/CrossProcessPaint.h"

class MacIOSurface;

namespace mozilla {

class EncoderAgent;

namespace dom {

class ElementVideoStream final {
 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(ElementVideoStream)

  struct Options {
    uint32_t mWidth;
    uint32_t mHeight;
    uint32_t mFramesPerSecond;
    uint32_t mBitsPerSecond;
    bool mH265;
    bool operator==(const Options& aOther) const = default;
  };

  using CreatePromise =
      MozPromise<RefPtr<ElementVideoStream>, MediaResult, true>;
  using EncodePromise = MozPromise<nsCString, MediaResult, true>;

  static RefPtr<CreatePromise> Create(const Options& aOptions);

  bool Matches(const Options& aOptions) const { return mOptions == aOptions; }
  RefPtr<EncodePromise> Encode(
      gfx::CrossProcessPaint::ResolvedFragmentMap&& aFragments,
      uint64_t aFrameIndex);
  RefPtr<EncodePromise> EncodeSurface(RefPtr<gfx::DataSourceSurface> aSurface,
                                      uint64_t aFrameIndex);
#ifdef XP_MACOSX
  RefPtr<EncodePromise> EncodeIOSurface(RefPtr<MacIOSurface> aSurface,
                                        const gfx::IntRect& aSourceRect,
                                        uint64_t aFrameIndex);
#endif
  void Shutdown();

 private:
  struct PendingEncode {
    UniquePtr<gfx::CrossProcessPaint::ResolvedFragmentMap> mFragments;
    RefPtr<gfx::DataSourceSurface> mSurface;
#ifdef XP_MACOSX
    RefPtr<MacIOSurface> mIOSurface;
    gfx::IntRect mSourceRect;
#endif
    uint64_t mFrameIndex;
    RefPtr<EncodePromise::Private> mPromise;
  };

  explicit ElementVideoStream(const Options& aOptions);
  ~ElementVideoStream();
  void EncodeNow(PendingEncode&& aEncode);
  void EncodeDone();

  Options mOptions;
  RefPtr<EncoderAgent> mEncoder;
  UniquePtr<PendingEncode> mPendingEncode;
  bool mPipelined = false;
  bool mEncoding = false;
  bool mShutdown = false;

#ifdef XP_MACOSX
  RefPtr<MacIOSurface> mIOSurface;
#endif
};

}  // namespace dom
}  // namespace mozilla

#endif  // mozilla_dom_ElementVideoStream_h
