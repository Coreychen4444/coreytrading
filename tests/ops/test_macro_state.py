"""ops/macro_state —— 可选情报工作区宏观状态的校验器 / 发布器; 交易侧只用 `validate_document` (只读)。

公开仓不带情报工作区的 schema 与示例, 这里用合成 schema 钉住接口合同: 非对象直接拒绝、schema 错误
先于语义检查返回、state_hash 只看 material 键、_same_payload 忽略 writer 自有的链接字段。
"""
import json

from quant.ops import macro_state as state


def test_non_object_and_schema_errors_return_before_semantics(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "required": ["run_kind", "generated_at"],
                                  "properties": {"run_kind": {"type": "string"},
                                                 "generated_at": {"type": "object"}}}))
    assert state.validate_document("nope", schema) == ["$: macro state must be an object"]
    errors = state.validate_document({"run_kind": 3}, schema)
    assert errors and all(e.startswith("$") for e in errors)
    assert any("generated_at" in e for e in errors) and any("run_kind" in e for e in errors)


def test_state_hash_reads_material_keys_only():
    base = {k: {"v": 1} for k in state.MATERIAL_KEYS}
    h = state.state_hash(dict(base, run_id="a", state_hash=None))
    assert h == state.state_hash(dict(base, run_id="b")) and len(h) == 64
    assert h != state.state_hash(dict(base, signals={"v": 2}))


def test_same_payload_ignores_writer_owned_linkage():
    left = {"a": 1, "previous_run_id": "x", "state_hash": "h1"}
    right = {"a": 1, "previous_run_id": "y", "state_hash": "h2"}
    assert state._same_payload(left, right)
    assert not state._same_payload(left, {"a": 2, "previous_run_id": "x", "state_hash": "h1"})
