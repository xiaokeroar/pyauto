// 内联 SVG 图标（24×24 线性风格），不依赖图标库和外部图片。

const PATHS = {
  cursor: '<path d="M5 3.5 19 12l-6.2 1.4L9.9 20 5 3.5Z"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M6 13.5h.01M9.5 13.5h.01M13 13.5h.01M16.5 13.5h.01M8 16.5h8"/>',
  text: '<path d="M4 6h16M12 6v13M8 19h8"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  scan: '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/><path d="M7.5 12h9"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.4"/><path d="m4 17 5-5 4.5 4.5L17 13l3.5 3.5"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7.5 10 2.5 2.2-2.5 2.3M12.5 14.5H17"/>',
  code: '<path d="m8.5 8-4 4 4 4M15.5 8l4 4-4 4"/>',
  braces: '<path d="M9 4.5c-2 0-2.5 1-2.5 2.5v2c0 1.5-.8 2-2 2 1.2 0 2 .5 2 2v2c0 1.5.5 2.5 2.5 2.5M15 4.5c2 0 2.5 1 2.5 2.5v2c0 1.5.8 2 2 2-1.2 0-2 .5-2 2v2c0 1.5-.5 2.5-2.5 2.5"/>',
  crosshair: '<circle cx="12" cy="12" r="7.5"/><path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21"/>',
  crop: '<path d="M6.5 2.5v15h15M2.5 6.5h15v15"/>',
  trash: '<path d="M4.5 7h15M9.5 7V5h5v2M7 7l1 13h8l1-13"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 5.5v-1a2 2 0 0 0-2-2h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h1"/>',
  play: '<path d="M7 4.5v15l12-7.5-12-7.5Z"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  save: '<path d="M5 4h11l3 3v13H5V4Z"/><path d="M8.5 4v5h6V4M8.5 20v-6h7v6"/>',
  download: '<path d="M12 4v10M8 10.5l4 4 4-4M5 19h14"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="14.5" cy="7" r="2.1"/><circle cx="8.5" cy="17" r="2.1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  spark: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="3.2"/>',
  file: '<path d="M14 3v5h5"/><path d="M20 21H4V3h10l6 6v12Z"/>',
  repeat: '<path d="M4.5 9.5A4.5 4.5 0 0 1 9 5h6.5M13 2.5 15.5 5 13 7.5"/><path d="M19.5 14.5A4.5 4.5 0 0 1 15 19H8.5M11 21.5 8.5 19 11 16.5"/>',
  stack: '<path d="m12 3 8.5 4.5L12 12 3.5 7.5 12 3Z"/><path d="m3.5 12 8.5 4.5L20.5 12"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5"/>',
  pencil: '<path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="m14.5 6.5 3 3"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
};

/**
 * 生成图标元素。
 * @param {keyof typeof PATHS} name
 * @param {number} size
 */
export function icon(name, size = 16) {
  const path = PATHS[name] || PATHS.braces;
  const span = document.createElement("span");
  span.className = "icon";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  return span.firstElementChild;
}

export const STEP_ICONS = {
  click: "cursor",
  type: "text",
  key: "keyboard",
  wait: "clock",
  ocr: "scan",
  image: "image",
  repeat: "repeat",
  group: "stack",
  shell: "terminal",
  python: "code",
  sleep: "clock",
  raw: "braces",
};

export const STEP_LABELS = {
  click: "点击",
  type: "输入",
  key: "按键",
  wait: "等待",
  ocr: "找字",
  image: "找图",
  repeat: "重复",
  group: "组合",
  shell: "命令",
  python: "脚本",
  sleep: "等待",
  raw: "自定义",
};
