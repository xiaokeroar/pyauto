// 中栏：当前步骤的参数表单；没有选中步骤时显示整体设置。

import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { api } from "../api.js";
import * as store from "../store.js";
import { openPicker } from "./picker.js";
import { openPointPicker } from "./pointpick.js";
import { openRules } from "./rules.js";
import { PALETTE } from "./library.js";
import { toast } from "../toast.js";

/** 当前正在编辑的位置：顶层第几步，或者重复块里的第几个子动作。 */
const CTX = { index: -1, child: -1 };

function upd(patch) {
  store.updateTask(CTX.index, patch, CTX.child);
}

export function renderEditor(root) {
  const task = store.currentStep();
  if (!task) {
    renderSettings(root);
    return;
  }

  const index = store.state.selected;
  CTX.index = index;
  CTX.child = store.state.child;

  const kind = store.stepKind(task);
  const children = [];
  if (CTX.child >= 0) children.push(breadcrumb(index));
  children.push(baseSection(task, index));

  if (kind === "click") children.push(clickSection(task, index));
  else if (kind === "type" || kind === "key") children.push(textSection(task, index, kind));
  else if (kind === "wait") children.push(waitSection(task, index));
  else if (kind === "ocr") children.push(ocrSection(task, index));
  else if (kind === "image") children.push(imageSection(task, index));
  else if (kind === "repeat") children.push(repeatSection(task, index));
  else if (kind === "group") children.push(groupSection(task, index));
  else if (kind === "shell") children.push(shellSection(task, index));
  else if (kind === "python") children.push(pythonSection(task, index));
  else children.push(rawSection(task, index));

  // 重复块本身没配过异常处理，只有子动作可以
  if (CTX.child >= 0 || kind !== "repeat") children.push(rulesCallout(task, index));

  // 切换步骤会整块重绘，先记住滚动位置，绘完放回去，别让参数区跳回顶部
  const scroll = root.scrollTop;
  mount(root, children);
  if (root.scrollTop !== scroll) root.scrollTop = Math.min(scroll, root.scrollHeight);
}

/** 在重复块里编辑子动作时，顶部给一条「返回」。 */
function breadcrumb(index) {
  const parent = store.tasks()[index];
  return h(
    "button.breadcrumb",
    { type: "button", "on:click": () => store.selectStep(index, -1) },
    [icon("up", 14), h("span", { text: `返回「${store.stepTitle(parent) || "重复"}」` })],
  );
}

/* ------------------------------------------------------------ 通用控件 */

function section(title, fields) {
  return h("section.form-section", {}, [
    h("h3.form-section__title", { text: title }),
    h("div.form-grid", {}, fields),
  ]);
}

function field(label, control, { full = false, hint = "" } = {}) {
  const node = h(`div.field${full ? ".field--full" : ""}`, {}, [
    h("label.field__label", { for: control.id || undefined, text: label }),
    control,
    hint ? h("p.field__hint", { text: hint }) : null,
  ]);
  return node;
}

function input({ id, value, onInput, placeholder = "", mono = false, type = "text" }) {
  return h("input", {
    id,
    class: `input${mono ? " input--mono" : ""}`,
    type,
    value: value ?? "",
    placeholder,
    "on:input": (event) => onInput(event.target.value),
  });
}

function textInput({ id, value, onInput, placeholder = "" }) {
  return h("input", {
    id,
    class: "input",
    type: "text",
    value: value ?? "",
    placeholder,
    "on:input": (event) => onInput(String(event.target.value)),
  });
}

function numberInput({ id, value, onInput, step = 1, min, placeholder = "" }) {
  return h("input", {
    id,
    class: "input",
    type: "number",
    step: String(step),
    min: min !== undefined ? String(min) : undefined,
    value: value ?? 0,
    placeholder,
    "on:input": (event) => {
      const num = Number(event.target.value);
      onInput(Number.isFinite(num) ? num : 0);
    },
  });
}

