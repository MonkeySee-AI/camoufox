/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

const Ci = Components.interfaces;
const Cr = Components.results;
const Cu = Components.utils;

const {Helper} = ChromeUtils.importESModule('chrome://juggler/content/Helper.js');
const {NetUtil} = ChromeUtils.importESModule('resource://gre/modules/NetUtil.sys.mjs');
const {setTimeout} = ChromeUtils.importESModule('resource://gre/modules/Timer.sys.mjs');

const dragService = Cc["@mozilla.org/widget/dragservice;1"].getService(
  Ci.nsIDragService
);
const obs = Cc["@mozilla.org/observer-service;1"].getService(
  Ci.nsIObserverService
);

const helper = new Helper();
const HUMANIZED_TEXT_INTERVAL_MS = 10;
const KEYBOARD_BACKSPACE = "<BACKSPACE>";
const KEYBOARD_STOP = "<STOP>";

function isHumanizeEnabled() {
  return ChromeUtils.rotundaGetBool('humanize.enabled', false);
}

function rotundaDebug(message) {
  if (typeof ChromeUtils.rotundaDebug === "function" &&
      typeof ChromeUtils.isRotundaDebug === "function" &&
      ChromeUtils.isRotundaDebug())
    ChromeUtils.rotundaDebug(message);
}

function textInputUnits(text) {
  if (!text)
    return [];
  if (typeof Intl !== 'undefined' && Intl.Segmenter) {
    const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
    return Array.from(segmenter.segment(text), segment => segment.segment);
  }
  return Array.from(text);
}

function activeEditableText(frame) {
  const domWindow = frame.domWindow();
  const document = domWindow ? domWindow.document : null;
  const element = document ? document.activeElement : null;
  if (!element)
    return "";
  if (typeof element.value === "string")
    return element.value;
  if (element.isContentEditable)
    return element.textContent || "";
  return "";
}

function keyboardTrajectoryPlan(initialText, finalText) {
  const flatPlan = ChromeUtils.rotundaGetKeyboardTrajectory(initialText, finalText);
  if (!flatPlan || flatPlan.length < 2)
    return null;

  const plan = [];
  for (let i = 0; i + 1 < flatPlan.length; i += 2) {
    const dtMs = Math.max(0, Number(flatPlan[i]) || 0);
    const action = flatPlan[i + 1];
    if (!action || action === KEYBOARD_STOP)
      continue;
    plan.push({dtMs, action});
  }
  return plan.length ? plan : null;
}

async function applyKeyboardAction(frame, tip, action) {
  if (action === KEYBOARD_BACKSPACE) {
    const event = new (frame.domWindow().KeyboardEvent)("", {
      key: "Backspace",
      code: "Backspace",
      keyCode: 8
    });
    const flags = 0;
    tip.keydown(event, flags);
    tip.keyup(event, flags);
    return;
  }
  tip.commitCompositionWith(action);
}

async function commitTextInput(frame, text, keyEvent = undefined, humanizeEnabled = isHumanizeEnabled()) {
  const tip = frame.textInputProcessor();
  if (!humanizeEnabled || !text) {
    if (keyEvent)
      tip.commitCompositionWith(text, keyEvent);
    else
      tip.commitCompositionWith(text);
    return;
  }

  const initialText = activeEditableText(frame);
  const plan = keyboardTrajectoryPlan(initialText, initialText + text);
  if (plan) {
    for (const step of plan) {
      if (step.dtMs > 0)
        await new Promise(resolve => setTimeout(resolve, step.dtMs));
      await applyKeyboardAction(frame, tip, step.action);
    }
    return;
  }

  rotundaDebug(
    `Keyboard trajectory unavailable; falling back to fixed cadence for ${text.length} inserted chars ` +
    `(initial length ${initialText.length}).`
  );
  const units = textInputUnits(text);
  for (let i = 0; i < units.length; ++i) {
    if (keyEvent)
      tip.commitCompositionWith(units[i], keyEvent);
    else
      tip.commitCompositionWith(units[i]);
    if (i + 1 < units.length)
      await new Promise(resolve => setTimeout(resolve, HUMANIZED_TEXT_INTERVAL_MS));
  }
}

