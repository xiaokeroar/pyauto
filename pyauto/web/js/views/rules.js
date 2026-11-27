// 异常处理（因果规则）编辑器：当屏幕上出现 X，就先做 Y，再回来重试。

import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { toast } from "../toast.js";

let items = [];
let selected = 0;
let resolveFn = null;
let controller = null;

const el = (id) => document.getElementById(id);

const THEN_KINDS = [
  { value: "click", label: "点击" },
  { value: "type", label: "输入" },
  { value: "key", label: "按键" },
  { value: "wait", label: "等待" },
  { value: "image", label: "找图" },
];

export function initRules() {
  const dialog = el("rulesDialog");
  dialog.addEventListener("close", () => finish(null));
  el("ruleAdd").onclick = () => {
    items.push(blankRule());
    selected = items.length - 1;
    render();
  };
  el("rulesConfirm").onclick = () => {
    const error = validate();
    if (error) {
      toast(error, "danger");
      return;
    }
    finish(items.map(toConfigRule));
  };
  document.querySelectorAll("#rulesDialog [data-close]").forEach((btn) => {
    btn.onclick = () => finish(null);
  });
}

/**
 * @param {{rules?: object[], title?: string}} opts
 * @returns {Promise<object[] | null>}
 */
export function openRules({ rules = [], title = "异常处理" } = {}) {
  const dialog = el("rulesDialog");
  if (!dialog) return Promise.resolve(null);
  items = (rules || []).map(fromConfigRule);
  if (!items.length) items = [blankRule()];
  selected = 0;
  el("rulesTitle").textContent = title;
  render();
  dialog.showModal();
  return new Promise((resolve) => {
    resolveFn = resolve;
  });
}

function blankRule() {
  return {
    name: "",
    when: { kind: "image", value: "", threshold: 0.88, exact: false },
    then: [],
    thenAction: "retry",
  };
}

function fromConfigRule(rule) {
  const when = rule.when || {};
  const isImage = Boolean(when.image);
  return {
    name: rule.name || "",
    when: {
      kind: isImage ? "image" : "ocr",
      value: isImage ? when.image : when.ocr || "",
      threshold: when.threshold ?? 0.88,
      exact: Boolean(when.exact),
    },
    then: (rule.then || []).map(fromTask),
    thenAction: rule.then_action || "retry",
  };
}

function fromTask(task) {
  const type = task.type;
  if (type === "sleep") return { kind: "wait", params: String(task.seconds ?? 1) };
  if (type === "image") return { kind: "image", params: String(task.template || "") };
  if (type === "input") {
    if (task.action === "type") return { kind: "type", params: String(task.value || "") };
    if (task.action === "key") return { kind: "key", params: String(task.value || "") };
    return { kind: "click", params: `${task.x ?? 0}, ${task.y ?? 0}` };
  }
  return { kind: "wait", params: "0.5" };
}

function toTask(item) {
  if (item.kind === "wait") {
    const seconds = Number(item.params);
    return { type: "sleep", seconds: Number.isFinite(seconds) ? seconds : 0.5 };
  }
  if (item.kind === "image") return { type: "image", template: item.params.trim(), action: "click" };
  if (item.kind === "type") return { type: "input", action: "type", value: item.params };
  if (item.kind === "key") return { type: "input", action: "key", value: item.params };
  const [x, y] = item.params.split(",").map((part) => Number(part.trim()));
  return { type: "input", action: "click", x: Number.isFinite(x) ? Math.round(x) : 0, y: Number.isFinite(y) ? Math.round(y) : 0 };
}

function toConfigRule(rule) {
  const when =
    rule.when.kind === "image"
      ? { image: rule.when.value.trim(), threshold: Number(rule.when.threshold) || 0.88 }
      : { ocr: rule.when.value.trim(), ...(rule.when.exact ? { exact: true } : {}) };
  return {
    ...(rule.name.trim() ? { name: rule.name.trim() } : {}),
    when,
    then: rule.then.map(toTask),
    then_action: rule.thenAction,
  };
}

function validate() {
  for (let i = 0; i < items.length; i += 1) {
    const rule = items[i];
    if (!rule.when.value.trim()) {
      return `第 ${i + 1} 条还没填${rule.when.kind === "image" ? "模板图片" : "要找的文字"}`;
    }
    if (!rule.then.length) return `第 ${i + 1} 条还没有「先做这些」的动作`;
  }
  return "";
}

/* ------------------------------------------------------------ 渲染 */

function render() {
  renderList();
  renderForm();
}

function renderList() {
  const list = el("rulesList");
  mount(
    list,
    items.map((rule, index) =>
      h(
        "button.rule-item",
        {
          type: "button",
          role: "option",
          "aria-selected": String(index === selected),
          "on:click": () => {
            selected = index;
            render();
          },
        },
        [
          h("span.rule-item__title", { text: ruleLabel(rule, index) }),
          h("span.rule-item__meta", { text: `${rule.then.length} 个处理动作` }),
        ],
      ),
    ),
  );
}

function ruleLabel(rule, index) {
  const value = rule.when.value.trim();
  const head = rule.when.kind === "image" ? `图片 ${value ? value.split(/[\\/]/).pop() : "未选"}` : `文字「${value || "未填"}」`;
  return `${index + 1}. ${head}`;
}

