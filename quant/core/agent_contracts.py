"""Provider-neutral research result contracts; validation has no side effects.

The three roles describe work, never model choice. A valid result is still an
untrusted analysis or proposal: this module grants no filesystem, trading, or
writer permission and does not validate whether prose is factually correct.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from typing import Any


CONTRACT_VERSION = "1.0.0"
RESULT_SCHEMA_VERSION = "1.1.0"
# 宏观不是模型角色：宏观层是纯代码判读（core/macro_lens，零模型调用），不再有模型宏观研究。
ROLES = ("technical", "risk", "valuation")


class ContractError(ValueError):
    """An input or result does not satisfy the research contract."""


def _json_value(value: Any, path: str = "$") -> None:
    """Reject Python-only values, non-string object keys, and nonfinite numbers."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError(f"{path}: nonfinite number")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError(f"{path}: object keys must be strings")
            _json_value(item, f"{path}.{key}")
        return
    raise ContractError(f"{path}: not a JSON value")


def canonical_hash(value: Any) -> str:
    """SHA-256 of strict, sorted, compact UTF-8 JSON, including JSON null."""
    _json_value(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# 宿主每轮刷新、画卡 writer 原样保留的字段 —— 不参与卡片并发判据。
# 黑名单式 (新字段默认仍进哈希): 未知字段 = 保守方向 = 宁可空转不可覆盖写。
#   broker           stock_fetch / state_io 每轮 heartbeat 刷报价快照
#   moomoo_synced_at watchlist_sync 每轮盖自选分组同步时间
#   agent.valuation  旧估值镜像 (已退役, schema 禁写; 保留在名单里只为不让残留卡误判并发)
HOST_REFRESHED_CARD_FIELDS = ("broker", "moomoo_synced_at")
HOST_REFRESHED_AGENT_FIELDS = ("valuation",)


def card_revision(card: Any) -> str:
    """卡片并发判据: writer 会读写的那部分内容的哈希。

    为什么不是整张卡: expected-revision 原先哈希整张卡,
    含 stock_fetch 每轮刷的 broker 报价块 —— 任何耗时几分钟的画卡 run 只要跟一轮
    行情刷新撞上就 revision conflict 空转 (提案与几分钟
    后重跑逐字相同)。报价不是并发写: writer 原样保留它, 提案新鲜度由 snapshot TTL
    与 precommit 复检管, 不靠这个哈希。regime 转移 / 并发画卡都写在 agent 块里,
    照样拦得住。
    """
    if not isinstance(card, dict):
        return canonical_hash(card)
    view = {key: value for key, value in card.items() if key not in HOST_REFRESHED_CARD_FIELDS}
    agent = card.get("agent")
    if isinstance(agent, dict):
        view["agent"] = {key: value for key, value in agent.items()
                         if key not in HOST_REFRESHED_AGENT_FIELDS}
    # 存在性也不算: 首次落 broker 块 / 首次盖估值镜像同样是宿主刷新, 不是并发写。
    return canonical_hash({"card_writer_scope": view})


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _array(items: dict, *, minimum: int = 0, maximum: int = 100) -> dict:
    return {"type": "array", "items": items, "minItems": minimum,
            "maxItems": maximum}


_TEXT = {"type": "string", "minLength": 1, "maxLength": 12000}
_ID = {"type": "string", "minLength": 1, "maxLength": 256}
_REFS = _array(_ID)
_OBSERVATION = _object({"id": _ID, "kind": {"type": "string", "enum": ["fact", "inference"]},
                         "text": _TEXT, "evidence_refs": _REFS})
_HYPOTHESIS = _object({"id": _ID, "statement": _TEXT, "evidence_refs": _REFS,
                      "invalidation": _TEXT})
_RISK = _object({"id": _ID, "text": _TEXT, "evidence_refs": _REFS})
_REQUEST = _object({"role": {"type": "string", "enum": list(ROLES)},
                   "question": _TEXT, "evidence_refs": _REFS})
_ZONE_PROPOSAL = _object({
    "kind": {"type": "string", "const": "zone_update"},
    "zone_update_args": _array({"type": "string", "minLength": 1, "maxLength": 32000},
                               minimum=1, maximum=100),
})
_REVIEW = _object({
    "run_id": _ID,
    "verdict": {"type": "string", "enum": ["approve", "reject", "needs_review"]},
    "reasons": _array(_TEXT, minimum=1, maximum=20),
})


def _schema_for(role: str) -> dict:
    mode = "proposal_only" if role == "technical" else "analysis_only"
    proposal = ({"anyOf": [{"type": "null"}, _ZONE_PROPOSAL]}
                if role == "technical" else {"type": "null"})
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{role} research result", **_object({
        "schema_version": {"type": "string", "const": RESULT_SCHEMA_VERSION},
        "contract_version": {"type": "string", "const": CONTRACT_VERSION},
        "role": {"type": "string", "const": role},
        "snapshot_id": _ID,
        "input_hash": {"type": "string", "minLength": 64, "maxLength": 64},
        "generated_at": {"type": "string", "format": "date-time"},
        "expires_at": {"type": "string", "format": "date-time"},
        "summary": _TEXT,
        "observations": _array(_OBSERVATION),
        "hypotheses": _array(_HYPOTHESIS),
        "risks": _array(_RISK),
        "unknowns": _array(_TEXT),
        "proposal": proposal,
        "requests": _array(_REQUEST, maximum=12),
        "review": {"anyOf": [{"type": "null"}, _REVIEW]},
        "authority": _object({
            "mode": {"type": "string", "const": mode},
            "may_write_state": {"type": "boolean", "const": False},
            "may_place_orders": {"type": "boolean", "const": False},
        }),
    })}
    return schema


