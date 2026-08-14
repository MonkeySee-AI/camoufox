const base = process.argv[2];
if (!base)
  throw new Error("missing Playwright driver lib path");

require(base + "/protocol/validator.js");
require(base + "/server/index.js");
const {maybeFindValidator} = require(base + "/protocol/validatorPrimitives.js");
const {PageDispatcher} = require(base + "/server/dispatchers/pageDispatcher.js");

const context = {binary: "fromBase64", isUnderTest: () => false, tChannelImpl: () => null};

const screencastValidator = maybeFindValidator("Page", "screencastStart", "Params");
const screencast = screencastValidator(
  {quality: 91, sendFrames: true, record: false, selector: "#target", fps: 37},
  "",
  context,
);
if (screencast.selector !== "#target" || screencast.fps !== 37)
  throw new Error("element screencast validator dropped selector parameters");

const videoValidator = maybeFindValidator("Page", "videoStreamStart", "Params");
if (!videoValidator)
  throw new Error("missing videoStreamStart validator scheme");
const video = videoValidator(
  {size: {width: 3840, height: 2160}, selector: "#target", fps: 60, bitrate: 35000000, codec: "h265"},
  "",
  context,
);
if (video.codec !== "h265" || video.bitrate !== 35000000 || video.fps !== 60)
  throw new Error("video stream validator dropped encoder parameters");
if (!maybeFindValidator("Page", "videoStreamStop", "Params"))
  throw new Error("missing videoStreamStop validator scheme");

if (!String(PageDispatcher.prototype.screencastStart).includes('session.send("Page.startScreencast"'))
  throw new Error("missing element screencast dispatcher path");
if (!String(PageDispatcher.prototype.videoStreamStart).includes('session.send("Page.startVideoStream"'))
  throw new Error("missing video stream dispatcher path");
if (!String(PageDispatcher.prototype.videoStreamStop).includes('session.send("Page.stopVideoStream"'))
  throw new Error("missing video stream stop dispatcher path");

async function assertPlatformGate() {
  const platform = Object.getOwnPropertyDescriptor(process, "platform");
  Object.defineProperty(process, "platform", {...platform, value: "linux"});
  try {
    await PageDispatcher.prototype.videoStreamStart.call({}, {});
    throw new Error("native video streaming unexpectedly allowed Linux");
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
