/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const {Helper, EventWatcher} = ChromeUtils.importESModule('chrome://juggler/content/Helper.js');
const {NetUtil} = ChromeUtils.importESModule('resource://gre/modules/NetUtil.sys.mjs');
const {NetworkObserver, PageNetwork} = ChromeUtils.importESModule('chrome://juggler/content/NetworkObserver.js');
const {PageTarget} = ChromeUtils.importESModule('chrome://juggler/content/TargetRegistry.js');
const {AppConstants} = ChromeUtils.importESModule('resource://gre/modules/AppConstants.sys.mjs');
const {clearTimeout, setTimeout} = ChromeUtils.importESModule('resource://gre/modules/Timer.sys.mjs');

const Cc = Components.classes;
const Ci = Components.interfaces;
const Cu = Components.utils;
const XUL_NS = 'http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul';
const helper = new Helper();
const HUMANIZED_MOUSE_INTERVAL_MS = 10;
const HUMANIZED_MOUSEMOVE_ACK_TIMEOUT_MS = 250;

function hashConsoleMessage(params) {
  return params.location.lineNumber + ':' + params.location.columnNumber + ':' + params.location.url;
}

function isHumanizeEnabled() {
  return ChromeUtils.rotundaGetBool('humanize.enabled', false);
}

// Bound live-video latency and memory: when encoding falls behind, capture
// ticks are skipped instead of queueing frames that are already stale. Mirrors
// kMaxNativeFramesInFlight in nsScreencastService.cpp.
const kMaxElementFramesInFlight = 8;
// Capture failures can be transient (encoder still warming up, a passing
// IPC or capture error), so the loops only stop after this many consecutive
// failures. Element detachment is permanent and stops immediately.
const kMaxConsecutiveCaptureFailures = 60;
// Empty video frames are the routine no-frame-this-tick signal, so a broken
// stream that only ever returns empties is caught by elapsed time instead.
const kElementVideoStallTimeoutMs = 10000;

function isPermanentCaptureError(error) {
  return String(error?.message ?? error).includes('detached from document');
}

function pngSize(data) {
  const header = atob(data.slice(0, 32));
  if (header.length < 24 || header.slice(1, 4) !== 'PNG')
    throw new Error('Native element snapshot did not return PNG data');
  const readUint32 = offset =>
    ((header.charCodeAt(offset) << 24) >>> 0) +
    (header.charCodeAt(offset + 1) << 16) +
    (header.charCodeAt(offset + 2) << 8) +
    header.charCodeAt(offset + 3);
  return {width: readUint32(16), height: readUint32(20)};
}

function humanizedMousePlan(fromX, fromY, toX, toY, bounds, clickAtEnd = false) {
  if (!isHumanizeEnabled() || fromX === toX && fromY === toY)
    return null;

  // ChromeUtils returns a compact native array in repeated
  // [x, y, dtMs, action] groups. Keep the native boundary simple, then turn it
  // into named JS objects before the dispatcher starts walking the path.
  const flatPlan = ChromeUtils.rotundaGetMouseTrajectory(
    Math.round(fromX),
    Math.round(fromY),
    Math.round(toX),
    Math.round(toY),
    clickAtEnd
  );
  if (!flatPlan || flatPlan.length < 4)
    return null;

  const plan = [];
  for (let i = 0; i + 3 < flatPlan.length; i += 4) {
    let x = flatPlan[i];
    let y = flatPlan[i + 1];
    const dtMs = Math.max(0, flatPlan[i + 2] || 0);
    const action = flatPlan[i + 3] || 0;
    if (bounds) {
      // The model can generate tiny excursions outside the viewport. Clamp here
      // so the renderer still receives legal content coordinates.
      x = Math.max(1, Math.min(Math.max(1, bounds.width - 1), x));
      y = Math.max(1, Math.min(Math.max(1, bounds.height - 1), y));
    }
    // Back-to-back duplicate points produce no visual or DOM value but do add
    // waits, so drop them before dispatching.
    if (plan.length && plan[plan.length - 1].x === x && plan[plan.length - 1].y === y)
      continue;
    plan.push({x, y, dtMs, action});
  }

  // The current mouse position is already true in the browser. Start with the
  // first actual movement and force the final point to be the requested target.
  if (plan.length && plan[0].x === fromX && plan[0].y === fromY)
    plan.shift();
  if (!plan.length || plan[plan.length - 1].x !== toX || plan[plan.length - 1].y !== toY)
    plan.push({x: toX, y: toY, dtMs: HUMANIZED_MOUSE_INTERVAL_MS, action: clickAtEnd ? 1 : 0});

  return plan;
}

class WorkerHandler {
  constructor(session, contentChannel, workerId) {
    this._session = session;
    this._contentWorker = contentChannel.connect(workerId);
    this._workerConsoleMessages = new Set();
    this._workerId = workerId;

    const emitWrappedProtocolEvent = eventName => {
      return params => {
        this._session.emitEvent('Page.dispatchMessageFromWorker', {
          workerId,
          message: JSON.stringify({method: eventName, params}),
        });
      }
    }

    this._eventListeners = [
      contentChannel.register(workerId, {
        runtimeConsole: (params) => {
          this._workerConsoleMessages.add(hashConsoleMessage(params));
          emitWrappedProtocolEvent('Runtime.console')(params);
        },
        runtimeExecutionContextCreated: emitWrappedProtocolEvent('Runtime.executionContextCreated'),
        runtimeExecutionContextDestroyed: emitWrappedProtocolEvent('Runtime.executionContextDestroyed'),
      }),
    ];
  }

  async sendMessage(message) {
    const [domain, method] = message.method.split('.');
    if (domain !== 'Runtime')
      throw new Error('ERROR: can only dispatch to Runtime domain inside worker');
    const result = await this._contentWorker.send(method, message.params);
    this._session.emitEvent('Page.dispatchMessageFromWorker', {
      workerId: this._workerId,
      message: JSON.stringify({result, id: message.id}),
    });
  }

