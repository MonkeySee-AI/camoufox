/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_ElementVideoStreamMac_h
#define mozilla_dom_ElementVideoStreamMac_h

#include "mozilla/RefPtr.h"

class MacIOSurface;

namespace mozilla::dom {

bool CompositeElementVideoSurface(const RefPtr<MacIOSurface>& aSource,
                                  const RefPtr<MacIOSurface>& aDestination);

}  // namespace mozilla::dom

#endif  // mozilla_dom_ElementVideoStreamMac_h