RESULT_SCHEMAS = {role: _schema_for(role) for role in ROLES}


def result_schema(role: str) -> dict:
    """Return an isolated JSON Schema for model output or adapter validation."""
    if role not in ROLES:
        raise ContractError(f"unknown role: {role!r}")
    return deepcopy(RESULT_SCHEMAS[role])


def _timestamp(value: str, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError(f"{path}: expected ISO datetime with timezone") from exc
    if "T" not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{path}: expected ISO datetime with timezone")
    return parsed


def _validate(value: Any, schema: dict, path: str) -> None:
    """Validate exactly the JSON Schema keywords used above, without dependencies."""
    if "anyOf" in schema:
        for variant in schema["anyOf"]:
            try:
                _validate(value, variant, path)
                return
            except ContractError:
                pass
        raise ContractError(f"{path}: does not match any allowed shape")
    expected = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int,
             "null": type(None)}
    if expected == "number":
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ContractError(f"{path}: expected finite number")
    elif expected and type(value) is not types[expected]:
        raise ContractError(f"{path}: expected {expected}")
    if expected in ("integer", "number"):
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            raise ContractError(f"{path}: number outside bounds")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ContractError(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: unsupported value")
    if expected == "object":
        properties = schema["properties"]
        if set(value) - set(properties):
            raise ContractError(f"{path}: unknown fields {sorted(set(value) - set(properties))}")
        missing = set(schema["required"]) - set(value)
        if missing:
            raise ContractError(f"{path}: missing fields {sorted(missing)}")
        for key, child in properties.items():
            _validate(value[key], child, f"{path}.{key}")
    elif expected == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise ContractError(f"{path}: invalid item count")
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]")
    elif expected == "string":
        if not value.strip() or not schema.get("minLength", 1) <= len(value) <= schema.get("maxLength", 12000):
            raise ContractError(f"{path}: empty or overlong string")
        if "\x00" in value:
            raise ContractError(f"{path}: NUL is forbidden")
        if schema.get("format") == "date-time":
            _timestamp(value, path)