class WorkerData {
  constructor(pageAgent, browserChannel, worker) {
    this._workerRuntime = worker.channel().connect('runtime');
    this._browserWorker = browserChannel.connect(worker.id());
    this._worker = worker;
    const emit = name => {
      return (...args) => this._browserWorker.emit(name, ...args);
    };
    this._eventListeners = [
      worker.channel().register('runtime', {
        runtimeConsole: emit('runtimeConsole'),
        runtimeExecutionContextCreated: emit('runtimeExecutionContextCreated'),
        runtimeExecutionContextDestroyed: emit('runtimeExecutionContextDestroyed'),
      }),
      browserChannel.register(worker.id(), {
        evaluate: (options) => this._workerRuntime.send('evaluate', options),
        callFunction: (options) => this._workerRuntime.send('callFunction', options),
        getObjectProperties: (options) => this._workerRuntime.send('getObjectProperties', options),
        disposeObject: (options) => this._workerRuntime.send('disposeObject', options),
      }),
    ];
  }

  dispose() {
    this._workerRuntime.dispose();
    this._browserWorker.dispose();
    helper.removeListeners(this._eventListeners);
  }
}

export class PageAgent {
  constructor(browserChannel, frameTree) {
    this._browserChannel = browserChannel;
    this._browserPage = browserChannel.connect('page');
    this._frameTree = frameTree;
    this._runtime = frameTree.runtime();

    this._workerData = new Map();
    this._elementScreencastNode = null;
    this._elementScreencastViewport = false;

    const docShell = frameTree.mainFrame().docShell();
    this._docShell = docShell;

    // Dispatch frameAttached events for all initial frames
    for (const frame of this._frameTree.frames()) {
      this._onFrameAttached(frame);
      if (frame.url())
        this._onNavigationCommitted(frame);
      if (frame.pendingNavigationId())
        this._onNavigationStarted(frame);
    }

    // Report created workers.
    for (const worker of this._frameTree.workers())
      this._onWorkerCreated(worker);

    // Report execution contexts.
    this._browserPage.emit('runtimeExecutionContextsCleared', {});
    for (const context of this._runtime.executionContexts())
      this._onExecutionContextCreated(context);

    if (this._frameTree.isPageReady()) {
      this._browserPage.emit('pageReady', {});
      const mainFrame = this._frameTree.mainFrame();
      const domWindow = mainFrame.domWindow();
      const document = domWindow ? domWindow.document : null;
      const readyState = document ? document.readyState : null;
      // Sometimes we initialize later than the first about:blank page is opened.
      // In this case, the page might've been loaded already, and we need to issue
      // the `DOMContentLoaded` and `load` events.
      if (mainFrame.url() === 'about:blank' && readyState === 'complete')
        this._emitAllEvents(this._frameTree.mainFrame());
    }

    this._eventListeners = [
      helper.addObserver(this._linkClicked.bind(this, false), 'juggler-link-click'),
      helper.addObserver(this._linkClicked.bind(this, true), 'juggler-link-click-sync'),
      helper.addObserver(this._onWindowOpenInNewContext.bind(this), 'juggler-window-open-in-new-context'),
      helper.addObserver(this._filePickerShown.bind(this), 'juggler-file-picker-shown'),
      helper.addObserver(this._onDocumentOpenLoad.bind(this), 'juggler-document-open-loaded'),
      helper.on(this._frameTree, 'frameattached', this._onFrameAttached.bind(this)),
      helper.on(this._frameTree, 'framedetached', this._onFrameDetached.bind(this)),
      helper.on(this._frameTree, 'navigationstarted', this._onNavigationStarted.bind(this)),
      helper.on(this._frameTree, 'navigationcommitted', this._onNavigationCommitted.bind(this)),
      helper.on(this._frameTree, 'navigationaborted', this._onNavigationAborted.bind(this)),
      helper.on(this._frameTree, 'samedocumentnavigation', this._onSameDocumentNavigation.bind(this)),
      helper.on(this._frameTree, 'pageready', () => this._browserPage.emit('pageReady', {})),
      helper.on(this._frameTree, 'workercreated', this._onWorkerCreated.bind(this)),
      helper.on(this._frameTree, 'workerdestroyed', this._onWorkerDestroyed.bind(this)),
      helper.on(this._frameTree, 'websocketcreated', event => this._browserPage.emit('webSocketCreated', event)),
      helper.on(this._frameTree, 'websocketopened', event => this._browserPage.emit('webSocketOpened', event)),
      helper.on(this._frameTree, 'websocketframesent', event => this._browserPage.emit('webSocketFrameSent', event)),
      helper.on(this._frameTree, 'websocketframereceived', event => this._browserPage.emit('webSocketFrameReceived', event)),
      helper.on(this._frameTree, 'websocketclosed', event => this._browserPage.emit('webSocketClosed', event)),
      helper.on(this._frameTree, 'inputevent', inputEvent => {
        this._browserPage.emit('pageInputEvent', inputEvent);
        if (inputEvent.type === 'dragstart') {
          // After the dragStart event is dispatched and handled by Web,
          // it might or might not create a new drag session, depending on its preventing default.
          setTimeout(() => {
            const session = this._getCurrentDragSession();
            this._browserPage.emit('pageInputEvent', { type: 'juggler-drag-finalized', dragSessionStarted: !!session });
          }, 0);
        }
      }),
      helper.addObserver(this._onWindowOpen.bind(this), 'webNavigation-createdNavigationTarget-from-js'),
      this._runtime.events.onErrorFromWorker(({ domWindow, message, stack, location }) => {
        const frame = this._frameTree.frameForDocShell(domWindow.docShell);
        if (!frame)
          return;
        this._browserPage.emit('pageUncaughtError', {
          frameId: frame.id(),
          message,
          stack,
          location,
        });
      }),
      this._runtime.events.onConsoleMessage(msg => this._browserPage.emit('runtimeConsole', msg)),
      this._runtime.events.onRuntimeError(this._onRuntimeError.bind(this)),
      this._runtime.events.onExecutionContextCreated(this._onExecutionContextCreated.bind(this)),
      this._runtime.events.onExecutionContextDestroyed(this._onExecutionContextDestroyed.bind(this)),
      this._runtime.events.onBindingCalled(this._onBindingCalled.bind(this)),
      browserChannel.register('page', {
        adoptNode: this._adoptNode.bind(this),
        crash: this._crash.bind(this),
        describeNode: this._describeNode.bind(this),
        dispatchKeyEvent: this._dispatchKeyEvent.bind(this),
        dispatchDragEvent: this._dispatchDragEvent.bind(this),
        dispatchTouchEvent: this._dispatchTouchEvent.bind(this),
        dispatchTapEvent: this._dispatchTapEvent.bind(this),
        getContentQuads: this._getContentQuads.bind(this),
        getFullAXTree: this._getFullAXTree.bind(this),
        insertText: this._insertText.bind(this),
        startElementScreencast: this._startElementScreencast.bind(this),
        captureElementScreencastFrame: this._captureElementScreencastFrame.bind(this),
        stopElementScreencast: this._stopElementScreencast.bind(this),
        scrollIntoViewIfNeeded: this._scrollIntoViewIfNeeded.bind(this),
        setFileInputFiles: this._setFileInputFiles.bind(this),
        evaluate: this._runtime.evaluate.bind(this._runtime),
        callFunction: this._runtime.callFunction.bind(this._runtime),
        getObjectProperties: this._runtime.getObjectProperties.bind(this._runtime),
        disposeObject: this._runtime.disposeObject.bind(this._runtime),
      }),
    ];
  }

