"""The declarative plugin UI contract (BIZ-303): what a plugin may describe instead of shipping frontend code.

Two surfaces, one version (`SCHEMA_VERSION`):

* **Settings form** — generated from the plugin's pydantic `settings_model` (JSON Schema). Supported field kinds: `boolean`,
  `number` (int/float, `minimum`/`maximum`), `text` (str, optionally with `maxLength`), `list` (list[str]) and `choices`
  (list of a fixed string enum). Anything else (nested objects, lists of non-strings, ...) is *unsupported*: the page shows the
  field as not editable here rather than guessing an input, and the plugin carries a diagnostic.
* **`schema` tabs** — the JSON a plugin's `ui_schema(tab_id)` returns (docs/plugins.md "Declarative UI"). `validate_tab_schema`
  accepts exactly the constructs below and rejects everything else with a path-prefixed message.

Both are validated when the plugin is registered (`inspect_manifest`). A problem never stops registration or boot: it is
recorded as a plugin-scoped diagnostic and the affected contribution fails closed (a bad tab is not offered; a bad settings
field is not rendered). The frontend re-validates what it receives (`frontend/src/api/uiSchema.ts`, same rules, same shared test
corpus `contracts/ui-schema-cases.json`) so it never renders a document it was not built for. The document is read once, at
registration: a `ui_schema` that needs runtime state fails with a diagnostic rather than being asked again.

Tab document (v1)::

    {"schema_version": 1,                       # optional, defaults to 1; any other value fails closed
     "title": "Library",
     "blocks": [
       {"type": "form",  "title"?, "description"?, "load": "path", "save": "path", "fields": [FIELD, ...]},
       {"type": "table", "title"?, "description"?, "data": "path", "columns": [{"key", "label"}, ...], "empty"?,
        "create"?: {"path", "label"?, "fields": [FIELD, ...]},
        "row_actions"?: [{"label", "method": "POST"|"DELETE"|"PATCH", "path", "confirm"?, "body"?}]}]}
    FIELD = {"key", "label", "type": "text"|"number"|"textarea"|"checkbox"|"select", "required"?, "help"?,
             "options"?: [{"value","label"}]            # select only, non-empty
             "min"?, "max"?                              # number only
             "max_length"?                               # text/textarea only
             "visible_if"?: {"key": <another field of the same list>, "equals": <value>}}   # conditional visibility

Paths are relative to the plugin's own API (`/api/v1/plugins/<id>/...`), may carry `{key}` placeholders (any key of the row; the frontend refuses an empty value or `.`/`..`) and may not
contain `..`, a scheme or a leading `/`."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .manifest import PluginManifest

SCHEMA_VERSION = 1
FIELD_TYPES = ("text", "number", "textarea", "checkbox", "select")
BLOCK_TYPES = ("form", "table")
ACTION_METHODS = ("POST", "DELETE", "PATCH")
SETTINGS_KINDS = ("boolean", "number", "text", "list", "choices")

_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./{}]+$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SchemaProblem(ValueError):
    """The declarative schema uses something the contract does not allow (message is path-prefixed and safe to show)."""


@dataclass(frozen=True)
class Diagnostic:
    """A plugin-scoped problem with one UI contribution. `scope` is `ui.tab.<tab id>` or `settings.<field>`."""
    scope: str
    message: str

    def as_dict(self) -> dict:
        return {"scope": self.scope, "message": self.message}


@dataclass
class UiReport:
    # tab id -> the validated document (only tabs that passed)
    tabs: dict[str, dict] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def tab_problem(self, tab_id: str) -> str | None:
        return next((d.message for d in self.diagnostics if d.scope == f"ui.tab.{tab_id}"), None)


# ---- tab documents ---------------------------------------------------------------------------------------------------


def _need(cond: bool, where: str, message: str) -> None:
    if not cond:
        raise SchemaProblem(f"{where}: {message}")


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _check_unknown(obj: dict, allowed: set[str], where: str) -> None:
    extra = sorted(set(obj) - allowed)
    _need(not extra, where, f"unsupported key(s) {extra}")


def _check_path(v: Any, where: str) -> None:
    _need(isinstance(v, str) and bool(v), where, "a path is required")
    _need(bool(_PATH_RE.fullmatch(v)) and ".." not in v.split("/") and not v.startswith("/") and ":" not in v,
          where, f"{v!r} is not a path relative to the plugin's API")


def _check_field(f: Any, where: str, siblings: set[str]) -> None:
    _need(isinstance(f, dict), where, "a field must be an object")
    t = f.get("type")
    _need(t in FIELD_TYPES, f"{where}.type", f"unsupported field type {t!r} (supported: {', '.join(FIELD_TYPES)})")
    allowed = {"key", "label", "type", "required", "help", "visible_if"}
    allowed |= {"options"} if t == "select" else {"min", "max"} if t == "number" else {"max_length"} if t in ("text", "textarea") else set()
    _check_unknown(f, allowed, where)
    _need(isinstance(f.get("key"), str) and bool(_KEY_RE.match(f["key"])), f"{where}.key", "a field key must be an identifier")
    _need(_is_str(f.get("label")), f"{where}.label", "a label is required")
    _need(isinstance(f.get("required", False), bool), f"{where}.required", "must be true or false")
    _need(f.get("help") is None or isinstance(f["help"], str), f"{where}.help", "must be text")
    if t == "select":
        opts = f.get("options")
        _need(isinstance(opts, list) and bool(opts), f"{where}.options", "a select needs at least one option")
        for i, o in enumerate(opts):
            _need(isinstance(o, dict) and set(o) == {"value", "label"} and isinstance(o["value"], str) and _is_str(o["label"]),
                  f"{where}.options[{i}]", "an option is {value, label}")
    if t == "number":
        lo, hi = f.get("min"), f.get("max")
        for name, v in (("min", lo), ("max", hi)):
            _need(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)), f"{where}.{name}", "must be a number")
        _need(lo is None or hi is None or lo <= hi, where, "min is greater than max")
    if t in ("text", "textarea") and f.get("max_length") is not None:
        _need(isinstance(f["max_length"], int) and not isinstance(f["max_length"], bool) and f["max_length"] > 0,
              f"{where}.max_length", "must be a positive integer")
    vis = f.get("visible_if")
    if vis is not None:
        _need(isinstance(vis, dict) and set(vis) == {"key", "equals"}, f"{where}.visible_if", "is {key, equals}")
        _need(vis["key"] in siblings and vis["key"] != f.get("key"), f"{where}.visible_if.key",
              f"{vis['key']!r} is not another field of this form")
        _need(isinstance(vis["equals"], (str, int, float, bool)), f"{where}.visible_if.equals", "must be text, a number or true/false")


def _check_fields(fields: Any, where: str) -> None:
    _need(isinstance(fields, list) and bool(fields), where, "at least one field is required")
    keys = [f.get("key") for f in fields if isinstance(f, dict)]
    _need(len(keys) == len(set(keys)), where, "field keys must be unique")
    for i, f in enumerate(fields):
        _check_field(f, f"{where}[{i}]", set(keys))


def _check_block(b: Any, where: str) -> None:
    _need(isinstance(b, dict), where, "a block must be an object")
    t = b.get("type")
    _need(t in BLOCK_TYPES, f"{where}.type", f"unsupported block type {t!r} (supported: {', '.join(BLOCK_TYPES)})")
    common = {"type", "title", "description"}
    for k in ("title", "description"):
        _need(b.get(k) is None or isinstance(b[k], str), f"{where}.{k}", "must be text")
    if t == "form":
        _check_unknown(b, common | {"load", "save", "fields"}, where)
        _check_path(b.get("load"), f"{where}.load")
        _check_path(b.get("save"), f"{where}.save")
        _check_fields(b.get("fields"), f"{where}.fields")
        return
    _check_unknown(b, common | {"data", "columns", "create", "row_actions", "empty"}, where)
    _check_path(b.get("data"), f"{where}.data")
    cols = b.get("columns")
    _need(isinstance(cols, list) and bool(cols), f"{where}.columns", "at least one column is required")
    for i, c in enumerate(cols):
        _need(isinstance(c, dict) and set(c) == {"key", "label"} and isinstance(c["key"], str) and _is_str(c["label"]),
              f"{where}.columns[{i}]", "a column is {key, label}")
    _need(b.get("empty") is None or isinstance(b["empty"], str), f"{where}.empty", "must be text")
    if (create := b.get("create")) is not None:
        _need(isinstance(create, dict), f"{where}.create", "must be an object")
        _check_unknown(create, {"path", "label", "fields"}, f"{where}.create")
        _check_path(create.get("path"), f"{where}.create.path")
        _check_fields(create.get("fields"), f"{where}.create.fields")
    actions = b.get("row_actions")
    _need(actions is None or isinstance(actions, list), f"{where}.row_actions", "must be a list")
    for i, a in enumerate(actions or []):
        w = f"{where}.row_actions[{i}]"
        _need(isinstance(a, dict), w, "an action must be an object")
        _check_unknown(a, {"label", "method", "path", "confirm", "body"}, w)
        _need(_is_str(a.get("label")), f"{w}.label", "a label is required")
        _need(a.get("method") in ACTION_METHODS, f"{w}.method", f"must be one of {', '.join(ACTION_METHODS)}")
        _check_path(a.get("path"), f"{w}.path")
        _need(a.get("confirm") is None or isinstance(a["confirm"], str), f"{w}.confirm", "must be text")
        _need(a.get("body") is None or isinstance(a["body"], dict), f"{w}.body", "must be an object")


def validate_tab_schema(doc: Any) -> dict:
    """Return `doc` when it is a valid v1 tab document; raise `SchemaProblem` (path-prefixed message) otherwise."""
    _need(isinstance(doc, dict), "schema", "must be an object")
    version = doc.get("schema_version", SCHEMA_VERSION)
    _need(version == SCHEMA_VERSION, "schema_version",
          f"{version!r} is not supported (this Themis renders schema_version {SCHEMA_VERSION})")
    _check_unknown(doc, {"schema_version", "title", "blocks"}, "schema")
    _need(doc.get("title") is None or isinstance(doc["title"], str), "title", "must be text")
    blocks = doc.get("blocks")
    _need(isinstance(blocks, list), "blocks", "must be a list")
    for i, b in enumerate(blocks):
        _check_block(b, f"blocks[{i}]")
    return doc


# ---- the settings form -----------------------------------------------------------------------------------------------


def _resolve(prop: dict, defs: dict) -> dict:
    ref = prop.get("$ref") or next((a["$ref"] for a in prop.get("allOf", []) if "$ref" in a), None)
    return {**defs.get(ref.rsplit("/", 1)[-1], {}), **{k: v for k, v in prop.items() if k not in ("$ref", "allOf")}} if ref else prop


def settings_kind(prop: dict, defs: dict | None = None) -> str | None:
    """The supported field kind a JSON-Schema property renders as, or None when the contract does not cover it. Mirrors
    `settingsKind` in frontend/src/api/uiSchema.ts: change both together."""
    defs = defs or {}
    prop = _resolve(prop, defs)
    branches = [_resolve(b, defs) for b in prop["anyOf"]] if "anyOf" in prop else [prop]
    branches = [b for b in branches if b.get("type") != "null"]
    kinds = set()
    for b in branches:
        t = b.get("type")
        if isinstance(t, list):                          # JSON Schema allows `"type": ["string", "null"]`
            t = next((x for x in t if x != "null"), None)
        if t == "boolean":
            kinds.add("boolean")
        elif t in ("integer", "number"):
            kinds.add("number")
        elif t == "string" or (t is None and b.get("enum") and all(isinstance(e, str) for e in b["enum"])):
            kinds.add("text")
        elif t == "array":
            items = _resolve(b.get("items") or {}, defs)
            if items.get("type") not in (None, "string") or (items.get("type") is None and not items.get("enum")):
                return None
            kinds.add("choices" if items.get("enum") else "list")
        else:
            return None
    return kinds.pop() if len(kinds) == 1 else None


def settings_diagnostics(model: type[BaseModel], secret_fields: frozenset[str]) -> list[Diagnostic]:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    out = []
    for name, prop in schema.get("properties", {}).items():
        if name in secret_fields:
            continue                                   # secrets render as write-only password inputs whatever their type
        if settings_kind(prop, defs) is None:
            out.append(Diagnostic(f"settings.{name}", f"setting {name!r} uses a type the settings form cannot render; "
                                                     f"it is shown as not editable here (supported: {', '.join(SETTINGS_KINDS)})"))
    return out


# ---- registration-time inspection ------------------------------------------------------------------------------------


def inspect_manifest(m: PluginManifest) -> UiReport:
    """Validate everything declarative about `m`. Never raises: a broken contribution is reported and left out."""
    report = UiReport()
    try:
        report.diagnostics += settings_diagnostics(m.settings_model, m.secret_fields)
    except Exception as e:                              # a settings model whose JSON schema cannot even be generated
        report.diagnostics.append(Diagnostic("settings", f"settings schema could not be generated: {type(e).__name__}"))
    for tab in m.ui.tabs:
        if tab.renderer != "schema":
            continue
        scope = f"ui.tab.{tab.id}"
        try:
            doc = m.ui_schema(tab.id) if m.ui_schema is not None else None
            if doc is None:
                raise SchemaProblem("schema: the plugin provides no schema for this tab")
            report.tabs[tab.id] = validate_tab_schema(doc)
        except SchemaProblem as e:
            report.diagnostics.append(Diagnostic(scope, str(e)))
        except Exception as e:                          # the plugin's own provider raised: name the type, never the message
            report.diagnostics.append(Diagnostic(scope, f"schema: the plugin's ui_schema failed ({type(e).__name__})"))
    return report