  dispose() {
    this._contentWorker.dispose();
    helper.removeListeners(this._eventListeners);
  }
}

export class PageHandler {
  constructor(target, session, contentChannel) {
    this._session = session;
    this._contentChannel = contentChannel;
    this._contentPage = contentChannel.connect('page');
    this._workers = new Map();
    this._elementScreencast = null;
    this._videoStream = null;

    this._pageTarget = target;
    this._pageNetwork = PageNetwork.forPageTarget(target);

    const emitProtocolEvent = eventName => {
      return (...args) => this._session.emitEvent(eventName, ...args);
    }

    this._isDragging = false;
    this._lastMousePosition = { x: 0, y: 0 };

    this._reportedFrameIds = new Set();
    this._networkEventsForUnreportedFrameIds = new Map();

    // `Page.ready` protocol event is emitted whenever page has completed initialization, e.g.
    // finished all the transient navigations to the `about:blank`.
    //
    // We'd like to avoid reporting meaningful events before the `Page.ready` since they are likely
    // to be ignored by the protocol clients.
    this._isPageReady = false;

    if (this._pageTarget.videoRecordingInfo())
      this._onVideoRecordingStarted();

    this._pageEventSink = {};
    helper.decorateAsEventEmitter(this._pageEventSink);

    this._pendingEventWatchers = new Set();
    this._eventListeners = [
      helper.on(this._pageTarget, PageTarget.Events.DialogOpened, this._onDialogOpened.bind(this)),
      helper.on(this._pageTarget, PageTarget.Events.DialogClosed, this._onDialogClosed.bind(this)),
      helper.on(this._pageTarget, PageTarget.Events.Crashed, () => {
        this._session.emitEvent('Page.crashed', {});
      }),
      helper.on(this._pageTarget, PageTarget.Events.ScreencastStarted, this._onVideoRecordingStarted.bind(this)),
      helper.on(this._pageTarget, PageTarget.Events.ScreencastFrame, this._onScreencastFrame.bind(this)),
      helper.on(this._pageNetwork, PageNetwork.Events.Request, this._handleNetworkEvent.bind(this, 'Network.requestWillBeSent')),
      helper.on(this._pageNetwork, PageNetwork.Events.Response, this._handleNetworkEvent.bind(this, 'Network.responseReceived')),
      helper.on(this._pageNetwork, PageNetwork.Events.RequestFinished, this._handleNetworkEvent.bind(this, 'Network.requestFinished')),
      helper.on(this._pageNetwork, PageNetwork.Events.RequestFailed, this._handleNetworkEvent.bind(this, 'Network.requestFailed')),
      contentChannel.register('page', {
        pageBindingCalled: emitProtocolEvent('Page.bindingCalled'),
        pageDispatchMessageFromWorker: emitProtocolEvent('Page.dispatchMessageFromWorker'),
        pageEventFired: emitProtocolEvent('Page.eventFired'),
        pageFileChooserOpened: emitProtocolEvent('Page.fileChooserOpened'),
        pageFrameAttached: this._onFrameAttached.bind(this),
        pageFrameDetached: emitProtocolEvent('Page.frameDetached'),
        pageLinkClicked: emitProtocolEvent('Page.linkClicked'),
        pageWillOpenNewWindowAsynchronously: emitProtocolEvent('Page.willOpenNewWindowAsynchronously'),
        pageNavigationAborted: emitProtocolEvent('Page.navigationAborted'),
        pageNavigationCommitted: emitProtocolEvent('Page.navigationCommitted'),
        pageNavigationStarted: emitProtocolEvent('Page.navigationStarted'),
        pageReady: this._onPageReady.bind(this),
        pageInputEvent: (event) => this._pageEventSink.emit(event.type, event),
        pageSameDocumentNavigation: emitProtocolEvent('Page.sameDocumentNavigation'),
        pageUncaughtError: emitProtocolEvent('Page.uncaughtError'),
        pageWorkerCreated: this._onWorkerCreated.bind(this),
        pageWorkerDestroyed: this._onWorkerDestroyed.bind(this),
        runtimeConsole: params => {
          const consoleMessageHash = hashConsoleMessage(params);
          for (const worker of this._workers.values()) {
            if (worker._workerConsoleMessages.has(consoleMessageHash)) {
              worker._workerConsoleMessages.delete(consoleMessageHash);
              return;
            }
          }
          this._session.emitEvent('Runtime.console', params);
        },
        runtimeExecutionContextCreated: emitProtocolEvent('Runtime.executionContextCreated'),
        runtimeExecutionContextDestroyed: emitProtocolEvent('Runtime.executionContextDestroyed'),
        runtimeExecutionContextsCleared: emitProtocolEvent('Runtime.executionContextsCleared'),

        webSocketCreated: emitProtocolEvent('Page.webSocketCreated'),
        webSocketOpened: emitProtocolEvent('Page.webSocketOpened'),
        webSocketClosed: emitProtocolEvent('Page.webSocketClosed'),
        webSocketFrameReceived: emitProtocolEvent('Page.webSocketFrameReceived'),
        webSocketFrameSent: emitProtocolEvent('Page.webSocketFrameSent'),
      }),
    ];
  }

  async dispose() {
    // Tear down synchronously: the loops stop and the content-side stops are
    // posted before the channel goes away; awaiting a content round trip here
    // would leave live event listeners emitting into a disposed session.
    void this._stopElementScreencast();
    void this._stopVideoStream();
    this._contentPage.dispose();
    for (const watcher of this._pendingEventWatchers)
      watcher.dispose();
    helper.removeListeners(this._eventListeners);
  }

  _onVideoRecordingStarted() {
    const info = this._pageTarget.videoRecordingInfo();
    this._session.emitEvent('Page.videoRecordingStarted', { screencastId: info.sessionId, file: info.file });
  }

