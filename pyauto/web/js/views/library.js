// 左栏：动作库 + 步骤列表。

import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { toast } from "../toast.js";
import * as store from "../store.js";

export const PALETTE = [
  { kind: "click", label: "点击", icon: "cursor", make: () => ({ type: "input", action: "click", x: 960, y: 540, name: "点击" }) },
  { kind: "type", label: "输入", icon: "text", make: () => ({ type: "input", action: "type", value: "", name: "输入文字" }) },
  { kind: "key", label: "按键", icon: "keyboard", make: () => ({ type: "input", action: "key", value: "ctrl+s", name: "按键" }) },
  { kind: "wait", label: "等待", icon: "clock", make: () => ({ type: "sleep", seconds: 1, name: "等一会儿" }) },
  { kind: "ocr", label: "找字", icon: "scan", make: () => ({ type: "ocr", screen: true, expect: "", click: false, name: "找字" }) },
  { kind: "image", label: "找图", icon: "image", make: () => ({ type: "image", template: "", action: "click", threshold: 0.88, name: "找图" }) },
  { kind: "repeat", label: "重复", icon: "repeat", make: () => ({ type: "repeat", times: 5, interval: 0.5, tasks: [], name: "重复" }) },
  { kind: "shell", label: "命令", icon: "terminal", make: () => ({ type: "shell", cmd: "echo hello", name: "执行命令" }) },
  { kind: "python", label: "脚本", icon: "code", make: () => ({ type: "python", script: "", code: "", name: "跑脚本" }) },
  { kind: "raw", label: "自定义", icon: "braces", make: () => ({ type: "shell", cmd: "", name: "自定义步骤" }) },
];

export function renderPalette(root) {
  const buttons = PALETTE.map((item) =>
    h(
      "button.palette__btn",
      {
        type: "button",
        "aria-label": `添加${item.label}步骤`,
        title: `添加${item.label}`,
        "on:click": () => store.addTask(item.make()),
      },
      [icon(item.icon, 18), h("span", { text: item.label })],
    ),
  );
  mount(root, buttons);
}

export function renderSteps(root) {
  const list = store.tasks();

  if (state().loading) {
    mount(root, [h("div.skeleton"), h("div.skeleton"), h("div.skeleton")]);
    return;
  }
  if (!list.length) {
    mount(root, [
      h("div.empty", {}, [
        icon("spark", 20),
        h("p.empty__title", { text: "还没有步骤" }),
        h("p", { text: "从左边的动作库点一个开始，比如「找字」。" }),
      ]),
    ]);
    return;
  }

  // 重绘会把列表滚动位置和焦点一起清掉，先记下来，绘完再放回去
  const scroll = root.scrollTop;
  const hadFocus = root.contains(document.activeElement);

  const rows = [];
  list.forEach((task, index) => {
    const nested = task.type === "repeat" || task.type === "group";
    const open = !nested || store.isExpanded(index, task);
    rows.push(row(task, index, -1, nested));
    if (!nested || !open) return;
    const kids = Array.isArray(task.tasks) ? task.tasks : [];
    if (!kids.length) {
      rows.push(
        h("div.step-hint", { class: "step-hint--repeat" }, [
          h("p.step-hint__line", {
            text: task.type === "group" ? "这个组合块是空的：在下面选一个动作加进来" : "空重复块：在下面选一个动作，加进这里面",
          }),
          h(
            "div.step-hint__row",
            {},
            PALETTE.map((item) =>
              h(
                "button.step-hint__btn",
                {
                  type: "button",
                  title: `把「${item.label}」加进这个块`,
                  "on:click": () => store.addChild(index, item.make()),
                },
                [icon(item.icon, 14), h("span", { text: item.label })],
              ),
            ),
          ),
        ]),
      );
    }
    kids.forEach((child, ci) => rows.push(row(child, index, ci)));
  });
  mount(root, rows);

  const current = root.querySelector(`.step[data-index="${state().selected}"]`);
  if (current && hadFocus) {
    current.focus({ preventScroll: true });
  }
  if (root.scrollTop !== scroll) {
    root.scrollTop = scroll;
  }
}

/** 只改选中态，不重建行，切换步骤时列表不会跳回顶部。 */
export function refreshSelection(root) {
  const selected = state().selected;
  const child = state().child;
  let current = null;
  root.querySelectorAll(".step").forEach((node) => {
    const index = Number(node.dataset.index);
    const kids = node.dataset.child === undefined ? -1 : Number(node.dataset.child);
    const on = index === selected && kids === (kids >= 0 ? child : -1);
    node.setAttribute("aria-selected", String(on));
    if (on) current = node;
  });
  if (current) current.scrollIntoView({ block: "nearest" });
}

