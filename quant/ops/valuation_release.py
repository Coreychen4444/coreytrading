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
import hashlib
import argparse
import json

from quant.core.agent_contracts import canonical_hash
from quant.core.io_json import atomic_write_json, read_json, state_lock
from quant.core.paths import (ROOT, VALUATION_RELEASES_PATH, VALUATION_RELEASE_LOCK_PATH,
                              VALUATION_RELEASE_POLICY_PATH, VALUATION_COMPANY_MODELS_PATH)
from quant.valuation.releases import append_event, journal, resolve, KINDS
from quant.valuation.company_model import build as build_company, VERSION, stamp


def _now():
    return datetime.now(timezone.utc).isoformat()


def code_manifest(root=ROOT):
    """Measured code/locked dependency fingerprint; no submitted hash is trusted."""
    root = Path(root)
    paths = list((root / 'quant/valuation').glob('*.py'))
    paths += list((root / 'quant/core').glob('*.py'))
    paths += [root / p for p in ('quant/ops/valuation_release.py',
                                'quant/ops/valuation_daily.py',
                                'quant/ops/agent_inputs.py', 'quant/ops/agent_snapshots.py',
                                'quant/ops/agent_workflow.py',
                                'quant/report/valuation_report.py', 'requirements.txt')]
    manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(paths)}
    if not list((root / 'quant/valuation').glob('*.py')):
        raise ValueError('valuation implementation unavailable')
    return manifest


def host_context(ticker, kind, *, as_of=None, models_path=VALUATION_COMPANY_MODELS_PATH,
                 policy_path=VALUATION_RELEASE_POLICY_PATH, root=ROOT):
    """Load current company input and host-owned identity attestations each time.

    Policy is deliberately outside candidate/company documents. An attestation
    records a verified exact review; this function does not authenticate people
    or create acceptance evidence. Missing policy enables no output.
    """
    now = as_of or _now()
    if kind not in KINDS:
        raise ValueError('unsupported output kind')
    package = read_json(Path(models_path))[ticker]
    if package['ticker'] != ticker:
        raise ValueError('company catalog identity mismatch')
    receipt = build_company(package, as_of=now)
    if receipt['status'] != 'research_only':
        raise ValueError('company input does not reconcile')
    policy = read_json(Path(policy_path)) if Path(policy_path).exists() else {
        'schema_version': '1.0', 'enabled_outputs': {}, 'verified_reviews': []}
    if policy['schema_version'] != '1.0':
        raise ValueError('unknown host release policy schema')
    outputs = policy['enabled_outputs']
    if not isinstance(outputs, dict) or not isinstance(policy['verified_reviews'], list):
        raise ValueError('invalid host release policy')
    for scope in outputs.values():
        if not isinstance(scope, list) or not set(scope) <= KINDS or len(scope) != len(set(scope)):
            raise ValueError('invalid company output enablement')
    trust = {}
    for review in policy['verified_reviews']:
        if review['ticker'] != ticker or review['kind'] != kind:
            continue
        digest = review['review_hash']
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)
                or not review['reviewer_id'] or not review['identity_basis']
                or stamp(review['verified_at']) > stamp(now)):
            raise ValueError('invalid review identity attestation')
        if stamp(review['valid_until']) <= stamp(now):
            continue
        if review['ticker'] == ticker and review['kind'] == kind:
            if digest in trust:
                raise ValueError('duplicate verified review')
            trust[digest] = review['reviewer_id']
    return {'input_hash': canonical_hash(package), 'code_hash': canonical_hash(code_manifest(root)),
            'model_version': VERSION, 'enabled': kind in outputs.get(ticker, []),
            'trusted_review_hashes': set(trust), 'reviewer_ids': trust}


def production_release(ticker, kind, *, as_of=None, expected_input_hash=None,
                       path=VALUATION_RELEASES_PATH, context_fn=None):
    """Current output with frozen/live input binding and no previous-price fallback."""
    now = as_of or _now()
    held = {'status': 'withheld', 'ticker': ticker, 'kind': kind, 'authority': 'analysis_only'}
    try:
        context = (context_fn or host_context)(ticker, kind, as_of=now)
        if not context['enabled']:
            return dict(held, reason='release_channel_disabled')
        if expected_input_hash is not None and context['input_hash'] != expected_input_hash:
            return dict(held, reason='frozen_input_changed')
        document = load(path)
        selected = read_release(ticker=ticker, kind=kind, current_input_hash=context['input_hash'],
                                current_code_hash=context['code_hash'], enabled=True,
                                trusted_review_hashes=context['trusted_review_hashes'], path=path,
                                clock=lambda: now)
        if selected['status'] == 'released':
            review = document['artifacts'][selected['review_hash']]
            if (selected['model_version'] != context['model_version']
                    or review['reviewer_id'] != context['reviewer_ids'].get(selected['review_hash'])):
                return dict(held, reason='model_or_verified_reviewer_mismatch')
        return selected
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        return dict(held, reason='host_release_context_unavailable: ' + str(exc))


def production_submit(event, artifacts, *, expected_head, apply=False,
                      path=VALUATION_RELEASES_PATH, lock_path=VALUATION_RELEASE_LOCK_PATH,
                      context_fn=None, clock=_now):
    """Promote only via live host policy; revocation works with expired inputs."""
    def current():
        context = (context_fn or host_context)(event['ticker'], event['kind'], as_of=clock())
        if not context['enabled']:
            raise ValueError('release channel disabled by host')
        document = load(path)
        review = artifacts.get(event['review_hash']) or document['artifacts'].get(event['review_hash'])
        candidate = artifacts.get(event['candidate_hash']) or document['artifacts'].get(event['candidate_hash'])
        if (not review or not candidate or candidate['model_version'] != context['model_version']
                or review['reviewer_id'] != context['reviewer_ids'].get(event['review_hash'])):
            raise ValueError('exact model and verified reviewer identity required')
        return context
    return submit(event, artifacts, expected_head=expected_head, apply=apply, path=path,
                  lock_path=lock_path, current_context=current, clock=clock)


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status', help='read current publication and measured host hashes')
    status.add_argument('--tickers', nargs='+')
    send = commands.add_parser('submit', help='validate an exact event/artifact request; preview by default')
    send.add_argument('--request', type=Path, required=True)
    send.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'submit':
            request = read_json(args.request)
            if set(request) != {'event', 'artifacts', 'expected_head'}:
                raise ValueError('request needs only event/artifacts/expected_head; host owns hashes and trust')
            result = production_submit(**request, apply=args.apply)
        else:
            from quant.valuation.universe import coverage_tickers
            result = {'as_of': _now(), 'authority': 'analysis_only', 'tickers': {}}
            for ticker in args.tickers or coverage_tickers():
                ticker = ticker.upper()
                result['tickers'][ticker] = {}
                for kind in sorted(KINDS):
                    row = production_release(ticker, kind, as_of=result['as_of'])
                    try:
                        context = host_context(ticker, kind, as_of=result['as_of'])
                        row['host'] = {k: context[k] for k in ('input_hash', 'code_hash', 'model_version', 'enabled')}
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                        row['host_error'] = str(exc)
                    result['tickers'][ticker][kind] = row
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'status': 'rejected', 'reason': str(exc), 'authority': 'analysis_only'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
