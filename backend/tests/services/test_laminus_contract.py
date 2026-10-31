"""BIZ-248 contract: construction + G-code production resolution against fakes (docs/architecture/laminus-contract.md)."""
from __future__ import annotations

import pytest

from app.services.providers import contract as c
from tests.fake_providers import FakeGcodeProducer, FakeProjectConstructor

X1C, U1 = "uuid-x1c", "uuid-u1"
BED = (256.0, 256.0, 256.0)
T_X1C, T_U1 = c.TargetPrinter(BED, X1C), c.TargetPrinter(BED, U1, toolheads=4)
MODEL = c.WorkItem(c.InputKind.MODEL_3MF)


def ready(*ps):
    return {p.identity.provider_id: p.status() for p in ps}


def test_construct_resolves_to_the_single_active_provider_and_produces_a_project(tmp_path):
    p = FakeProjectConstructor()
    out = c.resolve_construct(p, p.status())
    assert (out.action, out.provider_id) == (c.Action.PROCEED, "fake-constructor")
    art = p.construct(c.ConstructRequest((c.ConstructInput(tmp_path / "a.stl", 1, "1"),), c.TargetPrinter(BED)), tmp_path)
    assert art.kind is c.ArtifactKind.PROJECT_3MF and art.path.read_bytes() == b"3mf"
    assert p.calls[0].target.model_uuid is None and p.calls[0].target.bed_mm == BED   # bed-only target is valid


def test_construct_blocks_without_provider_or_when_not_ready():
    assert c.resolve_construct(None, None) == c.WorkOutcome(c.Action.BLOCKED, reason=c.Reason.NO_PROVIDER)
    p = FakeProjectConstructor(c.Readiness.NOT_READY)
    out = c.resolve_construct(p, p.status())
    assert out.action is c.Action.BLOCKED and out.reason is c.Reason.DEFAULT_UNAVAILABLE


def test_model_input_resolves_to_default_provider_and_produces(tmp_path):
    a = FakeGcodeProducer("a")
    out = c.resolve_work(MODEL, T_X1C, {"a": a}, ready(a), default="a")
    assert (out.action, out.provider_id) == (c.Action.PRODUCE, "a")
    req = c.ProduceRequest(tmp_path / "m.3mf", c.InputKind.MODEL_3MF, 1, T_X1C)
    art = a.produce(req, tmp_path)
    assert art.kind is c.ArtifactKind.GCODE and art.produced_by.provider_id == "a"


def test_preferred_provider_beats_default():
    a, b = FakeGcodeProducer("a"), FakeGcodeProducer("b")
    out = c.resolve_work(MODEL, T_X1C, {"a": a, "b": b}, ready(a, b), preferred="b", default="a")
    assert out.provider_id == "b"


def test_gcode_is_pass_through_with_no_provider_even_when_providers_are_down():
    item = c.WorkItem(c.InputKind.GCODE, frozenset({X1C}))
    assert c.resolve_work(item, T_X1C, {}, {}) == c.WorkOutcome(c.Action.PASS_THROUGH)
    down = FakeGcodeProducer("a", state=c.Readiness.UNREACHABLE)
    out = c.resolve_work(item, T_X1C, {"a": down}, ready(down), preferred="a")
    assert out.action is c.Action.PASS_THROUGH and down.calls == []


def test_gcode_for_another_model_or_unknown_eligibility_blocks():
    assert c.resolve_work(c.WorkItem(c.InputKind.GCODE_3MF, frozenset({X1C})), T_U1, {}, {}).reason is c.Reason.MODEL_INELIGIBLE
    assert c.resolve_work(c.WorkItem(c.InputKind.GCODE), T_X1C, {}, {}).reason is c.Reason.ELIGIBILITY_UNKNOWN


