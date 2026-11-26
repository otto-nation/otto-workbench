// Settings view: global models.* pickers (Session + three tiers), read-only harness preview,
// and pinned Sync action bar. Pure view over state from settings/main.js and _lib/settings.js.
import { div } from "gpui-kit";
import { h_flex, v_flex, Popover } from "gpui-base";
import { theme } from "herdr";
import { LAYOUT, SP } from "./_lib/tokens.js";
import {
  SETTINGS_TARGETS, TARGET_LABEL, currentValues, displayValue, isCustomValue,
  previewModel, syncBarModel, targetOptions,
} from "./_lib/settings.js";
import {
  actionBar, button, caption, card, errorLine, hoverRow, keyValue, menuButton,
  pane, paneHeader, placeholder, popoverSurface, scrollBody, sectionLabel,
  solidButton, statusText, text, textInput,
} from "./_ui/ui.js";

function choicesFailCaption(error) {
  const kind = error?.kind;
  const msg = error?.message ?? "";
  if (kind === "timeout") return "Timed out listing models — enter a model id.";
  if (kind === "outdated" || /invalid choice: ['"]models['"]/i.test(msg)) {
    return "Update pr on this machine to pick from the catalogue (otto-workbench sync).";
  }
  return "Could not list models — enter a model id.";
}

function saveCustomButton(target, props) {
  return div().flex_none().child(
    button(`settings-${target}-save`, "Save", () => props.onSaveCustom(target), {
      disabled: props.busy,
      reason: props.busy ? "Saving" : null,
    }),
  );
}

function renderCustomInput(target, props, open) {
  const row = hoverRow(`settings-${target}-custom`, { selected: open })
    .on_click(() => props.onOpenCustom(target))
    .child(text("label", "Custom…").px(SP.xs).py(SP.xxs));
  if (!open) return row;
  return v_flex().gap(SP.xxs)
    .child(row)
    .child(textInput(props.customInputs.get(target)))
    .child(saveCustomButton(target, props));
}

function renderOptionRow(target, row, idx, props) {
  const t = theme();
  const id = `settings-${target}-opt-${idx}`;
  if (row.disabled) {
    return v_flex().id(id).px(SP.xs).py(SP.xxs).gap(SP.xxs)
      .child(text("label", row.label, t.muted))
      .when(Boolean(row.reason), (e) => e.child(caption(row.reason)));
  }
  return hoverRow(id, { selected: row.selected })
    .on_click(() => { if (!props.busy) props.onPick(target, row.ref); })
    .child(text("label", row.label).px(SP.xs).py(SP.xxs));
}

function renderPopoverContent(target, current, props) {
  const choices = props.choices;
  if (choices == null) {
    return popoverSurface().child(caption("Loading…"));
  }
  if (!choices.ok) {
    return popoverSurface()
      .child(caption(choicesFailCaption(choices.error)))
      .child(textInput(props.customInputs.get(target)))
      .child(saveCustomButton(target, props));
  }
  const opts = targetOptions(choices.doc, target, props.query, current);
  const kids = [];
  // One index across sections: GPUI keys hit-testing by id.
  let n = 0;
  for (const section of opts.sections) {
    kids.push(sectionLabel(section.label));
    for (const row of section.rows) kids.push(renderOptionRow(target, row, n++, props));
  }
  const customOpen = props.customOpen === target || isCustomValue(choices.doc, target, current);
  return popoverSurface()
    .when(Boolean(opts.catalogueError), (e) => e.child(caption(opts.catalogueError)))
    .child(textInput(props.search))
    .child(div().id(`settings-${target}-list`).max_h(LAYOUT.bodyMaxH).overflow_y_scrollbar()
      .child(v_flex().gap(SP.xxs).children(kids)))
    .child(renderCustomInput(target, props, customOpen));
}

function renderTargetRow(target, current, props) {
  const label = TARGET_LABEL[target];
  const shown = displayValue(current);
  const hasValue = Boolean(current);
  const trigger = menuButton(label, { active: hasValue });
  const popover = Popover.new(`settings-${target}-popover`)
    .open(props.openTarget === target)
    .on_open_change((open) => props.onToggleTarget(target, open, current))
    .trigger(trigger)
    .content(renderPopoverContent(target, current, props));
  return h_flex().gap(SP.sm).items_center().min_w(0)
    .child(popover)
    .child(
      hasValue
        ? text("code", shown).min_w(0).flex_1().truncate()
        : caption(shown).min_w(0).flex_1().truncate(),
    );
}

function renderModelsCard(values, props) {
  return card("settings-models", { linked: false }).gap(SP.sm)
    .child(sectionLabel("Models"))
    .child(caption("Machine-wide defaults (~/.config/workbench/config.yml)"))
    .children(SETTINGS_TARGETS.map((t) => renderTargetRow(t, values[t], props)));
}

function renderPreviewCard(doc) {
  const { rows, skipped } = previewModel(doc);
  return card("settings-preview", { linked: false }).gap(SP.sm)
    .child(sectionLabel("Preview"))
    .child(rows.length > 0 ? keyValue(rows) : caption("No models configured"))
    .children(skipped.map((note) => caption(note)));
}

function renderSyncBar(props) {
  const bar = syncBarModel({ dirty: props.dirty, saving: props.saving, syncing: props.syncing });
  const slot = bar.status
    ? statusText(bar.status, bar.text)
    : caption(bar.text);
  return actionBar()
    .when(Boolean(props.note), (e) => e.child(errorLine(props.note)))
    .child(h_flex().gap(SP.sm).items_center()
      .child(solidButton("settings-sync", "Sync", () => props.onSync(), {
        disabled: bar.disabled,
        reason: bar.reason,
      }))
      .child(slot));
}

export function renderSettings(props) {
  const header = paneHeader("Settings", {
    spinning: props.spinning,
    onRefresh: () => props.onRefresh(),
    id: "settings-refresh",
  });

  if (props.models == null) {
    return pane("settings")
      .child(header)
      .child(scrollBody().p(SP.lg).child(placeholder("Loading…")));
  }

  if (!props.models.ok) {
    return pane("settings")
      .child(header)
      .child(scrollBody().p(SP.lg).gap(SP.sm)
        .child(errorLine(props.models.error?.message ?? "Could not read config models"))
        .child(div().flex_none().child(button("settings-retry", "Retry", () => props.onRefresh()))));
  }

  const values = currentValues(props.models.doc);
  return pane("settings")
    .child(header)
    .child(scrollBody().p(SP.lg).gap(SP.md)
      .child(renderModelsCard(values, props))
      .child(renderPreviewCard(props.models.doc)))
    .child(renderSyncBar(props));
}