function select({ id, value, options, onChange }) {
  const el = h(
    "select",
    { id, class: "select", "on:change": (event) => onChange(event.target.value) },
    options.map((opt) => h("option", { value: opt.value, text: opt.label, selected: opt.value === value })),
  );
  el.value = value;
  return el;
}

function checkbox(label, checked, onChange, id) {
  return h("label.checkbox", {}, [
    h("input", {
      id,
      type: "checkbox",
      checked: Boolean(checked),
      "on:change": (event) => onChange(event.target.checked),
    }),
    h("span", { text: label }),
  ]);
}

/* ------------------------------------------------------------ 各类型 */

function baseSection(task, index) {
  const kind = store.stepKind(task);
  return section("基本信息", [
    field(
      "说明（会变成任务名）",
      input({
        id: "step-name",
        value: task.name || "",
        placeholder: store.stepTitle(task),
        onInput: (value) => upd({ name: value }),
      }),
      { full: true, hint: `类型：${kindLabel(kind)}　·　${task.type}${task.action ? ` / ${task.action}` : ""}` },
    ),
  ]);
}

function clickSection(task, index) {
  return section("点击位置", [
    field("X 坐标", numberInput({ id: "step-x", value: task.x, onInput: (v) => upd({ x: Math.round(v) }) })),
    field("Y 坐标", numberInput({ id: "step-y", value: task.y, onInput: (v) => upd({ y: Math.round(v) }) })),
    field(
      "",
      h(
        "button.btn.btn-ghost.btn-small",
        {
          type: "button",
          "on:click": async () => {
            const picked = await openPicker({ mode: "point", title: "点一下要点击的位置" });
            if (picked && picked.x !== undefined) {
              upd({ x: picked.x, y: picked.y, name: task.name || "点击" });
              toast(`坐标：${picked.x}, ${picked.y}`);
            }
          },
        },
        [icon("crosshair", 14), "在屏幕上取坐标"],
      ),
      { full: true },
    ),
  ]);
}

function textSection(task, index, kind) {
  if (kind === "type") {
    return section("要输入的文字", [
      field(
        "文字",
        h("textarea", {
          id: "step-value",
          class: "textarea",
          rows: "3",
          placeholder: "比如：hello pyauto",
          "on:input": (event) => upd({ value: event.target.value }),
          text: task.value || "",
        }),
        { full: true, hint: "中文也可以：pyauto 用 SendInput 直接发字符，不走剪贴板。" },
      ),
    ]);
  }
  return section("按键组合", [
    field(
      "组合键",
      input({
        id: "step-value",
        value: task.value || "",
        placeholder: "ctrl+s / enter / alt+tab",
        onInput: (value) => upd({ value }),
      }),
      { full: true, hint: "用 + 连接；单键直接写名字，比如 enter。" },
    ),
  ]);
}

function waitSection(task, index) {
  return section("等待", [
    field(
      "秒数",
      numberInput({
        id: "step-wait",
        value: task.seconds ?? 1,
        step: 0.1,
        min: 0,
        onInput: (value) => upd({ seconds: value }),
      }),
      { hint: "运行中按停止会立刻打断等待。" },
    ),
  ]);
}

function ocrSection(task, index) {
  return section("找字", [
    field(
      "要找到的文字",
      input({
        id: "step-expect",
        value: task.expect || "",
        placeholder: "比如：同意",
        onInput: (value) => upd({ expect: value }),
      }),
      { full: true },
    ),
    field("只在这块区域找（可空）", boxControl(task, index), { full: true }),
    field("", checkbox("找到就点它", task.click, (checked) => upd({ click: checked })), {
      full: true,
    }),
    field(
      "找之前先等（秒）",
      numberInput({
        id: "step-sleep-before",
        value: task.sleep_before ?? 0,
        step: 0.1,
        min: 0,
        onInput: (value) => upd({ sleep_before: value }),
      }),
    ),
    field(
      "",
      checkbox("找不到就跳过（算这一步过了，继续往下跑）", Boolean(task.allow_missing), (checked) =>
        upd({ allow_missing: checked }),
      ),
      { full: true, hint: "适合「有就点、没有就继续」的弹窗、广告、提示条。" },
    ),
  ]);
}

