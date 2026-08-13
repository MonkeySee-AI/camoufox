/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_ElementVideoStreamMac_h
#define mozilla_dom_ElementVideoStreamMac_h

#include "mozilla/RefPtr.h"
#include "mozilla/UniquePtr.h"
#include "mozilla/gfx/Rect.h"

class MacIOSurface;
class nsIWidget;

namespace mozilla::layers {
class NativeLayerRootSnapshotter;
}

namespace mozilla::dom {

UniquePtr<layers::NativeLayerRootSnapshotter> CreateWindowVideoSnapshotter(
    nsIWidget* aWidget);
bool CompositeElementVideoSurface(const RefPtr<MacIOSurface>& aSource,
                                  const RefPtr<MacIOSurface>& aDestination,
                                  const gfx::IntRect& aSourceRect,
                                  const gfx::IntRect& aDestinationRect,
                                  bool aSourceIsFlipped = false);

}  // namespace mozilla::dom

#endif  // mozilla_dom_ElementVideoStreamMac_h
