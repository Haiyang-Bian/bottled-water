"""Persistent lease lifetime wraps the P2 driver, including terminal cleanup."""

from .restricted import WindowsRestrictedDriver


class ManagedRestrictedDriver(WindowsRestrictedDriver):
    def __init__(self, *args, authority, host, **kwargs):
        super().__init__(*args, **kwargs)
        self.authority, self.host_id = authority, host
        self.registered_run = None

    async def prepare(self, context):
        if self.registered_run is None:
            self.authority.register(context.run_id, self.host_id,
                                    self.prepared.generation, self.prepared.snapshot)
            self.registered_run = context.run_id
        await super().prepare(context)

    def record(self, state, grants=None):
        self.audit["host_id"] = self.host_id
        if self.job:
            self.audit["job_name"] = self.job.name
        super().record(state, grants)
        if self.registered_run:
            with self.authority.store.transaction():
                self.authority.audit("run.isolation", {
                    "run": self.registered_run, "host": self.host_id,
                    "audit_path": str(self.private.parent / (self.private.name + "-acl.json")),
                    "state": state,
                })

    async def aclose(self):
        cleaned = False
        try:
            await super().aclose()
            cleaned = True
        finally:
            if self.registered_run:
                self.authority.finish(self.registered_run, cleaned=cleaned)