function repeatSection(task, index) {
  const kids = Array.isArray(task.tasks) ? task.tasks : [];
  const timed = Boolean(task.at || task.every);
  const mode = timed ? "time" : "count";
  const modeRow = h("div.segmented", {}, [
    h("button.segmented__btn", {
      type: "button",
      role: "tab",
      "aria-selected": String(mode === "count"),
      text: "按次数",
      "on:click": () => setRepeatMode("count"),
    }),
    h("button.segmented__btn", {
      type: "button",
      role: "tab",
      "aria-selected": String(mode === "time"),
      text: "按时间",
      "on:click": () => setRepeatMode("time"),
    }),
  ]);

  function setRepeatMode(next) {
    const patch =
      next === "time"
        ? { at: task.at || "", every: task.every || "10m", times: task.times ?? 1, interval: null }
        : { at: null, every: null, interval: task.interval ?? 0 };
    upd(patch);
    // upd 走的是当前选中项；渲染由订阅里的 steps/task 事件触发，这里再补一枪防止没刷新
    store.setState({}, "task-shape");
  }

  const countFields = [
    field(
      "重复几次",
      numberInput({
        id: "step-times",
        value: task.times ?? 1,
        min: 1,
        onInput: (value) => upd({ times: Math.max(1, Math.round(value)) }),
      }),
      { hint: "下面这些动作整体跑这么多次" },
    ),
    field(
      "每轮之间等几秒",
      numberInput({
        id: "step-interval",
        value: task.interval ?? 0,
        step: 0.1,
        min: 0,
        onInput: (value) => upd({ interval: Math.max(0, value) }),
      }),
    ),
  ];

  const timeFields = [
    field(
      "开始时间",
      textInput({
        id: "step-at",
        value: task.at ?? "",
        placeholder: "18:00（末尾加 Z 表示 UTC，例如 18:00Z）",
        onInput: (value) => upd({ at: value.trim() || null }),
      }),
      { hint: "过了这个点就顺延到明天；留空表示现在就开始" },
    ),
    field(
      "每隔多久一次",
      textInput({
        id: "step-every",
        value: task.every ?? "",
        placeholder: "10m / 30s / 2h",
        onInput: (value) => upd({ every: value.trim() || null }),
      }),
      { hint: "留空就是只执行一次" },
    ),
    field(
      "一共执行几次",
      numberInput({
        id: "step-times",
        value: task.times ?? 1,
        min: 1,
        onInput: (value) => upd({ times: Math.max(1, Math.round(value)) }),
      }),
      { hint: "18:00 起、每 10 分钟、5 次 → 18:00 / 18:10 / 18:20 / 18:30 / 18:40" },
    ),
  ];

  return section("重复", [
    field("执行方式", modeRow, { full: true, hint: "按次数：连跑 N 轮；按时间：到点开始，按间隔再跑" }),
    ...(timed ? timeFields : countFields),
    field(
      "里面的动作",
      h("div", {}, [
        kids.length
          ? h(
              "div.repeat-kids",
              {},
              kids.map((kid, ci) =>
                h(
                  "button.repeat-kid",
                  { type: "button", "on:click": () => store.selectStep(index, ci) },
                  [
                    h("span.repeat-kid__no", { text: String(ci + 1) }),
                    h("span.repeat-kid__name", { text: store.stepTitle(kid) }),
                    h("span.repeat-kid__kind", { text: kindLabel(store.stepKind(kid)) }),
                  ],
                ),
              ),
            )
          : h("p.field__hint", { text: "还空着：在下面选一个动作，就加进这个重复块里" }),
        h("div.repeat-add", {}, [
          h("span.repeat-add__label", { text: "再加一步：" }),
          ...PALETTE.map((item) =>
            h(
              "button.repeat-add__btn",
              {
                type: "button",
                title: `把「${item.label}」加进这个重复块`,
                "on:click": () => store.addChild(index, item.make()),
              },
              [icon(item.icon, 13), h("span", { text: item.label })],
            ),
          ),
        ]),
      ]),
      { full: true, hint: kids.length ? "点一个进去改它的参数" : "" },
    ),
  ]);
}

