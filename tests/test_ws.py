"""Regression tests for the websocket subscription broadcast tree.

These tests guard the fix for a broadcast bug where a single badly-disconnected
client would abort the whole broadcast: the dead socket was being removed from
``SubscriptionTree.subscribers`` *while it was being iterated*, which raises
``RuntimeError: Set changed size during iteration``. That exception propagated
before the recursion into the child subtrees ran, so the remaining same-level
subscribers and every resource-level subscriber stopped receiving events while
the dead connection lingered.

The tests use lightweight fakes so they exercise the real ``SubscriptionTree``
broadcast logic without a server, a database, or a real socket.
"""

import pytest
from starlette.websockets import WebSocketState

from spoolman.ws import SubscriptionTree, WebsocketManager


class FakeEvent:
    """Minimal stand-in for an :class:`~spoolman.api.v1.models.Event`.

    ``SubscriptionTree.send`` only relies on the event having a ``json()``
    method, so this keeps the tests hermetic and independent of the Pydantic
    model stack while still asserting that the serialized payload is delivered
    verbatim (i.e. the wire format is untouched by the fix).
    """

    def __init__(self, wire: str = '{"type":"updated"}') -> None:
        """Store the canned wire representation."""
        self._wire = wire
        self.json_calls = 0

    def json(self) -> str:
        """Return the canned JSON string, counting each call."""
        self.json_calls += 1
        return self._wire


class FakeWebSocket:
    """A fake websocket that records what it was sent.

    It mimics only the surface that ``SubscriptionTree.send`` touches:
    ``client_state``/``application_state`` for liveness checks, an async
    ``send_text`` that records (or fails), and ``client`` for logging.
    """

    def __init__(
        self,
        *,
        client_state: WebSocketState = WebSocketState.CONNECTED,
        application_state: WebSocketState = WebSocketState.CONNECTED,
        fail_on_send: bool = False,
    ) -> None:
        """Configure the fake socket's liveness and send behaviour."""
        self.client_state = client_state
        self.application_state = application_state
        self.fail_on_send = fail_on_send
        self.received: list[str] = []
        self.client = None

    async def send_text(self, data: str) -> None:
        """Record the sent payload, or raise if configured to fail."""
        if self.fail_on_send:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')
        self.received.append(data)


def connected_ws() -> FakeWebSocket:
    """Build a healthy, fully-connected fake socket."""
    return FakeWebSocket(
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )


def disconnected_ws() -> FakeWebSocket:
    """Build a fake socket that reports as badly disconnected."""
    return FakeWebSocket(
        client_state=WebSocketState.DISCONNECTED,
        application_state=WebSocketState.DISCONNECTED,
    )


@pytest.mark.asyncio
async def test_dead_socket_does_not_block_same_level_subscribers() -> None:
    """A dead subscriber must not stop other subscribers on the same level."""
    tree = SubscriptionTree()
    healthy_a = connected_ws()
    dead = disconnected_ws()
    healthy_b = connected_ws()
    for ws in (healthy_a, dead, healthy_b):
        tree.add((), ws)

    evt = FakeEvent()
    await tree.send((), evt)

    # Both healthy sockets received the event exactly once...
    assert healthy_a.received == [evt.json()]
    assert healthy_b.received == [evt.json()]
    # ...the dead one received nothing and was pruned from the tree.
    assert dead.received == []
    assert dead not in tree.subscribers
    assert healthy_a in tree.subscribers
    assert healthy_b in tree.subscribers


@pytest.mark.asyncio
async def test_dead_root_socket_does_not_block_resource_level_subscribers() -> None:
    """A dead root subscriber must not starve resource-level subscribers.

    This is the core regression: the broadcast for ``("spool", "5")`` walks the
    root level first. A dead root socket previously aborted the walk before it
    ever recursed into the ``spool`` / ``spool/5`` subtrees.
    """
    tree = SubscriptionTree()
    dead_root = disconnected_ws()
    healthy_all_spools = connected_ws()
    healthy_spool_5 = connected_ws()

    tree.add((), dead_root)
    tree.add(("spool",), healthy_all_spools)
    tree.add(("spool", "5"), healthy_spool_5)

    evt = FakeEvent()
    await tree.send(("spool", "5"), evt)

    # Recursion reached both the all-spools and the specific-spool subscribers.
    assert healthy_all_spools.received == [evt.json()]
    assert healthy_spool_5.received == [evt.json()]
    # The dead root socket was cleaned up.
    assert dead_root not in tree.subscribers


@pytest.mark.asyncio
async def test_send_failure_on_connected_socket_is_cleaned_up() -> None:
    """A socket that looks connected but fails to send is dropped, not fatal."""
    tree = SubscriptionTree()
    failing = FakeWebSocket(fail_on_send=True)  # reports CONNECTED, raises on send
    healthy = connected_ws()
    tree.add((), failing)
    tree.add((), healthy)

    evt = FakeEvent()
    await tree.send((), evt)  # must not raise

    assert healthy.received == [evt.json()]
    assert failing not in tree.subscribers
    assert healthy in tree.subscribers


@pytest.mark.asyncio
async def test_dead_socket_pruned_from_correct_node() -> None:
    """A dead subscriber is removed from its own node, leaving others intact."""
    tree = SubscriptionTree()
    healthy_root = connected_ws()
    dead_spool_5 = disconnected_ws()
    tree.add((), healthy_root)
    tree.add(("spool", "5"), dead_spool_5)

    evt = FakeEvent()
    await tree.send(("spool", "5"), evt)

    # The dead socket is gone from the spool/5 node specifically.
    assert dead_spool_5 not in tree.children["spool"].children["5"].subscribers
    assert tree.children["spool"].children["5"].subscribers == set()
    # The unrelated root subscriber is untouched and still received the event.
    assert healthy_root in tree.subscribers
    assert healthy_root.received == [evt.json()]


@pytest.mark.asyncio
async def test_healthy_hierarchy_broadcast_unchanged() -> None:
    """The happy-path hierarchy delivery (and its isolation) is preserved."""
    tree = SubscriptionTree()
    root = connected_ws()
    all_spools = connected_ws()
    spool_5 = connected_ws()
    spool_6 = connected_ws()  # different resource id, must NOT receive the event

    tree.add((), root)
    tree.add(("spool",), all_spools)
    tree.add(("spool", "5"), spool_5)
    tree.add(("spool", "6"), spool_6)

    evt = FakeEvent()
    await tree.send(("spool", "5"), evt)

    assert root.received == [evt.json()]
    assert all_spools.received == [evt.json()]
    assert spool_5.received == [evt.json()]
    # Sibling resource is correctly not notified.
    assert spool_6.received == []
    # Everyone is still subscribed; nothing was wrongly pruned.
    for ws in (root, all_spools, spool_5, spool_6):
        assert ws.client_state == WebSocketState.CONNECTED


@pytest.mark.asyncio
async def test_manager_send_survives_dead_subscriber() -> None:
    """End-to-end through WebsocketManager: a dead client cannot break delivery."""
    manager = WebsocketManager()
    dead = disconnected_ws()
    healthy = connected_ws()
    manager.connect((), dead)
    manager.connect(("spool", "5"), healthy)

    evt = FakeEvent()
    await manager.send(("spool", "5"), evt)

    assert healthy.received == [evt.json()]
    assert dead not in manager.tree.subscribers
