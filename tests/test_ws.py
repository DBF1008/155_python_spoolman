"""Unit tests for spoolman.ws — SubscriptionTree and WebsocketManager."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.websockets import WebSocketState

from spoolman.ws import SubscriptionTree, WebsocketManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockWebSocket:
    """Lightweight mock of a Starlette WebSocket for unit testing.

    Each instance gets its own event loop–safe list of sent messages so we
    can assert on delivery after a broadcast.
    """

    def __init__(
        self,
        *,
        client_state: WebSocketState = WebSocketState.CONNECTED,
        application_state: WebSocketState = WebSocketState.CONNECTED,
        send_side_effect: Any = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.client_state = client_state
        self.application_state = application_state
        self.sent: list[str] = []
        self._send_side_effect = send_side_effect
        self.client = MagicMock()
        self.client.host = host

    async def send_text(self, data: str) -> None:
        """Record the message or raise the configured side-effect."""
        if self._send_side_effect is not None:
            if isinstance(self._send_side_effect, BaseException):
                raise self._send_side_effect
            if callable(self._send_side_effect):
                self._send_side_effect(data)
                return
        self.sent.append(data)


class MockEvent:
    """Minimal stand-in for spoolman.api.v1.models.Event."""

    def __init__(self, payload: str = "test_event") -> None:
        self._payload = payload

    def json(self) -> str:
        return self._payload


# ---------------------------------------------------------------------------
# SubscriptionTree — add / remove
# ---------------------------------------------------------------------------


class TestSubscriptionTreeAddRemove:
    """Tests for add() and remove()."""

    def test_add_at_root(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add((), ws)
        assert ws in tree.subscribers

    def test_add_at_resource_level(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add(("spool",), ws)
        assert "spool" in tree.children
        assert ws in tree.children["spool"].subscribers

    def test_add_at_specific_id(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add(("spool", "42"), ws)
        assert ws in tree.children["spool"].children["42"].subscribers

    def test_remove_from_root(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add((), ws)
        tree.remove((), ws)
        assert ws not in tree.subscribers

    def test_remove_from_resource(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add(("spool",), ws)
        tree.remove(("spool",), ws)
        assert ws not in tree.children["spool"].subscribers

    def test_remove_from_specific_id(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add(("spool", "1"), ws)
        tree.remove(("spool", "1"), ws)
        assert ws not in tree.children["spool"].children["1"].subscribers

    def test_remove_idempotent_root(self) -> None:
        """Removing a websocket that is not present must not raise."""
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.remove((), ws)  # ws was never added — must not raise

    def test_remove_idempotent_child(self) -> None:
        """Removing from an existing node where the ws is absent must not raise."""
        tree = SubscriptionTree()
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        tree.add(("spool",), ws1)
        tree.remove(("spool",), ws2)  # ws2 was never added

    def test_remove_nonexistent_child_path(self) -> None:
        """Removing along a path that doesn't exist must not raise."""
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.remove(("nonexistent", "99"), ws)

    def test_multiple_subscribers_same_path(self) -> None:
        tree = SubscriptionTree()
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        tree.add(("spool",), ws1)
        tree.add(("spool",), ws2)
        assert len(tree.children["spool"].subscribers) == 2

        tree.remove(("spool",), ws1)
        assert ws1 not in tree.children["spool"].subscribers
        assert ws2 in tree.children["spool"].subscribers


# ---------------------------------------------------------------------------
# SubscriptionTree.send() — broadcast correctness
# ---------------------------------------------------------------------------


