/* Stream Manager replies over the trial's authenticated browser WebSocket. */
(() => {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const request = new Request(input, options);
    const url = new URL(request.url);
    if (url.origin !== location.origin || request.method !== 'POST' || !/^\/api\/projects\/[a-zA-Z0-9_-]+\/message\/stream$/.test(url.pathname)) {
      return originalFetch(request);
    }
    const body = new Uint8Array(await request.arrayBuffer());
    if (body.length > 256 * 1024 * 1024) return new Response(JSON.stringify({ detail: '这条消息超过 256 MB，发不出去。大文件请放进项目目录，或让 Argus 从网址下载。' }), { status: 413 });
    let encoded = '';
    for (let i = 0; i < body.length; i += 16384) encoded += String.fromCharCode(...body.subarray(i, i + 16384));
    const endpoint = new URL('/trial/stream', location.href);
    endpoint.protocol = 'wss:';
    const socket = new WebSocket(endpoint);
    socket.binaryType = 'arraybuffer';
    let controller, resolveStart, rejectStart;
    let ended = false, started = false;
    const ready = new Promise((resolve, reject) => { resolveStart = resolve; rejectStart = reject; });
    const stream = new ReadableStream({
      start(value) { controller = value; },
      cancel() { ended = true; clearTimeout(timer); socket.close(); },
    });
    function fail() {
      if (ended) return;
      ended = true; clearTimeout(timer);
      const error = new Error('和工作区的连接断了，这条消息可能没发出去。请刷新页面看一下，没有的话再发一次。');
      if (!started) rejectStart(error);
      controller.error(error);
      socket.close();
    }
    const timer = setTimeout(fail, 120000);
    socket.onopen = () => socket.send(JSON.stringify({ method: 'POST', path: url.pathname, body: btoa(encoded) }));
    socket.onmessage = event => {
      if (ended) return;
      try {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data);
          if (message.type === 'start') { started = true; clearTimeout(timer); resolveStart(message); }
          if (message.type === 'end') { ended = true; controller.close(); socket.close(); }
        } else {
          if (controller.desiredSize < -256) return fail();
          controller.enqueue(new Uint8Array(event.data));
        }
      } catch { fail(); }
    };
    socket.onerror = fail;
    socket.onclose = () => { if (!ended) fail(); };
    request.signal.addEventListener('abort', fail, { once: true });
    if (request.signal.aborted) fail();
    const start = await ready;
    return new Response(stream, { status: start.status, headers: start.headers });
  };
})();