  _emitAllEvents(frame) {
    this._browserPage.emit('pageEventFired', {
      frameId: frame.id(),
      name: 'DOMContentLoaded',
    });
    this._browserPage.emit('pageEventFired', {
      frameId: frame.id(),
      name: 'load',
    });
  }

  _onExecutionContextCreated(executionContext) {
    this._browserPage.emit('runtimeExecutionContextCreated', {
      executionContextId: executionContext.id(),
      auxData: executionContext.auxData(),
    });
  }

  _onExecutionContextDestroyed(executionContext) {
    this._browserPage.emit('runtimeExecutionContextDestroyed', {
      executionContextId: executionContext.id(),
    });
  }

  _onWorkerCreated(worker) {
    const workerData = new WorkerData(this, this._browserChannel, worker);
    this._workerData.set(worker.id(), workerData);
    this._browserPage.emit('pageWorkerCreated', {
      workerId: worker.id(),
      frameId: worker.frame().id(),
      url: worker.url(),
    });
  }

  _onWorkerDestroyed(worker) {
    const workerData = this._workerData.get(worker.id());
    if (!workerData)
      return;
    this._workerData.delete(worker.id());
    workerData.dispose();
    this._browserPage.emit('pageWorkerDestroyed', {
      workerId: worker.id(),
    });
  }

