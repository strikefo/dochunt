import pytest
from dochunt.budget import Budget, BudgetExceeded
from dochunt.models import Limits


def test_twelfth_research_request_exhausts_allowance():
    b = Budget(Limits(), lambda: 0)
    for _ in range(12): b.consume(research_requests=1)
    with pytest.raises(BudgetExceeded): b.consume(research_requests=1)
    assert b.usage.research_requests == 12


def test_review_retries_preserve_explicit_three_request_limit():
    b = Budget(Limits(review_requests=3), lambda: 0)
    for _ in range(3): b.consume(review_requests=1)
    with pytest.raises(BudgetExceeded): b.consume(review_requests=1)


@pytest.mark.parametrize('name,limit', [('searches',20),('fetches',30),('input_tokens',120000),('output_tokens',24000)])
def test_exact_limits(name, limit):
    b = Budget(Limits(), lambda: 0)
    b.consume(**{name:limit})
    with pytest.raises(BudgetExceeded): b.consume(**{name:1})


def test_runtime_deadline():
    now = [0]
    b = Budget(Limits(), lambda: now[0])
    now[0] = 1500
    with pytest.raises(BudgetExceeded): b.consume(searches=1)


def test_atomic_and_invalid_counters():
    b = Budget(Limits(), lambda: 0)
    with pytest.raises(BudgetExceeded): b.consume(searches=1, fetches=31)
    assert b.usage.searches == 0
    with pytest.raises(ValueError): b.consume(searches=-1)


def test_blocking_work_has_bounded_client_wait():
    import threading
    import time
    from dochunt.budget import bounded_call
    release=threading.Event()
    started=time.monotonic()
    try:
        with pytest.raises(TimeoutError): bounded_call(release.wait,0.02)
        assert time.monotonic()-started<1
    finally: release.set()
