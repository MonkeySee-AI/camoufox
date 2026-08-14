"use strict";

const Module = require("module");
const path = require("path");

const PATCH_FLAG = Symbol.for("rotunda.elementScreencastPatch");

function normalized(filename) {
  return String(filename || "").replace(/\\/g, "/");
}

function patchValidator(validatorFilename) {
  const primitives = require(path.join(path.dirname(validatorFilename), "validatorPrimitives.js"));
  const {scheme, tBoolean, tInt, tObject, tOptional, tString} = primitives;
  scheme.PageScreencastStartParams = tObject({
    size: tOptional(tObject({width: tInt, height: tInt})),
    quality: tOptional(tInt),
    sendFrames: tOptional(tBoolean),
    record: tOptional(tBoolean),
    selector: tOptional(tString),
    fps: tOptional(tInt),
  });
  scheme.PageVideoStreamStartParams = tObject({
    size: tOptional(tObject({width: tInt, height: tInt})),
    selector: tOptional(tString),
    fps: tOptional(tInt),
    bitrate: tOptional(tInt),
    codec: tOptional(tString),
  });
  scheme.PageVideoStreamStartResult = tObject({});
  scheme.PageVideoStreamStopParams = tObject({});
  scheme.PageVideoStreamStopResult = tObject({});
}

function patchPageDispatcher(dispatcherModule) {
  const PageDispatcher = dispatcherModule && dispatcherModule.PageDispatcher;
  if (!PageDispatcher || PageDispatcher.prototype[PATCH_FLAG])
    return;

  const original = PageDispatcher.prototype.screencastStart;
  PageDispatcher.prototype.screencastStart = async function(params, progress) {
    if (!params.selector)
      return await original.call(this, params, progress);
    if (!params.sendFrames || params.record)
      throw new Error("Element screencast only supports live frame streaming");
    if (this._screencastClient || this._videoStreamClient || this._videoRecorder || this._page.screencast._clients.size)
      throw new Error("Screencast is already running");

    const element = await progress.race(
      this._page.mainFrame().querySelector(params.selector, {strict: false}),
    );
    if (!element)
      throw new Error(`Element screencast selector did not match: ${params.selector}`);

    const session = this._page.delegate && this._page.delegate._session;
    if (!session)
      throw new Error("Element screencast requires Rotunda's Firefox/Juggler backend");

    const client = {
      onFrame: frame => this._dispatchEvent("screencastFrame", {data: frame.buffer}),
      dispose() {},
    };
    this._screencastClient = client;
    // Raw add: addClient would auto-start the default viewport screencast on
    // the 0→1 transition. removeClient stays the API so teardown still flows
    // through Page.stopScreencast.
    this._page.screencast._clients.add(client);
    try {
      await session.send("Page.startScreencast", {
        fps: params.fps ?? 25,
        frameId: element._context.frame._id,
        objectId: element._objectId,
      });
    } catch (error) {
      this._screencastClient = undefined;
      this._page.screencast.removeClient(client);
      throw error;
    } finally {
      element?.dispose();
    }
    return {};
  };

  PageDispatcher.prototype.videoStreamStart = async function(params, progress) {
    if (process.platform !== "darwin")
      throw new Error("Native video streaming is currently supported only on macOS. Linux and Microsoft Windows are not supported yet; contributions are welcome.");
    if (this._screencastClient || this._videoStreamClient || this._videoRecorder || this._page.screencast._clients.size)
      throw new Error("Screencast is already running");

    const element = params.selector ? await progress.race(
      this._page.mainFrame().querySelector(params.selector, {strict: false}),
    ) : null;
    if (params.selector && !element)
      throw new Error(`Video stream selector did not match: ${params.selector}`);

    const session = this._page.delegate && this._page.delegate._session;
    if (!session)
      throw new Error("Video streaming requires Rotunda's Firefox/Juggler backend");

    const client = {
      onFrame: frame => this._dispatchEvent("screencastFrame", {data: frame.buffer}),
      dispose() {},
    };
    this._videoStreamClient = client;
    // Raw add (and raw delete in videoStreamStop): addClient would auto-start
    // the default viewport screencast, and video stream teardown flows through
    // Page.stopVideoStream instead of the screencast stop path.
    this._page.screencast._clients.add(client);
    try {
      const options = {
        fps: params.fps ?? 25,
        bitrate: params.bitrate ?? 12000000,
        codec: params.codec ?? "h264",
        width: params.size?.width,
        height: params.size?.height,
      };
      if (element) {
        options.frameId = element._context.frame._id;
        options.objectId = element._objectId;
      }
      await session.send("Page.startVideoStream", options);
    } catch (error) {
      this._videoStreamClient = undefined;
      this._page.screencast._clients.delete(client);
      throw error;
    } finally {
      element?.dispose();
    }
    return {};
  };

  PageDispatcher.prototype.videoStreamStop = async function() {
    const client = this._videoStreamClient;
    if (!client)
      return {};
    this._videoStreamClient = undefined;
    const session = this._page.delegate && this._page.delegate._session;
    try {
      if (session)
        await session.send("Page.stopVideoStream");
    } finally {
      this._page.screencast._clients.delete(client);
    }
    return {};
  };

  Object.defineProperty(PageDispatcher.prototype, PATCH_FLAG, {value: true});
}

function maybePatch(filename, moduleExports) {
  const file = normalized(filename);
  if (file.endsWith("/lib/protocol/validator.js"))
    patchValidator(filename);
  else if (file.endsWith("/lib/server/dispatchers/pageDispatcher.js"))
    patchPageDispatcher(moduleExports);
}

function tryPatch(filename, moduleExports) {
  try {
    maybePatch(filename, moduleExports);
  } catch (error) {
    // Playwright circularly loads dispatchers; a later completed load retries.
    if (!(error instanceof ReferenceError))
      throw error;
  }
}

const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {
  let filename;
  try {
    filename = Module._resolveFilename(request, parent, isMain);
  } catch (error) {
    // Let Node produce the original load error below.
  }
  const moduleExports = originalLoad.apply(this, arguments);
  if (filename)
    tryPatch(filename, moduleExports);
  return moduleExports;
};

for (const [filename, cached] of Object.entries(require.cache))
  tryPatch(filename, cached.exports);
