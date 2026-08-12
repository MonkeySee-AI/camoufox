const base = process.argv[2];
if (!base)
  throw new Error("missing Playwright driver lib path");

require(base + "/protocol/validator.js");
require(base + "/server/index.js");
const {maybeFindValidator} = require(base + "/protocol/validatorPrimitives.js");
const {PageDispatcher} = require(base + "/server/dispatchers/pageDispatcher.js");

const validator = maybeFindValidator("Page", "screencastStart", "Params");
const validated = validator(
  {quality: 91, sendFrames: true, record: false, selector: "#target", fps: 37},
  "",
  {binary: "fromBase64", isUnderTest: () => false, tChannelImpl: () => null},
);
if (validated.selector !== "#target" || validated.fps !== 37)
  throw new Error("element screencast validator dropped selector parameters");
if (!String(PageDispatcher.prototype.screencastStart).includes('session.send("Page.startScreencast"'))
  throw new Error("missing element screencast dispatcher path");