function row(task, index, child = -1, collapsible = false) {
  const selected = index === state().selected && (child < 0 ? state().child < 0 : state().child === child);
  const kind = store.stepKind(task);
  const isChild = child >= 0;
  const parent = state().config.tasks[index];
  const siblings = isChild ? (Array.isArray(parent.tasks) ? parent.tasks : []) : store.tasks();

  const tools = h("div.step__tools", {}, [
    tool("up", "上移", index, "move", -1, () => store.moveTask(index, -1, child), child >= 0 ? child === 0 : index === 0),
    tool("down", "下移", index, "move", 1, () => store.moveTask(index, 1, child), child >= 0 ? child === siblings.length - 1 : index === store.tasks().length - 1),
    tool("copy", "复制这一步", index, "dup", 0, () => store.duplicateTask(index, child), false),
    tool("trash", "删除这一步", index, "delete", 0, () => store.removeTask(index, child), false, "step__tool--danger"),
  ]);

  const mark = state().mark;
  const marked = !isChild && mark && index >= mark.from && index <= mark.to;
  const open = collapsible ? store.isExpanded(index, task) : false;
  const classes = `step${isChild ? " step--child" : ""}${marked ? " is-marked" : ""}${collapsible ? " step--block" : ""}${open ? " is-open" : ""}`;
  return h(
    "div",
    {
      class: classes,
      role: "option",
      "aria-selected": String(selected),
      "aria-label": isChild
        ? `第 ${index + 1} 步里第 ${child + 1} 个动作：${store.stepTitle(task)}`
        : `第 ${index + 1} 步：${store.stepTitle(task)}`,
      tabindex: "-1",
      dataset: isChild ? { index: String(index), child: String(child) } : { index: String(index) },
      "on:click": (event) => {
        if (event.target.closest("button")) return;
        if (!isChild && event.shiftKey) {
          store.markTo(index);
          return;
        }
        store.selectStep(index, child);
      },
    },
    [
      h("span.step__lead", {}, [
        collapsible
          ? h(
              "button.step__fold",
              {
                type: "button",
                "aria-label": open ? "收起这个块" : "展开这个块",
                "aria-expanded": String(open),
                title: open ? "收起" : "展开",
                "on:click": (event) => {
                  event.stopPropagation();
                  store.toggleExpanded(index);
                },
              },
              [icon(open ? "chevronDown" : "chevronRight", 13)],
            )
          : null,
        h("span.step__index", { text: isChild ? `↳${child + 1}` : String(index + 1).padStart(2, "0") }),
      ].filter(Boolean)),
      h("span.step__main", {}, [
        h("span.step__name", { text: store.stepTitle(task) }),
        h("span.step__meta", {}, [
          h("em", { text: kindLabel(kind) }),
          h("span", { text: store.stepMeta(task) }),
        ]),
      ]),
      tools,
    ].filter(Boolean),
  );
}

function kindLabel(kind) {
  const found = PALETTE.find((item) => item.kind === kind);
  if (found) return found.label;
  if (kind === "group") return "组合";
  return kind === "wait" ? "等待" : kind;
}

function tool(iconName, label, index, act, delta, onClick, disabled, extraClass = "") {
  return h(
    "button",
    {
      type: "button",
      class: `step__tool ${extraClass}`.trim(),
      "aria-label": label,
      title: label,
      disabled: disabled || undefined,
      dataset: { act, index: String(index), delta: String(delta) },
      "on:click": (event) => {
        event.stopPropagation();
        onClick();
      },
    },
    [icon(iconName, 14)],
  );
}

/** 步骤列表支持键盘操作：上下切换、Delete 删除、Enter 跳到参数区。 */
export function bindStepsKeyboard(root) {
  root.addEventListener("keydown", (event) => {
    const list = store.tasks();
    if (!list.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const next = Math.min(list.length - 1, Math.max(0, state().selected + delta));
      store.selectStep(next);
      const node = root.querySelector(`[data-index="${next}"]`);
      if (node) node.scrollIntoView({ block: "nearest" });
    } else if (event.key === "Delete" || event.key === "Backspace") {
      if (state().selected >= 0) {
        event.preventDefault();
        store.removeTask(state().selected);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      document.getElementById("editor")?.focus();
    }
  });
}

function state() {
  return store.state;
}

/**
 * 动作库下面的「我的组合」：一个组合一颗按钮，点一下把整段插到当前步骤后面。
 * 悬停时露出「改名」「删除」两个小按钮。
 */
export function renderCombos(grid, section) {
  const items = state().combos || [];
  const empty = document.getElementById("comboEmpty");
  if (empty) empty.hidden = items.length > 0;
  if (section) section.classList.toggle("is-empty", !items.length);
  if (!grid) return;

  mount(
    grid,
    items.map((item) =>
      h("div.combo-chip", {}, [
        h(
          "button.combo-chip__main",
          {
            type: "button",
            title: `插入「${item.name}」的 ${item.tasks.length} 步`,
            "on:click": () => {
              const n = store.insertGroup(item.name, item.tasks);
              toast(`已插入「${item.name}」${n} 步（收在组合块里）`);
            },
          },
          [
            icon("stack", 15),
            h("span.combo-chip__name", { text: item.name }),
            h("span.combo-chip__meta", { text: `${item.tasks.length}` }),
          ],
        ),
        h("div.combo-chip__tools", {}, [
          h(
            "button.combo-chip__tool",
            {
              type: "button",
              title: `改名或改内容：${item.name}`,
              "aria-label": `编辑组合 ${item.name}`,
              "on:click": () =>
                document.dispatchEvent(new CustomEvent("combo:edit", { detail: item })),
            },
            [icon("pencil", 12)],
          ),
          h(
            "button.combo-chip__tool",
            {
              type: "button",
              title: `删除组合：${item.name}`,
              "aria-label": `删除组合 ${item.name}`,
              "on:click": async () => {
                if (!confirm(`删除组合「${item.name}」？当前任务里的步骤不会动。`)) return;
                await store.deleteCombo(item.name);
                toast(`已删除组合「${item.name}」`);
              },
            },
            [icon("trash", 12)],
          ),
        ]),
      ]),
    ),
  );
}
