"""Dispatch, independent review, idempotency and lease fencing on synthetic evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import threading
import time

import pytest

from quant.core.agent_contracts import CONTRACT_VERSION, RESULT_SCHEMA_VERSION, ROLES
from quant.core.io_json import atomic_write_json, read_json
from quant.ops import agent_runtime as runtime
from quant.ops.agent_snapshots import make_snapshot


@pytest.fixture
def routes():
    return {"version": 1, "roles": {
        role: {"provider": "claude", "model": None,
               "fallback": [{"provider": "codex", "model": None}],
               "reviewer": {"provider": "codex", "model": None}, "timeout_s": 600}
        for role in ROLES}, "limits": {"max_parallel": 3, "max_calls": 8},
        "autopaint": {"enabled": True, "apply": False, "review": True}}


@pytest.fixture
def profile_routes(routes):
    config = deepcopy(routes)
    config.update(version=2, model_profiles={
        "anthropic": {"runtime": "claude", "primary": {"model": "claude-fable-5-1", "effort": "xhigh"},
                      "fallback": [{"model": "claude-opus-5", "effort": "xhigh"}]},
        "openai": {"runtime": "codex", "primary": {"model": "gpt-6-astra", "effort": "high"},
                   "fallback": [{"model": "gpt-5.6-sol", "effort": "high"}]}})
    config["roles"] = {role: {"profile": "openai" if role in ("macro", "valuation") else "anthropic",
                              "reviewer_profile": "anthropic" if role in ("macro", "valuation") else "openai",
                              "timeout_s": 600} for role in ROLES}
    return config


@pytest.fixture
def packet():
    return make_snapshot({"synthetic_fact": {"value": 123}})


def result_for(prompt):
    request = json.loads(prompt.split("\n", 1)[1])
    s, role = request["snapshot"], request["role"]
    target = request["review_target"]
    return {"schema_version": RESULT_SCHEMA_VERSION, "contract_version": CONTRACT_VERSION,
            **({"valuation_scenarios": []} if role == "macro" else {}),
            "role": role, "snapshot_id": s["snapshot_id"], "input_hash": s["input_hash"],
            "generated_at": s["as_of"], "expires_at": s["expires_at"], "summary": "合成证据分析",
            "observations": [{"id": "f1", "kind": "fact", "text": "来自冻结证据",
                              "evidence_refs": s["evidence_refs"][:1]}],
            "hypotheses": [], "risks": [], "unknowns": [], "proposal": None, "requests": [],
            "review": {"run_id": target["run_id"], "verdict": "approve", "reasons": ["证据相符"]} if target else None,
            "authority": {"mode": "proposal_only" if role == "technical" else "analysis_only",
                          "may_write_state": False, "may_place_orders": False}}


def fake_runner(provider, prompt, schema, **kwargs):
    return {"status": "succeeded", "payload": result_for(prompt), "provider": provider,
            "model": "synthetic-model", "elapsed_s": 0, "usage": {}}


def test_generation_schema_binds_source_ids_and_excludes_self_requests(packet):
    schema = runtime.execution_schema("technical", packet)
    props = schema["properties"]
    assert props["snapshot_id"]["const"] == packet["snapshot_id"]
    assert props["input_hash"]["const"] == packet["input_hash"]
    assert props["generated_at"]["const"] == packet["as_of"]
    assert "technical" not in props["requests"]["items"]["properties"]["role"]["enum"]
    for field in ("observations", "risks", "hypotheses", "requests"):
        assert props[field]["items"]["properties"]["evidence_refs"]["items"]["enum"] == packet["evidence_refs"]
    assert props["review"] == {"type": "null"}
    reviewed = runtime.execution_schema("technical", packet, {"run_id": "a" * 32})
    assert reviewed["properties"]["proposal"] == {"type": "null"}
    assert reviewed["properties"]["review"]["properties"]["run_id"]["const"] == "a" * 32


def test_global_concurrency_queues_without_spending_extra_calls(routes, packet, tmp_path, monkeypatch):
    from quant.ops import agent_health
    real_acquire = agent_health.acquire_call
    checks = []
    def acquire(*args, **kwargs):
        checks.append(True)
        if len(checks) == 1:
            return {"status": "concurrency_limit", "lease_id": None}
        return real_acquire(*args, **kwargs)
    monkeypatch.setattr(agent_health, "acquire_call", acquire)
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: None)
    record = runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=fake_runner)
    assert record["status"] == "succeeded" and len(record["attempts"]) == 1
    assert len(checks) == 2
    ledger = read_json(tmp_path / "control" / "global-calls.json")
    assert len(ledger["calls"]) == 1


def test_automatic_switch_is_rechecked_after_waiting_for_writer(routes, packet, tmp_path, monkeypatch):
    from quant.ops import agent_submit
    routes["autopaint"]["apply"] = True
    record = runtime.run_role("technical", "分析", packet, routes=routes,
                              run_dir=tmp_path, runner=fake_runner, review=True)
    monkeypatch.setattr(runtime, "load_routes", lambda: routes)
    writes = []
    def apply(_record, *, apply=False, on_precommit=None):
        if not apply:
            return {"can_apply": True}
        routes["autopaint"]["apply"] = False
        on_precommit({"test": "prepared"})
        writes.append(True)
        return {"status": "applied"}
    monkeypatch.setattr(agent_submit, "apply_proposal", apply)
    with pytest.raises(agent_submit.SubmitError, match="disabled"):
        runtime.submit_run(record["run_id"], apply=True, automatic=True, run_dir=tmp_path)
    assert writes == []
    assert read_json(runtime._run_path(tmp_path, record["run_id"]))["submission"]["status"] == "rejected"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("provider", runtime.PROVIDERS)
def test_any_role_can_switch_provider_without_changing_contract(role, provider, routes, packet, tmp_path):
    routes["roles"][role]["provider"] = provider
    record = runtime.run_role(role, "分析", packet, routes=routes, run_dir=tmp_path, runner=fake_runner)
    assert record["status"] == "succeeded"
    assert record["provider"] == provider
    assert record["result"]["role"] == role


def test_completed_job_is_idempotent(routes, packet, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(args[0])
        return fake_runner(*args, **kwargs)
    a = runtime.run_role("technical", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    b = runtime.run_role("technical", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    assert a["run_id"] == b["run_id"] and calls == ["claude"]


def test_failure_uses_only_configured_bounded_fallback(routes, packet, tmp_path):
    calls = []
    def run(provider, *args, **kwargs):
        calls.append(provider)
        return {"status": "timeout", "error": "synthetic timeout"} if provider == "claude" else fake_runner(provider, *args, **kwargs)
    record = runtime.run_role("risk", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    assert calls == ["claude", "codex"] and record["status"] == "succeeded"


def test_valid_abstention_does_not_shop_for_another_answer(routes, packet, tmp_path):
    calls = []
    def run(*args, **kwargs):
        calls.append(args[0])
        out = fake_runner(*args, **kwargs)
        out["payload"]["unknowns"] = ["证据不足，不能提出交易计划"]
        return out
    runtime.run_role("technical", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    assert calls == ["claude"]


def test_unexpected_runner_failure_is_counted_and_sanitized(routes, packet, tmp_path):
    def run(provider, *args, **kwargs):
        if provider == "claude":
            raise RuntimeError("secret-must-not-appear")
        return fake_runner(provider, *args, **kwargs)
    record = runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    assert record["status"] == "succeeded" and len(record["attempts"]) == 2
    assert record["attempts"][0]["error"] == "runner_exception:RuntimeError"
    assert "secret-must-not-appear" not in json.dumps(record)


def test_review_is_independent_and_bound_to_exact_result(routes, packet, tmp_path):
    record = runtime.run_role("technical", "分析", packet, routes=routes,
                              run_dir=tmp_path, runner=fake_runner, review=True)
    assert record["status"] == "succeeded"
    assert record["provider"] != record["review"]["provider"]
    assert record["review"]["result"]["review"]["run_id"] == record["run_id"]
    assert record["review"]["reviewed_result_hash"] == runtime.canonical_hash(record["result"])


def test_fallback_does_not_turn_same_provider_into_independent_reviewer(routes, packet, tmp_path):
    def run(provider, *args, **kwargs):
        return {"status": "failed"} if provider == "claude" else fake_runner(provider, *args, **kwargs)
    record = runtime.run_role("technical", "分析", packet, routes=routes, run_dir=tmp_path,
                              runner=run, review=True)
    assert record["status"] == "needs_review" and record["review"] is None


def test_wrong_snapshot_or_authority_fails_even_if_runner_succeeded(routes, packet, tmp_path):
    def run(*args, **kwargs):
        out = fake_runner(*args, **kwargs)
        out["payload"]["authority"]["may_write_state"] = True
        return out
    record = runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
    assert record["status"] == "failed"
    assert all(x["status"] == "invalid_result" for x in record["attempts"])


def test_concurrent_callers_only_start_one_worker(routes, packet, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(*args, **kwargs):
        calls.append(args[0]); entered.set()
        assert release.wait(5)
        return fake_runner(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.run_role, "macro", "分析", packet,
                            routes=routes, run_dir=tmp_path, runner=run)
        assert entered.wait(5)
        second = runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=run)
        assert second["status"] == "running"
        release.set()
        assert first.result()["status"] == "succeeded"
    assert len(calls) == 1


def test_expired_lease_revokes_old_publish(tmp_path):
    a, claimed = runtime._claim(tmp_path, "key", {"role": "macro"}, 30)
    assert claimed
    path = tmp_path / "jobs" / "key.json"
    job = read_json(path); job["lease_until"] = 0; atomic_write_json(path, job)
    b, claimed = runtime._claim(tmp_path, "key", {"role": "macro"}, 30)
    assert claimed and a["run_id"] != b["run_id"]
    assert runtime._publish(tmp_path, dict(a, status="succeeded", result={"late": True}))["status"] == "superseded"
    assert read_json(path)["run_id"] == b["run_id"]


def test_expired_owner_cannot_publish_without_a_replacement(tmp_path):
    record, _ = runtime._claim(tmp_path, "key", {"role": "macro"}, 30)
    job_path = tmp_path / "jobs" / "key.json"
    job = read_json(job_path)
    job["lease_until"] = 0
    atomic_write_json(job_path, job)
    result = runtime._publish(tmp_path, dict(record, status="succeeded", result={"late": True}))
    assert result["status"] == "superseded" and result["result"] is None
    assert read_json(runtime._run_path(tmp_path, record["run_id"]))["status"] == "superseded"
    replacement, claimed = runtime._claim(tmp_path, "key", {"role": "macro"}, 30)
    assert claimed and replacement["run_id"] != record["run_id"]


@pytest.mark.parametrize("side_effect_possible", [False, True])
def test_submission_failure_preserves_retry_barrier_when_outcome_uncertain(
        side_effect_possible, routes, packet, tmp_path, monkeypatch):
    from quant.ops import agent_submit
    record = runtime.run_role("technical", "分析", packet, routes=routes,
                              run_dir=tmp_path, runner=fake_runner, review=True)
    calls = []
    def submit(_record, *, apply=False, on_precommit=None):
        if not apply:
            return {"can_apply": True}
        calls.append("writer")
        raise agent_submit.SubmitError("synthetic writer failure", side_effect_possible=side_effect_possible)
    monkeypatch.setattr(agent_submit, "apply_proposal", submit)
    with pytest.raises(agent_submit.SubmitError):
        runtime.submit_run(record["run_id"], apply=True, run_dir=tmp_path)
    stored = read_json(runtime._run_path(tmp_path, record["run_id"]))
    assert stored["submission"]["status"] == ("applying" if side_effect_possible else "rejected")
    if side_effect_possible:
        with pytest.raises(ValueError, match="uncertain"):
            runtime.submit_run(record["run_id"], apply=True, run_dir=tmp_path)
        assert calls == ["writer"]


def test_successful_submission_is_not_replayed(routes, packet, tmp_path, monkeypatch):
    from quant.ops import agent_submit
    record = runtime.run_role("technical", "分析", packet, routes=routes,
                              run_dir=tmp_path, runner=fake_runner, review=True)
    calls = []
    def submit(_record, *, apply=False, on_precommit=None):
        if not apply:
            return {"can_apply": True}
        calls.append("writer")
        return {"status": "applied", "run_id": record["run_id"]}
    monkeypatch.setattr(agent_submit, "apply_proposal", submit)
    first = runtime.submit_run(record["run_id"], apply=True, run_dir=tmp_path)
    assert runtime.submit_run(record["run_id"], apply=True, run_dir=tmp_path) == first
    assert calls == ["writer"]


def test_expired_snapshot_rejected_before_job_or_model(routes, packet, tmp_path):
    packet["expires_at"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(ValueError):
        runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=fake_runner)
    assert list(tmp_path.iterdir()) == []


def test_route_cannot_disable_review_for_live_autopaint(routes):
    routes["autopaint"].update(apply=True, review=False)
    with pytest.raises(ValueError, match="requires"):
        runtime.validate_routes(routes)


def test_profile_routes_resolve_without_mutating_source_and_remain_valid(profile_routes):
    original = deepcopy(profile_routes)
    config = runtime.validate_routes(profile_routes)
    assert profile_routes == original
    assert config["version"] == 1 and "model_profiles" not in config
    assert runtime.validate_routes(config) == config
    for role in ROLES:
        route = config["roles"][role]
        openai = role in ("macro", "valuation")
        assert route["provider"] == ("codex" if openai else "claude")
        assert route["model"] == ("gpt-6-astra" if openai else "claude-fable-5-1")
        assert route["effort"] == ("high" if openai else "xhigh")
        assert route["fallback"][0]["provider"] == route["provider"]
        assert route["reviewer"]["provider"] != route["provider"]
        assert route["reviewer_fallback"][0]["provider"] == route["reviewer"]["provider"]
    profile_routes["model_profiles"]["openai"]["primary"]["model"] = "future-model"
    assert config["roles"]["macro"]["model"] == "gpt-6-astra"


@pytest.mark.parametrize("change", [
    lambda c: c.update(untrusted="extra"),
    lambda c: c["model_profiles"].update({"Bad profile": deepcopy(c["model_profiles"]["openai"])}),
    lambda c: c["model_profiles"]["openai"].update(provider="other"),
    lambda c: c["model_profiles"]["openai"].update(runtime="unconfigured"),
    lambda c: c["model_profiles"]["openai"]["primary"].update(model=None),
    lambda c: c["model_profiles"]["openai"]["primary"].update(effort="ultra"),
    lambda c: c["model_profiles"]["anthropic"]["primary"].update(effort="none"),
    lambda c: c["model_profiles"]["openai"]["primary"].update(fallback=True),
    lambda c: c["model_profiles"]["openai"].update(fallback=[deepcopy(c["model_profiles"]["openai"]["primary"])]),
    lambda c: c["roles"]["macro"].update(profile="missing"),
    lambda c: c["roles"]["macro"].update(reviewer_profile="openai"),
    lambda c: c["roles"]["macro"].update(model="untrusted-override"),
    lambda c: c["roles"].pop("risk"),
])
def test_invalid_profile_configuration_is_rejected(profile_routes, change):
    change(profile_routes)
    with pytest.raises(ValueError):
        runtime.validate_routes(profile_routes)


def test_legacy_identifiers_and_injected_runner_without_effort_remain_supported(routes, packet, tmp_path):
    routes["roles"]["macro"]["model"] = "custom:model/version-1"
    called = []
    def legacy(provider, prompt, schema, *, model, timeout_s, cwd):
        called.append(model)
        return fake_runner(provider, prompt, schema)
    record = runtime.run_role("macro", "分析", packet, routes=routes, run_dir=tmp_path, runner=legacy)
    assert record["status"] == "succeeded" and called == ["custom:model/version-1"]
    assert record["requested_model"] == "custom:model/version-1"
    assert record["requested_effort"] is None


def test_same_runtime_model_fallback_has_distinct_fenced_calls(profile_routes, packet, tmp_path):
    calls = []
    def runner(provider, prompt, schema, **kwargs):
        calls.append((provider, kwargs["model"], kwargs["effort"]))
        if kwargs["model"] == "gpt-6-astra":
            return {"status": "unavailable", "error": "synthetic unavailable"}
        return {**fake_runner(provider, prompt, schema), "model": kwargs["model"],
                "reported_model": "gpt-5.6-sol-20260901", "reported_models": ["gpt-5.6-sol-20260901"],
                "model_verification": "reported"}
    record = runtime.run_role("macro", "分析", packet, routes=profile_routes, run_dir=tmp_path, runner=runner)
    assert record["status"] == "succeeded"
    assert calls == [("codex", "gpt-6-astra", "high"), ("codex", "gpt-5.6-sol", "high")]
    assert record["requested_model"] == "gpt-5.6-sol" and record["requested_effort"] == "high"
    assert record["reported_model"] == "gpt-5.6-sol-20260901"
    assert record["model_verification"] == "reported"
    ledger = read_json(tmp_path / "control" / "global-calls.json")
    assert len(ledger["calls"]) == 2
    assert len({attempt["call_id"] for attempt in record["attempts"]}) == 2
    assert {attempt["call_id"] for attempt in record["attempts"]} == {call["run_id"] for call in ledger["calls"]}
    assert all(call["status"] == "released" for call in ledger["calls"])
    replay = runtime.run_role("macro", "分析", packet, routes=profile_routes, run_dir=tmp_path, runner=runner)
    assert replay["run_id"] == record["run_id"] and len(calls) == 2


def test_primary_and_independent_review_can_each_fall_back_within_global_cap(profile_routes, packet, tmp_path):
    profile_routes["global_limits"] = {"max_parallel": 3, "max_calls": 4, "window_s": 3600, "lease_grace_s": 60}
    calls = []
    def runner(provider, prompt, schema, **kwargs):
        calls.append((provider, kwargs["model"], kwargs["effort"]))
        if kwargs["model"] in ("claude-fable-5-1", "gpt-6-astra"):
            return {"status": "failed", "error": "synthetic failure"}
        return fake_runner(provider, prompt, schema)
    record = runtime.run_role("technical", "分析", packet, routes=profile_routes,
                              run_dir=tmp_path, runner=runner, review=True)
    assert record["status"] == "succeeded"
    assert calls == [("claude", "claude-fable-5-1", "xhigh"), ("claude", "claude-opus-5", "xhigh"),
                     ("codex", "gpt-6-astra", "high"), ("codex", "gpt-5.6-sol", "high")]
    assert record["review"]["provider"] != record["provider"]
    assert len(record["attempts"]) == len(record["review"]["attempts"]) == 2
    denied = runtime.run_role("macro", "分析", packet, routes=profile_routes,
                              run_dir=tmp_path, runner=runner)
    assert denied["status"] == "failed" and denied["error"] == "global_model_limit:window_budget"
    assert len(calls) == 4


def test_reviewer_abstention_never_tries_second_model(profile_routes, packet, tmp_path):
    calls = []
    def runner(provider, prompt, schema, **kwargs):
        calls.append(kwargs["model"])
        output = fake_runner(provider, prompt, schema)
        if output["payload"]["review"]:
            output["payload"]["review"]["verdict"] = "needs_review"
        return output
    record = runtime.run_role("technical", "分析", packet, routes=profile_routes,
                              run_dir=tmp_path, runner=runner, review=True)
    assert record["status"] == "needs_review"
    assert calls == ["claude-fable-5-1", "gpt-6-astra"]


def test_reviewer_secondary_cannot_fall_back_to_actual_primary_runtime(routes, packet, tmp_path):
    route = routes["roles"]["technical"]
    route["reviewer_fallback"] = [{"provider": "claude", "model": "another-model"}]
    calls = []
    def runner(provider, prompt, schema, **kwargs):
        calls.append(provider)
        return {"status": "failed"} if provider == "codex" else fake_runner(provider, prompt, schema)
    record = runtime.run_role("technical", "分析", packet, routes=routes,
                              run_dir=tmp_path, runner=runner, review=True)
    assert record["status"] == "needs_review" and calls == ["claude", "codex"]


def test_status_does_not_accept_arbitrary_file_path(tmp_path):
    with pytest.raises(ValueError):
        runtime._run_path(tmp_path, "../../state/positions")


def test_isolated_workers_receive_referenced_policy_and_actual_writer_contract():
    from quant.ops.agent_submit import FLAG_ARITY
    for role, method in (("macro", "agents/methods/macro-inference-contract.md"),
                         ("valuation", "agents/methods/news-valuation-overlay.md"),
                         ("technical", "trade/strategy/trading-rules.md"),
                         ("risk", "trade/strategy/trading-rules.md")):
        policy = runtime.policy_context(role)
        if (runtime.ROOT / method).exists():
            assert method in policy
        else:   # optional intelligence workspace absent: named, not fabricated
            assert method in policy["optional_methods_missing"] and method not in policy
    for role in ("macro", "valuation"):
        assert runtime.policy_context(role)["quant/valuation/README.md"] == (
            runtime.ROOT / "quant/valuation/README.md").read_text()
    policy = runtime.policy_context("technical")
    assert policy["technical_proposal_contract"]["flag_arity"] == FLAG_ARITY
    assert "--ticker" not in policy["technical_proposal_contract"]["flag_arity"]


def test_freeze_uses_only_explicit_json_evidence(tmp_path, capsys):
    data, output = tmp_path / "data.json", tmp_path / "snapshot.json"
    atomic_write_json(data, {"synthetic_report": {"value": 123, "source": "fixture"}})
    assert runtime.main(["freeze", "--data", str(data), "--output", str(output)]) == 0
    snapshot = read_json(output)
    assert snapshot["data"] == read_json(data)
    assert snapshot["input_hash"] == runtime.canonical_hash(snapshot["data"])
    assert json.loads(capsys.readouterr().out)["snapshot_id"] == snapshot["snapshot_id"]