  _onWindowOpen(subject) {
    if (!(subject instanceof Ci.nsIPropertyBag2))
      return;
    const props = subject.QueryInterface(Ci.nsIPropertyBag2);
    const hasUrl = props.hasKey('url');
    const createdDocShell = props.getPropertyAsInterface('createdTabDocShell', Ci.nsIDocShell);
    if (!hasUrl && createdDocShell === this._docShell && this._frameTree.forcePageReady())
      this._emitAllEvents(this._frameTree.mainFrame());
  }

  _linkClicked(sync, anchorElement) {
    if (anchorElement.ownerGlobal.docShell !== this._docShell)
      return;
    this._browserPage.emit('pageLinkClicked', { phase: sync ? 'after' : 'before' });
  }

  _onWindowOpenInNewContext(docShell) {
    // TODO: unify this with _onWindowOpen if possible.
    const frame = this._frameTree.frameForDocShell(docShell);
    if (!frame)
      return;
    this._browserPage.emit('pageWillOpenNewWindowAsynchronously');
  }

  _filePickerShown(inputElement) {
    const frame = this._findFrameForNode(inputElement);
    if (!frame)
      return;
    this._browserPage.emit('pageFileChooserOpened', {
      executionContextId: frame.mainExecutionContext().id(),
      element: frame.mainExecutionContext().rawValueToRemoteObject(inputElement)
    });
  }

  _findFrameForNode(node) {
    return this._frameTree.frames().find(frame => {
      const doc = frame.domWindow().document;
      return node === doc || node.ownerDocument === doc;
    });
  }

  onWindowEvent(event) {
    if (event.type !== 'DOMContentLoaded' && event.type !== 'load')
      return;
    if (!event.target.ownerGlobal)
      return;
    const docShell = event.target.ownerGlobal.docShell;
    const frame = this._frameTree.frameForDocShell(docShell);
    if (!frame)
      return;
    this._browserPage.emit('pageEventFired', {
      frameId: frame.id(),
      name: event.type,
    });
  }

  _onRuntimeError({ executionContext, message, stack, location }) {
    this._browserPage.emit('pageUncaughtError', {
      frameId: executionContext.auxData().frameId,
      message: message.toString(),
      stack: stack.toString(),
      location,
    });
  }

  _onDocumentOpenLoad(document) {
    const docShell = document.ownerGlobal.docShell;
    const frame = this._frameTree.frameForDocShell(docShell);
    if (!frame)
      return;
    this._browserPage.emit('pageEventFired', {
      frameId: frame.id(),
      name: 'load'
    });
  }

  _onNavigationStarted(frame) {
    this._browserPage.emit('pageNavigationStarted', {
      frameId: frame.id(),
      navigationId: frame.pendingNavigationId(),
    });
  }

  _onNavigationAborted(frame, navigationId, errorText) {
    this._browserPage.emit('pageNavigationAborted', {
      frameId: frame.id(),
      navigationId,
      errorText,
    });
    if (!frame._initialNavigationDone && frame !== this._frameTree.mainFrame())
      this._emitAllEvents(frame);
    frame._initialNavigationDone = true;
  }

