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
    video: tOptional(tBoolean),
    bitrate: tOptional(tInt),
  });
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
    if (this._screencastClient || this._videoRecorder || this._page.screencast._clients.size)
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
      quality: params.quality,
    };
    this._screencastClient = client;
    this._page.screencast._clients.add(client);
    try {
      await session.send("Page.startScreencast", {
        quality: params.quality ?? 90,
        fps: params.fps ?? 25,
        video: params.video ?? false,
        bitrate: params.bitrate ?? 12000000,
        width: params.size?.width,
        height: params.size?.height,
        frameId: element._context.frame._id,
        objectId: element._objectId,
      });
    } catch (error) {
      this._screencastClient = undefined;
      this._page.screencast.removeClient(client);
      throw error;
    } finally {
      element.dispose();
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

const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {
  let filename;
  try {
    filename = Module._resolveFilename(request, parent, isMain);
  } catch (error) {
    // Let Node produce the original load error below.
  }
  const moduleExports = originalLoad.apply(this, arguments);
  if (filename) {
    try {
      maybePatch(filename, moduleExports);
    } catch (error) {
      // Playwright circularly loads dispatchers; the completed outer load retries.
      if (!(error instanceof ReferenceError))
        throw error;
    }
  }
  return moduleExports;
};

for (const [filename, cached] of Object.entries(require.cache)) {
  try {
    maybePatch(filename, cached.exports);
  } catch (error) {
    // A later completed module load retries the patch.
    if (!(error instanceof ReferenceError))
      throw error;
  }
}
