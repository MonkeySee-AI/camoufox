/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// Services is available as a global in XPCOM component context

// Load SimpleChannel in browser-process global.
Services.scriptloader.loadSubScript('chrome://juggler/content/SimpleChannel.js');
const {Dispatcher} = ChromeUtils.importESModule("chrome://juggler/content/protocol/Dispatcher.js");
const {BrowserHandler} = ChromeUtils.importESModule("chrome://juggler/content/protocol/BrowserHandler.js");
const {NetworkObserver} = ChromeUtils.importESModule("chrome://juggler/content/NetworkObserver.js");
const {TargetRegistry} = ChromeUtils.importESModule("chrome://juggler/content/TargetRegistry.js");
const {Helper} = ChromeUtils.importESModule('chrome://juggler/content/Helper.js');
const {ActorManagerParent} = ChromeUtils.importESModule('resource://gre/modules/ActorManagerParent.sys.mjs');
const helper = new Helper();

const Cc = Components.classes;
const Ci = Components.interfaces;

const lazy = {};

ChromeUtils.defineESModuleGetters(lazy, {
  executeSoon: "chrome://remote/content/shared/Sync.sys.mjs",
  HttpServer: "chrome://remote/content/server/httpd.sys.mjs",
});

ChromeUtils.defineLazyGetter(lazy, "threadManager", () => {
  return Cc["@mozilla.org/thread-manager;1"].getService();
});

const DEFAULT_HOST = "localhost";
// RFC 6455 WebSocket handshake GUID used when deriving Sec-WebSocket-Accept
// from the client-provided Sec-WebSocket-Key.
const WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

function writeString(output, data) {
  return new Promise((resolve, reject) => {
    const wait = () => {
      if (data.length === 0) {
        resolve();
        return;
      }

      output.asyncWait(
        () => {
          try {
            const written = output.write(data, data.length);
            data = data.slice(written);
            wait();
          } catch (ex) {
            reject(ex);
          }
        },
        0,
        0,
        lazy.threadManager.currentThread
      );
    };
    wait();
  });
}

async function writeHttpResponse(output, headers, body = "") {
  const response = `${headers.join("\r\n")}\r\n\r\n${body}`;
  await writeString(output, response);
}

function computeWebSocketAcceptKey(key) {
  const hash = Cc["@mozilla.org/security/hash;1"].createInstance(Ci.nsICryptoHash);
  hash.init(Ci.nsICryptoHash.SHA1);
  const bytes = Array.from(`${key}${WEBSOCKET_GUID}`, ch => ch.charCodeAt(0));
  hash.update(bytes, bytes.length);
  return hash.finish(true);
}

async function createWebSocket(transport, input, output) {
  const transportProvider = {
    setListener(upgradeListener) {
      lazy.executeSoon(() => {
        upgradeListener.onTransportAvailable(transport, input, output);
      });
    },
  };

  return new Promise((resolve, reject) => {
    const socket = WebSocket.createServerWebSocket(
      null,
      [],
      transportProvider,
      ""
    );
    socket.addEventListener("close", () => {
      input.close();
      output.close();
    });

    socket.onopen = () => resolve(socket);
    socket.onerror = err => reject(err);
  });
}

// Register JSWindowActors that will be instantiated for each frame.
ActorManagerParent.addJSWindowActors({
  JugglerFrame: {
    parent: {
      esModuleURI: 'chrome://juggler/content/JugglerFrameParent.sys.mjs',
    },
    child: {
      esModuleURI: 'chrome://juggler/content/JugglerFrameChild.sys.mjs',
      events: {
        // Normally, we instantiate an actor when a new window is created.
        DOMWindowCreated: {},
        // However, for same-origin iframes, the navigation from about:blank
        // to the URL will share the same window, so we need to also create
        // an actor for a new document via DOMDocElementInserted.
        DOMDocElementInserted: {},
        // Also, listening to DOMContentLoaded.
        DOMContentLoaded: {},
        DOMWillOpenModalDialog: {},
        DOMModalDialogClosed: {},
      },
    },
    allFrames: true,
  },
});

let browserStartupFinishedCallback;
let browserStartupFinishedPromise = new Promise(x => browserStartupFinishedCallback = x);

export class Juggler {
  constructor() {
    this._pipeRequested = false;
    this._requestedPort = null;
    this._port = null;
    this._server = null;
    this._wsPath = null;
    this._activeSocketConnection = null;
  }

  get classDescription() { return "Sample command-line handler"; }
  get classID() { return Components.ID('{f7a74a33-e2ab-422d-b022-4fb213dd2639}'); }
  get contractID() { return "@mozilla.org/remote/juggler;1" }
  get QueryInterface() {
    return ChromeUtils.generateQI([ Ci.nsICommandLineHandler, Ci.nsIObserver ]);
  }
  get helpInfo() {
    return "  --juggler-pipe       Enable Juggler automation over inherited pipe fds\n" +
      "  --juggler-port <port> Enable Juggler automation over an HTTP/WebSocket port\n";
  }

