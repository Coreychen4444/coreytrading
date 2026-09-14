"""Persistent valuation release transactions; no trading authority or messaging.

The registry is a single atomic document: a promotion cannot become visible
without its artifacts. Review allowlists and current hashes are trusted host
inputs, never taken from the submitted candidate. Defaults publish nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from datetime import datetime, timezone
from copy import deepcopy

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json, state_lock
from quant.core.paths import VALUATION_RELEASES_PATH, VALUATION_RELEASE_LOCK_PATH
from quant.valuation.releases import append_event, journal, resolve


def _now():
    return datetime.now(timezone.utc).isoformat()


def load(path=VALUATION_RELEASES_PATH):
    """Atomic snapshot read. Missing is empty, corrupt is an error, never fallback."""
    path = Path(path)
    if not path.exists():
        return {'schema_version': '1.0', 'events': [], 'artifacts': {}}
    document = read_json(path)
    if document['schema_version'] != '1.0':
        raise ValueError('unknown release registry schema')
    journal(document['events'])
    for digest, artifact in document['artifacts'].items():
        if canonical_hash(artifact) != digest:
            raise ValueError('changed stored release artifact')
    return document


def submit(event, artifacts, *, expected_head, current_input_hash=None,
           current_code_hash=None, trusted_review_hashes=(), apply=False,
           path=VALUATION_RELEASES_PATH, lock_path=VALUATION_RELEASE_LOCK_PATH, clock=_now,
           current_context=None):
    """Preview by default. Locked CAS + atomic document replacement when applied.

    event_id is the idempotency key; uncertain writes must be retried with the
    exact same event/artifacts, never a fresh ID. An existing event returns its
    historical submission receipt, not an assertion that it is still released.
    """
    if 'at' in event or 'request_hash' in event:
        raise ValueError('host owns submission clock and request hash')
    request_hash = canonical_hash({'event': event, 'artifacts': artifacts})

    def prepare():
        document = load(path)
        for old in document['events']:
            if old['event_id'] == event['event_id']:
                if old.get('request_hash') != request_hash:
                    raise ValueError('event id reused with different content')
                return document, {'status': 'already_applied', 'event_hash': old['event_hash'],
                                  'event_id': old['event_id'], 'authority': 'analysis_only'}
        merged = deepcopy(document['artifacts'])
        for digest, artifact in artifacts.items():
            if canonical_hash(artifact) != digest:
                raise ValueError('submitted artifact digest mismatch')
            merged[digest] = deepcopy(artifact)
        at = clock()
        prepared = dict(event, at=at, request_hash=request_hash)
        events = append_event(document['events'], prepared, expected_head=expected_head)
        if event['action'] == 'promote':
            context = current_context() if current_context is not None else {
                'input_hash': current_input_hash, 'code_hash': current_code_hash,
                'trusted_review_hashes': trusted_review_hashes}
            if event['review_hash'] not in context['trusted_review_hashes']:
                raise ValueError('review is not trusted by host')
            selected = resolve(events, merged, ticker=event['ticker'], kind=event['kind'], as_of=at,
                               current_input_hash=context['input_hash'], current_code_hash=context['code_hash'],
                               enabled=True)
            if selected['status'] != 'released':
                raise ValueError('promotion rejected: ' + selected['reason'])
        document = {'schema_version': '1.0', 'events': events, 'artifacts': merged}
        return document, {'status': 'prepared', 'event_hash': events[-1]['event_hash'],
                          'event_id': event['event_id'], 'authority': 'analysis_only'}

    if not apply:
        _, receipt = prepare()
        return receipt
    with state_lock(Path(lock_path)):
        document, receipt = prepare()  # All checks rerun under the dedicated lock.
        if receipt['status'] == 'already_applied':
            return receipt
        atomic_write_json(Path(path), document)
        # Atomic writer may raise after replacing. Retrying identical event finds
        # its committed ID; it does not create a second promotion or revocation.
        receipt['status'] = 'applied'
        return receipt


def read_release(*, ticker, kind, current_input_hash, current_code_hash,
                 trusted_review_hashes=(), enabled=False, path=VALUATION_RELEASES_PATH, clock=_now,
                 current_context=None):
    """Re-read current registry and host trust for each request; no cached success."""
    held = {'status': 'withheld', 'ticker': ticker, 'kind': kind, 'authority': 'analysis_only'}
    if not enabled:
        return dict(held, reason='release_channel_disabled')
    try:
        document = load(path)
        context = current_context() if current_context is not None else {
            'input_hash': current_input_hash, 'code_hash': current_code_hash,
            'trusted_review_hashes': trusted_review_hashes}
        selected = resolve(document['events'], document['artifacts'], ticker=ticker, kind=kind,
                           as_of=clock(), current_input_hash=context['input_hash'],
                           current_code_hash=context['code_hash'], enabled=True)
        if selected['status'] == 'released' and selected['review_hash'] not in context['trusted_review_hashes']:
            return dict(held, reason='review_no_longer_trusted')
        return selected
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return dict(held, reason='release_registry_unavailable: ' + str(exc))
