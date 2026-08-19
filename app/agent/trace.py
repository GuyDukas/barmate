"""Accumulates the steps array returned by /api/execute.

The step schema is fixed by the brief, and the module names have to match the
architecture image exactly. They are a three-way contract between the diagram,
this trace and /api/agent_info, so an unrecognised name is an error rather than
a new entry.
"""

VALID_MODULES = {"Reasoner", "KnowledgeRetriever", "Reflector", "Reviser"}


class Trace:
    def __init__(self):
        self.steps = []
        # Kept apart from the steps because the reviewer must be handed what
        # the tools returned, not the model's account of what they returned.
        self.observations = []

    def add(self, module, system_prompt, user_prompt, response, observation=None):
        if module not in VALID_MODULES:
            raise ValueError(
                f"'{module}' is not a module on the architecture diagram. "
                f"Expected one of {sorted(VALID_MODULES)}")
        payload = dict(response) if isinstance(response, dict) else {"response": response}
        if observation is not None:
            payload["observation"] = observation
            self.observations.append(observation)
        self.steps.append({
            "module": module,
            "prompt": {"System_prompt": system_prompt, "User_prompt": user_prompt},
            "response": payload,
        })

    def as_list(self):
        return self.steps
