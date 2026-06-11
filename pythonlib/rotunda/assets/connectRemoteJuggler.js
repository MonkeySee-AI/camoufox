// Copyright (c) 2026 Pierce Freeman.

const fs = require('fs/promises');
const http = require('http');
const https = require('https');
const os = require('os');
const path = require('path');
const { randomUUID } = require('crypto');

const packageRoot = process.env.PLAYWRIGHT_PACKAGE_ROOT || process.cwd();

function requireFromPackage(relativePath) {
  return require(path.join(packageRoot, relativePath));
}

function debugProtocolLoggerFrom(debugLogger) {
  return protocolLogger => (direction, message) => {
    if (protocolLogger)
      protocolLogger(direction, message);
    if (!debugLogger?.isEnabled?.('protocol'))
      return;
    let text = JSON.stringify(message);
    if (text.length > 5120)
      text = `${text.substring(0, 2560)} <<<<<( LOG TRUNCATED )>>>>> ${text.substring(text.length - 2560)}`;
    debugLogger.log('protocol', `${direction === 'send' ? 'SEND' : 'RECV'} ${text}`);
  };
}

function loadPlaywrightInternals() {
  let legacyError;
  try {
    const { PlaywrightServer } = requireFromPackage('lib/remote/playwrightServer.js');
    const { createPlaywright } = requireFromPackage('lib/server/playwright.js');
    const { helper } = requireFromPackage('lib/server/helper.js');
    const { WebSocketTransport } = requireFromPackage('lib/server/transport.js');
    const { RecentLogsCollector } = requireFromPackage('lib/server/utils/debugLogger.js');
    const { removeFolders } = requireFromPackage('lib/server/utils/fileUtils.js');
    return {
      PlaywrightServer,
      createPlaywright,
      debugProtocolLogger: helper.debugProtocolLogger,
      WebSocketTransport,
      RecentLogsCollector,
      removeFolders,
    };
  } catch (error) {
    legacyError = error;
  }

  try {
    const coreBundle = requireFromPackage('lib/coreBundle.js');
    const { remote, server, utils } = coreBundle;
    const internals = {
      PlaywrightServer: remote?.PlaywrightServer,
      createPlaywright: server?.createPlaywright,
      debugProtocolLogger: debugProtocolLoggerFrom(utils?.debugLogger),
      WebSocketTransport: server?.WebSocketTransport,
      RecentLogsCollector: utils?.RecentLogsCollector,
      removeFolders: utils?.removeFolders,
    };
    for (const [name, value] of Object.entries(internals)) {
      if (!value)
        throw new Error(`Playwright coreBundle is missing ${name}`);
    }
    return internals;
  } catch (bundleError) {
    bundleError.message = [
      `Unable to load Playwright internals from ${packageRoot}.`,
      `Legacy layout error: ${legacyError?.message || legacyError}.`,
      `Bundled layout error: ${bundleError.message}.`,
    ].join(' ');
    throw bundleError;
  }
}

const {
  PlaywrightServer,
  createPlaywright,
  debugProtocolLogger,
  WebSocketTransport,
  RecentLogsCollector,
  removeFolders,
} = loadPlaywrightInternals();

function collectData() {
  return new Promise((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => data += chunk);
    process.stdin.on('end', () => {
      resolve(JSON.parse(Buffer.from(data, 'base64').toString()));
    });
  });
}

function normalizeEndpoint(endpoint) {
  if (!endpoint)
    throw new Error('Missing remote Juggler endpoint');
  if (/^(http|https|ws|wss):\/\//.test(endpoint))
    return endpoint;
  return `http://${endpoint}`;
}

function readJson(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const client = parsed.protocol === 'https:' ? https : http;
    const request = client.get(parsed, { headers }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`Unexpected status ${response.statusCode} from ${url}: ${body}`));
          return;
        }
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(new Error(`Invalid JSON from ${url}: ${error.message}`));
        }
      });
    });
    request.on('error', reject);
  });
}

