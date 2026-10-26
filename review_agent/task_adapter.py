"""Task boundaries shared by triple and ontology agents without fake candidates."""
from typing import Protocol


class ReviewTaskAdapter(Protocol):
    def load_state(self, task_id): ...
    def check_current(self, state): ...
    def planner_input(self, state, budget): ...
    def execute_tool(self, action, state): ...
    def freeze_proposal(self, state): ...
    def finalize(self, state): ...


class TripleTaskAdapter:
    def __init__(self, runtime): self.runtime = runtime
    def load_state(self, task_id): return self.runtime.repository.get_candidate(task_id)
    def check_current(self, state): return not self.runtime.should_stop()
    def planner_input(self, state, budget): return state.planner_input(budget, self.runtime.policy.definitions())
    def execute_tool(self, action, state): return self.runtime.tools.execute(action["action"], action["arguments"])
    def freeze_proposal(self, state):
        return self.runtime.orchestrator._proposal(state.audit_id, state.candidate, state.guard, state.bundle,
            self.runtime.budget.usage["validation_rounds"], state.context)
    def finalize(self, state): return self.runtime.policy.final_gaps(state)
