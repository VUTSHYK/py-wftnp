"""Optional mDNS advertisement for a running WFTNP TCP server."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Iterable
from uuid import UUID

from .discovery import SERVICE_TYPE


class ServerAdvertisement:
    """Register and unregister a WFTNP service; own only the Zeroconf we create."""

    def __init__(
        self,
        *,
        name: str,
        address: str,
        port: int,
        service_uuids: Iterable[UUID] = (),
        serial_number: str | None = None,
        mac_address: str | None = None,
    ) -> None:
        if not name or "." in name:
            raise ValueError("mDNS instance name must be nonempty and contain no dots")
        ip = ipaddress.ip_address(address)
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError("mDNS requires a reachable unicast address, not a bind wildcard")
        if not 1 <= port <= 65535:
            raise ValueError("mDNS port must be in 1..65535")
        self.name = name
        self.address = str(ip)
        self.port = port
        self.service_uuids = tuple(service_uuids)
        self.serial_number = serial_number
        self.mac_address = mac_address
        self._zeroconf = None
        self._info = None

    async def start(self) -> None:
        if self._zeroconf is not None:
            return
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError as exc:
            raise ImportError("Install wftnp[discovery] to advertise WFTNP over mDNS") from exc

        properties: dict[str, str] = {}
        if self.service_uuids:
            properties["ble-service-uuids"] = ",".join(str(uuid) for uuid in self.service_uuids)
        if self.serial_number is not None:
            properties["serial-number"] = self.serial_number
        if self.mac_address is not None:
            properties["mac-address"] = self.mac_address

        # MyWhoosh matches the SRV target against the instance name with
        # spaces replaced by hyphens. Keep the target an ASCII DNS label.
        hostname_label = re.sub(r"[^A-Za-z0-9-]", "-", self.name.replace(" ", "-"))
        hostname_label = hostname_label.strip("-")[:63].rstrip("-") or "wftnp"

        info = ServiceInfo(
            SERVICE_TYPE,
            f"{self.name}.{SERVICE_TYPE}",
            addresses=[
                socket.inet_pton(
                    socket.AF_INET6 if ":" in self.address else socket.AF_INET,
                    self.address,
                )
            ],
            port=self.port,
            properties=properties,
            server=f"{hostname_label}.local.",
        )
        zeroconf = AsyncZeroconf()
        try:
            await zeroconf.async_register_service(info, strict=False)
        except BaseException:
            await zeroconf.async_close()
            raise
        self._zeroconf = zeroconf
        self._info = info

    async def stop(self) -> None:
        zeroconf = self._zeroconf
        info = self._info
        if zeroconf is None or info is None:
            return
        self._zeroconf = None
        self._info = None
        try:
            await zeroconf.async_unregister_service(info)
        finally:
            await zeroconf.async_close()
