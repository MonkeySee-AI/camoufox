/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#import <CoreImage/CoreImage.h>

#include "ElementVideoStreamMac.h"
#include "mozilla/gfx/MacIOSurface.h"

namespace mozilla::dom {

bool CompositeElementVideoSurface(const RefPtr<MacIOSurface>& aSource,
                                  const RefPtr<MacIOSurface>& aDestination,
                                  const gfx::IntRect& aDestinationRect) {
  @autoreleasepool {
    static CIContext* const context =
        [[CIContext alloc] initWithOptions:@{kCIContextUseSoftwareRenderer : @NO}];
    const gfx::IntSize sourceSize = aSource->GetSize();
    const gfx::IntSize destinationSize = aDestination->GetSize();
    CIImage* foreground =
        [CIImage imageWithIOSurface:aSource->GetIOSurfaceRef().get()];
    foreground = [foreground imageByApplyingTransform:CGAffineTransformMake(
                                 (CGFloat)aDestinationRect.width / sourceSize.width,
                                 0, 0,
                                 (CGFloat)aDestinationRect.height / sourceSize.height,
                                 aDestinationRect.x, aDestinationRect.y)];
    CIImage* background =
        [[CIImage imageWithColor:[CIColor colorWithRed:0 green:0 blue:0 alpha:1]]
            imageByCroppingToRect:CGRectMake(0, 0, destinationSize.width,
                                             destinationSize.height)];
    CIImage* image = [foreground imageByCompositingOverImage:background];
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