  _onSameDocumentNavigation(frame) {
    this._browserPage.emit('pageSameDocumentNavigation', {
      frameId: frame.id(),
      url: frame.url(),
    });
  }

  _onNavigationCommitted(frame) {
    this._browserPage.emit('pageNavigationCommitted', {
      frameId: frame.id(),
      navigationId: frame.lastCommittedNavigationId() || undefined,
      url: frame.url(),
      name: frame.name(),
    });
    frame._initialNavigationDone = true;
  }

  _onFrameAttached(frame) {
    this._browserPage.emit('pageFrameAttached', {
      frameId: frame.id(),
      parentFrameId: frame.parentFrame() ? frame.parentFrame().id() : undefined,
    });
  }

  _onFrameDetached(frame) {
    this._browserPage.emit('pageFrameDetached', {
      frameId: frame.id(),
    });
  }

  _onBindingCalled({executionContextId, name, payload}) {
    this._browserPage.emit('pageBindingCalled', {
      executionContextId,
      name,
      payload
    });
  }

  dispose() {
    this._elementScreencastNode = null;
    this._elementScreencastViewport = false;
    for (const workerData of this._workerData.values())
      workerData.dispose();
    this._workerData.clear();
    helper.removeListeners(this._eventListeners);
  }

  async _adoptNode({frameId, objectId, executionContextId}) {
    const frame = this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    let unsafeObject;
    if (!objectId) {
      unsafeObject = frame.domWindow().frameElement;
    } else {
      unsafeObject = frame.unsafeObject(objectId);
    }
    const context = this._runtime.findExecutionContext(executionContextId);
    const fromPrincipal = unsafeObject.nodePrincipal;
    const toFrame = this._frameTree.frame(context.auxData().frameId);
    const toPrincipal = toFrame.domWindow().document.nodePrincipal;
    if (!toPrincipal.subsumes(fromPrincipal))
      return { remoteObject: null };
    return { remoteObject: context.rawValueToRemoteObject(unsafeObject) };
  }

  async _setFileInputFiles({objectId, frameId, files}) {
    const frame = this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    const unsafeObject = frame.unsafeObject(objectId);
    if (!unsafeObject)
      throw new Error('Object is not input!');
    let nsFiles;
    if (unsafeObject.webkitdirectory) {
      nsFiles = await new Directory(files[0]).getFiles(true);
    } else {
      nsFiles = await Promise.all(files.map(filePath => File.createFromFileName(filePath)));
    }
    unsafeObject.mozSetFileArray(nsFiles);
    const events = [
      new (frame.domWindow().Event)('input', { bubbles: true, cancelable: true, composed: true }),
      new (frame.domWindow().Event)('change', { bubbles: true, cancelable: true, composed: true }),
    ];
    for (const event of events)
      unsafeObject.dispatchEvent(event);
  }

  _getContentQuads({objectId, frameId}) {
    const frame = this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    const unsafeObject = frame.unsafeObject(objectId);
    if (!unsafeObject.getBoxQuads)
      throw new Error('RemoteObject is not a node');
    const quads = unsafeObject.getBoxQuads({relativeTo: this._frameTree.mainFrame().domWindow().document, recurseWhenNoFrame: true}).map(quad => {
      return {
        p1: {x: quad.p1.x, y: quad.p1.y},
        p2: {x: quad.p2.x, y: quad.p2.y},
        p3: {x: quad.p3.x, y: quad.p3.y},
        p4: {x: quad.p4.x, y: quad.p4.y},
      };
    });
    return {quads};
  }

  _describeNode({objectId, frameId}) {
    const frame = this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    const unsafeObject = frame.unsafeObject(objectId);
    const browsingContextGroup = frame.docShell().browsingContext.group;
    const frames = this._frameTree.allFramesInBrowsingContextGroup(browsingContextGroup);
    let contentFrame;
    let ownerFrame;
    for (const frame of frames) {
      if (unsafeObject.contentWindow && frame.docShell() === unsafeObject.contentWindow.docShell)
        contentFrame = frame;
      const document = frame.domWindow().document;
      if (unsafeObject === document || unsafeObject.ownerDocument === document)
        ownerFrame = frame;
    }
    return {
      contentFrameId: contentFrame ? contentFrame.id() : undefined,
      ownerFrameId: ownerFrame ? ownerFrame.id() : undefined,
    };
  }

