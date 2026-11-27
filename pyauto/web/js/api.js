// 后端接口封装：只走本地同源的 /api。

const BASE = "";

async function request(path, { method = "GET", body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(BASE + path, init);
  } catch (err) {
    throw new Error("连不上本地服务：确认 pyauto web 还在跑");
  }

  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    data = {};
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

export const api = {
  meta: () => request("/api/meta"),
  loadConfig: (path) => request(`/api/config${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  saveConfig: (config, path) => request("/api/config", { method: "POST", body: { config, path } }),
  preview: (config) => request("/api/preview", { method: "POST", body: { config } }),
  run: (config, dryRun = false) => request("/api/run", { method: "POST", body: { config, dry_run: dryRun } }),
  stop: () => request("/api/stop", { method: "POST", body: {} }),
  state: () => request("/api/state"),
  screenshot: (box) => request("/api/screenshot", { method: "POST", body: { box } }),
  templates: () => request("/api/templates"),
  files: (path) => request(`/api/files${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  newFile: (name) => request("/api/file/new", { method: "POST", body: { name } }),
  combos: () => request("/api/combos"),
  saveCombo: (name, tasks) => request("/api/combos", { method: "POST", body: { name, tasks } }),
  deleteCombo: (name) => request("/api/combos/delete", { method: "POST", body: { name } }),
  grabTemplate: (box, name) => request("/api/template", { method: "POST", body: { box, name } }),
  uploadTemplate: (name, data) => request("/api/template/upload", { method: "POST", body: { name, data } }),
  ocr: (payload) => request("/api/ocr", { method: "POST", body: payload }),
};

/**
 * 订阅运行事件（SSE），断线由浏览器自动重连。
 * @param {{since?: number, onEvent: (e: object) => void, onOpen?: () => void, onError?: () => void}} opts
 */
export function subscribeEvents({ since = 0, onEvent, onOpen, onError }) {
  const source = new EventSource(`${BASE}/api/events?since=${since}`);
  source.onopen = () => onOpen && onOpen();
  source.onerror = () => onError && onError();
  source.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data));
    } catch (err) {
      /* 单条坏数据不该打断整个日志流 */
    }
  };
  return source;
}
