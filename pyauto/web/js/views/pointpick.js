// 在模板图的「大图」里点位置：给找图动作选「点在图片的哪一处」。
// 结果是一对 0–1 的比例（相对模板图左上角），跟分辨率、缩放都无关。

const el = (id) => document.getElementById(id);

let resolveFn = null;
let point = [0.5, 0.5];
let result = null;

const clamp = (v) => Math.min(1, Math.max(0, v));
const round3 = (v) => Math.round(v * 1000) / 1000;

let onResize = null;
let sized = false;

/** 按容器可用空间等比放大/缩小图片；元素尺寸 == 画出来的尺寸，比例换算才准。 */
function fit() {
  const stage = el("pointStage");
  const img = el("pointImage");
  const dialog = el("pointDialog");
  if (!stage || !img || !img.naturalWidth) return;
  const pad = 8;

  // 第一次量：把弹层宽度收到跟图差不多，别留一大条空白
  if (!sized && dialog && img.naturalHeight) {
    const availH = Math.max(240, stage.clientHeight - pad * 2);
    const wByH = img.naturalWidth * (availH / img.naturalHeight);
    dialog.style.width = `${Math.min(1680, Math.max(760, Math.round(wByH + 150)))}px`;
    sized = true;
    requestAnimationFrame(() => fit());
    return;
  }

  const boxW = Math.max(1, stage.clientWidth - pad * 2);
  const boxH = Math.max(1, stage.clientHeight - pad * 2);
  const scale = Math.min(boxW / img.naturalWidth, boxH / img.naturalHeight);
  const w = Math.max(1, Math.round(img.naturalWidth * scale));
  const h = Math.max(1, Math.round(img.naturalHeight * scale));
  img.style.width = `${w}px`;
  img.style.height = `${h}px`;
  const zoom = el("pointZoom");
  if (zoom) zoom.textContent = `${Math.round(scale * 100)}%`;
  paint();
}

function paint() {
  const dot = el("pointDot");
  dot.hidden = false;
  dot.style.left = `${point[0] * 100}%`;
  dot.style.top = `${point[1] * 100}%`;
  el("pointRx").textContent = `${Math.round(point[0] * 100)}%`;
  el("pointRy").textContent = `${Math.round(point[1] * 100)}%`;
  el("pointConfirm").disabled = false;
}

function finish(value) {
  result = value;
  const dialog = el("pointDialog");
  if (dialog.open) dialog.close();
}

export function initPointPicker() {
  const dialog = el("pointDialog");
  if (!dialog) return;

  el("pointCanvas").addEventListener("click", (event) => {
    const img = el("pointImage");
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    point = [
      round3(clamp((event.clientX - rect.left) / rect.width)),
      round3(clamp((event.clientY - rect.top) / rect.height)),
    ];
    paint();
  });

  el("pointConfirm").addEventListener("click", () => finish([...point]));
  document
    .querySelectorAll("#pointDialog [data-close]")
    .forEach((btn) => btn.addEventListener("click", () => finish(null)));
  dialog.addEventListener("close", () => {
    if (onResize) {
      window.removeEventListener("resize", onResize);
      onResize = null;
    }
    const fn = resolveFn;
    resolveFn = null;
    if (fn) fn(result);
    result = null;
  });
}

/**
 * 打开「在模板图上选位置」的弹层。
 * @param {{src: string, point?: number[], title?: string}} opts
 * @returns {Promise<number[] | null>} 选好的 [横向比例, 纵向比例]，取消返回 null
 */
export function openPointPicker({ src, point: initial = null, title = "" } = {}) {
  const dialog = el("pointDialog");
  if (!dialog) return Promise.resolve(null);

  point = Array.isArray(initial) && initial.length >= 2
    ? [Number(initial[0]) || 0, Number(initial[1]) || 0]
    : [0.5, 0.5];
  result = null;

  el("pointTitle").textContent = title || "在模板图上选位置";
  dialog.style.width = "";
  sized = false;
  el("pointImage").src = src;
  el("pointImage").style.width = "";
  el("pointImage").style.height = "";
  el("pointDot").hidden = true;
  el("pointRx").textContent = "—";
  el("pointRy").textContent = "—";
  const zoom = el("pointZoom");
  if (zoom) zoom.textContent = "—";
  el("pointConfirm").disabled = true;

  return new Promise((resolve) => {
    resolveFn = resolve;
    dialog.showModal();
    const img = el("pointImage");
    const ready = () => requestAnimationFrame(() => fit());
    if (img.complete && img.naturalWidth) ready();
    else img.addEventListener("load", ready, { once: true });
    onResize = () => fit();
    window.addEventListener("resize", onResize);
  });
}