  async _scrollIntoViewIfNeeded({objectId, frameId, rect}) {
    const frame = this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    const unsafeObject = frame.unsafeObject(objectId);
    if (!unsafeObject.isConnected)
      throw new Error('Node is detached from document');
    if (!rect)
      rect = { x: -1, y: -1, width: -1, height: -1};
    if (unsafeObject.scrollRectIntoViewIfNeeded)
      unsafeObject.scrollRectIntoViewIfNeeded(rect.x, rect.y, rect.width, rect.height);
    else
      throw new Error('Node does not have a layout object');
  }

  _getNodeBoundingBox(unsafeObject) {
    if (!unsafeObject.getBoxQuads)
      throw new Error('RemoteObject is not a node');
    const quads = unsafeObject.getBoxQuads({relativeTo: this._frameTree.mainFrame().domWindow().document});
    if (!quads.length)
      return;
    // Nodes can fragment into multiple layout quads. Collapse them into one
    // axis-aligned box in main-frame coordinates so the protocol has a single
    // target rectangle for scrolling and pointer planning.
    let x1 = Infinity;
    let y1 = Infinity;
    let x2 = -Infinity;
    let y2 = -Infinity;
    for (const quad of quads) {
      const boundingBox = quad.getBounds();
      x1 = Math.min(boundingBox.x, x1);
      y1 = Math.min(boundingBox.y, y1);
      x2 = Math.max(boundingBox.x + boundingBox.width, x2);
      y2 = Math.max(boundingBox.y + boundingBox.height, y2);
    }
    return {x: x1, y: y1, width: x2 - x1, height: y2 - y1};
  }

  _startElementScreencast({objectId, frameId, video = false}) {
    const viewport = !frameId && !objectId;
    const frame = viewport ? this._frameTree.mainFrame() : this._frameTree.frame(frameId);
    if (!frame)
      throw new Error('Failed to find frame with id = ' + frameId);
    const unsafeObject = viewport ? frame.domWindow().document.documentElement : frame.unsafeObject(objectId);
    if (!unsafeObject)
      throw new Error('Failed to find screencast target');
    this._elementScreencastNode = unsafeObject;
    this._elementScreencastViewport = viewport;
    if (!viewport) {
      const box = this._getNodeBoundingBox(unsafeObject);
      if (!box || box.width <= 0 || box.height <= 0)
        throw new Error('Screencast element has no visible layout box');
    }
    this._elementScreencastVideo = video;
  }

  async _captureElementScreencastFrame({width, height, fps, bitrate, codec, frameIndex}) {
    const node = this._elementScreencastNode;
    if (!node || !node.isConnected)
      throw new Error('Screencast element is detached from document');
    return {
      data: this._elementScreencastVideo
        ? await node.ownerGlobal.windowGlobalChild.encodeElementVideoFrame(
            this._elementScreencastViewport ? null : node,
            width, height, fps, bitrate, codec, frameIndex)
        : await node.ownerGlobal.windowGlobalChild.drawElementSnapshot(node),
    };
  }

  async _stopElementScreencast() {
    if (this._elementScreencastVideo && this._elementScreencastNode)
      await this._elementScreencastNode.ownerGlobal.windowGlobalChild.stopElementVideoStream();
    this._elementScreencastNode = null;
    this._elementScreencastViewport = false;
    this._elementScreencastVideo = false;
  }

