"""A worker's lease is checked for every business write, including after model latency."""

from contextlib import contextmanager
from datetime import timedelta
from app import schema as s
from app.db import lock, now, row, transaction, update


class LostLease(Exception):
    pass


class Execution:
    def __init__(self, tenant, run_id, lease, model, gateway=None, sender=None):
        self.tenant, self.run_id, self.lease = tenant, run_id, lease
        self.model, self.gateway, self.sender = model, gateway, sender

    @contextmanager
    def tx(self):
        with transaction(self.tenant) as c:
            lock(c, "mutation", self.tenant)
            r = row(c, s.runs, self.tenant, self.run_id)
            if r.lease_token != self.lease or r.status != "running":
                raise LostLease()
            update(
                c, s.runs, self.tenant, r.id, lease_until=now() + timedelta(seconds=120)
            )
            yield c, r
