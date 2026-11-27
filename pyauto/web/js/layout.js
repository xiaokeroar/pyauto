// 可拖动的分栏边界：左栏宽度、右栏宽度、日志高度，双击复位，尺寸记在 localStorage。

const KEY = "pyauto.layout.v2";
const DEFAULTS = { left: 240, preview: 360, console: 180 };
const LIMITS = {
  left: [200, 440],
  preview: [260, 560],
  console: [120, 420],
};
const STEP = 16;
// 中间那栏至少留这么多，窗口窄时先压两侧
const MIN_EDITOR = 340;

function clamp(value, [min, max]) {
  return Math.min(Math.max(Math.round(value), min), max);
}

function read() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      left: clamp(saved.left ?? DEFAULTS.left, LIMITS.left),
      preview: clamp(saved.preview ?? DEFAULTS.preview, LIMITS.preview),
      console: clamp(saved.console ?? DEFAULTS.console, LIMITS.console),
    };
  } catch (err) {
    return { ...DEFAULTS };
  }
}

function write(sizes) {
  try {
    localStorage.setItem(KEY, JSON.stringify(sizes));
  } catch (err) {
    /* 隐私模式下写不了就算了 */
  }
}

/**
 * 让三个分割条可以拖动，调整 grid 的三条轨道。
 * @param {HTMLElement} workspace
 */
export function initLayout(workspace) {
  const handles = {
    left: workspace.querySelector('[data-split="left"]'),
    preview: workspace.querySelector('[data-split="preview"]'),
    console: workspace.querySelector('[data-split="console"]'),
  };
  if (!handles.left || !handles.preview || !handles.console) return;

  const panels = {
    library: workspace.querySelector(".panel--library"),
    editor: workspace.querySelector(".panel--editor"),
    preview: workspace.querySelector(".panel--preview"),
    console: workspace.querySelector(".panel--console"),
  };
  if (Object.values(panels).some((panel) => !panel)) return;

  let sizes = read();

  function place() {
    if (window.matchMedia("(max-width: 900px)").matches) return;
    const box = workspace.getBoundingClientRect();
    const lib = panels.library.getBoundingClientRect();
    const editor = panels.editor.getBoundingClientRect();
    const preview = panels.preview.getBoundingClientRect();
    const consolePanel = panels.console.getBoundingClientRect();

    handles.left.style.left = `${lib.right - box.left + (editor.left - lib.right) / 2 - 5}px`;
    handles.left.style.top = `${lib.top - box.top}px`;
    handles.left.style.height = `${lib.height}px`;

    handles.preview.style.left = `${editor.right - box.left + (preview.left - editor.right) / 2 - 5}px`;
    handles.preview.style.top = `${preview.top - box.top}px`;
    handles.preview.style.height = `${preview.height}px`;

    handles.console.style.left = `${lib.left - box.left}px`;
    handles.console.style.top = `${editor.bottom - box.top + (consolePanel.top - editor.bottom) / 2 - 5}px`;
    handles.console.style.width = `${editor.right - lib.left}px`;
  }

  /**
   * 按窗口宽度收一收：窄窗口里别让两侧把中间挤没（也顺手把以前存的过大尺寸收回来）。
   */
  function normalise() {
    const width = workspace.clientWidth || window.innerWidth;
    if (!width) return;
    const gap = 40; // 两列间距 + 内边距的粗算
    const maxLeft = Math.min(LIMITS.left[1], Math.max(LIMITS.left[0], Math.round(width * 0.23)));
    const maxPreview = Math.min(
      LIMITS.preview[1],
      Math.max(LIMITS.preview[0], Math.round(width * 0.31)),
    );
    sizes.left = clamp(Math.min(sizes.left, maxLeft), LIMITS.left);
    sizes.preview = clamp(Math.min(sizes.preview, maxPreview), LIMITS.preview);

    let over = sizes.left + sizes.preview + gap + MIN_EDITOR - width;
    while (over > 0 && (sizes.left > LIMITS.left[0] || sizes.preview > LIMITS.preview[0])) {
      // 谁更宽就先削谁，削到下限再削另一个
      if (sizes.preview >= sizes.left && sizes.preview > LIMITS.preview[0]) {
        sizes.preview -= Math.min(STEP, over);
      } else if (sizes.left > LIMITS.left[0]) {
        sizes.left -= Math.min(STEP, over);
      } else {
        sizes.preview -= Math.min(STEP, over);
      }
      over = sizes.left + sizes.preview + gap + MIN_EDITOR - width;
    }
  }

  function apply() {
    normalise();
    workspace.style.setProperty("--w-left", `${sizes.left}px`);
    workspace.style.setProperty("--w-preview", `${sizes.preview}px`);
    workspace.style.setProperty("--h-console", `${sizes.console}px`);
    requestAnimationFrame(place);
  }

  function drag(kind, event) {
    event.preventDefault();
    handles[kind].setPointerCapture?.(event.pointerId);
    const start = { x: event.clientX, y: event.clientY, sizes: { ...sizes } };
    document.body.classList.add("is-resizing", `is-resizing--${kind}`);

    const move = (ev) => {
      if (kind === "left") {
        sizes.left = clamp(start.sizes.left + (ev.clientX - start.x), LIMITS.left);
      } else if (kind === "preview") {
        sizes.preview = clamp(start.sizes.preview - (ev.clientX - start.x), LIMITS.preview);
      } else {
        sizes.console = clamp(start.sizes.console - (ev.clientY - start.y), LIMITS.console);
      }
      apply();
    };

    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      document.body.classList.remove("is-resizing", `is-resizing--${kind}`);
      write(sizes);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  }

  function nudge(kind, delta) {
    if (kind === "console") {
      sizes.console = clamp(sizes.console + delta, LIMITS.console);
    } else if (kind === "preview") {
      sizes.preview = clamp(sizes.preview - delta, LIMITS.preview);
    } else {
      sizes.left = clamp(sizes.left + delta, LIMITS.left);
    }
    apply();
    write(sizes);
  }

  Object.entries(handles).forEach(([kind, handle]) => {
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      drag(kind, event);
    });
    handle.addEventListener("dblclick", () => {
      sizes = { ...sizes, [kind]: DEFAULTS[kind] };
      apply();
      write(sizes);
    });
    handle.addEventListener("keydown", (event) => {
      const horizontal = kind !== "console";
      const back = horizontal ? "ArrowLeft" : "ArrowUp";
      const forward = horizontal ? "ArrowRight" : "ArrowDown";
      if (event.key !== back && event.key !== forward) return;
      event.preventDefault();
      const step = event.shiftKey ? STEP * 3 : STEP;
      nudge(kind, event.key === forward ? step : -step);
    });
  });

  const resetBtn = document.getElementById("btnResetLayout");
  resetBtn?.addEventListener("click", () => {
    sizes = { ...DEFAULTS };
    apply();
    write(sizes);
  });

  const observer = new ResizeObserver(() => place());
  observer.observe(workspace);
  window.addEventListener("resize", () => {
    apply();
  });
  apply();
}