  _onScreencastFrame(params) {
    this._session.emitEvent('Page.screencastFrame', params);
  }

  _onPageReady(event) {
    this._isPageReady = true;
    this._session.emitEvent('Page.ready');
    for (const dialog of this._pageTarget.dialogs())
      this._onDialogOpened(dialog);
  }

  _onDialogOpened(dialog) {
    if (!this._isPageReady)
      return;
    this._session.emitEvent('Page.dialogOpened', {
      dialogId: dialog.id(),
      type: dialog.type(),
      message: dialog.message(),
      defaultValue: dialog.defaultValue(),
    });
  }

  _onDialogClosed(dialog) {
    if (!this._isPageReady)
      return;
    this._session.emitEvent('Page.dialogClosed', { dialogId: dialog.id(), });
  }

  _onWorkerCreated({workerId, frameId, url}) {
    const worker = new WorkerHandler(this._session, this._contentChannel, workerId);
    this._workers.set(workerId, worker);
    this._session.emitEvent('Page.workerCreated', {workerId, frameId, url});
  }

  _onWorkerDestroyed({workerId}) {
    const worker = this._workers.get(workerId);
    if (!worker)
      return;
    this._workers.delete(workerId);
    worker.dispose();
    this._session.emitEvent('Page.workerDestroyed', {workerId});
  }

  _handleNetworkEvent(protocolEventName, eventDetails, frameId) {
    if (!this._reportedFrameIds.has(frameId)) {
      let events = this._networkEventsForUnreportedFrameIds.get(frameId);
      if (!events) {
        events = [];
        this._networkEventsForUnreportedFrameIds.set(frameId, events);
      }
      events.push({eventName: protocolEventName, eventDetails});
    } else {
      this._session.emitEvent(protocolEventName, eventDetails);
    }
  }

  _onFrameAttached({frameId, parentFrameId}) {
    this._session.emitEvent('Page.frameAttached', {frameId, parentFrameId});
    this._reportedFrameIds.add(frameId);
    const events = this._networkEventsForUnreportedFrameIds.get(frameId) || [];
    this._networkEventsForUnreportedFrameIds.delete(frameId);
    for (const {eventName, eventDetails} of events)
      this._session.emitEvent(eventName, eventDetails);
  }

  async ['Page.close']({runBeforeUnload}) {
    // Postpone target close to deliver response in session.
    Services.tm.dispatchToMainThread(() => {
      this._pageTarget.close(runBeforeUnload);
    });
  }

  async ['Page.setViewportSize']({viewportSize}) {
    await this._pageTarget.setViewportSize(viewportSize === null ? undefined : viewportSize);
  }

  async ['Page.setZoom']({zoom}) {
    await this._pageTarget.setZoom(zoom);
  }

  async ['Runtime.evaluate'](options) {
    return await this._contentPage.send('evaluate', options);
  }

  async ['Runtime.callFunction'](options) {
    return await this._contentPage.send('callFunction', options);
  }

  async ['Runtime.getObjectProperties'](options) {
    return await this._contentPage.send('getObjectProperties', options);
  }

  async ['Runtime.disposeObject'](options) {
    return await this._contentPage.send('disposeObject', options);
  }

  async ['Heap.collectGarbage']() {
    Services.obs.notifyObservers(null, "child-gc-request");
    Cu.forceGC();
    Services.obs.notifyObservers(null, "child-cc-request");
    Cu.forceCC();
  }

  async ['Network.getResponseBody']({requestId}) {
    return this._pageNetwork.getResponseBody(requestId);
  }

  async ['Network.setExtraHTTPHeaders']({headers}) {
    this._pageNetwork.setExtraHTTPHeaders(headers);
  }

  async ['Network.setRequestInterception']({enabled}) {
    if (enabled)
      this._pageNetwork.enableRequestInterception();
    else
      this._pageNetwork.disableRequestInterception();
  }

  async ['Network.resumeInterceptedRequest']({requestId, url, method, headers, postData}) {
    this._pageNetwork.resumeInterceptedRequest(requestId, url, method, headers, postData);
  }

  async ['Network.abortInterceptedRequest']({requestId, errorCode}) {
    this._pageNetwork.abortInterceptedRequest(requestId, errorCode);
  }

  async ['Network.fulfillInterceptedRequest']({requestId, status, statusText, headers, base64body}) {
    this._pageNetwork.fulfillInterceptedRequest(requestId, status, statusText, headers, base64body);
  }

  async ['Accessibility.getFullAXTree'](params) {
    return await this._contentPage.send('getFullAXTree', params);
  }

  async ['Page.setFileInputFiles'](options) {
    return await this._contentPage.send('setFileInputFiles', options);
  }

  async ['Page.setEmulatedMedia']({colorScheme, type, reducedMotion, forcedColors, contrast}) {
    this._pageTarget.setColorScheme(colorScheme || null);
    this._pageTarget.setReducedMotion(reducedMotion || null);
    this._pageTarget.setForcedColors(forcedColors || null);
    this._pageTarget.setContrast(contrast || null);
    this._pageTarget.setEmulatedMedia(type);
  }

  async ['Page.bringToFront'](options) {
    await this._pageTarget.activateAndRun(() => {});
  }

  async ['Page.setCacheDisabled']({cacheDisabled}) {
    return await this._pageTarget.setCacheDisabled(cacheDisabled);
  }

  async ['Page.addBinding']({ worldName, name, script }) {
    return await this._pageTarget.addBinding(worldName, name, script);
  }

  async ['Page.adoptNode'](options) {
    return await this._contentPage.send('adoptNode', options);
  }