/** 组合块：一整段动作收在一行里，可以折叠。 */
function groupSection(task, index) {
  const kids = Array.isArray(task.tasks) ? task.tasks : [];
  const open = store.isExpanded(index, task);
  return section("组合块", [
    field(
      "这一项叫什么",
      input({
        id: "step-block-name",
        value: task.name || "",
        placeholder: "比如：组合：购买物品通用点击",
        onInput: (value) => upd({ name: value }),
      }),
      { full: true, hint: "只影响列表里显示的名字，不影响里面动作" },
    ),
    field(
      "里面的动作",
      h("div", {}, [
        h("div.block-fold", {}, [
          h(
            "button.btn.btn-ghost.btn-small",
            {
              type: "button",
              "aria-expanded": String(open),
              "on:click": () => store.toggleExpanded(index),
            },
            [icon(open ? "chevronDown" : "chevronRight", 14), h("span", { text: open ? "收起" : "展开" })],
          ),
          h("span.block-fold__count", { text: `${kids.length} 步` }),
        ]),
        open
          ? kids.length
            ? h(
                "div.repeat-kids",
                {},
                kids.map((kid, ci) =>
                  h(
                    "button.repeat-kid",
                    { type: "button", "on:click": () => store.selectStep(index, ci) },
                    [
                      h("span.repeat-kid__no", { text: String(ci + 1) }),
                      h("span.repeat-kid__name", { text: store.stepTitle(kid) }),
                      h("span.repeat-kid__kind", { text: kindLabel(store.stepKind(kid)) }),
                    ],
                  ),
                ),
              )
            : h("p.field__hint", { text: "还空着：在下面选一个动作，就加进这个组合块里" })
          : h("p.field__hint", { text: "已收起：点上面的「展开」看里面的步骤" }),
        h("div.repeat-add", {}, [
          h("span.repeat-add__label", { text: "再加一步：" }),
          ...PALETTE.map((item) =>
            h(
              "button.repeat-add__btn",
              {
                type: "button",
                title: `把「${item.label}」加进这个组合块`,
                "on:click": () => store.addChild(index, item.make()),
              },
              [icon(item.icon, 13), h("span", { text: item.label })],
            ),
          ),
        ]),
      ]),
      { full: true, hint: kids.length ? "点一个进去改它的参数" : "" },
    ),
  ]);
}