class TestSubscriptionTreeSend:
    """Tests for the async send() broadcast method."""

    @pytest.mark.asyncio
    async def test_send_to_single_root_subscriber(self) -> None:
        tree = SubscriptionTree()
        ws = MockWebSocket()
        tree.add((), ws)

        await tree.send(("spool", "1"), MockEvent("hello"))

        assert ws.sent == ["hello"]

    @pytest.mark.asyncio
    async def test_send_delivers_to_all_root_subscribers(self) -> None:
        tree = SubscriptionTree()
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        ws3 = MockWebSocket()
        tree.add((), ws1)
        tree.add((), ws2)
        tree.add((), ws3)

        await tree.send(("spool", "1"), MockEvent("evt"))

        assert ws1.sent == ["evt"]
        assert ws2.sent == ["evt"]
        assert ws3.sent == ["evt"]

    @pytest.mark.asyncio
    async def test_send_cascades_through_hierarchy(self) -> None:
        """Broadcast must reach root, resource-level, and ID-level subscribers."""
        tree = SubscriptionTree()

        root_ws = MockWebSocket()
        spool_ws = MockWebSocket()
        spool1_ws = MockWebSocket()
        spool2_ws = MockWebSocket()

        tree.add((), root_ws)
        tree.add(("spool",), spool_ws)
        tree.add(("spool", "1"), spool1_ws)
        tree.add(("spool", "2"), spool2_ws)

        await tree.send(("spool", "1"), MockEvent("cascade"))

        # Root, spool-level, and spool-1 should all receive the event
        assert root_ws.sent == ["cascade"]
        assert spool_ws.sent == ["cascade"]
        assert spool1_ws.sent == ["cascade"]
        # spool-2 should NOT receive a spool-1 event
        assert spool2_ws.sent == []

    @pytest.mark.asyncio
    async def test_send_skips_disconnected_subscriber(self) -> None:
        """A subscriber with DISCONNECTED state should be cleaned up and skipped."""
        tree = SubscriptionTree()

        healthy = MockWebSocket()
        disconnected = MockWebSocket(client_state=WebSocketState.DISCONNECTED)

        tree.add((), healthy)
        tree.add((), disconnected)

        await tree.send(("spool", "1"), MockEvent("evt"))

        # Healthy subscriber gets the event
        assert healthy.sent == ["evt"]
        # Disconnected subscriber was cleaned up
        assert disconnected not in tree.subscribers
        assert len(tree.subscribers) == 1

    @pytest.mark.asyncio
    async def test_send_skips_app_disconnected_subscriber(self) -> None:
        """A subscriber with DISCONNECTED application_state should also be cleaned up."""
        tree = SubscriptionTree()

        healthy = MockWebSocket()
        disconnected = MockWebSocket(application_state=WebSocketState.DISCONNECTED)

        tree.add((), healthy)
        tree.add((), disconnected)

        await tree.send(("spool", "1"), MockEvent("evt"))

        assert healthy.sent == ["evt"]
        assert disconnected not in tree.subscribers

    @pytest.mark.asyncio
    async def test_send_isolates_send_text_failure(self) -> None:
        """If send_text() raises for one subscriber, others must still receive."""
        tree = SubscriptionTree()

        good1 = MockWebSocket()
        good2 = MockWebSocket()
        bad = MockWebSocket(send_side_effect=ConnectionError("connection reset"))

        tree.add((), good1)
        tree.add((), bad)
        tree.add((), good2)

        await tree.send(("spool", "1"), MockEvent("evt"))

        # Both healthy subscribers received the event
        assert good1.sent == ["evt"]
        assert good2.sent == ["evt"]
        # The bad connection was cleaned up
        assert bad not in tree.subscribers
        assert len(tree.subscribers) == 2

    @pytest.mark.asyncio
    async def test_send_does_not_crash_on_disconnected_during_iteration(self) -> None:
        """Regression: original code modified set during iteration → RuntimeError."""
        tree = SubscriptionTree()

        # Mix of healthy and disconnected subscribers
        healthy_sockets = [MockWebSocket() for _ in range(5)]
        bad_sockets = [
            MockWebSocket(client_state=WebSocketState.DISCONNECTED) for _ in range(3)
        ]

        for ws in healthy_sockets:
            tree.add((), ws)
        for ws in bad_sockets:
            tree.add((), ws)

        # This MUST NOT raise RuntimeError
        await tree.send(("spool", "1"), MockEvent("evt"))

        # All healthy subscribers got the event
        for ws in healthy_sockets:
            assert ws.sent == ["evt"]

        # All bad connections were cleaned up
        for ws in bad_sockets:
            assert ws not in tree.subscribers

        # Only healthy sockets remain
        assert len(tree.subscribers) == 5

    @pytest.mark.asyncio
    async def test_send_continues_to_children_after_cleanup(self) -> None:
        """After cleaning up bad subscribers at a parent level, children must
        still receive the event (the original bug stopped the recursion)."""
        tree = SubscriptionTree()

        # Root level has a disconnected subscriber
        root_bad = MockWebSocket(client_state=WebSocketState.DISCONNECTED)
        root_good = MockWebSocket()
        tree.add((), root_bad)
        tree.add((), root_good)

        # Child level has a healthy subscriber
        child_ws = MockWebSocket()
        tree.add(("spool", "1"), child_ws)

        await tree.send(("spool", "1"), MockEvent("deep"))

        # Both the root-level good subscriber and the child received the event
        assert root_good.sent == ["deep"]
        assert child_ws.sent == ["deep"]
        # Bad connection was cleaned up
        assert root_bad not in tree.subscribers

    @pytest.mark.asyncio
    async def test_send_with_empty_subscribers(self) -> None:
        """Sending to a node with no subscribers should recurse into children."""
        tree = SubscriptionTree()

        child_ws = MockWebSocket()
        tree.add(("spool", "1"), child_ws)

        # No root-level subscribers, but the event should still reach the child
        await tree.send(("spool", "1"), MockEvent("evt"))
        assert child_ws.sent == ["evt"]

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_path(self) -> None:
        """Sending to a path that has no subscribers or children should not crash."""
        tree = SubscriptionTree()
        # No subscribers anywhere
        await tree.send(("spool", "999"), MockEvent("evt"))  # must not raise

    @pytest.mark.asyncio
    async def test_send_mixed_failures_and_disconnections(self) -> None:
        """Stress test: mix of healthy, disconnected, and send-failing sockets."""
        tree = SubscriptionTree()

        good1 = MockWebSocket()
        good2 = MockWebSocket()
        disconnected = MockWebSocket(client_state=WebSocketState.DISCONNECTED)
        send_fails = MockWebSocket(send_side_effect=RuntimeError("broken pipe"))

        tree.add(("vendor",), good1)
        tree.add(("vendor",), disconnected)
        tree.add(("vendor",), good2)
        tree.add(("vendor",), send_fails)

        await tree.send(("vendor", "5"), MockEvent("mix"))

        # Good subscribers received the event
        assert good1.sent == ["mix"]
        assert good2.sent == ["mix"]
        # Bad connections were cleaned up
        vendor_node = tree.children["vendor"]
        assert disconnected not in vendor_node.subscribers
        assert send_fails not in vendor_node.subscribers
        assert len(vendor_node.subscribers) == 2