  handle(cmdLine) {
    // flag has to be consumed in nsICommandLineHandler:handle
    // to avoid issues on macos. See Marionette.jsm::handle() for more details.
    // TODO: remove after Bug 1724251 is fixed.
    this._pipeRequested = cmdLine.handleFlag("juggler-pipe", false) || this._pipeRequested;
    const port = this._handleJugglerPortFlag(cmdLine);
    if (port !== null)
      this._requestedPort = port;
  }

  _handleJugglerPortFlag(cmdLine) {
    let port = null;
    try {
      port = cmdLine.handleFlagWithParam("juggler-port", false);
    } catch (e) {
      throw new Error("--juggler-port requires a port value");
    }
    return port === null ? null : parsePort(port);
  }

  // This flow is taken from Remote agent and Marionette.
  // See https://github.com/mozilla-firefox/firefox/blob/35e22180b0b61413dd8eccf6c00b1c6fac073eee/remote/components/RemoteAgent.sys.mjs#L417
  async observe(subject, topic) {
    switch (topic) {
      case "profile-after-change":
        Services.obs.addObserver(this, "command-line-startup");
        Services.obs.addObserver(this, "browser-idle-startup-tasks-finished");
        break;
      case "command-line-startup":
        Services.obs.removeObserver(this, topic);
        const cmdLine = subject;
        this._pipeRequested = cmdLine.handleFlag('juggler-pipe', false) || this._pipeRequested;
        const port = this._handleJugglerPortFlag(cmdLine);
        if (port !== null)
          this._requestedPort = port;
        if (!this._pipeRequested && this._requestedPort === null)
          return;
        if (this._pipeRequested && this._requestedPort !== null)
          throw new Error("Use only one of --juggler-pipe or --juggler-port");

        this._silent = cmdLine.findFlag('silent', false) >= 0;
        if (this._silent) {
          Services.startup.enterLastWindowClosingSurvivalArea();
          browserStartupFinishedCallback();
        }
        Services.obs.addObserver(this, "final-ui-startup");
        break;
      case "browser-idle-startup-tasks-finished":
        browserStartupFinishedCallback();
        break;
      // Used to wait until the initial application window has been opened.
      case "final-ui-startup":
        Services.obs.removeObserver(this, topic);

        const targetRegistry = new TargetRegistry();
        new NetworkObserver(targetRegistry);

        const loadStyleSheet = () => {
          if (Cc["@mozilla.org/gfx/info;1"].getService(Ci.nsIGfxInfo).isHeadless) {
            const styleSheetService = Cc["@mozilla.org/content/style-sheet-service;1"].getService(Components.interfaces.nsIStyleSheetService);
            const ioService = Cc["@mozilla.org/network/io-service;1"].getService(Components.interfaces.nsIIOService);
            const uri = ioService.newURI('chrome://juggler/content/content/hidden-scrollbars.css', null, null);
            styleSheetService.loadAndRegisterSheet(uri, styleSheetService.AGENT_SHEET);
          }
        };

        // Force create hidden window here, otherwise its creation later closes the web socket!
        // Since https://phabricator.services.mozilla.com/D219834, hiddenDOMWindow is only available on MacOS.
        if (Services.appShell.hasHiddenWindow) {
          Services.appShell.hiddenDOMWindow;
        }

        loadStyleSheet();
        if (this._pipeRequested)
          this._startPipe(targetRegistry);
        else
          await this._startPort(targetRegistry, this._requestedPort);
        break;
    }
  }

  _createBrowserHandler(connection, targetRegistry, onclose) {
    const dispatcher = new Dispatcher(connection);
    const browserHandler = new BrowserHandler(
      dispatcher.rootSession(),
      dispatcher,
      targetRegistry,
      browserStartupFinishedPromise,
      onclose
    );
    dispatcher.rootSession().setHandler(browserHandler);
    return browserHandler;
  }

  _startPipe(targetRegistry) {
    let pipeStopped = false;
    let browserHandler;
    const pipe = Cc['@mozilla.org/juggler/remotedebuggingpipe;1'].getService(Ci.nsIRemoteDebuggingPipe);
    const connection = {
      QueryInterface: ChromeUtils.generateQI([Ci.nsIRemoteDebuggingPipeClient]),
      receiveMessage(message) {
        if (this.onmessage)
          this.onmessage({ data: message });
      },
      disconnected() {
        if (browserHandler)
          browserHandler['Browser.close']();
      },
      send(message) {
        if (pipeStopped) {
          // We are missing the response to Browser.close,
          // but everything works fine. Once we actually need it,
          // we have to stop the pipe after the response is sent.
          return;
        }
        pipe.sendMessage(message);
      },
    };
    pipe.init(connection);
    browserHandler = this._createBrowserHandler(connection, targetRegistry, () => {
      if (this._silent)
        Services.startup.exitLastWindowClosingSurvivalArea();
      connection.onclose();
      pipe.stop();
      pipeStopped = true;
    });
    dump(`\nJuggler listening to the pipe\n`);
  }

