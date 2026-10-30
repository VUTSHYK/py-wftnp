import asyncio
from uuid import UUID

import pytest

from wftnp import ClientState, Endpoint, NotConnected, OperationRejected, RequestTimeout, WftnpClient

from .support.server import CHARACTERISTIC, SERVICE, eventually


async def test_public_operations(client, server):
    assert (await client.discover_services())[0].uuid == SERVICE
    assert (await client.discover_characteristics(SERVICE))[0].uuid == CHARACTERISTIC
    assert await client.read_characteristic(CHARACTERISTIC) == b"value"
    await client.write_characteristic(CHARACTERISTIC, b"\x00\xff")
    assert server.requests[-1].payload == CHARACTERISTIC.bytes + b"\x00\xff"


async def test_fail_fast_when_not_started(server):
    client = WftnpClient(server.endpoint)
    with pytest.raises(NotConnected):
        await client.discover_services()
    with pytest.raises(NotConnected):
        await client.wait_ready()
    with pytest.raises(NotConnected):
        await client.subscribe(CHARACTERISTIC)
    await client.stop()


async def test_context_manager_and_idempotent_start(server):
    async with WftnpClient(server.endpoint) as client:
        await client.start()
        assert client.state == ClientState.READY
        assert len(server.peers) == 1
    assert client.state == ClientState.STOPPED


async def test_context_failure_stops_supervisor(server):
    server.server.close()
    await server.server.wait_closed()
    client = WftnpClient(server.endpoint, auto_reconnect=False)
    with pytest.raises(NotConnected):
        async with client:
            pytest.fail("Must not enter")
    assert client.state == ClientState.STOPPED
    assert client._task is None


async def test_rejected_subscribe_cleans_up_without_deadlock(client, server):
    async def reject(peer, req):
        if req.operation == 5:
            await peer.respond(req, status=4)
            return True
        return False

    server.handler = reject
    async with asyncio.timeout(1):
        with pytest.raises(OperationRejected):
            await client.subscribe(CHARACTERISTIC, lambda _: None)
    assert not client._subscriptions
    assert client.state == ClientState.READY


async def test_state_listener_errors_are_isolated(server, caplog):
    client = WftnpClient(server.endpoint)
    states = []
    remove = client.add_state_listener(states.append)

    def bad_listener(state):
        raise RuntimeError("listener failure")

    remove_bad = client.add_state_listener(bad_listener)
    await client.start()
    await client.wait_ready()
    remove_bad()
    remove()
    await client.stop()
    assert states == [ClientState.CONNECTING, ClientState.RESTORING, ClientState.READY]
    assert "listener failure" in caplog.text


@pytest.mark.parametrize(
    "name,value",
    [
        ("connect_timeout", 0),
        ("request_timeout", -1),
        ("close_timeout", float("nan")),
        ("heartbeat_interval", 0),
        ("reconnect_initial", 0),
        ("reconnect_max", float("inf")),
    ],
)
def test_invalid_timeouts(name, value):
    with pytest.raises(ValueError):
        WftnpClient(Endpoint("host"), **{name: value})


def test_invalid_backoff_range():
    with pytest.raises(ValueError):
        WftnpClient(Endpoint("host"), reconnect_initial=3, reconnect_max=1)


async def test_invalid_subscriptions(client):
    with pytest.raises(ValueError):
        await client.subscribe(CHARACTERISTIC, buffer_size=0)
    sub = await client.subscribe(CHARACTERISTIC)
    with pytest.raises(ValueError):
        await client.subscribe(CHARACTERISTIC)
    await sub.close()
    with pytest.raises(ValueError):
        await client.wait_ready(0)


async def test_unknown_notification_is_ignored(client, server):
    sub = await client.subscribe(CHARACTERISTIC)
    await server.peers[-1].notify(b"ignored", UUID(int=1))
    await server.peers[-1].notify(b"accepted")
    assert (await asyncio.wait_for(anext(sub), 1)).value == b"accepted"


async def test_oversized_write_does_not_damage_connection(client):
    with pytest.raises(ValueError):
        await client.write_characteristic(CHARACTERISTIC, b"x" * 65520)
    assert await client.read_characteristic(CHARACTERISTIC) == b"value"


async def test_notifications_can_arrive_before_subscribe_response(client, server):
    async def early(peer, req):
        if req.operation == 5:
            await peer.notify(b"early")
        return False

    server.handler = early
    sub = await client.subscribe(CHARACTERISTIC)
    assert (await asyncio.wait_for(anext(sub), 1)).value == b"early"


async def test_cancel_subscribe_removes_intent(client, server):
    async def silent(peer, req):
        return req.operation == 5

    server.handler = silent
    task = asyncio.create_task(client.subscribe(CHARACTERISTIC, lambda _: None))
    await eventually(lambda: server.requests[-1].operation == 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client._subscriptions


async def test_subscription_deadline_includes_wait_for_bookkeeping(client):
    client._request_timeout = 0.02
    async with client._subscription_lock:
        with pytest.raises(RequestTimeout):
            await client.subscribe(CHARACTERISTIC)
    assert not client._subscriptions
    assert client.state == ClientState.READY
    client._request_timeout = 1.0
    assert await client.read_characteristic(CHARACTERISTIC) == b"value"


async def test_subscribe_timeout_removes_intent(client, server):
    async def silent(peer, req):
        return req.operation == 5

    server.handler = silent
    with pytest.raises(RequestTimeout):
        await client.subscribe(CHARACTERISTIC, lambda _: None)
    assert not client._subscriptions
