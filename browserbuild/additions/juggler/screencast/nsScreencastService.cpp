/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "nsScreencastService.h"

#include "ElementVideoStream.h"
#ifdef XP_MACOSX
#  include "ElementVideoStreamMac.h"
#  include "mozilla/gfx/MacIOSurface.h"
#  include "mozilla/layers/CompositorThread.h"
#  include "mozilla/layers/NativeLayer.h"
#endif
#include "gfxContext.h"
#include "gfxPlatform.h"
#include "ScreencastEncoder.h"
#include "HeadlessWidget.h"
#include "HeadlessWindowCapturer.h"
#include "mozilla/Base64.h"
#include "mozilla/ClearOnShutdown.h"
#include "mozilla/PresShell.h"
#include "mozilla/StaticPtr.h"
#include "mozilla/layers/WebRenderLayerManager.h"
#include "nsIDocShell.h"
#include "nsIWidget.h"
#include "nsIObserverService.h"
#include "nsIRandomGenerator.h"
#include "nsISupportsPrimitives.h"
#include "nsITimer.h"
#include "nsThreadManager.h"
#include "mozilla/TimeStamp.h"
#include "modules/desktop_capture/desktop_capturer.h"
#include "modules/desktop_capture/desktop_capture_options.h"
#include "modules/desktop_capture/desktop_frame.h"
#include "modules/video_capture/video_capture.h"
#include "mozilla/widget/PlatformWidgetTypes.h"
#include "video_engine/desktop_capture_impl.h"
#include "VideoEngine.h"

extern "C" {
#include "jpeglib.h"
}
#include <libyuv.h>

#include <bit>

using namespace mozilla::widget;