  async _startPort(targetRegistry, requestedPort) {
    this._server = new lazy.HttpServer();
    this._wsPath = `/devtools/browser/${helper.generateId()}`;

    const versionHandler = (request, response) => {
      response.setHeader("Content-Type", "application/json; charset=utf-8", false);
      response.write(JSON.stringify(this._versionPayload()));
    };
    const listHandler = (request, response) => {
      response.setHeader("Content-Type", "application/json; charset=utf-8", false);
      response.write(JSON.stringify([{
        id: "juggler",
        type: "browser",
        title: "Rotunda Juggler",
        webSocketDebuggerUrl: this._wsEndpoint(),
      }]));
    };

    this._server.registerPathHandler("/json/version", versionHandler);
    this._server.registerPathHandler("/json", listHandler);
    this._server.registerPathHandler("/json/list", listHandler);
    this._server.registerPathHandler(this._wsPath, async (request, response) => {
      await this._acceptWebSocket(request, response, targetRegistry);
    });

    this._server._start(requestedPort === 0 ? -1 : requestedPort, DEFAULT_HOST);
    this._port = this._server._port;
    dump(`\nJuggler listening on ${this._wsEndpoint()}\n`);
  }

  _wsEndpoint() {
    return `ws://${DEFAULT_HOST}:${this._port}${this._wsPath}`;
  }

  _versionPayload() {
    return {
      Browser: "Rotunda/Juggler",
      "Protocol-Version": "1.0",
      "User-Agent": Services.appinfo.name,
      webSocketDebuggerUrl: this._wsEndpoint(),
    };
  }

  _isAllowedWebSocketHost(hostHeader) {
    return [
      `${DEFAULT_HOST}:${this._port}`,
      `127.0.0.1:${this._port}`,
      `[::1]:${this._port}`,
    ].includes(hostHeader);
  }

  _validateWebSocketRequest(request, headers) {
    if (request.method !== "GET")
      throw new Error("The handshake request must use GET method");

    const host = headers.get("host");
    if (!this._isAllowedWebSocketHost(host))
      throw new Error(`The handshake request has incorrect Host header ${host}`);

    const upgrade = headers.get("upgrade");
    if (!upgrade || upgrade.toLowerCase() !== "websocket")
      throw new Error(`The handshake request has incorrect Upgrade header: ${upgrade}`);

    const connection = headers.get("connection");
    if (!connection || !connection.split(",").map(t => t.trim().toLowerCase()).includes("upgrade"))
      throw new Error("The handshake request has incorrect Connection header");

    const version = headers.get("sec-websocket-version");
    if (!version || version !== "13")
      throw new Error("The handshake request must have Sec-WebSocket-Version: 13");

    const key = headers.get("sec-websocket-key");
    if (!key)
      throw new Error("The handshake request must have a Sec-WebSocket-Key header");

    return key;
  }

  async _upgradeWebSocket(request, response) {
    const headers = new Map();
    for (const [key, values] of Object.entries(request._headers._headers))
      headers.set(key.toLowerCase(), values.join("\n"));

    let acceptKey;
    try {
      acceptKey = computeWebSocketAcceptKey(this._validateWebSocketRequest(request, headers));
    } catch (e) {
      response.setStatusLine(request.httpVersion, 400, "Bad Request");
      response.setHeader("Content-Type", "text/plain; charset=utf-8", false);
      response.write(e.message);
      throw e;
    }

    response._powerSeized = true;
    const { transport, input, output } = response._connection;
    await writeHttpResponse(output, [
      "HTTP/1.1 101 Switching Protocols",
      "Server: httpd.js",
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Accept: ${acceptKey}`,
    ]);

    return createWebSocket(transport, input, output);
  }

  async _acceptWebSocket(request, response, targetRegistry) {
    if (this._activeSocketConnection) {
      response.setStatusLine(request.httpVersion, 409, "Conflict");
      response.setHeader("Content-Type", "text/plain; charset=utf-8", false);
      response.write("Juggler already has an active WebSocket connection.");
      return;
    }

    const webSocket = await this._upgradeWebSocket(request, response);
    let closed = false;
    let browserHandler;
    const httpdConnection = response._connection;
    const closeConnection = () => {
      if (closed)
        return;
      closed = true;
      if (connection.onclose)
        connection.onclose();
      this._activeSocketConnection = null;
      browserHandler = null;
      try {
        httpdConnection.close();
      } catch (e) {
      }
    };
    const connection = {
      receiveMessage(message) {
        if (this.onmessage)
          this.onmessage({ data: message });
      },
      send(message) {
        if (!closed)
          webSocket.send(message);
      },
    };

    webSocket.addEventListener("message", event => connection.receiveMessage(event.data));
    webSocket.addEventListener("close", closeConnection);
    webSocket.addEventListener("error", closeConnection);

    this._activeSocketConnection = connection;
    browserHandler = this._createBrowserHandler(connection, targetRegistry, () => {
      closeConnection();
      try {
        webSocket.close(1000);
      } catch (e) {
      }
    });
  }

}

function parsePort(rawPort) {
  const port = Number(rawPort);
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    throw new Error(`Invalid --juggler-port value: ${rawPort}`);
  return port;
}

const jugglerInstance = new Juggler();

// This is used by the XPCOM codepath which expects a constructor
export var JugglerFactory = function() {
  return jugglerInstance;
};
