class ExecutionError(RuntimeError):
    """Base class for classified execution failures."""


class PreSubmitRejectedError(ExecutionError):
    """A local or explicit exchange rejection known to precede acceptance."""


class SubmissionUncertainError(ExecutionError):
    """The request may have reached OKX and must be reconciled before retry."""


class StateChangedError(ExecutionError):
    """Optimistic state transition lost a compare-and-swap race."""