def test_no_eligible_provider_blocks():
    assert c.resolve_work(MODEL, T_X1C, {}, {}, default="a").reason is c.Reason.NO_PROVIDER
    a = FakeGcodeProducer("a", models=frozenset({U1}))          # does not serve the X1C
    assert c.resolve_work(MODEL, T_X1C, {"a": a}, ready(a), default="a").reason is c.Reason.DEFAULT_INELIGIBLE
    assert c.resolve_work(MODEL, T_X1C, {"a": a}, ready(a)).reason is c.Reason.NO_PROVIDER   # enabled but no default
    stl_only = FakeGcodeProducer("s", accepted={c.InputKind.STL})
    assert c.resolve_work(MODEL, T_X1C, {"s": stl_only}, ready(stl_only), default="s").reason is c.Reason.DEFAULT_INELIGIBLE


@pytest.mark.parametrize("state", [c.Readiness.NOT_READY, c.Readiness.UNREACHABLE])
def test_unavailable_preferred_provider_blocks_and_never_falls_back(state):
    pref, other = FakeGcodeProducer("pref", state=state), FakeGcodeProducer("other")
    out = c.resolve_work(MODEL, T_X1C, {"pref": pref, "other": other}, ready(pref, other), preferred="pref", default="other")
    assert (out.action, out.provider_id, out.reason) == (c.Action.BLOCKED, "pref", c.Reason.PREFERENCE_UNAVAILABLE)


def test_dormant_and_ineligible_preferences_block_without_fallback():
    other = FakeGcodeProducer("other")
    out = c.resolve_work(MODEL, T_X1C, {"other": other}, ready(other), preferred="gone", default="other")
    assert out.reason is c.Reason.PREFERENCE_DORMANT
    pref = FakeGcodeProducer("pref", models=frozenset({U1}))
    out = c.resolve_work(MODEL, T_X1C, {"pref": pref, "other": other}, ready(pref, other), preferred="pref", default="other")
    assert out.reason is c.Reason.PREFERENCE_INELIGIBLE


def test_each_target_printer_resolves_independently():
    a = FakeGcodeProducer("a", models=frozenset({X1C}))
    b = FakeGcodeProducer("b", models=frozenset({U1}))
    res = c.resolve_job(MODEL, {"p1": T_X1C, "p2": T_U1, "p3": c.TargetPrinter(BED, "uuid-other")},
                        {"a": a, "b": b}, ready(a, b), preferred={"p1": "a", "p2": "b"}, default="a")
    assert res["p1"].provider_id == "a" and res["p2"].provider_id == "b"
    assert res["p3"].action is c.Action.BLOCKED and res["p3"].reason is c.Reason.DEFAULT_INELIGIBLE   # others unaffected
    gc = c.resolve_job(c.WorkItem(c.InputKind.GCODE, frozenset({X1C, U1})),
                       {"p1": T_X1C, "p3": c.TargetPrinter(BED, "uuid-other")}, {}, {})
    assert gc["p1"].action is c.Action.PASS_THROUGH and gc["p3"].action is c.Action.BLOCKED


def test_cancelled_token_aborts_production(tmp_path):
    a, tok = FakeGcodeProducer("a"), c.CancelToken()
    tok.cancel()
    req = c.ProduceRequest(tmp_path / "m.3mf", c.InputKind.MODEL_3MF, 1, T_X1C)
    with pytest.raises(c.ProviderError) as e:
        a.produce(req, tmp_path, cancel=tok)
    assert e.value.code is c.ErrorCode.CANCELLED


def test_provider_refuses_gcode_input(tmp_path):
    a = FakeGcodeProducer("a")
    with pytest.raises(c.ProviderError) as e:
        a.produce(c.ProduceRequest(tmp_path / "x.gcode", c.InputKind.GCODE, 1, T_X1C), tmp_path)
    assert e.value.code is c.ErrorCode.UNSUPPORTED_INPUT


def test_inspection_dto_has_findings_is_derived():
    assert not c.OverrideInspection().has_findings
    assert c.OverrideInspection(slot_warning=c.SlotWarning(4, 1)).has_findings
    assert c.OverrideInspection(setting_changes=(c.SettingChange("k", "1", "2"),)).has_findings


def test_provider_error_is_picklable_and_hashable():
    import pickle
    e = c.ProviderError(c.ErrorCode.TIMEOUT, "slow")
    r = pickle.loads(pickle.dumps(e))
    assert (r.code, r.message, str(r)) == (c.ErrorCode.TIMEOUT, "slow", "timeout: slow")
    hash(e)