  async ['Page.screenshot']({ mimeType, clip, omitDeviceScaleFactor, quality = 80}) {
    const rect = new DOMRect(clip.x, clip.y, clip.width, clip.height);

    const browsingContext = this._pageTarget.linkedBrowser().browsingContext;
    // `win.devicePixelRatio` returns a non-overriden value to priveleged code.
    // See https://bugzilla.mozilla.org/show_bug.cgi?id=1761032
    // See https://phabricator.services.mozilla.com/D141323
    const devicePixelRatio = browsingContext.overrideDPPX || this._pageTarget._window.devicePixelRatio;
    const scale = omitDeviceScaleFactor ? 1 : devicePixelRatio;
    const canvasWidth = rect.width * scale;
    const canvasHeight = rect.height * scale;

    const MAX_CANVAS_DIMENSIONS = 32767;
    const MAX_CANVAS_AREA = 472907776;
    if (canvasWidth > MAX_CANVAS_DIMENSIONS || canvasHeight > MAX_CANVAS_DIMENSIONS)
      throw new Error('Cannot take screenshot larger than ' + MAX_CANVAS_DIMENSIONS);
    if (canvasWidth * canvasHeight > MAX_CANVAS_AREA)
      throw new Error('Cannot take screenshot with more than ' + MAX_CANVAS_AREA + ' pixels');

    let snapshot;
    while (!snapshot) {
      try {
        //TODO(fission): browsingContext will change in case of cross-group navigation.
        snapshot = await browsingContext.currentWindowGlobal.drawSnapshot(
          rect,
          scale,
          "rgb(255,255,255)"
        );
      } catch (e) {
        // The currentWindowGlobal.drawSnapshot might throw
        // NS_ERROR_LOSS_OF_SIGNIFICANT_DATA if called during navigation.
        // wait a little and re-try.
        await new Promise(x => setTimeout(x, 50));
      }
    }

    const win = browsingContext.topChromeWindow.ownerGlobal;
    const canvas = win.document.createElementNS('http://www.w3.org/1999/xhtml', 'canvas');
    canvas.width = canvasWidth;
    canvas.height = canvasHeight;
    let ctx = canvas.getContext('2d');
    ctx.drawImage(snapshot, 0, 0);
    snapshot.close();

    if (mimeType === 'image/jpeg') {
      if (quality < 0 || quality > 100)
        throw new Error('Quality must be an integer value between 0 and 100; received ' + quality);
      quality /= 100;
    } else {
      quality = undefined;
    }
    const dataURL = canvas.toDataURL(mimeType, quality);
    return { data: dataURL.substring(dataURL.indexOf(',') + 1) };
  }

  async ['Page.getContentQuads'](options) {
    return await this._contentPage.send('getContentQuads', options);
  }

  async ['Page.navigate']({frameId, url, referer}) {
    const browsingContext = this._pageTarget.frameIdToBrowsingContext(frameId);
    let sameDocumentNavigation = false;
    try {
      const uri = NetUtil.newURI(url);
      // This is the same check that verifes browser-side if this is the same-document navigation.
      // See CanonicalBrowsingContext::SupportsLoadingInParent.
      sameDocumentNavigation = browsingContext.currentURI && uri.hasRef && uri.equalsExceptRef(browsingContext.currentURI);
    } catch (e) {
      throw new Error(`Invalid url: "${url}"`);
    }
    let referrerURI = null;
    let referrerInfo = null;
    if (referer) {
      try {
        referrerURI = NetUtil.newURI(referer);
        const ReferrerInfo = Components.Constructor(
          '@mozilla.org/referrer-info;1',
          'nsIReferrerInfo',
          'init'
        );
        referrerInfo = new ReferrerInfo(Ci.nsIReferrerInfo.UNSAFE_URL, true, referrerURI);
      } catch (e) {
        throw new Error(`Invalid referer: "${referer}"`);
      }
    }

    let navigationId;
    const unsubscribe = helper.addObserver((browsingContext, topic, loadIdentifier) => {
      navigationId = helper.toProtocolNavigationId(loadIdentifier);
    }, 'juggler-navigation-started-browser');
    browsingContext.loadURI(Services.io.newURI(url), {
      triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
      loadFlags: Ci.nsIWebNavigation.LOAD_FLAGS_IS_LINK,
      referrerInfo,
      // postData: null,
      // headers: null,
      // Fake user activation.
      hasValidUserGestureActivation: true,
    });
    unsubscribe();

    return {
      navigationId: sameDocumentNavigation ? null : navigationId,
    };
  }

  async ['Page.goBack']({}) {
    const browsingContext = this._pageTarget.linkedBrowser().browsingContext;
    if (!browsingContext.embedderElement?.canGoBack)
      return { success: false };
    browsingContext.goBack();
    return { success: true };
  }

  async ['Page.goForward']({}) {
    const browsingContext = this._pageTarget.linkedBrowser().browsingContext;
    if (!browsingContext.embedderElement?.canGoForward)
      return { success: false };
    browsingContext.goForward();
    return { success: true };
  }

  async ['Page.reload']() {
    await this._pageTarget.activateAndRun(() => {
      const doc = this._pageTarget._tab.linkedBrowser.ownerDocument;
      doc.getElementById('Browser:Reload').doCommand();
    });
  }

  async ['Page.describeNode'](options) {
    return await this._contentPage.send('describeNode', options);
  }

  async ['Page.scrollIntoViewIfNeeded'](options) {
    return await this._contentPage.send('scrollIntoViewIfNeeded', options);
  }

  async ['Page.setInitScripts']({ scripts }) {
    return await this._pageTarget.setInitScripts(scripts);
  }

  async ['Page.dispatchKeyEvent']({type, keyCode, code, key, repeat, location, text}) {
    // key events don't fire if we are dragging.
    if (this._isDragging) {
      if (type === 'keydown' && key === 'Escape') {
        await this._contentPage.send('dispatchDragEvent', {
          type: 'dragover',
          x: this._lastMousePosition.x,
          y: this._lastMousePosition.y,
          modifiers: 0
        });
        await this._contentPage.send('dispatchDragEvent', {type: 'dragend'});
        this._isDragging = false;
      }
      return;
    }
    return await this._contentPage.send('dispatchKeyEvent', {type, keyCode, code, key, repeat, location, text});
  }