def validate_snapshot(snapshot: dict, *, now: datetime | str | None = None) -> dict:
    """Check the exact frozen input manifest, its data hash, and validity window."""
    _json_value(snapshot)
    if type(snapshot) is not dict:
        raise ContractError("snapshot: expected object")
    expected = {"snapshot_id", "input_hash", "as_of", "expires_at", "evidence_refs", "data"}
    if set(snapshot) != expected:
        raise ContractError("snapshot: expected exactly snapshot_id, input_hash, as_of, expires_at, evidence_refs, data")
    _validate(snapshot["snapshot_id"], _ID, "snapshot.snapshot_id")
    if type(snapshot["data"]) is not dict:
        raise ContractError("snapshot.data: expected object")
    if snapshot["input_hash"] != canonical_hash(snapshot["data"]):
        raise ContractError("snapshot.input_hash: does not match data")
    _validate(snapshot["evidence_refs"], _array(_ID, maximum=10000), "snapshot.evidence_refs")
    if len(set(snapshot["evidence_refs"])) != len(snapshot["evidence_refs"]):
        raise ContractError("snapshot.evidence_refs: duplicate evidence IDs")
    as_of = _timestamp(snapshot["as_of"], "snapshot.as_of")
    expires = _timestamp(snapshot["expires_at"], "snapshot.expires_at")
    if expires <= as_of:
        raise ContractError("snapshot.expires_at: must follow as_of")
    if now is not None:
        instant = _instant(now)
        if as_of > instant:
            raise ContractError("snapshot.as_of: is in the future")
        if expires <= instant:
            raise ContractError("snapshot.expires_at: snapshot has expired")
    return deepcopy(snapshot)


def _instant(now: datetime | str) -> datetime:
    instant = _timestamp(now, "now") if isinstance(now, str) else now
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ContractError("now: expected timezone-aware datetime")
    return instant


def validate_result(role: str, payload: dict, *, snapshot: dict,
                    now: datetime | str | None = None) -> dict:
    """Validate shape, role authority, evidence membership, and snapshot binding.

    ``snapshot`` is the caller's trusted manifest with frozen data, evidence IDs,
    hash and validity window. ``now`` enables expiry/future-output checks without
    reading a clock in this pure module. Return a detached copy on success.
    No natural-language claim or proposed CLI argument gains execution rights.
    """
    schema = result_schema(role)
    _json_value(payload)
    _validate(payload, schema, "result")
    validate_snapshot(snapshot, now=now)
    for key in ("snapshot_id", "input_hash"):
        if payload[key] != snapshot.get(key):
            raise ContractError(f"result.{key}: does not match snapshot")
    digest = payload["input_hash"]
    if any(char not in "0123456789abcdef" for char in digest):
        raise ContractError("result.input_hash: expected lowercase SHA-256")
    refs = snapshot.get("evidence_refs")
    allowed_refs = set(refs)
    seen_ids: set[str] = set()
    for field in ("observations", "hypotheses", "risks", "requests"):
        for index, item in enumerate(payload[field]):
            label = f"result.{field}[{index}]"
            ids = item["evidence_refs"]
            if len(set(ids)) != len(ids) or set(ids) - allowed_refs:
                raise ContractError(f"{label}.evidence_refs: duplicate or unknown evidence")
            if item.get("kind") == "fact" and not ids:
                raise ContractError(f"{label}: facts require evidence")
            if "id" in item:
                if item["id"] in seen_ids:
                    raise ContractError(f"{label}.id: duplicate claim ID")
                seen_ids.add(item["id"])
            if field == "requests" and item["role"] == role:
                raise ContractError(f"{label}.role: requests must target another role")
    generated = _timestamp(payload["generated_at"], "result.generated_at")
    expires = _timestamp(payload["expires_at"], "result.expires_at")
    if expires <= generated:
        raise ContractError("result.expires_at: must follow generated_at")
    if generated < _timestamp(snapshot["as_of"], "snapshot.as_of"):
        raise ContractError("result.generated_at: predates snapshot")
    if expires > _timestamp(snapshot["expires_at"], "snapshot.expires_at"):
        raise ContractError("result.expires_at: exceeds snapshot validity")
    if payload["review"] is not None and payload["proposal"] is not None:
        raise ContractError("result.review: a reviewer cannot also submit a proposal")
    if now is not None:
        instant = _instant(now)
        if generated > instant:
            raise ContractError("result.generated_at: is in the future")
        if expires <= instant:
            raise ContractError("result.expires_at: result has expired")
    return deepcopy(payload)