  async _dispatchKeyEvent({type, keyCode, code, key, repeat, location, text}) {
    const frame = this._frameTree.mainFrame();
    const tip = frame.textInputProcessor();
    let keyEvent = new (frame.domWindow().KeyboardEvent)("", {
      key,
      code,
      location,
      repeat,
      keyCode
    });
    if (type === 'keydown') {
      if (text && text !== key) {
        await commitTextInput(frame, text, keyEvent);
      } else {
        const flags = 0;
        tip.keydown(keyEvent, flags);
      }
    } else if (type === 'keyup') {
      if (text)
        throw new Error(`keyup does not support text option`);
      const flags = 0;
      tip.keyup(keyEvent, flags);
    } else {
      throw new Error(`Unknown type ${type}`);
    }
  }

  async _dispatchTouchEvent({type, touchPoints, modifiers}) {
    const frame = this._frameTree.mainFrame();
    const defaultPrevented = frame.domWindow().windowUtils.sendTouchEvent(
      type.toLowerCase(),
      touchPoints.map((point, id) => id),
      touchPoints.map(point => point.x),
      touchPoints.map(point => point.y),
      touchPoints.map(point => point.radiusX === undefined ? 1.0 : point.radiusX),
      touchPoints.map(point => point.radiusY === undefined ? 1.0 : point.radiusY),
      touchPoints.map(point => point.rotationAngle === undefined ? 0.0 : point.rotationAngle),
      touchPoints.map(point => point.force === undefined ? 1.0 : point.force),
      touchPoints.map(point => 0),
      touchPoints.map(point => 0),
      touchPoints.map(point => 0),
      modifiers);
    return {defaultPrevented};
  }

  async _dispatchTapEvent({x, y, modifiers}) {
    // Force a layout at the point in question, because touch events
    // do not seem to trigger one like mouse events.
    this._frameTree.mainFrame().domWindow().windowUtils.elementFromPoint(
      x,
      y,
      false /* aIgnoreRootScrollFrame */,
      true /* aFlushLayout */);

    await this._dispatchTouchEvent({
      type: 'touchstart',
      modifiers,
      touchPoints: [{x, y}]
    });
    await this._dispatchTouchEvent({
      type: 'touchend',
      modifiers,
      touchPoints: [{x, y}]
    });
  }

  _getCurrentDragSession() {
    const frame = this._frameTree.mainFrame();
    const domWindow = frame?.domWindow();
    return domWindow ? dragService.getCurrentSession(domWindow) : undefined;
  }

  async _dispatchDragEvent({type, x, y, modifiers}) {
    const session = this._getCurrentDragSession();
    const dropEffect = session.dataTransfer.dropEffect;

    if ((type === 'drop' && dropEffect !== 'none') || type ===  'dragover') {
      const win = this._frameTree.mainFrame().domWindow();
      // Dragover/drop travel through the mouse synthesizer because Gecko's drag
      // pipeline expects widget mouse coordinates plus the active drag session,
      // not a plain DOM DragEvent constructed in JS.
      win.windowUtils.jugglerSendMouseEvent(
        type,
        x,
        y,
        0, /*button*/
        0, /*clickCount*/
        modifiers,
        false /*aIgnoreRootScrollFrame*/,
        0.0 /*pressure*/,
        0 /*inputSource*/,
        true /*isDOMEventSynthesized*/,
        false /*isWidgetEventSynthesized*/,
        0 /*buttons*/,
        win.windowUtils.DEFAULT_MOUSE_POINTER_ID /* pointerIdentifier */,
        false /*disablePointerEvent*/,
      );
      return;
    }
    if (type === 'dragend') {
      // The source-side dragend is session cleanup, so finish the platform drag
      // session directly after drop handling has had its chance to run.
      const session = this._getCurrentDragSession();
      session?.endDragSession(true);
      return;
    }
  }

  async _insertText({text, humanizeEnabled}) {
    const frame = this._frameTree.mainFrame();
    const shouldHumanize = typeof humanizeEnabled === "boolean" ? humanizeEnabled : isHumanizeEnabled();
    await commitTextInput(frame, text, undefined, shouldHumanize);
  }

  async _crash() {
    dump(`Crashing intentionally\n`);
    // This is to intentionally crash the frame.
    // We crash by using js-ctypes and dereferencing
    // a bad pointer. The crash should happen immediately
    // upon loading this frame script.
    const { ctypes } = ChromeUtils.importESModule('resource://gre/modules/ctypes.sys.mjs');
    ChromeUtils.privateNoteIntentionalCrash();
    const zero = new ctypes.intptr_t(8);
    const badptr = ctypes.cast(zero, ctypes.PointerType(ctypes.int32_t));
    badptr.contents;
  }