  async ['Page.dispatchTouchEvent'](options) {
    return await this._contentPage.send('dispatchTouchEvent', options);
  }

  async ['Page.dispatchTapEvent'](options) {
    return await this._contentPage.send('dispatchTapEvent', options);
  }

  async ['Page.dispatchMouseEvent']({type, x, y, button, clickCount, modifiers, buttons}) {
    const win = this._pageTarget._window;
    const notifyCursorOverlay = (eventType, chromeX, chromeY) => {
      try {
        const overlayWin = this._pageTarget._linkedBrowser.ownerGlobal || win;
        if (typeof overlayWin.__rotundaSetCursorOverlay === 'function') {
          overlayWin.__rotundaSetCursorOverlay(chromeX, chromeY, eventType);
          return;
        }
        overlayWin.dispatchEvent(new overlayWin.CustomEvent('RotundaCursorMove', {
          detail: {type: eventType, x: chromeX, y: chromeY},
        }));
      } catch (e) {
        // Cursor overlay is optional; never block input dispatch.
      }
    };
    const sendEvents = async (types, eventX = x, eventY = y, overlayType = null, options = {}) => {
      // 1. Scroll element to the desired location first; the coordinates are relative to the element.
      this._pageTarget._linkedBrowser.scrollRectIntoViewIfNeeded(eventX, eventY, 0, 0);
      // 2. Get element's bounding box in the browser after the scroll is completed.
      const boundingBox = this._pageTarget._linkedBrowser.getBoundingClientRect();
      // 3. Make sure compositor is flushed after scrolling.
      if (win.windowUtils.flushApzRepaints())
        await helper.awaitTopic('apz-repaints-flushed');

      const watcher = new EventWatcher(this._pageEventSink, types, this._pendingEventWatchers);
      const promises = [];
      for (const type of types) {
        // Protocol callers speak in web-content coordinates. Gecko's synthetic
        // event API wants chrome-window coordinates, so offset by the linked
        // browser's current box after any scroll adjustment.
        const chromeX = eventX + boundingBox.left;
        const chromeY = eventY + boundingBox.top;
        // The renderer still gets the real DOM mouse event type. The overlay can
        // receive a higher-level visual phase, e.g. "clicksettle", when we want
        // the cursor graphic to prepare for a click without changing dispatch.
        notifyCursorOverlay(overlayType || type, chromeX, chromeY);
        // This dispatches to the renderer synchronously.
        const jugglerEventId = win.windowUtils.jugglerSendMouseEvent(
          type,
          chromeX,
          chromeY,
          button,
          clickCount,
          modifiers,
          false /* aIgnoreRootScrollFrame */,
          0.0 /* pressure */,
          0 /* inputSource */,
          true /* isDOMEventSynthesized */,
          false /* isWidgetEventSynthesized */,
          buttons,
          win.windowUtils.DEFAULT_MOUSE_POINTER_ID /* pointerIdentifier */,
          false /* disablePointerEvent */
        );
        const eventPromise = watcher.ensureEvent(type, eventObject => eventObject.jugglerEventId === jugglerEventId);
        if (options.timeoutMs) {
          promises.push(Promise.race([
            eventPromise.catch(() => null),
            new Promise(resolve => setTimeout(() => resolve(null), options.timeoutMs)),
          ]));
        } else {
          promises.push(eventPromise);
        }
      }
      try {
        await Promise.all(promises);
      } finally {
        watcher.dispose();
      }
    };
    const createHumanizedMouseScheduler = points => {
      let nextDueTime = Date.now();
      return async index => {
        if (index === 0)
          return;
        // Schedule against the cumulative model timeline so renderer/APZ ack
        // time does not get added to every modeled interval.
        const delay = points[index].dtMs || HUMANIZED_MOUSE_INTERVAL_MS;
        nextDueTime += delay;
        const remaining = nextDueTime - Date.now();
        if (remaining > 0)
          await new Promise(resolve => setTimeout(resolve, remaining));
      };
    };
    const sendDragOver = async (eventX, eventY) => {
      const watcher = new EventWatcher(this._pageEventSink, ['dragover'], this._pendingEventWatchers);
      await this._contentPage.send('dispatchDragEvent', {type: 'dragover', x: eventX, y: eventY, modifiers});
      await watcher.ensureEventsAndDispose(['dragover']);
    };
    const sendDragOverPath = async (points, startIndex = 0, waitForPoint = createHumanizedMouseScheduler(points)) => {
      for (let i = startIndex; i < points.length; ++i) {
        const point = points[i];
        await waitForPoint(i);
        // Once Gecko has started a drag session, mousemove is no longer the
        // right event shape. Continue the same path as dragover events instead.
        await sendDragOver(point.x, point.y);
      }
    };
    const sendPotentialDragPath = async (points) => {
      const waitForPoint = createHumanizedMouseScheduler(points);
      for (let i = 0; i < points.length; ++i) {
        const point = points[i];
        await waitForPoint(i);
        const watcher = new EventWatcher(this._pageEventSink, ['dragstart', 'juggler-drag-finalized'], this._pendingEventWatchers);
        try {
          // Button-held moves begin as normal mousemove events. If content turns
          // one into a drag, the watcher flips us into dragover dispatch for the
          // rest of this planned path.
          await sendEvents(['mousemove'], point.x, point.y);

          if (watcher.hasEvent('dragstart')) {
            const eventObject = await watcher.ensureEvent('juggler-drag-finalized');
            this._isDragging = eventObject.dragSessionStarted;
          }
        } finally {
          watcher.dispose();
        }

        if (this._isDragging) {
          await sendDragOverPath(points, i + 1, waitForPoint);
          return;
        }
      }
    };
    const sendMouseMovePath = async (points, settleForClick = false) => {
      const waitForPoint = createHumanizedMouseScheduler(points);
      for (let i = 0; i < points.length; ++i) {
        const point = points[i];
        await waitForPoint(i);
        // For click-bound paths, start settling the visual cursor over the final
        // few move events so the arrow is upright before mousedown lands.
        const overlayType = settleForClick && i >= points.length - 3 ? 'clicksettle' : null;
        await sendEvents(['mousemove'], point.x, point.y, overlayType, {timeoutMs: HUMANIZED_MOUSEMOVE_ACK_TIMEOUT_MS});
      }
    };

    // We must switch to proper tab in the tabbed browser so that
    // 1. Event is dispatched to a proper renderer.
    // 2. We receive an ack from the renderer for the dispatched event.
    await this._pageTarget.activateAndRun(async () => {
      this._pageTarget.ensureContextMenuClosed();
      // If someone asks us to dispatch mouse event outside of viewport, then we normally would drop it.
      const boundingBox = this._pageTarget._linkedBrowser.getBoundingClientRect();
      if (x < 0 || y < 0 || x > boundingBox.width || y > boundingBox.height) {
        if (type !== 'mousemove')
          return;

        // A special hack: if someone tries to do `mousemove` outside of
        // viewport coordinates, then move the mouse off from the Web Content.
        // This way we can eliminate all the hover effects.
        // NOTE: since this won't go inside the renderer, there's no need to wait for ACK.
        win.windowUtils.jugglerSendMouseEvent(
          'mousemove',
          0 /* x */,
          0 /* y */,
          button,
          clickCount,
          modifiers,
          false /* aIgnoreRootScrollFrame */,
          0.0 /* pressure */,
          0 /* inputSource */,
          true /* isDOMEventSynthesized */,
          false /* isWidgetEventSynthesized */,
          buttons,
          win.windowUtils.DEFAULT_MOUSE_POINTER_ID /* pointerIdentifier */,
          false /* disablePointerEvent */
        );
        return;
      }

      if (type === 'mousedown') {
        if (this._isDragging)
          return;

        const previousMousePosition = this._lastMousePosition || {x: 0, y: 0};
        const points = humanizedMousePlan(previousMousePosition.x, previousMousePosition.y, x, y, boundingBox, true);
        // Humanized click movement is split from the actual press: first walk the
        // pointer to the target, then send mousedown/contextmenu at the final
        // coordinate once the overlay has had a chance to settle.
        if (points)
          await sendMouseMovePath(points, true);
        this._lastMousePosition = { x, y };
        const eventNames = button === 2 ? ['mousedown', 'contextmenu'] : ['mousedown'];
        await sendEvents(eventNames);
        return;
      }

      if (type === 'mousemove') {
        const previousMousePosition = this._lastMousePosition || {x: 0, y: 0};
        this._lastMousePosition = { x, y };
        if (this._isDragging) {
          // Active drag sessions cannot be represented as plain mousemove in
          // content. Preserve humanized timing but emit dragover/drop semantics.
          const points = humanizedMousePlan(previousMousePosition.x, previousMousePosition.y, x, y, boundingBox);
          if (points)
            await sendDragOverPath(points);
          else
            await sendDragOver(x, y);
          return;
        }

        if (buttons === 0) {
          // Hover movement is the simplest case: walk the modeled path as
          // mousemove events and let the overlay follow the same points.
          const points = humanizedMousePlan(previousMousePosition.x, previousMousePosition.y, x, y, boundingBox);
          if (points) {
            await sendMouseMovePath(points);
            return;
          }
        }

        if (buttons) {
          // Button-held movement might become a drag depending on page behavior,
          // so dispatch one point at a time and watch for Gecko's dragstart ack.
          const points = humanizedMousePlan(previousMousePosition.x, previousMousePosition.y, x, y, boundingBox);
          if (points) {
            await sendPotentialDragPath(points);
            return;
          }
        }

        const watcher = new EventWatcher(this._pageEventSink, ['dragstart', 'juggler-drag-finalized'], this._pendingEventWatchers);
        await sendEvents(['mousemove']);

        // The order of events after 'mousemove' is sent:
        // 1. [dragstart] - might or might NOT be emitted
        // 2. [mousemove] - always emitted. This was awaited as part of `sendEvents` call.
        // 3. [juggler-drag-finalized] - only emitted if dragstart was emitted.

        if (watcher.hasEvent('dragstart')) {
          const eventObject = await watcher.ensureEvent('juggler-drag-finalized');
          this._isDragging = eventObject.dragSessionStarted;
        }
        watcher.dispose();
        return;
      }

      if (type === 'mouseup') {
        const previousMousePosition = this._lastMousePosition || {x, y};
        this._lastMousePosition = { x, y };
        if (this._isDragging) {
          // Finish any remaining humanized movement before drop/dragend so the
          // drop target matches the final requested coordinate.
          const points = humanizedMousePlan(previousMousePosition.x, previousMousePosition.y, x, y, boundingBox);
          if (points)
            await sendDragOverPath(points);
          else
            await sendDragOver(x, y);
          await this._contentPage.send('dispatchDragEvent', {type: 'drop', x, y, modifiers});
          await this._contentPage.send('dispatchDragEvent', {type: 'dragend', x, y, modifiers});
          // NOTE:
          // - 'drop' event might not be dispatched at all, depending on dropAction.
          // - 'dragend' event might not be dispatched at all, if the source element was removed
          //   during drag. However, it'll be dispatched synchronously in the renderer.
          this._isDragging = false;
        } else {
          await sendEvents(['mouseup']);
        }
        return;
      }
    }, { muteNotificationsPopup: true });
  }

