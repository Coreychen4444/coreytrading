"""Hand calculations and disclosure-shaped securities cases; no network/private files."""
from copy import deepcopy

import pytest

from quant.valuation.company_eps import calculate, aggregate


def block():
    return {'basis':'explicit test securities', 'basic_equivalents':[],
            'participation':None, 'debt_if_converted':[]}


def note(id_, shares, interest):
    return {'id':id_, 'basis':'after-tax interest, not cash settlement',
            'shares':shares, 'after_tax_interest':interest}


def test_disclosed_numerator_addback_is_not_profit_or_debt_conversion():
    b=block(); b['debt_if_converted']=[note('aggregate_disclosed_only',15.0,3.0)]
    r=calculate(income=150.0,control_income=150.0,basic_common=250.0,incremental=.5+12.0,securities=b)
    assert r['basic_common_income']==150.0
    assert r['diluted_income']==pytest.approx(153.0)
    assert r['diluted_shares']==pytest.approx(277.5)
    assert round(r['diluted_eps'],2)==.55


def test_issuer_combined_equal_rights_presentation():
    # Issuer presents common+as-converted preferred as a combined EPS class.
    # A legal common-only numerator is a separate disclosure, not this one.
    b=block(); b['basic_equivalents']=[{'id':'A','basis':'issuer combined equal-rights preferred presentation','shares':20.0}]
    r=calculate(income=300,control_income=300,basic_common=850.0,incremental=25.0,securities=b)
    assert r['basic_common_income']==300
    assert r['basic_shares']==pytest.approx(870.0)
    assert round(r['diluted_eps'],2)==.34
    assert r['diluted_shares']==pytest.approx(895.0)
    assert r['diluted_eps']==pytest.approx(300/895.0)


def test_prefunded_warrants_stay_in_loss_period_basic_shares():
    b=block();b['basic_equivalents']=[{'id':'prefunded','basis':'nominal exercise price','shares':20.0}]
    b['debt_if_converted']=[note('notes',60.0,100)]
    r=calculate(income=-200.0,control_income=-200.0,basic_common=250.0,incremental=10.0,securities=b)
    assert r['basic_shares']==pytest.approx(270.0)
    assert r['diluted_shares']==r['basic_shares']
    assert r['included']==[]


def test_continuing_loss_controls_despite_discontinued_gain():
    b=block();b['debt_if_converted']=[note('convert',30,1)]
    r=calculate(income=100,control_income=-10,basic_common=100,incremental=20,securities=b)
    assert r['diluted_eps']==1 and r['diluted_shares']==100


def test_securities_ranked_not_naively_added_together():
    b=block();b['debt_if_converted']=[note('less_dilutive',20,15),note('most_dilutive',100,0)]
    r=calculate(income=100,control_income=100,basic_common=100,incremental=0,securities=b)
    assert r['included']==['most_dilutive']
    assert r['excluded']==['less_dilutive']
    assert r['diluted_eps']==.5
    b['debt_if_converted'].reverse()
    assert calculate(income=100,control_income=100,basic_common=100,incremental=0,securities=b)==r


def test_participating_unvested_shares_do_not_share_losses():
    b=block();b['participation']={'id':'unvested','basis':'nonforfeitable dividends not losses',
        'shares':10,'loss_participation':False,'convertible':False,'common_dividends':0,'participant_dividends':0}
    assert calculate(income=-100,control_income=-100,basic_common=100,incremental=0,securities=b)['basic_common_income']==-100
    r=calculate(income=110,control_income=110,basic_common=100,incremental=0,securities=b)
    assert r['basic_common_income']==100 and r['diluted_eps']==1


def test_annual_retests_raw_income_not_quarterly_antidilution():
    b=block();b['debt_if_converted']=[note('N',100,1)]
    raw=[{'income':n,'control_income':n,'basic_common':100,'incremental':0,'securities':deepcopy(b)}for n in [-10,-10,100,100]]
    assert calculate(**raw[0])['included']==[]
    merged=aggregate(raw,[91]*4);r=calculate(**merged)
    assert r['diluted_income']==184 and r['diluted_shares']==200
    assert r['diluted_eps']==.92


@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),True,-1])
def test_invalid_conversion_shares_rejected(bad):
    b=block();b['debt_if_converted']=[note('N',bad,1)]
    with pytest.raises(ValueError):calculate(income=100,control_income=100,basic_common=100,incremental=0,securities=b)


def test_duplicate_security_and_changing_annual_rights_rejected():
    b=block();b['debt_if_converted']=[note('N',100,1),note('N',100,1)]
    with pytest.raises(ValueError):calculate(income=1,control_income=1,basic_common=1,incremental=0,securities=b)
    b=block();b['participation']={'id':'P','basis':'test','shares':1,'loss_participation':False,'convertible':False,'common_dividends':0,'participant_dividends':0}
    one={'income':1,'control_income':1,'basic_common':1,'incremental':0,'securities':b}
    two=deepcopy(one);two['securities']['participation']['loss_participation']=True
    with pytest.raises(ValueError,match='changing participation'):aggregate([one,two],[91,91])


def test_participant_dividends_common_loss_excludes_ordinary_dilution():
    b=block();b['participation']={'id':'P','basis':'declared nonforfeitable dividends',
        'shares':10,'loss_participation':False,'convertible':False,
        'common_dividends':0,'participant_dividends':100}
    b['debt_if_converted']=[note('N',100,1)]
    r=calculate(income=10,control_income=10,basic_common=100,incremental=20,securities=b)
    assert r['basic_common_income']==-90
    assert r['diluted_shares']==100 and r['diluted_eps']==-.9
    assert r['included']==[]


def test_positive_common_continuing_control_applies_to_total_loss():
    r=calculate(income=-100,control_income=100,basic_common=100,incremental=20,securities=block())
    assert r['diluted_shares']==120
    assert r['diluted_eps']==pytest.approx(-100/120)