async function jugglerWSEndpoint(endpoint, headers = {}) {
  endpoint = normalizeEndpoint(endpoint);
  if (endpoint.startsWith('ws://') || endpoint.startsWith('wss://'))
    return endpoint;

  const versionURL = new URL(endpoint);
  if (versionURL.pathname === '' || versionURL.pathname === '/') {
    versionURL.pathname = '/json/version';
  } else if (!versionURL.pathname.endsWith('/json/version')) {
    if (!versionURL.pathname.endsWith('/'))
      versionURL.pathname += '/';
    versionURL.pathname += 'json/version';
  }
  const version = await readJson(versionURL.toString(), headers);
  if (!version.webSocketDebuggerUrl)
    throw new Error(`Remote Juggler endpoint did not expose webSocketDebuggerUrl at ${versionURL}`);
  return version.webSocketDebuggerUrl;
}

async function closeTransport(transport) {
  if (!transport)
    return;
  if (transport.closeAndWait)
    await transport.closeAndWait().catch(() => {});
  else if (transport.close)
    transport.close();
}

async function main() {
  const options = await collectData();
  const artifactsDir = await fs.mkdtemp(path.join(os.tmpdir(), 'rotunda-remote-juggler-'));
  let transport;
  let server;
  let browser;
  let closingBrowser = false;

  const closeBrowser = async () => {
    if (closingBrowser)
      return;
    closingBrowser = true;
    if (browser?.isConnected()) {
      await browser.session.send('Browser.close').catch(() => {});
    }
    await closeTransport(transport);
  };

  const cleanup = async () => {
    await server?.close().catch(() => {});
    await closeBrowser();
    await removeFolders([artifactsDir]).catch(() => {});
  };

  process.once('SIGINT', () => cleanup().finally(() => process.exit(130)));
  process.once('SIGTERM', () => cleanup().finally(() => process.exit(143)));
  process.once('SIGHUP', () => cleanup().finally(() => process.exit(129)));

  const jugglerEndpoint = await jugglerWSEndpoint(options.endpoint, options.headers || {});
  transport = await WebSocketTransport.connect(null, jugglerEndpoint, {
    headers: options.headers || {},
    followRedirects: true,
  });

  const playwright = createPlaywright({ sdkLanguage: 'javascript', isServer: true });
  const browserProcess = {
    onclose: undefined,
    process: undefined,
    close: closeBrowser,
    kill: closeBrowser,
  };
  const browserOptions = {
    name: 'firefox',
    browserType: 'firefox',
    // Playwright <= 1.58 reads this private BrowserOptions field.
    isChromium: false,
    slowMo: options.slowMo || 0,
    persistent: options.attachToDefaultContext === false ? undefined : { noDefaultViewport: true },
    headful: true,
    artifactsDir,
    downloadsPath: options.downloadsPath || artifactsDir,
    tracesDir: options.tracesDir || artifactsDir,
    browserProcess,
    proxy: undefined,
    protocolLogger: debugProtocolLogger(),
    browserLogsCollector: new RecentLogsCollector(),
    originalLaunchOptions: {
      firefoxUserPrefs: options.firefoxUserPrefs || {},
    },
  };
  browser = await playwright.firefox.connectToTransport(transport, browserOptions);

  const wsPath = options.wsPath
    ? (options.wsPath.startsWith('/') ? options.wsPath : `/${options.wsPath}`)
    : `/${randomUUID()}`;
  server = new PlaywrightServer({
    mode: 'launchServer',
    path: wsPath,
    maxConnections: Infinity,
    preLaunchedBrowser: browser,
  });
  const wsEndpoint = await server.listen(options.serverPort || 0, options.serverHost || '127.0.0.1');
  console.log(JSON.stringify({ wsEndpoint, jugglerEndpoint }));
  process.stdin.resume();
}

main().catch(error => {
  console.error(error.stack || error.message);
  process.exit(1);
});