  async ['Page.dispatchWheelEvent']({x, y, button, deltaX, deltaY, deltaZ, modifiers }) {
    const deltaMode = 0; // WheelEvent.DOM_DELTA_PIXEL
    const lineOrPageDeltaX = deltaX > 0 ? Math.floor(deltaX) : Math.ceil(deltaX);
    const lineOrPageDeltaY = deltaY > 0 ? Math.floor(deltaY) : Math.ceil(deltaY);

    await this._pageTarget.activateAndRun(async () => {
      this._pageTarget.ensureContextMenuClosed();

      // 1. Scroll element to the desired location first; the coordinates are relative to the element.
      this._pageTarget._linkedBrowser.scrollRectIntoViewIfNeeded(x, y, 0, 0);
      // 2. Get element's bounding box in the browser after the scroll is completed.
      const boundingBox = this._pageTarget._linkedBrowser.getBoundingClientRect();

      const win = this._pageTarget._window;
      // 3. Make sure compositor is flushed after scrolling.
      if (win.windowUtils.flushApzRepaints())
        await helper.awaitTopic('apz-repaints-flushed');

      win.windowUtils.sendWheelEvent(
        // Wheel synthesis uses the same content-to-chrome coordinate hop as
        // mouse dispatch, but it does not participate in the cursor overlay.
        x + boundingBox.left,
        y + boundingBox.top,
        deltaX,
        deltaY,
        deltaZ,
        deltaMode,
        modifiers,
        lineOrPageDeltaX,
        lineOrPageDeltaY,
        0 /* options */);
    }, { muteNotificationsPopup: true });
  }

