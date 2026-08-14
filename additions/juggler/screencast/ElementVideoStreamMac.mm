/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#import <CoreImage/CoreImage.h>

#include "ElementVideoStreamMac.h"
#include "mozilla/Monitor.h"
#include "mozilla/gfx/MacIOSurface.h"
#include "mozilla/layers/CompositorThread.h"
#include "mozilla/layers/NativeLayer.h"
#include "nsCocoaWindow.h"
#include "nsThreadUtils.h"

namespace mozilla::dom {

UniquePtr<layers::NativeLayerRootSnapshotter> CreateWindowVideoSnapshotter(
    nsIWidget* aWidget) {
  MOZ_ASSERT(NS_IsMainThread());
  nsIWidget* topLevel = aWidget->GetTopLevelWidget();
  if (!topLevel) {
    return nullptr;
  }
  RefPtr<layers::NativeLayerRoot> root =
      static_cast<nsCocoaWindow*>(topLevel)->GetNativeLayerRoot();
  if (!root || !layers::CompositorThread()) {
    return nullptr;
  }

  Monitor monitor("CreateWindowVideoSnapshotter");
  UniquePtr<layers::NativeLayerRootSnapshotter> snapshotter;
  bool done = false;
  MOZ_ALWAYS_SUCCEEDS(layers::CompositorThread()->Dispatch(
      NS_NewRunnableFunction(__func__, [&] {
        snapshotter = root->CreateSnapshotter();
        MonitorAutoLock lock(monitor);
        done = true;
        lock.Notify();
      })));
  MonitorAutoLock lock(monitor);
  while (!done) {
    lock.Wait();
  }
  return snapshotter;
}

bool CompositeElementVideoSurface(const RefPtr<MacIOSurface>& aSource,
                                  const RefPtr<MacIOSurface>& aDestination,
                                  const gfx::IntRect& aSourceRect,
                                  const gfx::IntRect& aDestinationRect,
                                  bool aSourceIsFlipped) {
  @autoreleasepool {
    static CIContext* const context = [[CIContext alloc] initWithOptions:@{
      kCIContextUseSoftwareRenderer : @NO
    }];
    const gfx::IntSize sourceSize = aSource->GetSize();
    const gfx::IntSize destinationSize = aDestination->GetSize();
    CIImage* foreground =
        [CIImage imageWithIOSurface:aSource->GetIOSurfaceRef().get()];
    if (aSourceIsFlipped) {
      foreground = [foreground
          imageByApplyingTransform:CGAffineTransformMake(1, 0, 0, -1, 0,
                                                         sourceSize.height)];
    }
    const CGRect sourceRect =
        CGRectMake(aSourceRect.x, sourceSize.height - aSourceRect.YMost(),
                   aSourceRect.width, aSourceRect.height);
    foreground = [foreground imageByCroppingToRect:sourceRect];
    const CGFloat scaleX = (CGFloat)aDestinationRect.width / aSourceRect.width;
    const CGFloat scaleY =
        (CGFloat)aDestinationRect.height / aSourceRect.height;
    foreground = [foreground
        imageByApplyingTransform:CGAffineTransformMake(
                                     scaleX, 0, 0, scaleY,
                                     aDestinationRect.x -
                                         sourceRect.origin.x * scaleX,
                                     aDestinationRect.y -
                                         sourceRect.origin.y * scaleY)];
    CIImage* background = [[CIImage imageWithColor:[CIColor colorWithRed:0
                                                                   green:0
                                                                    blue:0
                                                                   alpha:1]]
        imageByCroppingToRect:CGRectMake(0, 0, destinationSize.width,
                                         destinationSize.height)];
    CIImage* image = [foreground imageByCompositingOverImage:background];
    CIRenderDestination* destination = [[[CIRenderDestination alloc]
        initWithIOSurface:(IOSurface*)aDestination->GetIOSurfaceRef().get()]
        autorelease];
    NSError* error = nil;
    CIRenderTask* task = [context startTaskToRender:image
                                      toDestination:destination
                                              error:&error];
    // Encoding must not observe a partially rendered NV12 IOSurface. This wait
    // is the current synchronization boundary; chain render completion into the
    // encoder instead if profiling shows the blocking handoff is the bottleneck.
    return task && [task waitUntilCompletedAndReturnError:&error];
  }
}

}  // namespace mozilla::dom
