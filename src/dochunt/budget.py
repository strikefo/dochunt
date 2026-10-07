"""Shared monotonic deadline and cumulative limits for every request/pass."""
from dataclasses import replace
from time import monotonic
from .models import Limits, Usage


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limits: Limits, clock=monotonic):
        self.limits = replace(limits)
        self.clock = clock
        self.started = clock()
        self.usage = Usage()
        self.blocked = False

    @property
    def remaining_seconds(self):
        if self.blocked: raise BudgetExceeded('Accounting uncertain; budget blocked')
        remaining = self.limits.application_deadline_minutes * 60 - (self.clock() - self.started)
        if remaining <= 0: raise BudgetExceeded('Application deadline exhausted')
        return remaining

    def remaining(self, name):
        self.remaining_seconds
        return max(0, getattr(self.limits, name) - getattr(self.usage, name))

    def check(self, **counts):
        self.remaining_seconds
        if self.blocked: raise BudgetExceeded('Accounting uncertain; budget blocked')
        if any(getattr(self.usage,k)>getattr(self.limits,k) for k in vars(self.usage)):
            raise BudgetExceeded('Returned usage exhausted budget')
        for name, value in counts.items():
            if name not in vars(self.usage) or type(value) is not int or value < 0:
                raise ValueError('Invalid usage counter')
            if getattr(self.usage, name) + value > getattr(self.limits, name):
                raise BudgetExceeded(f'{name} allowance exhausted')

    def consume(self, **counts):
        self.check(**counts)
        for name, value in counts.items(): setattr(self.usage, name, getattr(self.usage,name)+value)

    def record_returned(self, **counts):
        """Record paid usage even when returned hidden context exceeds the ceiling."""
        if any(k not in vars(self.usage) or type(v) is not int or v < 0 for k,v in counts.items()):
            raise ValueError('Invalid returned usage')
        for name,value in counts.items(): setattr(self.usage,name,getattr(self.usage,name)+value)
        self.check()
        for name in counts:
            if getattr(self.usage,name) > getattr(self.limits,name):
                raise BudgetExceeded(f'Returned {name} exceeded allowance; no further calls')

    def snapshot(self):
        return replace(self.usage)


def bounded_call(function, timeout, *args):
    """Bound the caller's wait even during DNS/socket stalls.

    A daemon worker may finish an already-started operation after timeout. Callers
    must block their budget on timeout, so no further request is issued. This is
    client cancellation, not a guarantee of cancellation at a remote provider.
    """
    import queue
    import threading
    if timeout <= 0: raise TimeoutError('Client deadline exhausted')
    result=queue.Queue(maxsize=1)
    def run():
        try: result.put((True,function(*args)))
        except Exception as exc: result.put((False,exc))
    worker=threading.Thread(target=run,daemon=True)
    worker.start()
    try: success,value=result.get(timeout=timeout)
    except queue.Empty as exc: raise TimeoutError('Client operation deadline exhausted') from exc
    if not success: raise value
    return value
