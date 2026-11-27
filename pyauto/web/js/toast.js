// 轻提示：右下角浮一层，2.6 秒自动走。

const host = () => document.getElementById("toasts");

/**
 * @param {string} message
 * @param {"info"|"danger"} [tone]
 */
export function toast(message, tone = "info") {
  const box = host();
  if (!box) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.tone = tone;
  node.textContent = message;
  box.append(node);
  window.setTimeout(() => node.remove(), 2600);
}
