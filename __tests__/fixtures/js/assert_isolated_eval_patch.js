const base = process.argv[2];
if (!base)
  throw new Error("missing Playwright driver lib path");

require(base + "/protocol/validator.js");
const { maybeFindValidator } = require(base + "/protocol/validatorPrimitives.js");
const { FrameDispatcher } = require(base + "/server/dispatchers/frameDispatcher.js");

const paramsValidator = maybeFindValidator("Frame", "rotundaEvaluateInUtility", "Params");
const resultValidator = maybeFindValidator("Frame", "rotundaEvaluateInUtility", "Result");
if (!paramsValidator || !resultValidator)
  throw new Error("missing isolated eval validator");

if (typeof FrameDispatcher.prototype.rotundaEvaluateInUtility !== "function")
  throw new Error("missing value isolated eval dispatcher");

const validated = paramsValidator(
  { expression: "() => 1", arg: { value: { n: 3 }, handles: [] } },
  "",
  { binary: "fromBase64", isUnderTest: () => false, tChannelImpl: () => null },
);
if (validated.expression !== "() => 1")
  throw new Error("validator did not preserve expression");