  async _getFullAXTree({objectId}) {
    let unsafeObject = null;
    if (objectId) {
      unsafeObject = this._frameTree.mainFrame().unsafeObject(objectId);
      if (!unsafeObject)
        throw new Error(`No object found for id "${objectId}"`);
    }

    const service = Cc["@mozilla.org/accessibilityService;1"]
      .getService(Ci.nsIAccessibilityService);
    const document = this._frameTree.mainFrame().domWindow().document;
    const docAcc = service.getAccessibleFor(document);

    while (docAcc.document.isUpdatePendingForJugglerAccessibility)
      await new Promise(x => this._frameTree.mainFrame().domWindow().requestAnimationFrame(x));

    async function waitForQuiet() {
      let state = {};
      docAcc.getState(state, {});
      if ((state.value & Ci.nsIAccessibleStates.STATE_BUSY) == 0)
        return;
      let resolve, reject;
      const promise = new Promise((x, y) => {resolve = x, reject = y});
      let eventObserver = {
        observe(subject, topic) {
          if (topic !== "accessible-event") {
            return;
          }

          // If event type does not match expected type, skip the event.
          let event = subject.QueryInterface(Ci.nsIAccessibleEvent);
          if (event.eventType !== Ci.nsIAccessibleEvent.EVENT_STATE_CHANGE) {
            return;
          }

          // If event's accessible does not match expected accessible,
          // skip the event.
          if (event.accessible !== docAcc) {
            return;
          }

          Services.obs.removeObserver(this, "accessible-event");
          resolve();
        },
      };
      Services.obs.addObserver(eventObserver, "accessible-event");
      return promise;
    }
    function buildNode(accElement) {
      let a = {}, b = {};
      accElement.getState(a, b);
      const tree = {
        role: service.getStringRole(accElement.role),
        name: accElement.name || '',
      };
      if (unsafeObject && unsafeObject === accElement.DOMNode)
        tree.foundObject = true;
      for (const userStringProperty of [
        'value',
        'description'
      ]) {
        tree[userStringProperty] = accElement[userStringProperty] || undefined;
      }

      const states = {};
      for (const name of service.getStringStates(a.value, b.value))
        states[name] = true;
      for (const name of ['selected',
        'focused',
        'pressed',
        'focusable',
        'required',
        'invalid',
        'modal',
        'editable',
        'busy',
        'checked',
        'multiselectable']) {
        if (states[name])
          tree[name] = true;
      }

      if (states['multi line'])
        tree['multiline'] = true;
      if (states['editable'] && states['readonly'])
        tree['readonly'] = true;
      if (states['checked'])
        tree['checked'] = true;
      if (states['mixed'])
        tree['checked'] = 'mixed';
      if (states['expanded'])
        tree['expanded'] = true;
      else if (states['collapsed'])
        tree['expanded'] = false;
      if (!states['enabled'])
        tree['disabled'] = true;

      const attributes = {};
      if (accElement.attributes) {
        for (const { key, value } of accElement.attributes.enumerate()) {
          attributes[key] = value;
        }
      }
      for (const numericalProperty of ['level']) {
        if (numericalProperty in attributes)
          tree[numericalProperty] = parseFloat(attributes[numericalProperty]);
      }
      for (const stringProperty of ['tag', 'roledescription', 'valuetext', 'orientation', 'autocomplete', 'keyshortcuts', 'haspopup']) {
        if (stringProperty in attributes)
          tree[stringProperty] = attributes[stringProperty];
      }
      const children = [];

      for (let child = accElement.firstChild; child; child = child.nextSibling) {
        children.push(buildNode(child));
      }
      if (children.length)
        tree.children = children;
      return tree;
    }
    await waitForQuiet();
    return {
      tree: buildNode(docAcc)
    };
  }
}
