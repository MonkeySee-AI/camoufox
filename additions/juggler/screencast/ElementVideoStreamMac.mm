/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#import <CoreImage/CoreImage.h>

#include "ElementVideoStreamMac.h"
#include "mozilla/gfx/MacIOSurface.h"

namespace mozilla::dom {

bool ScaleElementVideoSurface(const RefPtr<MacIOSurface>& aSource,
                              const RefPtr<MacIOSurface>& aDestination) {
  @autoreleasepool {
    static CIContext* const context =
        [[CIContext alloc] initWithOptions:@{kCIContextUseSoftwareRenderer : @NO}];
    const gfx::IntSize sourceSize = aSource->GetSize();
    const gfx::IntSize destinationSize = aDestination->GetSize();
    CIImage* image =
        [CIImage imageWithIOSurface:aSource->GetIOSurfaceRef().get()];
    image = [image imageByApplyingTransform:CGAffineTransformMakeScale(
        static_cast<double>(destinationSize.width) / sourceSize.width,
        static_cast<double>(destinationSize.height) / sourceSize.height)];
    CIRenderDestination* destination =
        [[[CIRenderDestination alloc]
            initWithIOSurface:(IOSurface*)aDestination->GetIOSurfaceRef().get()]
            autorelease];
    NSError* error = nil;
    return [context startTaskToRender:image
                        toDestination:destination
                                error:&error] != nil;
  }
}

}  // namespace mozilla::dom
