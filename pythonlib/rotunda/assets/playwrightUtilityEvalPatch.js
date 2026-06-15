"use strict";

const Module = require("module");
const path = require("path");

const PATCH_FLAG = Symbol.for("rotunda.utilityEvalPatch");

function normalized(filename) {
  return String(filename || "").replace(/\\/g, "/");
}

function patchValidator(validatorFilename) {
  const primitives = require(path.join(path.dirname(validatorFilename), "validatorPrimitives.js"));
  const { scheme, tBoolean, tObject, tOptional, tString, tType } = primitives;
  if (scheme.FrameRotundaEvaluateInUtilityParams)
    return;

  const evaluateParams = () => tObject({
    expression: tString,
    isFunction: tOptional(tBoolean),
    arg: tType("SerializedArgument"),
  });

  scheme.FrameRotundaEvaluateInUtilityParams = evaluateParams();
  scheme.FrameRotundaEvaluateInUtilityResult = tObject({
    value: tType("SerializedValue"),
  });
}

function patchFrameDispatcher(dispatcherFilename, dispatcherModule) {
  const FrameDispatcher = dispatcherModule && dispatcherModule.FrameDispatcher;
  if (!FrameDispatcher || FrameDispatcher.prototype[PATCH_FLAG])
    return;

  const dispatcherDir = path.dirname(dispatcherFilename);
  const { parseArgument, serializeResult } = require(path.join(dispatcherDir, "jsHandleDispatcher.js"));

  FrameDispatcher.prototype.rotundaEvaluateInUtility = async function(params, progress) {
    const value = await progress.race(
      this._frame.evaluateExpression(
        params.expression,
        { isFunction: params.isFunction, world: "utility" },
        parseArgument(params.arg),
      ),
    );
    return { value: serializeResult(value) };
  };

  Object.defineProperty(FrameDispatcher.prototype, PATCH_FLAG, { value: true });
}

function maybePatch(filename, moduleExports) {
  const file = normalized(filename);
  if (file.endsWith("/lib/protocol/validator.js"))
    patchValidator(filename);
  else if (file.endsWith("/lib/server/dispatchers/frameDispatcher.js"))
    patchFrameDispatcher(filename, moduleExports);
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
      if (process.env.ROTUNDA_UTILITY_EVAL_PATCH_DEBUG)
        console.error("[rotunda] failed to patch Playwright utility eval:", error && error.stack || error);
    }
  }
  return moduleExports;
};

for (const [filename, cached] of Object.entries(require.cache)) {
  try {
    maybePatch(filename, cached.exports);
  } catch (error) {
    if (process.env.ROTUNDA_UTILITY_EVAL_PATCH_DEBUG)
      console.error("[rotunda] failed to patch cached Playwright module:", error && error.stack || error);
  }
}
