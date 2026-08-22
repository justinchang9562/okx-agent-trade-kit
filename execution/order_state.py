from enum import Enum


class OrderState(str, Enum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    POSITION_UNPROTECTED = "POSITION_UNPROTECTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


ACTIVE_ORDER_STATES = {
    OrderState.APPROVED.value,
    OrderState.SUBMITTED.value,
    OrderState.SUBMISSION_UNKNOWN.value,
    OrderState.OPEN.value,
    OrderState.PARTIALLY_FILLED.value,
    OrderState.FILLED.value,
    OrderState.POSITION_UNPROTECTED.value,
}

MANAGED_POSITION_STATES = {
    OrderState.PARTIALLY_FILLED.value,
    OrderState.FILLED.value,
    OrderState.POSITION_UNPROTECTED.value,
}

ALLOWED_TRANSITIONS = {
    OrderState.APPROVED.value: {OrderState.SUBMITTED.value, OrderState.REJECTED.value},
    OrderState.SUBMITTED.value: {
        OrderState.SUBMITTED.value, OrderState.SUBMISSION_UNKNOWN.value, OrderState.OPEN.value,
        OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value, OrderState.REJECTED.value,
        OrderState.CANCELLED.value,
    },
    OrderState.SUBMISSION_UNKNOWN.value: {
        OrderState.SUBMISSION_UNKNOWN.value, OrderState.SUBMITTED.value, OrderState.OPEN.value,
        OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value, OrderState.REJECTED.value,
        OrderState.CANCELLED.value,
    },
    OrderState.OPEN.value: {
        OrderState.OPEN.value, OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value,
        OrderState.CANCELLED.value, OrderState.REJECTED.value,
    },
    OrderState.PARTIALLY_FILLED.value: {
        OrderState.PARTIALLY_FILLED.value, OrderState.FILLED.value,
        OrderState.CANCELLED.value, OrderState.POSITION_UNPROTECTED.value,
    },
    OrderState.FILLED.value: {
        OrderState.FILLED.value, OrderState.POSITION_UNPROTECTED.value, OrderState.CLOSED.value,
    },
    OrderState.POSITION_UNPROTECTED.value: {
        OrderState.POSITION_UNPROTECTED.value, OrderState.FILLED.value, OrderState.CLOSED.value,
    },
    OrderState.CANCELLED.value: {OrderState.CANCELLED.value},
    OrderState.REJECTED.value: {OrderState.REJECTED.value},
    OrderState.CLOSED.value: {OrderState.CLOSED.value},
}