# ---------------------------------------------------------------------------
# WebsocketManager — integration over SubscriptionTree
# ---------------------------------------------------------------------------


class TestWebsocketManager:
    """Tests for the WebsocketManager wrapper."""

    def test_connect_adds_subscriber(self) -> None:
        mgr = WebsocketManager()
        ws = MockWebSocket()
        mgr.connect(("spool",), ws)
        assert ws in mgr.tree.children["spool"].subscribers

    def test_disconnect_removes_subscriber(self) -> None:
        mgr = WebsocketManager()
        ws = MockWebSocket()
        mgr.connect(("spool",), ws)
        mgr.disconnect(("spool",), ws)
        assert ws not in mgr.tree.children["spool"].subscribers

    def test_disconnect_idempotent(self) -> None:
        """Calling disconnect twice must not raise KeyError."""
        mgr = WebsocketManager()
        ws = MockWebSocket()
        mgr.connect(("spool",), ws)
        mgr.disconnect(("spool",), ws)
        mgr.disconnect(("spool",), ws)  # second call must not raise

    def test_disconnect_without_connect(self) -> None:
        """Disconnecting a websocket that was never connected must not raise."""
        mgr = WebsocketManager()
        ws = MockWebSocket()
        mgr.disconnect(("spool",), ws)  # must not raise

    @pytest.mark.asyncio
    async def test_send_via_manager(self) -> None:
        mgr = WebsocketManager()
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        mgr.connect(("spool",), ws1)
        mgr.connect(("spool", "1"), ws2)

        await mgr.send(("spool", "1"), MockEvent("mgr_evt"))

        assert ws1.sent == ["mgr_evt"]
        assert ws2.sent == ["mgr_evt"]

    @pytest.mark.asyncio
    async def test_manager_send_with_bad_connection(self) -> None:
        """Manager-level send should clean up bad connections and keep going."""
        mgr = WebsocketManager()

        good = MockWebSocket()
        bad = MockWebSocket(send_side_effect=OSError("network unreachable"))

        mgr.connect(("filament",), good)
        mgr.connect(("filament",), bad)

        await mgr.send(("filament", "3"), MockEvent("resilient"))

        assert good.sent == ["resilient"]
        assert bad not in mgr.tree.children["filament"].subscribers

    @pytest.mark.asyncio
    async def test_full_hierarchy_broadcast(self) -> None:
        """Verify root → resource → ID cascading delivery through the manager."""
        mgr = WebsocketManager()

        root_ws = MockWebSocket()
        vendor_ws = MockWebSocket()
        vendor7_ws = MockWebSocket()
        spool_ws = MockWebSocket()

        mgr.connect((), root_ws)
        mgr.connect(("vendor",), vendor_ws)
        mgr.connect(("vendor", "7"), vendor7_ws)
        mgr.connect(("spool",), spool_ws)

        # Event for vendor 7
        await mgr.send(("vendor", "7"), MockEvent("v7"))

        assert root_ws.sent == ["v7"]
        assert vendor_ws.sent == ["v7"]
        assert vendor7_ws.sent == ["v7"]
        # Spool subscriber should NOT receive vendor events
        assert spool_ws.sent == []

    @pytest.mark.asyncio
    async def test_many_subscribers_with_intermittent_failures(self) -> None:
        """Ensure broadcast is stable across many subscribers with some failures."""
        mgr = WebsocketManager()

        good_sockets = [MockWebSocket() for _ in range(20)]
        failing_sockets = [
            MockWebSocket(send_side_effect=ConnectionError(f"fail_{i}"))
            for i in range(5)
        ]

        for ws in good_sockets:
            mgr.connect(("spool",), ws)
        for ws in failing_sockets:
            mgr.connect(("spool",), ws)

        # Multiple broadcasts should all succeed
        for i in range(3):
            await mgr.send(("spool", "1"), MockEvent(f"batch_{i}"))

        # All good sockets received all events
        for ws in good_sockets:
            assert ws.sent == ["batch_0", "batch_1", "batch_2"]

        # Failing sockets were cleaned up after the first broadcast
        spool_node = mgr.tree.children["spool"]
        for ws in failing_sockets:
            assert ws not in spool_node.subscribers

        # Only good sockets remain
        assert len(spool_node.subscribers) == 20