function pointPanel(task) {  const tplPath = String(task.template || "");
  const point =
    Array.isArray(task.point) && task.point.length >= 2
      ? [Number(task.point[0]) || 0, Number(task.point[1]) || 0]
      : null;
  const custom = point !== null;
  const say = (rx, ry) =>
    `相对模板图：横向 ${Math.round(rx * 100)}%、纵向 ${Math.round(ry * 100)}%（在图上再点一下可以改）`;

  const stage = h("div.point-pick__stage", { hidden: !custom }, []);
  if (custom) {
    if (tplPath) {
      const img = h("img.point-pick__img", {
        src: `/api/template/image?name=${encodeURIComponent(store.templateName(tplPath))}`,
        alt: "模板图，点一下选位置",
        draggable: "false",
      });
      img.addEventListener("click", (event) => {
        const rect = img.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const clamp = (v) => Math.min(1, Math.max(0, v));
        const round3 = (v) => Math.round(v * 1000) / 1000;
        upd({
          point: [
            round3(clamp((event.clientX - rect.left) / rect.width)),
            round3(clamp((event.clientY - rect.top) / rect.height)),
          ],
        });
      });
      stage.append(
        h("div.point-pick__canvas", {}, [
          img,
          h("span.point-pick__dot", {
            style: { left: `${point[0] * 100}%`, top: `${point[1] * 100}%` },
          }),
        ]),
      );
    } else {
      stage.append(h("p.point-pick__empty", { text: "先在上面选一张模板图，再来点位置。" }));
    }
  }

  const openBig = async () => {
    if (!tplPath) {
      toast("先在上面选一张模板图");
      return;
    }
    const picked = await openPointPicker({
      src: `/api/template/image?name=${encodeURIComponent(store.templateName(tplPath))}`,
      point: point || [0.5, 0.5],
      title: "在模板图上选位置",
    });
    if (picked) {
      upd({ point: picked });
      toast(`落点：横向 ${Math.round(picked[0] * 100)}%、纵向 ${Math.round(picked[1] * 100)}%`);
    }
  };

  const tab = (label, on, onClick) =>
    h(
      "button",
      {
        type: "button",
        class: "segmented__btn",
        "aria-selected": on ? "true" : "false",
        "on:click": onClick,
      },
      [label],
    );

  return h("div.block", {}, [
    h("div.block__head", {}, [icon("crosshair", 14), h("span", { text: "点在图片的哪一处" })]),
    h("div.block__row", {}, [
      h("div.segmented", {}, [
        tab("点正中", !custom, () => upd({ point: undefined })),
        tab("自己指定一处", custom, () => {
          if (!custom) upd({ point: point || [0.5, 0.5] });
          openBig();
        }),
      ]),
      h(
        "button.btn.btn-ghost.btn-small",
        { type: "button", title: "打开模板大图，点哪一处就点哪一处", "on:click": openBig },
        [icon("image", 14), "在网页里打开大图"],
      ),
    ]),
    stage,
    h("p.block__hint", {
      text: custom
        ? point
          ? say(point[0], point[1])
          : ""
        : "默认点图片正中。要按图标角上的小按钮，就选「自己指定一处」，会打开模板大图让你点。",
    }),
  ]);
}

function imageSection(task, index) {
  const templates = store.state.templates || [];
  const options = [{ value: "", label: "— 选择模板 —" }, ...templates.map((t) => ({ value: t.path, label: `${t.name}${t.size ? `  ${t.size[0]}×${t.size[1]}` : ""}` }))];
  const current = String(task.template || "");
  if (current && !options.some((opt) => opt.value === current)) {
    options.push({ value: current, label: current });
  }

  return section("找图", [
    field(
      "模板图片",
      h("div.template-pick", {}, [
        select({
          id: "step-template",
          value: current,
          options,
          onChange: (value) => upd({ template: value }),
        }),
        h("div.template-pick__actions", {}, [
          h(
            "button.btn.btn-ghost.btn-small",
            {
              type: "button",
              title: "在屏幕上框一块，直接存成模板",
              "on:click": async () => {
                const picked = await openPicker({ mode: "region", title: "框一块当模板", initial: task.box });
                if (!picked || !picked.box) return;
                try {
                  const saved = await api.grabTemplate(picked.box, "");
                  await applyTemplate(index, saved.path);
                  toast(`模板已保存并选中：${saved.name}`);
                } catch (err) {
                  toast(err.message, "danger");
                }
              },
            },
            [icon("crop", 14), "截取模板"],
          ),
          h(
            "button.btn.btn-ghost.btn-small",
            {
              type: "button",
              title: "从磁盘选一张图片，自动复制到 templates/",
              "on:click": () => browseTemplate(index),
            },
            [icon("image", 14), "浏览文件…"],
          ),
        ]),
      ]),
      { full: true, hint: "模板存在工程根 templates/ 下；从磁盘选的图会自动复制过来，换分辨率后要重新截。" },
    ),
    field(
      "找到后",
      select({
        id: "step-image-action",
        value: task.action || "click",
        options: [
          { value: "click", label: "点一下" },
          { value: "double", label: "双击" },
          { value: "check", label: "只判断，不点击" },
        ],
        onChange: (value) => upd({ action: value }),
      }),
    ),
    ...(String(task.action || "click") === "check" ? [] : [pointPanel(task)]),
    field(
      "相似度阈值",
      numberInput({
        id: "step-threshold",
        value: task.threshold ?? 0.88,
        step: 0.01,
        min: 0,
        onInput: (value) => upd({ threshold: value }),
      }),
      { hint: "0.85–0.92 之间一般够用。" },
    ),
    field(
      "同时要看到文字（可填）",
      h("div.stack-rows", {}, [
        input({
          id: "step-image-expect",
          value: task.expect || "",
          placeholder: "例如：确认、立即购买",
          onInput: (value) => upd({ expect: value }),
        }),
        checkbox("只认完全相同的文字", Boolean(task.exact), (checked) =>
          upd({ exact: checked }),
        ),
      ]),
      {
        full: true,
        hint: "填了就只有「图和这段字同时出现」才算命中，能压掉长得像的误点。",
      },
    ),
    field(
      "最多等几秒出现",
      numberInput({
        id: "step-image-wait",
        value: task.wait ?? 0,
        step: 0.5,
        min: 0,
        onInput: (value) => upd({ wait: value }),
      }),
      { hint: "0 = 只找一次。" },
    ),
    field(
      "找图区域（可空）",
      boxControl(task, index),
      { full: true, hint: "整个屏幕找不慢；只在固定区域找会更稳。" },
    ),
    field(
      "",
      checkbox("找不到就跳过（算这一步过了，继续往下跑）", Boolean(task.allow_missing), (checked) =>
        upd({ allow_missing: checked }),
      ),
      { full: true },
    ),
    ...(String(task.action || "click") === "check" ? [] : [thenPanel(task, index)]),
  ]);
}

