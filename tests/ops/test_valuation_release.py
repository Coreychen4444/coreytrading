from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from quant.ops import valuation_release as vr
from tests.valuation.test_releases import fixture, NOW


def setup(tmp_path):
    events, artifacts = fixture()
    event = {k: v for k, v in events[0].items() if k not in ('at', 'sequence', 'previous_hash', 'event_hash')}
    args = dict(path=tmp_path/'releases.json', lock_path=tmp_path/'.release.lock',
                current_input_hash='a'*64, current_code_hash='b'*64,
                trusted_review_hashes={event['review_hash']}, clock=lambda: NOW)
    return event, artifacts, args


def read(args, **kw):
    args = {k: v for k, v in args.items() if k != 'lock_path'}
    args.update(kw)
    return vr.read_release(ticker='SYN', kind='fair_value', enabled=True, **args)


def test_preview_has_no_files_and_apply_idempotent(tmp_path):
    event, artifacts, args = setup(tmp_path)
    assert vr.submit(event, artifacts, expected_head=None, **args)['status'] == 'prepared'
    assert not list(tmp_path.iterdir())
    receipt = vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert receipt['status'] == 'applied'
    assert read(args)['output']['point'] == 20
    again = vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert again['status'] == 'already_applied' and again['event_hash'] == receipt['event_hash']
    assert len(vr.load(args['path'])['events']) == 1
    with pytest.raises(ValueError, match='reused'):
        vr.submit(dict(event, reason='changed'), artifacts, expected_head=None, apply=True, **args)


def test_revocation_and_trust_removal_read_current_state(tmp_path):
    event, artifacts, args = setup(tmp_path)
    receipt = vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert read(args, trusted_review_hashes=[])['reason'] == 'review_no_longer_trusted'
    revoke = dict(event_id='revoke', action='revoke', ticker='SYN', kind='fair_value', reason='Drill', release_id='one')
    vr.submit(revoke, {}, expected_head=receipt['event_hash'], apply=True, **args)
    assert read(args)['reason'] == 'current_release_revoked'
    assert len(vr.load(args['path'])['artifacts']) == len(artifacts)


def test_untrusted_or_invalid_promotion_cannot_write(tmp_path):
    event, artifacts, args = setup(tmp_path)
    with pytest.raises(ValueError, match='trusted'):
        vr.submit(event, artifacts, expected_head=None, apply=True, **dict(args, trusted_review_hashes=[]))
    with pytest.raises(ValueError, match='promotion rejected'):
        vr.submit(event, artifacts, expected_head=None, apply=True, **dict(args, current_input_hash='c'*64))
    assert not args['path'].exists()


@pytest.mark.parametrize('after_replace', [False, True])
def test_uncertain_atomic_write_reconciles_same_id(tmp_path, monkeypatch, after_replace):
    event, artifacts, args = setup(tmp_path)
    real = vr.atomic_write_json
    def fail(path, document):
        if after_replace:
            real(path, document)
        raise OSError('synthetic write failure')
    monkeypatch.setattr(vr, 'atomic_write_json', fail)
    with pytest.raises(OSError):
        vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert read(args)['status'] == ('released' if after_replace else 'withheld')
    monkeypatch.setattr(vr, 'atomic_write_json', real)
    receipt = vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert receipt['status'] == ('already_applied' if after_replace else 'applied')
    assert len(vr.load(args['path'])['events']) == 1


def test_competing_cas_writers_only_one_commits(tmp_path):
    event, artifacts, args = setup(tmp_path)
    def submit(identity):
        try:
            return vr.submit(dict(event, event_id=identity), artifacts, expected_head=None, apply=True, **args)['status']
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ['one', 'two']))
    assert sorted(results) == ['applied', 'conflict']
    assert len(vr.load(args['path'])['events']) == 1


def test_corrupt_registry_is_not_treated_as_empty_or_old_success(tmp_path):
    event, artifacts, args = setup(tmp_path)
    vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    args['path'].write_text('{broken')
    assert read(args)['status'] == 'withheld'
    with pytest.raises(ValueError):
        vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert args['path'].read_text() == '{broken'


def test_host_context_reloaded_after_preview_and_on_each_read(tmp_path):
    event, artifacts, args = setup(tmp_path)
    context = {'input_hash': 'a'*64, 'code_hash': 'b'*64,
               'trusted_review_hashes': {event['review_hash']}}
    args['current_context'] = lambda: deepcopy(context)
    assert vr.submit(event, artifacts, expected_head=None, **args)['status'] == 'prepared'
    context['input_hash'] = 'c'*64
    with pytest.raises(ValueError, match='promotion rejected'):
        vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    context['input_hash'] = 'a'*64
    vr.submit(event, artifacts, expected_head=None, apply=True, **args)
    assert read(args)['status'] == 'released'
    context['trusted_review_hashes'] = set()
    assert read(args)['status'] == 'withheld'
