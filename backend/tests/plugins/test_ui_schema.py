"""The declarative UI contract (BIZ-303): tab documents and settings forms are validated when a plugin registers, a bad
contribution fails closed with a plugin-scoped diagnostic, and nothing about it can stop another plugin or boot."""
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from app import plugins
from app.plugins import ui_report
from app.plugins.manifest import HOST_API, PluginManifest, Provide, UiContribution, UiTab
from app.plugins.ui_schema import SCHEMA_VERSION, SchemaProblem, settings_kind, validate_tab_schema
from tests.fake_providers import FakeInventoryProvider

FORM = {"type": "form", "load": "settings", "save": "settings", "fields": [
    {"key": "mode", "label": "Mode", "type": "select", "options": [{"value": "a", "label": "A"}]},
    {"key": "url", "label": "URL", "type": "text", "max_length": 200, "visible_if": {"key": "mode", "equals": "a"}},
    {"key": "port", "label": "Port", "type": "number", "min": 1, "max": 65535},
    {"key": "on", "label": "On", "type": "checkbox"}]}
TABLE = {"type": "table", "data": "items", "columns": [{"key": "name", "label": "Name"}],
         "create": {"path": "items", "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]},
         "row_actions": [{"label": "Delete", "method": "DELETE", "path": "items/{id}", "confirm": "Sure?"}]}


def doc(*blocks, **over):
    return {"title": "T", "blocks": list(blocks), **over}


def test_a_full_valid_document_is_accepted_unchanged():
    d = doc(FORM, TABLE, schema_version=1)
    assert validate_tab_schema(d) is d and SCHEMA_VERSION == 1
    assert validate_tab_schema({"blocks": []}) == {"blocks": []}          # version is optional (defaults to 1)


CORPUS = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "ui-schema-cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CORPUS["tab_documents"], ids=lambda c: c["name"])
def test_the_shared_corpus_is_accepted_or_rejected_with_a_path_prefixed_message(case):
    """The same cases run against the frontend validator (uiSchema.test.ts): the two cannot drift."""
    if case["valid"]:
        assert validate_tab_schema(case["doc"]) is case["doc"]
        return
    with pytest.raises(SchemaProblem) as err:
        validate_tab_schema(case["doc"])
    assert case["message"] in str(err.value)


@pytest.mark.parametrize("case", CORPUS["settings_kinds"], ids=lambda c: json.dumps(c["prop"], sort_keys=True))
def test_the_shared_settings_kind_corpus(case):
    assert settings_kind(case["prop"], case.get("defs")) == case["kind"]


class _Settings(BaseModel):
    flag: bool = False
    port: int = Field(8080, ge=1, le=65535)
    ratio: float | None = None
    url: str = ""
    maybe: str | None = None
    tags: list[str] = []
    kinds: list[str] = Field(default_factory=list, json_schema_extra={"items": {"type": "string", "enum": ["a", "b"]}})
    nested: dict[str, int] = {}
    objs: list[dict] = []
    token: dict | None = None            # a secret, so its (unsupported) type is irrelevant


def test_settings_kinds_cover_the_supported_types_and_reject_the_rest():
    props = _Settings.model_json_schema()["properties"]
    got = {k: settings_kind(v) for k, v in props.items()}
    assert got["flag"] == "boolean" and got["port"] == "number" and got["ratio"] == "number"
    assert got["url"] == "text" and got["maybe"] == "text" and got["tags"] == "list"
    assert got["nested"] is None and got["objs"] is None
    assert got["kinds"] == "choices" and got["token"] is None


def test_an_enum_setting_is_text_and_a_fixed_choice_list_is_choices():
    from typing import Literal

    class S(BaseModel):
        level: Literal["low", "high"] = "low"
        picks: list[Literal["x", "y"]] = []

    props = S.model_json_schema()
    assert settings_kind(props["properties"]["level"], props.get("$defs")) == "text"
    assert settings_kind(props["properties"]["picks"], props.get("$defs")) == "choices"


def _manifest(plugin_id="ui_demo", ui_schema=None, tabs=(), settings=_Settings, secret=frozenset({"token"})):
    return PluginManifest(
        id=plugin_id, name="UI demo", version="1.0.0", host_api=HOST_API, settings_model=settings, secret_fields=secret,
        factory=lambda _s: FakeInventoryProvider(), provides={"inventory.filament": Provide()},
        ui=UiContribution(mode="page", tabs=tabs), ui_schema=ui_schema)


@pytest.fixture(autouse=True)
def _clean_reports():
    yield
    from app.plugins import _UI_REPORTS
    for pid in [p for p in _UI_REPORTS if p.startswith("ui_")]:
        plugins._REGISTRY.pop(pid, None)
        _UI_REPORTS.pop(pid, None)


def test_registering_reports_problems_per_contribution_and_never_raises():
    docs = {"good": doc(FORM), "bad": doc({"type": "chart"}), "boom": None, "none": None}

    def provider(tab):
        if tab == "boom":
            raise RuntimeError("secret-token-123 leaked in a message")
        return docs[tab]

    m = _manifest(ui_schema=provider, tabs=tuple(UiTab(t, t.title(), "schema") for t in docs))
    plugins.register_plugin(m)                                                  # does not raise

    r = ui_report("ui_demo")
    assert set(r.tabs) == {"good"}
    scopes = {d.scope: d.message for d in r.diagnostics}
    assert "unsupported block type 'chart'" in scopes["ui.tab.bad"]
    assert scopes["ui.tab.boom"] == "schema: the plugin's ui_schema failed (RuntimeError)"      # type only: the message may carry secrets
    assert "no schema" in scopes["ui.tab.none"]
    assert scopes["settings.nested"].startswith("setting 'nested' uses a type the settings form cannot render")
    assert "settings.token" not in scopes and "settings.port" not in scopes


def test_an_unknown_plugin_has_an_empty_report():
    assert ui_report("nope_nope").diagnostics == [] and ui_report("nope_nope").tabs == {}


def test_every_bundled_plugin_is_clean():
    plugins.load_bundled()
    problems = {m.id: [d.as_dict() for d in ui_report(m.id).diagnostics] for m in plugins.registered_plugins()}
    assert {k: v for k, v in problems.items() if v} == {}


def test_the_example_in_the_plugin_docs_is_a_valid_document():
    """docs/plugins.md "Declarative UI" ships a sample plugin tab; it must stay inside the contract it documents."""
    import json
    from pathlib import Path
    text = (Path(__file__).resolve().parents[3] / "docs" / "plugins.md").read_text(encoding="utf-8")
    section = text.split("### Declarative UI", 1)[1].split("###", 1)[0]
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.S)
    assert len(blocks) == 1
    validate_tab_schema(json.loads(blocks[0]))