/** 点完图之后接着做的事（从组合里挑，或单独加一步），收在找图这一步里。 */
function thenPanel(task, index) {
  const then = Array.isArray(task.then) ? task.then : [];
  const combos = store.state.combos || [];
  const options = [{ value: "", label: "— 从组合里挑 —" }, ...combos.map((c) => ({ value: c.name, label: `${c.name}（${c.tasks.length} 步）` }))];

  return field(
    "点完图之后（可选）",
    h("div.block", {}, [
      h("div.block__row", {}, [
        select({
          id: "step-then-combo",
          value: "",
          options,
          onChange: (name) => {
            const combo = combos.find((c) => c.name === name);
            if (!combo) return;
            const merged = then.concat(combo.tasks.map((t) => structuredClone(t)));
            upd({ then: merged });
          },
        }),
        h(
          "button.btn.btn-ghost.btn-small",
          { type: "button", "on:click": () => upd({ then: [] }) },
          "清空",
        ),
      ]),
      then.length
        ? h(
            "div.then-list",
            {},
            then.map((step, ti) =>
              h("div.then-item", {}, [
                h("span.then-item__no", { text: String(ti + 1) }),
                h("span.then-item__name", { text: store.stepTitle(step) }),
                h("span.then-item__kind", { text: kindLabel(store.stepKind(step)) }),
                h(
                  "button.then-item__del",
                  {
                    type: "button",
                    "aria-label": `删掉第 ${ti + 1} 个后续动作`,
                    title: "删掉这一步",
                    "on:click": () => upd({ then: then.filter((_, i) => i !== ti) }),
                  },
                  [icon("trash", 12)],
                ),
              ]),
            ),
          )
        : h("p.block__hint", { text: "选一个组合，点中图之后会接着跑它的步骤；不选就只点一下图。" }),
    ]),
    { full: true, hint: "后续步骤存进这一步的 JSON（then 数组），换任务文件也跟着走。" },
  );
}

function shellSection(task, index) {
  return section("执行命令", [
    field(
      "命令",
      h("textarea", {
        id: "step-cmd",
        class: "textarea",
        rows: "3",
        placeholder: "echo hello",
        "on:input": (event) => upd({ cmd: event.target.value }),
        text: task.cmd || "",
      }),
      { full: true },
    ),
    field(
      "工作目录（可空）",
      input({
        id: "step-cwd",
        value: task.cwd || "",
        placeholder: "默认是任务文件所在目录",
        mono: true,
        onInput: (value) => upd({ cwd: value }),
      }),
      { full: true },
    ),
  ]);
}