  async ['Page.insertText'](options) {
    return await this._contentPage.send('insertText', {
      ...options,
      humanizeEnabled: isHumanizeEnabled(),
    });
  }

  async ['Page.crash'](options) {
    return await this._contentPage.send('crash', options);
  }

  async ['Page.handleDialog']({dialogId, accept, promptText}) {
    const dialog = this._pageTarget.dialog(dialogId);
    if (!dialog)
      throw new Error('Failed to find dialog with id = ' + dialogId);
    if (accept)
      dialog.accept(promptText);
    else
      dialog.dismiss();
  }

  async ['Page.setInterceptFileChooserDialog']({ enabled }) {
    return await this._pageTarget.setInterceptFileChooserDialog(enabled);
  }

  // Screencast (`Page.startScreencast`) delivers ack-paced image frames: the
  // upstream JPEG viewport capture, or PNG frames of one element when
  // frameId/objectId are given. Video streaming (`Page.startVideoStream`)
  // delivers compressed RSE2 packets and is a separate feature; both emit
  // through `Page.screencastFrame`, so one session runs at most one of them.
  async ['Page.startScreencast'](options = {}) {
    const {width = 1280, height = 720, quality = 90, frameId, objectId,
           fps = 25} = options;
    if (fps < 1 || fps > 60)
      throw new Error('Screencast FPS must be between 1 and 60');
    if (this._videoStream)
      throw new Error('Screencast is already running');
    // Selector capture must paint in the content process to exclude
    // surrounding pixels.
    if (objectId || frameId)
      return await this._startElementScreencast({frameId, objectId, fps});
    if (this._elementScreencast)
      throw new Error('Screencast is already running');
    if (width < 10 || width > 10000 || height < 10 || height > 10000)
      throw new Error('Invalid size');
    if (quality < 1 || quality > 100)
      throw new Error('Screencast quality must be between 1 and 100');
    return await this._pageTarget.startScreencast({width, height, quality});
  }

  async ['Page.startVideoStream'](options = {}) {
    const {width = 1280, height = 720, fps = 25, bitrate = 12000000,
           codec = 'h264', frameId, objectId} = options;
    if (AppConstants.platform !== 'macosx')
      throw new Error('Native video streaming is currently supported only on macOS. Linux and Microsoft Windows are not supported yet; contributions are welcome.');
    if (fps < 1 || fps > 60)
      throw new Error('Video stream FPS must be between 1 and 60');
    if (width < 2 || height < 2 || width > 8192 || height > 8192 || width % 2 || height % 2)
      throw new Error('Video stream dimensions must be even and between 2 and 8192');
    if (bitrate < 1000 || bitrate > 500000000)
      throw new Error('Video stream bitrate must be between 1000 and 500000000');
    if (codec !== 'h264' && codec !== 'h265')
      throw new Error('Video stream codec must be h264 or h265');
    if (this._videoStream || this._elementScreencast || this._pageTarget.screencastInfo())
      throw new Error('Screencast is already running');

    // Selector capture must paint in the content process to exclude
    // surrounding pixels. Selectorless capture stays in the parent/compositor,
    // which enables the headed-macOS IOSurface fast path.
    if (objectId || frameId) {
      if (!frameId || !objectId)
        throw new Error('Element video stream requires frameId and objectId');
      const state = {
        id: helper.generateId(),
        interval: 1000 / fps,
        timer: null,
        width,
        height,
        startedAt: Date.now(),
        lastFrameIndex: -1,
        lastDataAt: Date.now(),
        capturesInFlight: 0,
        consecutiveFailures: 0,
        nextFrameAt: Date.now(),
      };
      const stream = {kind: 'element', state};
      // Claim the slot before the first await so concurrent starts and a stop
      // arriving mid-start observe this stream.
      this._videoStream = stream;
      try {
        await this._contentPage.send('startElementScreencast', {frameId, objectId, video: true, width, height, fps, bitrate, codec});
      } catch (error) {
        if (this._videoStream === stream)
          this._videoStream = null;
        throw error;
      }
      if (this._videoStream !== stream)
        throw new Error('Video stream was stopped');
      void this._captureElementVideoFrame(state);
      return {streamId: state.id};
    }

    const stream = {kind: 'viewport'};
    this._videoStream = stream;
    try {
      const {screencastId} = await this._pageTarget.startVideoStream({width, height, fps, bitrate, codec});
      stream.streamId = screencastId;
    } catch (error) {
      if (this._videoStream === stream)
        this._videoStream = null;
      throw error;
    }
    if (this._videoStream !== stream) {
      await this._pageTarget.stopScreencast();
      throw new Error('Video stream was stopped');
    }
    return {streamId: stream.streamId};
  }