function renderForm() {
  const form = el("rulesForm");
  const rule = items[selected];
  if (!rule) {
    mount(form, [h("p.field__hint", { text: "点左下角「新增规则」开始。" })]);
    return;
  }

  const whenImage = rule.when.kind === "image";

  mount(form, [
    h("div.form-grid", {}, [
      h("div.field.field--full", {}, [
        h("label.field__label", { text: "规则名（可空）" }),
        h("input.input", {
          value: rule.name,
          placeholder: "比如：公告弹窗",
          "on:input": (event) => {
            rule.name = event.target.value;
            renderList();
          },
        }),
      ]),
      h("div.field", {}, [
        h("label.field__label", { text: "当屏幕出现" }),
        selectControl(
          [
            { value: "image", label: "一张图片" },
            { value: "ocr", label: "一段文字" },
          ],
          rule.when.kind,
          (value) => {
            rule.when.kind = value;
            renderForm();
          },
        ),
      ]),
      h("div.field", {}, [
        h("label.field__label", { text: whenImage ? "模板图片路径" : "要找的文字" }),
        h("input.input", {
          class: whenImage ? "input input--mono" : "input",
          value: rule.when.value,
          placeholder: whenImage ? "templates/notice.png" : "比如：稍后再说",
          "on:input": (event) => {
            rule.when.value = event.target.value;
            renderList();
          },
        }),
      ]),
      whenImage
        ? h("div.field", {}, [
            h("label.field__label", { text: "相似度阈值" }),
            h("input.input", {
              type: "number",
              step: "0.01",
              min: "0",
              value: String(rule.when.threshold),
              "on:input": (event) => {
                rule.when.threshold = Number(event.target.value) || 0.88;
              },
            }),
          ])
        : h("div.field", {}, [
            h("label.checkbox", {}, [
              h("input", {
                type: "checkbox",
                checked: rule.when.exact,
                "on:change": (event) => {
                  rule.when.exact = event.target.checked;
                },
              }),
              h("span", { text: "只认完全相同的文字" }),
            ]),
          ]),
    ]),

    h("div.form-section", {}, [
      h("h3.form-section__title", { text: "先做这些（做完再回来重试本步）" }),
      h("div.then-list", {}, rule.then.map((item, index) => thenRow(rule, item, index))),
      h(
        "div.field__row",
        { style: { marginTop: "12px", flexWrap: "wrap" } },
        THEN_KINDS.map((kind) =>
          h(
            "button.btn.btn-ghost.btn-small",
            {
              type: "button",
              "on:click": () => {
                rule.then.push({ kind: kind.value, params: defaultParams(kind.value) });
                renderForm();
              },
            },
            [icon("plus", 12), kind.label],
          ),
        ),
      ),
    ]),

    h("div.form-grid", {}, [
      h("div.field", {}, [
        h("label.field__label", { text: "处理完之后" }),
        selectControl(
          [
            { value: "retry", label: "重试本步" },
            { value: "skip", label: "跳过本步" },
          ],
          rule.thenAction,
          (value) => {
            rule.thenAction = value;
          },
        ),
      ]),
      h("div.field", {}, [
        h("label.field__label", { text: "当前规则" }),
        h(
          "button.btn.btn-ghost",
          {
            type: "button",
            "on:click": () => {
              items.splice(selected, 1);
              if (!items.length) items = [blankRule()];
              selected = Math.max(0, selected - 1);
              render();
            },
          },
          [icon("trash", 14), "删除这条规则"],
        ),
      ]),
    ]),
  ]);
}

function thenRow(rule, item, index) {
  return h("div.then-row", {}, [
    selectControl(
      THEN_KINDS.map((kind) => ({ value: kind.value, label: kind.label })),
      item.kind,
      (value) => {
        item.kind = value;
        item.params = defaultParams(value);
        renderForm();
      },
    ),
    h("input.input", {
      class: item.kind === "click" || item.kind === "wait" || item.kind === "image" ? "input input--mono" : "input",
      value: item.params,
      placeholder: placeholders[item.kind],
      "on:input": (event) => {
        item.params = event.target.value;
      },
    }),
    h(
      "button.icon-btn",
      {
        type: "button",
        "aria-label": "删除这个处理动作",
        "on:click": () => {
          rule.then.splice(index, 1);
          renderForm();
        },
      },
      [icon("trash", 14)],
    ),
  ]);
}

const placeholders = {
  click: "x, y",
  type: "要输入的文字",
  key: "ctrl+s",
  wait: "秒数",
  image: "模板路径",
};

function defaultParams(kind) {
  if (kind === "click") return "960, 540";
  if (kind === "wait") return "0.8";
  if (kind === "key") return "enter";
  return "";
}

function selectControl(options, value, onChange) {
  const node = h(
    "select.select",
    { "on:change": (event) => onChange(event.target.value) },
    options.map((opt) => h("option", { value: opt.value, text: opt.label, selected: opt.value === value })),
  );
  node.value = value;
  return node;
}

function finish(result) {
  const dialog = el("rulesDialog");
  if (dialog && dialog.open) dialog.close();
  if (controller) {
    controller.abort();
    controller = null;
  }
  const fn = resolveFn;
  resolveFn = null;
  if (fn) fn(result);
}