namespace mozilla {

NS_IMPL_ISUPPORTS(nsScreencastService, nsIScreencastService)

namespace {

const uint32_t kMaxFramesInFlight = 1;
// Live video favors current frames over a growing latency queue. The native
// encoder can pipeline work, but capture stops at this bound until it catches
// up.
const uint32_t kMaxNativeFramesInFlight = 8;

StaticRefPtr<nsScreencastService> gScreencastService;

double ScreencastTimestampSeconds() {
  static const TimeStamp startedAt = TimeStamp::Now();
  return (TimeStamp::Now() - startedAt).ToSeconds();
}

webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx> CreateWindowCapturer(nsIWidget* widget) {
  if (gfxPlatform::IsHeadless()) {
    HeadlessWidget* headlessWidget = static_cast<HeadlessWidget*>(widget);
    return HeadlessWindowCapturer::Create(headlessWidget);
  }
  uintptr_t rawWindowId = reinterpret_cast<uintptr_t>(widget->GetNativeData(NS_NATIVE_WINDOW_WEBRTC_DEVICE_ID));
  if (!rawWindowId) {
    fprintf(stderr, "Failed to get native window id\n");
    return nullptr;
  }
  nsCString windowId;
  windowId.AppendPrintf("%" PRIuPTR, rawWindowId);
  bool captureCursor = false;
  static int moduleId = 0;
  return webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx>(webrtc::DesktopCaptureImpl::Create(++moduleId, windowId.get(), camera::CaptureDeviceType::Window, captureCursor));
}

nsresult generateUid(nsString& uid) {
  nsresult rv = NS_OK;
  nsCOMPtr<nsIRandomGenerator> rg = do_GetService("@mozilla.org/security/random-generator;1", &rv);
  NS_ENSURE_SUCCESS(rv, rv);

  uint8_t* buffer;
  const int kLen = 16;
  rv = rg->GenerateRandomBytes(kLen, &buffer);
  NS_ENSURE_SUCCESS(rv, rv);

  for (int i = 0; i < kLen; i++) {
    uid.AppendPrintf("%02x", buffer[i]);
  }
  free(buffer);
  return rv;
}
}

class nsScreencastService::Session : public webrtc::VideoSinkInterface<webrtc::VideoFrame>,
                                     public webrtc::RawFrameCallback {
  Session(
    nsIScreencastServiceClient* client,
    nsIWidget* widget,
    webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx>&& capturer,
    std::unique_ptr<ScreencastEncoder> encoder,
    int width, int height,
    int viewportWidth, int viewportHeight,
    gfx::IntMargin margin,
    uint32_t jpegQuality,
    bool nativeVideo,
    uint32_t fps,
    uint32_t bitrate,
    bool h265,
    uint32_t contentOffsetTop)
      : mClient(client)
      , mWidget(widget)
      , mCaptureModule(std::move(capturer))
      , mEncoder(std::move(encoder))
      , mJpegQuality(jpegQuality)
      , mWidth(width)
      , mHeight(height)
      , mViewportWidth(viewportWidth)
      , mViewportHeight(viewportHeight)
      , mMargin(margin)
      , mNativeVideo(nativeVideo)
      , mFPS(fps)
      , mBitrate(bitrate)
      , mH265(h265)
      , mContentOffsetTop(contentOffsetTop) {
  }
  ~Session() override = default;

 public:
  NS_INLINE_DECL_THREADSAFE_REFCOUNTING(Session)
  static RefPtr<Session> Create(
    nsIScreencastServiceClient* client,
    nsIWidget* widget,
    webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx>&& capturer,
    std::unique_ptr<ScreencastEncoder> encoder,
    int width, int height,
    int viewportWidth, int viewportHeight,
    gfx::IntMargin margin,
    uint32_t jpegQuality,
    bool nativeVideo,
    uint32_t fps,
    uint32_t bitrate,
    bool h265,
    uint32_t contentOffsetTop) {
    return do_AddRef(new Session(client, widget, std::move(capturer), std::move(encoder), width, height, viewportWidth, viewportHeight, margin, jpegQuality, nativeVideo, fps, bitrate, h265, contentOffsetTop));
  }

  webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx> ReuseCapturer(nsIWidget* widget) {
    if (mWidget == widget)
      return mCaptureModule;
    return nullptr;
  }

  nsIWidget* NativeVideoTopLevelWidget() {
    return mNativeVideo && !mNativeFailed && mWidget && !mWidget->Destroyed()
               ? mWidget->GetTopLevelWidget()
               : nullptr;
  }

  bool Start() {
    if (mNativeVideo) {
      dom::ElementVideoStream::Options options{
          static_cast<uint32_t>(mWidth), static_cast<uint32_t>(mHeight),
          mFPS, mBitrate, mH265};
      dom::ElementVideoStream::Create(options)->Then(
          GetCurrentSerialEventTarget(), __func__,
          [self = RefPtr{this}](RefPtr<dom::ElementVideoStream> aStream) {
            if (self->mStopped) {
              aStream->Shutdown();
              return;
            }
            self->mNativeStream = std::move(aStream);
#ifdef XP_MACOSX
            // Headed macOS has a Core Animation NativeLayerRoot whose pixels
            // can stay in IOSurfaces through VideoToolbox. Headless has no such
            // root, so it deliberately leaves this unset for the CPU fallback
            // in CaptureNativeFrame(). Removing the guard does not make the
            // headless path GPU-backed.
            if (!gfxPlatform::IsHeadless()) {
              self->mNativeSnapshotter =
                  dom::CreateWindowVideoSnapshotter(self->mWidget);
            }
#endif
            self->mNativeReady = true;
            // Skip missed ticks instead of emitting a burst of stale frames:
            // this is a realtime stream, not a lossless frame recorder.
            MOZ_ALWAYS_SUCCEEDS(NS_NewTimerWithFuncCallback(
                getter_AddRefs(self->mNativeTimer),
                [](nsITimer*, void* aClosure) {
                  static_cast<Session*>(aClosure)->CaptureNativeFrame();
                },
                self.get(), TimeDuration::FromSeconds(1.0 / self->mFPS),
                nsITimer::TYPE_REPEATING_PRECISE_CAN_SKIP,
                "nsScreencastService::CaptureNativeFrame"_ns));
          },
          [self = RefPtr{this}](const MediaResult& aError) {
            fprintf(stderr, "Failed to create native screencast encoder: %s\n",
                    aError.Description().get());
            // Release the per-window native slot and tell the client frames
            // will never come instead of squatting until an explicit stop.
            self->mNativeFailed = true;
            self->mClient->ScreencastStopped();
          });
      return true;
    }
    webrtc::VideoCaptureCapability capability;
    // The size is ignored in fact.
    capability.width = 1280;
    capability.height = 960;
    capability.maxFPS = ScreencastEncoder::fps;
    capability.videoType = webrtc::VideoType::kI420;
    int error = mCaptureModule->StartCaptureCounted(capability);
    if (error) {
      fprintf(stderr, "StartCapture error %d\n", error);
      return false;
    }

    if (mEncoder)
      mCaptureModule->RegisterCaptureDataCallback(this);
    else
      mCaptureModule->RegisterRawFrameCallback(this);
    return true;
  }

  void Stop() {
    if (mStopped.exchange(true)) {
      fprintf(stderr, "Screencast session has already been stopped\n");
      return;
    }
    mNativeReady = false;
    if (mNativeTimer) {
      mNativeTimer->Cancel();
      mNativeTimer = nullptr;
    }
#ifdef XP_MACOSX
    // The snapshotter is created and used on the compositor thread; destroy it
    // there too instead of releasing compositor-owned state on the main thread.
    // If the compositor thread is already gone (late shutdown), destroying on
    // the main thread is the remaining safe option.
    if (mNativeSnapshotter) {
      UniquePtr<layers::NativeLayerRootSnapshotter> snapshotter =
          std::move(mNativeSnapshotter);
      if (layers::CompositorThread()) {
        MOZ_ALWAYS_SUCCEEDS(
            layers::CompositorThread()->Dispatch(NS_NewRunnableFunction(
                __func__, [snapshotter = std::move(snapshotter)]() mutable {
                  snapshotter = nullptr;
                })));
      }
    }
#endif
    if (mCaptureModule) {
      if (mEncoder)
        mCaptureModule->DeRegisterCaptureDataCallback(this);
      else
        mCaptureModule->DeRegisterRawFrameCallback(this);
      mCaptureModule->StopCaptureCounted();
    }
    if (mNativeStream) {
      mNativeStream->Shutdown();
      mNativeStream = nullptr;
    }
    if (mEncoder) {
      mEncoder->finish([this, protect = RefPtr{this}] {
        NS_DispatchToMainThread(NS_NewRunnableFunction(
            "NotifyScreencastStopped", [this, protect = std::move(protect)]() -> void {
              mClient->ScreencastStopped();
            }));
      });
    } else {
      mClient->ScreencastStopped();
    }
  }

  void ScreencastFrameAck() {
    if (mNativeVideo)
      return;
    if (mFramesInFlight.load() == 0) {
      fprintf(stderr, "ScreencastFrameAck is called while there are no inflight frames\n");
      return;
    }
    mFramesInFlight.fetch_sub(1);
  }

  // These callbacks end up running on the VideoCapture thread.
  void OnFrame(const webrtc::VideoFrame& videoFrame) override {
    if (!mEncoder)
      return;
    mEncoder->encodeFrame(videoFrame);
  }

  // These callbacks end up running on the VideoCapture thread.
  void OnRawFrame(uint8_t* videoFrame, size_t videoFrameStride, const webrtc::VideoCaptureCapability& frameInfo) override {
    int pageWidth = frameInfo.width - mMargin.LeftRight();
    int pageHeight = frameInfo.height - mMargin.TopBottom();
    // Frame size is 1x1 when browser window is minimized.
    if (pageWidth <= 1 || pageHeight <= 1)
      return;
    // Headed Firefox brings sizes in sync slowly.
    if (mViewportWidth && pageWidth > mViewportWidth)
      pageWidth = mViewportWidth;
    if (mViewportHeight && pageHeight > mViewportHeight)
      pageHeight = mViewportHeight;

    if (mFramesInFlight.load() >= kMaxFramesInFlight)
      return;

    int screenshotWidth = pageWidth;
    int screenshotHeight = pageHeight;
    int screenshotTopMargin = mMargin.TopBottom();
    std::unique_ptr<uint8_t[]> canvas;
    uint8_t* canvasPtr = videoFrame;
    int canvasStride = videoFrameStride;

    if (mWidth < pageWidth || mHeight < pageHeight) {
      double scale = std::min(1., std::min((double)mWidth / pageWidth, (double)mHeight / pageHeight));
      int canvasWidth = frameInfo.width * scale;
      int canvasHeight = frameInfo.height * scale;
      canvasStride = canvasWidth * 4;

      screenshotWidth *= scale;
      screenshotHeight *= scale;
      screenshotTopMargin *= scale;

      canvas.reset(new uint8_t[canvasWidth * canvasHeight * 4]);
      canvasPtr = canvas.get();
      libyuv::ARGBScale(videoFrame,
                        videoFrameStride,
                        frameInfo.width,
                        frameInfo.height,
                        canvasPtr,
                        canvasStride,
                        canvasWidth,
                        canvasHeight,
                        libyuv::kFilterBilinear);
    }

    jpeg_compress_struct info;
    jpeg_error_mgr error;
    info.err = jpeg_std_error(&error);
    jpeg_create_compress(&info);

    unsigned char* bufferPtr = nullptr;
    unsigned long bufferSize;
    jpeg_mem_dest(&info, &bufferPtr, &bufferSize);

    info.image_width = screenshotWidth;
    info.image_height = screenshotHeight;

    if constexpr (std::endian::native == std::endian::little) {
      if (frameInfo.videoType == webrtc::VideoType::kARGB)
        info.in_color_space = JCS_EXT_BGRA;
      if (frameInfo.videoType == webrtc::VideoType::kBGRA)
        info.in_color_space = JCS_EXT_ARGB;
    } else {
      if (frameInfo.videoType == webrtc::VideoType::kARGB)
        info.in_color_space = JCS_EXT_ARGB;
      if (frameInfo.videoType == webrtc::VideoType::kBGRA)
        info.in_color_space = JCS_EXT_BGRA;
    }

    // # of color components in input image
    info.input_components = 4;

    jpeg_set_defaults(&info);
    jpeg_set_quality(&info, mJpegQuality, true);

    jpeg_start_compress(&info, true);
    while (info.next_scanline < info.image_height) {
      JSAMPROW row = canvasPtr + (screenshotTopMargin + info.next_scanline) * canvasStride;
      if (jpeg_write_scanlines(&info, &row, 1) != 1) {
        fprintf(stderr, "JPEG library failed to encode line\n");
        break;
      }
    }

    jpeg_finish_compress(&info);
    jpeg_destroy_compress(&info);

    nsCString base64;
    nsresult rv = mozilla::Base64Encode(reinterpret_cast<char *>(bufferPtr), bufferSize, base64);
    free(bufferPtr);
    if (NS_WARN_IF(NS_FAILED(rv))) {
      return;
    }

    mFramesInFlight.fetch_add(1);
    double timestamp = ScreencastTimestampSeconds();
    NS_DispatchToMainThread(NS_NewRunnableFunction(
        "NotifyScreencastFrame", [this, protect = RefPtr{this}, base64, pageWidth, pageHeight, timestamp]() -> void {
          if (mStopped)
            return;
          NS_ConvertUTF8toUTF16 utf16(base64);
          mClient->ScreencastFrame(utf16, pageWidth, pageHeight, timestamp);
        }));
  }

  // Wall-clock frame indices: PTS is frameIndex / fps, so deriving the index
  // from elapsed time makes skipped ticks produce real PTS gaps instead of
  // silently compressing the recorded timeline relative to wall clock.
  uint64_t NextFrameIndex() {
    MOZ_ASSERT(NS_IsMainThread());
    const TimeStamp now = TimeStamp::Now();
    if (mCaptureStart.IsNull())
      mCaptureStart = now;
    const double elapsedFrames = (now - mCaptureStart).ToSeconds() * mFPS;
    const uint64_t frameIndex = std::max(
        static_cast<uint64_t>(elapsedFrames + 0.5), mNextFrameIndexFloor);
    mNextFrameIndexFloor = frameIndex + 1;
    return frameIndex;
  }

  void CaptureNativeFrame() {
    MOZ_ASSERT(NS_IsMainThread());
    if (mStopped || !mNativeReady || !mNativeStream || mWidget->Destroyed() ||
        mFramesInFlight.load() >= kMaxNativeFramesInFlight)
      return;

    const gfx::IntSize widgetSize = mWidget->GetClientSize().ToUnknownSize();
    const uint32_t top = std::min(
        mContentOffsetTop, static_cast<uint32_t>(widgetSize.height));
    const int pageWidth = mViewportWidth
                              ? std::min(mViewportWidth, widgetSize.width)
                              : widgetSize.width;
    const int availableHeight = widgetSize.height - top;
    const int pageHeight = mViewportHeight
                               ? std::min(mViewportHeight, availableHeight)
                               : availableHeight;
    if (widgetSize.IsEmpty() || pageWidth <= 1 || pageHeight <= 1)
      return;

#ifdef XP_MACOSX
    // Fast headed-macOS path: snapshot the compositor directly to an IOSurface
    // and keep the frame GPU-shareable through color conversion and encoding.
    if (mNativeSnapshotter) {
      gfx::IntSize captureSize = widgetSize;
      gfx::IntPoint sourceOrigin(0, top);
      if (nsIWidget* topLevel = mWidget->GetTopLevelWidget()) {
        captureSize = topLevel->GetClientSize().ToUnknownSize();
        if (topLevel != mWidget) {
          const gfx::IntRect rootBounds =
              topLevel->GetScreenBounds().ToUnknownRect();
          const gfx::IntRect widgetBounds =
              mWidget->GetScreenBounds().ToUnknownRect();
          sourceOrigin = widgetBounds.TopLeft() - rootBounds.TopLeft();
        }
      }
      if (!layers::CompositorThread())
        return;
      mFramesInFlight.fetch_add(1);
      const uint64_t frameIndex = NextFrameIndex();
      const double timestamp = ScreencastTimestampSeconds();
      const gfx::IntRect sourceRect(sourceOrigin,
                                    gfx::IntSize(pageWidth, pageHeight));
      // Raw pointer is safe: this runnable and Stop()'s destroy runnable are
      // both dispatched from the main thread to the compositor thread, so FIFO
      // ordering guarantees every capture runs before destruction.
      layers::NativeLayerRootSnapshotter* snapshotter =
          mNativeSnapshotter.get();
      RefPtr<Session> self{this};
      MOZ_ALWAYS_SUCCEEDS(
          layers::CompositorThread()->Dispatch(NS_NewRunnableFunction(
              __func__, [self, snapshotter, captureSize, sourceRect, pageWidth,
                         pageHeight, frameIndex, timestamp] {
                RefPtr<MacIOSurface> surface =
                    snapshotter->GetWindowIOSurface(captureSize);
                NS_DispatchToMainThread(NS_NewRunnableFunction(
                    "EncodeNativeViewportFrame",
                    [self, surface = std::move(surface), sourceRect, pageWidth,
                     pageHeight, frameIndex, timestamp] {
                      if (self->mStopped || !self->mNativeStream || !surface) {
                        self->mFramesInFlight.fetch_sub(1);
                        return;
                      }
                      self->SubmitNativeFrame(
                          self->mNativeStream->EncodeIOSurface(
                              std::move(surface), sourceRect, frameIndex),
                          pageWidth, pageHeight, timestamp);
                    }));
              })));
      return;
    }
#endif

    // This compatibility path renders WebRender into CPU BGRA and copies the
    // cropped rows. A 4K frame is roughly 32 MiB, so this preserves headless and
    // cross-platform correctness but is not the 4K60 design. Replace it with a
    // compositor-owned surface export when headless 4K60 is required.
    WindowRenderer* renderer = mWidget->GetWindowRenderer();
    layers::WebRenderLayerManager* layerManager =
        renderer ? renderer->AsWebRender() : nullptr;
    if (!layerManager)
      return;
    RefPtr<gfx::DrawTarget> target =
        gfxPlatform::GetPlatform()->CreateOffscreenContentDrawTarget(
            widgetSize, gfx::SurfaceFormat::B8G8R8A8);
    UniquePtr<gfxContext> context =
        target ? gfxContext::CreateOrNull(target) : nullptr;
    if (!context ||
        !layerManager->BeginTransactionWithTarget(context.get(), nsCString()) ||
        !layerManager->EndEmptyTransaction())
      return;

    RefPtr<gfx::SourceSurface> snapshot = target->Snapshot();
    RefPtr<gfx::DataSourceSurface> source =
        snapshot ? snapshot->GetDataSurface() : nullptr;
    if (!source)
      return;

    RefPtr<gfx::DataSourceSurface> surface =
        gfx::Factory::CreateDataSourceSurface(
            gfx::IntSize(pageWidth, pageHeight),
            gfx::SurfaceFormat::B8G8R8X8);
    if (!surface)
      return;
    {
      gfx::DataSourceSurface::ScopedMap sourceMap(
          source, gfx::DataSourceSurface::MapType::READ);
      gfx::DataSourceSurface::ScopedMap destinationMap(
          surface, gfx::DataSourceSurface::MapType::WRITE);
      if (!sourceMap.IsMapped() || !destinationMap.IsMapped())
        return;
      for (int y = 0; y < pageHeight; ++y) {
        memcpy(destinationMap.GetData() + y * destinationMap.GetStride(),
               sourceMap.GetData() + (top + y) * sourceMap.GetStride(),
               pageWidth * 4);
      }
    }

    mFramesInFlight.fetch_add(1);
    const uint64_t frameIndex = NextFrameIndex();
    const double timestamp = ScreencastTimestampSeconds();
    SubmitNativeFrame(
        mNativeStream->EncodeSurface(std::move(surface), frameIndex), pageWidth,
        pageHeight, timestamp);
  }

  void SubmitNativeFrame(RefPtr<dom::ElementVideoStream::EncodePromise> aEncode,
                         int aPageWidth, int aPageHeight, double aTimestamp) {
    aEncode->Then(
        GetCurrentSerialEventTarget(), __func__,
        [self = RefPtr{this}, aPageWidth, aPageHeight,
         aTimestamp](nsCString&& aPacket) {
          self->mFramesInFlight.fetch_sub(1);
          if (self->mStopped || aPacket.IsEmpty())
            return;
          // The current XPCOM/Juggler contract carries AString, so binary video
          // packets require base64 and UTF-16 conversion. Change that contract
          // to a byte channel before optimizing these copies independently.
          nsCString base64;
          if (NS_FAILED(Base64Encode(aPacket, base64)))
            return;
          NS_ConvertUTF8toUTF16 utf16(base64);
          self->mClient->ScreencastFrame(utf16, aPageWidth, aPageHeight,
                                         aTimestamp);
        },
        [self = RefPtr{this}](const MediaResult&) {
          self->mFramesInFlight.fetch_sub(1);
        });
  }

 private:
  RefPtr<nsIScreencastServiceClient> mClient;
  // Strong reference: the native timer dereferences the widget on every tick,
  // and nothing else guarantees the widget outlives an unstopped session.
  nsCOMPtr<nsIWidget> mWidget;
  webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx> mCaptureModule;
  std::unique_ptr<ScreencastEncoder> mEncoder;
  uint32_t mJpegQuality;
  std::atomic<bool> mStopped = false;
  std::atomic<uint32_t> mFramesInFlight = 0;
  int mWidth;
  int mHeight;
  int mViewportWidth;
  int mViewportHeight;
  gfx::IntMargin mMargin;
  bool mNativeVideo;
  uint32_t mFPS;
  uint32_t mBitrate;
  bool mH265;
  uint32_t mContentOffsetTop;
  std::atomic<bool> mNativeReady = false;
  std::atomic<bool> mNativeFailed = false;
  TimeStamp mCaptureStart;
  uint64_t mNextFrameIndexFloor = 0;
  nsCOMPtr<nsITimer> mNativeTimer;
  RefPtr<dom::ElementVideoStream> mNativeStream;
#ifdef XP_MACOSX
  UniquePtr<layers::NativeLayerRootSnapshotter> mNativeSnapshotter;
#endif
};


// static
already_AddRefed<nsIScreencastService> nsScreencastService::GetSingleton() {
  if (gScreencastService) {
    return do_AddRef(gScreencastService);
  }

  gScreencastService = new nsScreencastService();
  // ClearOnShutdown(&gScreencastService);
  return do_AddRef(gScreencastService);
}

nsScreencastService::nsScreencastService() = default;

nsScreencastService::~nsScreencastService() {
}

static nsIWidget* WidgetForDocShell(nsIDocShell* aDocShell) {
  PresShell* presShell = aDocShell->GetPresShell();
  return presShell ? presShell->GetNearestWidget() : nullptr;
}

nsresult nsScreencastService::StartVideoRecording(nsIScreencastServiceClient* aClient, nsIDocShell* aDocShell, bool isVideo, const nsACString& aVideoFileName, uint32_t width, uint32_t height, uint32_t quality, uint32_t viewportWidth, uint32_t viewportHeight, uint32_t offsetTop, nsAString& sessionId) {
  MOZ_RELEASE_ASSERT(NS_IsMainThread(), "Screencast service must be started on the Main thread.");

  nsIWidget* widget = WidgetForDocShell(aDocShell);
  if (!widget)
    return NS_ERROR_UNEXPECTED;

  webrtc::scoped_refptr<webrtc::VideoCaptureModuleEx> capturer = nullptr;
  for (auto& it : mIdToSession) {
    capturer = it.second->ReuseCapturer(widget);
    if (capturer)
      break;
  }
  if (!capturer)
    capturer = CreateWindowCapturer(widget);
  if (!capturer)
    return NS_ERROR_FAILURE;

  gfx::IntMargin margin;
  // Screen bounds is the widget location on screen.
  auto screenBounds = widget->GetScreenBounds().ToUnknownRect();
  // Client bounds is the content location, in terms of parent widget.
  // To use it, we need to translate it to screen coordinates first.
  auto clientBounds = widget->GetClientBounds().ToUnknownRect();
  for (auto parent = widget->GetParent(); parent != nullptr; parent = parent->GetParent()) {
    auto pb = parent->GetClientBounds().ToUnknownRect();
    clientBounds.MoveBy(pb.X(), pb.Y());
  }
  // Crop the image to exclude frame (if any).
  margin = screenBounds - clientBounds;
  // Crop the image to exclude controls.
  margin.top += offsetTop;

  nsCString error;
  std::unique_ptr<ScreencastEncoder> encoder;
  if (isVideo) {
    encoder = ScreencastEncoder::create(error, PromiseFlatCString(aVideoFileName), width, height, margin);
    if (!encoder) {
      fprintf(stderr, "Failed to create ScreencastEncoder: %s\n", error.get());
      return NS_ERROR_FAILURE;
    }
  }

  nsString uid;
  nsresult rv = generateUid(uid);
  NS_ENSURE_SUCCESS(rv, rv);
  sessionId = uid;

  auto session = Session::Create(aClient, widget, std::move(capturer), std::move(encoder), width, height, viewportWidth, viewportHeight, margin, isVideo ? 0 : quality, false, 0, 0, false, offsetTop);
  if (!session->Start())
    return NS_ERROR_FAILURE;
  mIdToSession.emplace(sessionId, std::move(session));
  return NS_OK;
}

nsresult nsScreencastService::StartNativeVideoStream(nsIScreencastServiceClient* aClient, nsIDocShell* aDocShell, uint32_t width, uint32_t height, uint32_t viewportWidth, uint32_t viewportHeight, uint32_t offsetTop, uint32_t fps, uint32_t bitrate, const nsACString& codec, nsAString& sessionId) {
  MOZ_RELEASE_ASSERT(NS_IsMainThread(), "Screencast service must be started on the Main thread.");

  nsIWidget* widget = WidgetForDocShell(aDocShell);
  if (!widget)
    return NS_ERROR_UNEXPECTED;

  if (nsIWidget* topLevel = widget->GetTopLevelWidget()) {
    // A NativeLayerRoot supports exactly one snapshotter (CreateSnapshotter
    // refuses otherwise), so a second native session on the same OS window
    // must be rejected instead of silently degrading to the CPU path.
    for (auto& it : mIdToSession) {
      if (it.second->NativeVideoTopLevelWidget() == topLevel) {
        fprintf(stderr,
                "A native screencast is already running for this window\n");
        return NS_ERROR_NOT_AVAILABLE;
      }
    }
  }

  nsString uid;
  nsresult rv = generateUid(uid);
  NS_ENSURE_SUCCESS(rv, rv);
  sessionId = uid;

  auto session = Session::Create(aClient, widget, nullptr, nullptr, width, height, viewportWidth, viewportHeight, gfx::IntMargin(), 0, true, fps, bitrate, codec.EqualsLiteral("h265"), offsetTop);
  if (!session->Start())
    return NS_ERROR_FAILURE;
  mIdToSession.emplace(sessionId, std::move(session));
  return NS_OK;
}

nsresult nsScreencastService::StopVideoRecording(const nsAString& aSessionId) {
  nsString sessionId(aSessionId);
  auto it = mIdToSession.find(sessionId);
  if (it == mIdToSession.end())
    return NS_ERROR_INVALID_ARG;
  it->second->Stop();
  mIdToSession.erase(it);
  return NS_OK;
}

nsresult nsScreencastService::ScreencastFrameAck(const nsAString& aSessionId) {
  nsString sessionId(aSessionId);
  auto it = mIdToSession.find(sessionId);
  if (it == mIdToSession.end())
    return NS_ERROR_INVALID_ARG;
  it->second->ScreencastFrameAck();
  return NS_OK;
}

}  // namespace mozilla
