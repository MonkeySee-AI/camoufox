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
const viewportVideo = validator(
  {quality: 90, sendFrames: true, record: false, video: true, fps: 60},
  "",
  {binary: "fromBase64", isUnderTest: () => false, tChannelImpl: () => null},
);
if (!viewportVideo.video || viewportVideo.fps !== 60)
  throw new Error("viewport video validator dropped native stream parameters");
if (!String(PageDispatcher.prototype.screencastStart).includes('session.send("Page.startScreencast"'))
  throw new Error("missing element screencast dispatcher path");

async function assertPlatformGate() {
  const platform = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", {...platform, value: "linux"});
  try {
    await PageDispatcher.prototype.screencastStart.call({}, {video: true});
    throw new Error("native video screencast unexpectedly allowed Linux");
  } catch (error) {
    if (!String(error).includes("supported only on macOS"))
      throw error;
  } finally {
    Object.defineProperty(process, "platform", platform);
  }
}

assertPlatformGate().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
