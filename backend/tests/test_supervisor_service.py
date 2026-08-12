from __future__ import annotations

from dataclasses import dataclass, field

from chaoxing_app.worker.service import SupervisorService, SupervisorServiceConfig


@dataclass
class RecordingSupervisor:
    active: list[str] = field(default_factory=lambda: ["task-1"])
    tick_claim_flags: list[bool] = field(default_factory=list)
    pause_calls: int = 0
    terminate_calls: int = 0

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(self.active)

    def tick(self, *, claim_new: bool = True) -> object:
        self.tick_claim_flags.append(claim_new)
        if not claim_new:
            self.active.clear()
        return object()

    def request_pause_all(self) -> tuple[str, ...]:
        self.pause_calls += 1
        return tuple(self.active)

    def terminate_all(self) -> tuple[str, ...]:
        self.terminate_calls += 1
        remaining = tuple(self.active)
        self.active.clear()
        return remaining


def test_service_shutdown_uses_cooperative_no_claim_ticks() -> None:
    supervisor = RecordingSupervisor()
    service = SupervisorService(
        supervisor=supervisor,  # type: ignore[arg-type]
        config=SupervisorServiceConfig(
            poll_interval_seconds=0.01,
            shutdown_grace_seconds=1,
        ),
    )

    assert service.stop() == ()
    assert supervisor.pause_calls == 1
    assert supervisor.tick_claim_flags == [False]
    assert supervisor.terminate_calls == 1


def test_service_terminates_workers_after_zero_grace_period() -> None:
    supervisor = RecordingSupervisor()
    service = SupervisorService(
        supervisor=supervisor,  # type: ignore[arg-type]
        config=SupervisorServiceConfig(shutdown_grace_seconds=0),
    )

    assert service.stop() == ("task-1",)
    assert supervisor.pause_calls == 1
    assert supervisor.tick_claim_flags == []
