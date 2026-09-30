"""OpenD discrete-quarter boundary; cumulative data must not become a flow."""
import pytest
from quant.core.financials import discrete_quarter


@pytest.mark.parametrize('value,expected',[(1,1),(2,2),(3,3),(4,4),
                                         ('Q1',1),('Q2',2),('Q3',3),('Q4',4)])
def test_current_sdk_and_older_discrete_report_encodings(value,expected):
    assert discrete_quarter(value)==expected


@pytest.mark.parametrize('value',[True,False,1.0,4.0,0,5,None,'FY','ANNUAL','H1','H2',
                                 'Q1_CUM','Q3_CUM','YTD','1','q1',' Q1 ',[],{}])
def test_cumulative_annual_and_ambiguous_encodings_cannot_create_quarterly_flows(value):
    assert discrete_quarter(value) is None
