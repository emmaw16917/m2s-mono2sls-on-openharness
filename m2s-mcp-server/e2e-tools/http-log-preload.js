// Logs every non-2xx HTTP response (and every request error) the E2E suite's
// axios sees, to the file in E2E_HTTP_LOG -- without editing the suite.
// Loaded with NODE_OPTIONS="--require <this file>".
const fs = require('fs');
const Module = require('module');

const target = process.env.E2E_HTTP_LOG;
const origLoad = Module._load;
const hooked = new WeakSet();

function write(entry) {
  if (!target) return;
  fs.appendFileSync(target, JSON.stringify({ t: new Date().toISOString(), pid: process.pid, ...entry }) + '\n');
}

function hook(axios) {
  if (!axios || hooked.has(axios) || !axios.interceptors) return;
  hooked.add(axios);
  axios.interceptors.response.use(
    (res) => {
      if (res.status >= 400) {
        write({
          method: res.config && res.config.method, url: res.config && res.config.url, status: res.status,
          headers: Object.fromEntries(Object.entries(res.headers || {}).filter(([k]) => /^x-amz|^content-type|^via|^server/i.test(k))),
          body: typeof res.data === 'string' ? res.data.slice(0, 500) : JSON.stringify(res.data).slice(0, 500),
          proxy: res.config && res.config.proxy,
        });
      }
      return res;
    },
    (err) => {
      write({ method: err.config && err.config.method, url: err.config && err.config.url, error: err.code || err.message,
              status: err.response && err.response.status });
      return Promise.reject(err);
    },
  );
  const create = axios.create;
  axios.create = function (...args) { const inst = create.apply(this, args); hook(inst); return inst; };
}

Module._load = function (request, parent, isMain) {
  const mod = origLoad.apply(this, arguments);
  if (request === 'axios') {
    hook(mod);
    if (mod && mod.default) hook(mod.default);
  }
  return mod;
};