function pythonSection(task, index) {
  return section("跑脚本", [
    field(
      "脚本路径（二选一）",
      input({
        id: "step-script",
        value: task.script || "",
        placeholder: "scripts/hello.py",
        mono: true,
        onInput: (value) => upd({ script: value }),
      }),
      { full: true, hint: "填了路径就跑文件；留空则跑下面的代码。" },
    ),
    field(
      "直接写代码",
      h("textarea", {
        id: "step-code",
        class: "textarea",
        rows: "6",
        placeholder: "print('hello from pyauto')",
        "on:input": (event) => upd({ code: event.target.value }),
        text: task.code || "",
      }),
      { full: true },
    ),
  ]);
}

function rawSection(task, index) {
  return section("自定义任务", [
    field(
      "type",
      input({
        id: "step-type",
        value: task.type || "shell",
        mono: true,
        onInput: (value) => upd({ type: value }),
      }),
    ),
    field("完整 JSON（改完点应用）", rawEditor(task, index), { full: true }),
  ]);
}

function rawEditor(task, index) {
  const area = h("textarea", {
    id: "step-raw",
    class: "textarea",
    rows: "8",
    spellcheck: "false",
    text: JSON.stringify(task, null, 2),
  });
  const apply = h(
    "button.btn.btn-ghost.btn-small",
    {
      type: "button",
      "on:click": () => {
        try {
          const parsed = JSON.parse(area.value);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("要是一个 JSON 对象");
          const list = store.tasks();
          list[index] = parsed;
          upd({});
          store.selectStep(index);
          toast("已替换这一步");
        } catch (err) {
          toast(`JSON 不合法：${err.message}`, "danger");
        }
      },
    },
    ["应用"],
  );
  return h("div.field__row", { style: { display: "grid", gap: "8px" } }, [area, h("div", {}, [apply])]);
}

function boxControl(task, index) {
  const box = Array.isArray(task.box) ? task.box : null;
  const label = box && box.some((v) => Number(v)) ? `${box[0]}, ${box[1]}  ${box[2]}×${box[3]}` : "整个屏幕";

  return h("div.field__row", { style: { alignItems: "center" } }, [
    h("span.input.input--mono", { style: { display: "flex", alignItems: "center" }, text: label }),
    h(
      "button.btn.btn-ghost.btn-small",
      {
        type: "button",
        "on:click": async () => {
          const picked = await openPicker({ mode: "region", title: "框选查找区域", initial: box });
          if (picked && picked.box) {
            upd({ box: picked.box });
            toast(`区域：${picked.box.join(", ")}`);
          }
        },
      },
      [icon("crop", 14), "框选"],
    ),
    box
      ? h(
          "button.icon-btn",
          {
            type: "button",
            "aria-label": "清除区域",
            title: "清除区域",
            "on:click": () => upd({ box: null }),
          },
          ["✕"],
        )
      : null,
  ]);
}

function rulesCallout(task, index) {
  const rules = Array.isArray(task.recover) ? task.recover : [];
  return h("div.rules-callout", {}, [
    h("div.rules-callout__text", {}, [
      h("p.rules-callout__title", { text: `这一步的异常处理${rules.length ? `（${rules.length} 条）` : ""}` }),
      h("p.rules-callout__hint", { text: "跑这一步之前，先看屏幕上有没有这些画面；有就先处理，再回来重试。" }),
    ]),
    h(
      "button.btn.btn-ghost.btn-small",
      {
        type: "button",
        "on:click": async () => {
          const result = await openRules({
            rules,
            title: "这一步的异常处理",
          });
          if (result) {
            store.upsertRulesForTask(CTX.index, result, CTX.child);
            toast(result.length ? `已保存 ${result.length} 条规则` : "已清空这一步的规则");
          }
        },
      },
      ["编辑…"],
    ),
  ]);
}

/* ------------------------------------------------------------ 整体设置 */

