"""Async WFTNP protocol client. Fitness profile decoding is deliberately external."""

from .client import WftnpClient
from .exceptions import (
    CallbackError,
    ConnectionLost,
    NotConnected,
    OperationRejected,
    ProtocolError,
    RequestTimeout,
    SubscriptionError,
    SubscriptionOverflow,
    WftnpError,
)
from .models import (
    Advertisement,
    Characteristic,
    CharacteristicProperties,
    ClientState,
    Endpoint,
    Notification,
    Service,
)
from .server import CharacteristicSpec, ServiceSpec, WftnpServer
from .subscription import Subscription

__all__ = [
    "Advertisement",
    "CallbackError",
    "Characteristic",
    "CharacteristicProperties",
    "CharacteristicSpec",
    "ClientState",
    "ConnectionLost",
    "Endpoint",
    "NotConnected",
    "Notification",
    "OperationRejected",
    "ProtocolError",
    "RequestTimeout",
    "Service",
    "ServiceSpec",
    "Subscription",
    "SubscriptionError",
    "SubscriptionOverflow",
    "WftnpClient",
    "WftnpError",
    "WftnpServer",
]
