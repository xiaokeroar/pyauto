// 极小的 DOM 助手：不引框架，避免依赖和构建步骤。

/**
 * 创建元素。
 * @param {string} tag 形如 "div" 或 "div.cls" 或 "div.cls#id"
 * @param {Object} [props] 属性：text / html / dataset / on:xxx / 其它直接 setAttribute
 * @param {Array|Node|string} [children]
 */
export function h(tag, props = {}, children = []) {
  const [name, ...rest] = tag.split(/(?=[.#])/);
  const el = document.createElement(name || "div");
  for (const token of rest) {
    if (token.startsWith(".")) el.classList.add(token.slice(1));
    else if (token.startsWith("#")) el.id = token.slice(1);
  }

  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "text") el.textContent = String(value);
    else if (key === "html") el.innerHTML = String(value);
    else if (key === "class") el.className = String(value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") {
      // 传字符串就走 style 属性，传对象走内联样式（不能把字符串 assign 到 el.style）
      if (typeof value === "string") el.setAttribute("style", value);
      else Object.assign(el.style, value);
    }
    else if (key.startsWith("on:") && typeof value === "function") {
      el.addEventListener(key.slice(3), value);
    } else if (key === "value") el.value = value;
    else if (key === "checked" || key === "disabled" || key === "selected") el[key] = Boolean(value);
    else el.setAttribute(key, String(value));
  }

  append(el, children);
  return el;
}

export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === undefined || child === null || child === false) continue;
    if (Array.isArray(child)) append(parent, child);
    else parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function mount(node, children) {
  clear(node);
  append(node, children);
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function debounce(fn, wait = 250) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}