async function editGlobalRules() {
  const result = await openRules({
    rules: store.state.config.recover || [],
    title: "全局异常处理",
  });
  if (!result) return;
  store.setRules(result);
  toast(result.length ? `已保存 ${result.length} 条全局规则` : "已清空全局规则");
}

function renderSettings(root) {
  const config = store.state.config;
  mount(root, [
    h("div.empty", {}, [
      icon("sliders", 20),
      h("p.empty__title", { text: "这里是整体设置" }),
      h("p", { text: "选中左边任意一步，就能改它的参数。下面是整份任务共用的设置。" }),
    ]),
    section("任务设置", [
      field(
        "日志文件",
        input({
          id: "cfg-log",
          value: config.log || "",
          mono: true,
          onInput: (value) => store.setSetting("log", value),
        }),
        { full: true },
      ),
      field(
        "运行历史（jsonl）",
        input({
          id: "cfg-history",
          value: config.history || "",
          mono: true,
          onInput: (value) => store.setSetting("history", value),
        }),
        { full: true },
      ),
      field(
        "单步超时（秒）",
        numberInput({
          id: "cfg-timeout",
          value: config.timeout ?? 120,
          min: 1,
          onInput: (value) => store.setSetting("timeout", value),
        }),
      ),
      field(
        "",
        checkbox("出错就停", config.stop_on_error !== false, (checked) => store.setSetting("stop_on_error", checked)),
        { full: true },
      ),
      field(
        "",
        checkbox(
          "出错时先跑全局异常处理，再重试这一步（仍不行就继续后面的步骤）",
          config.recover_on_error !== false,
          (checked) => store.setSetting("recover_on_error", checked),
        ),
        { full: true, hint: "配合下面的「全局异常处理」用：比如弹了公告窗，就先点掉它，再重试刚才失败的那步。" },
      ),
      field(
        "全局异常处理",
        h(
          "button.btn.btn-ghost.btn-small",
          { type: "button", "on:click": () => editGlobalRules() },
          [icon("sliders", 14), `编辑…（${(config.recover || []).length} 条）`],
        ),
        { full: true, hint: "这些规则每一步之前都会先查一遍，出错时也会用。" },
      ),
    ]),
  ]);
}

function kindLabel(kind) {
  const map = { click: "点击", type: "输入", key: "按键", wait: "等待", ocr: "找字", image: "找图", shell: "命令", python: "脚本", raw: "自定义" };
  return map[kind] || kind;
}

/** 从磁盘挑一张图，交给后端复制进 templates/，再填回当前步骤。 */
function browseTemplate(index) {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "image/png,image/jpeg,image/webp,image/bmp,image/gif";
  picker.style.display = "none";
  document.body.appendChild(picker);

  picker.addEventListener(
    "change",
    async () => {
      const file = picker.files && picker.files[0];
      picker.remove();
      if (!file) return;
      if (file.size > 12 * 1024 * 1024) {
        toast("图片太大（上限 12 MB）", "danger");
        return;
      }
      try {
        const data = await readDataURL(file);
        const saved = await api.uploadTemplate(file.name, data);
        await applyTemplate(index, saved.path);
        toast(`已复制到 ${saved.path}${saved.size ? `（${saved.size[0]}×${saved.size[1]}）` : ""}，已选中`);
      } catch (err) {
        toast(err.message, "danger");
      }
    },
    { once: true },
  );

  picker.click();
}

function readDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("这个文件读不出来"));
    reader.readAsDataURL(file);
  });
}

/**
 * 把新模板写进当前步骤并刷新下拉。
 * 顺序很重要：先改任务里的 template，再刷模板列表，这样重绘时下拉里既有新选项、又已经选中它。
 */
async function applyTemplate(index, path) {
  upd({ template: path });
  await store.refreshTemplates();

  // 兜底：万一列表接口失败没触发重绘，直接把下拉改过来
  const sel = document.getElementById("step-template");
  if (sel && sel.value !== path) {
    if (!Array.from(sel.options).some((opt) => opt.value === path)) {
      sel.appendChild(new Option(path, path));
    }
    sel.value = path;
  }
}