  async ['Page.stopVideoStream']() {
    await this._stopVideoStream();
  }

  async ['Page.screencastFrameAck'](options) {
    if (this._elementScreencast) {
      this._elementScreencast.waitingForAck = false;
      return;
    }
    if (this._videoStream)
      return;
    await this._pageTarget.screencastFrameAck(options);
  }

  async ['Page.stopScreencast'](options) {
    if (this._elementScreencast) {
      await this._stopElementScreencast();
      return;
    }
    // A video stream is not a screencast; it only stops via stopVideoStream.
    if (this._videoStream)
      return;
    await this._pageTarget.stopScreencast(options);
  }

  async _startElementScreencast({frameId, objectId, fps}) {
    if (!frameId || !objectId)
      throw new Error('Element screencast requires frameId and objectId');
    if (this._elementScreencast || this._pageTarget.screencastInfo())
      throw new Error('Screencast is already running');

    const state = {
      id: helper.generateId(),
      interval: 1000 / fps,
      timer: null,
      waitingForAck: false,
      consecutiveFailures: 0,
    };
    // Claim the slot before the first await so concurrent starts and a stop
    // arriving mid-start observe this screencast.
    this._elementScreencast = state;
    try {
      await this._contentPage.send('startElementScreencast', {frameId, objectId, video: false});
    } catch (error) {
      if (this._elementScreencast === state)
        this._elementScreencast = null;
      throw error;
    }
    if (this._elementScreencast !== state)
      throw new Error('Screencast was stopped');
    void this._captureElementPngFrame(state);
    return {screencastId: state.id};
  }

  // PNG mode is ack-paced and serial: capture, emit, wait for the client ack,
  // then schedule the next capture relative to when this one started.
  async _captureElementPngFrame(state) {
    if (this._elementScreencast !== state)
      return;
    const started = Date.now();
    try {
      if (!state.waitingForAck) {
        const {data} = await this._contentPage.send('captureElementScreencastFrame', {});
        const size = pngSize(data);
        if (this._elementScreencast !== state)
          return;
        state.consecutiveFailures = 0;
        state.waitingForAck = true;
        this._session.emitEvent('Page.screencastFrame', {
          data,
          deviceWidth: size.width,
          deviceHeight: size.height,
          timestamp: Date.now() / 1000,
        });
      }
    } catch (error) {
      state.consecutiveFailures++;
      if (this._elementScreencast === state &&
          (isPermanentCaptureError(error) || state.consecutiveFailures >= kMaxConsecutiveCaptureFailures)) {
        void this._stopElementScreencast();
        return;
      }
    }
    if (this._elementScreencast === state)
      state.timer = setTimeout(() => void this._captureElementPngFrame(state), Math.max(0, state.interval - (Date.now() - started)));
  }

  // Element video is pipelined on a fixed cadence: schedule the next tick
  // first so encode latency never compounds into the frame clock, and skip
  // ticks while too many captures are in flight rather than queueing stale
  // frames.
  async _captureElementVideoFrame(state) {
    if (this._videoStream?.state !== state)
      return;
    if (Date.now() - state.lastDataAt > kElementVideoStallTimeoutMs) {
      void this._stopVideoStream();
      return;
    }
    // Clamp catch-up so a long stall yields one prompt frame, not a burst of
    // zero-delay ticks until wall-clock parity is restored.
    state.nextFrameAt = Math.max(state.nextFrameAt + state.interval, Date.now() - state.interval);
    state.timer = setTimeout(() => void this._captureElementVideoFrame(state),
                             Math.max(0, state.nextFrameAt - Date.now()));
    if (state.capturesInFlight >= kMaxElementFramesInFlight)
      return;
    state.capturesInFlight++;
    try {
      // Wall-clock frame index: PTS is frameIndex / fps, so skipped ticks must
      // leave PTS gaps rather than compress the encoded timeline.
      const frameIndex = Math.max(Math.round((Date.now() - state.startedAt) / state.interval),
                                  state.lastFrameIndex + 1);
      state.lastFrameIndex = frameIndex;
      const {data} = await this._contentPage.send('captureElementScreencastFrame', {frameIndex});
      if (this._videoStream?.state !== state)
        return;
      state.consecutiveFailures = 0;
      if (!data)
        return;
      state.lastDataAt = Date.now();
      this._session.emitEvent('Page.screencastFrame', {
        data,
        deviceWidth: state.width,
        deviceHeight: state.height,
        timestamp: Date.now() / 1000,
      });
    } catch (error) {
      state.consecutiveFailures++;
      if (this._videoStream?.state === state &&
          (isPermanentCaptureError(error) || state.consecutiveFailures >= kMaxConsecutiveCaptureFailures))
        void this._stopVideoStream();
    } finally {
      state.capturesInFlight--;
    }
  }

  async _stopElementScreencast() {
    const state = this._elementScreencast;
    if (!state)
      return;
    this._elementScreencast = null;
    if (state.timer)
      clearTimeout(state.timer);
    await this._contentPage.send('stopElementScreencast').catch(() => {});
  }

  async _stopVideoStream() {
    const stream = this._videoStream;
    if (!stream)
      return;
    this._videoStream = null;
    if (stream.kind === 'element') {
      if (stream.state.timer)
        clearTimeout(stream.state.timer);
      await this._contentPage.send('stopElementScreencast').catch(() => {});
      return;
    }
    await this._pageTarget.stopScreencast().catch(() => {});
  }

  async ['Page.sendMessageToWorker']({workerId, message}) {
    const worker = this._workers.get(workerId);
    if (!worker)
      throw new Error('ERROR: cannot find worker with id ' + workerId);
    return await worker.sendMessage(JSON.parse(message));
  }
}
