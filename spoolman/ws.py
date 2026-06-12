"""Websocket functionality."""

import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from spoolman.api.v1.models import Event

logger = logging.getLogger(__name__)


class SubscriptionTree:
    """Subscription tree.

    This is a tree structure that allows us to efficiently send messages to
    all websockets that are subscribed to a certain pool of events.

    You can subscribe to different levels of the tree, for example:
    - ("vendor", "1") will subscribe to events for vendor 1
    - ("vendor") will subscribe to events for all vendors
    - () will subscribe to events for all vendors, filaments and spools
    """

    def __init__(self) -> None:
        """Initialize."""
        self.children: dict[str, SubscriptionTree] = {}
        self.subscribers: set[WebSocket] = set()

    def add(self, path: tuple[str, ...], websocket: WebSocket) -> None:
        """Add a websocket to the subscription tree."""
        if len(path) == 0:
            self.subscribers.add(websocket)
        else:
            if path[0] not in self.children:
                self.children[path[0]] = SubscriptionTree()
            self.children[path[0]].add(path[1:], websocket)

    def remove(self, path: tuple[str, ...], websocket: WebSocket) -> None:
        """Remove a websocket from the subscription tree.

        Idempotent: silently succeeds if the websocket is not present.
        """
        if len(path) == 0:
            self.subscribers.discard(websocket)
        elif path[0] in self.children:
            self.children[path[0]].remove(path[1:], websocket)

    async def send(self, path: tuple[str, ...], evt: Event) -> None:
        """Send a message to all websockets in this branch of the tree.

        * Iterates over a snapshot so removals during broadcast are safe.
        * Isolates each send_text() call — one subscriber failure never
          prevents delivery to the remaining subscribers.
        * Collects disconnected / failed sockets and removes them after the
          broadcast loop to avoid mutating the set during iteration.
        """
        disconnected: list[WebSocket] = []

        # Broadcast to all subscribers at this level
        for websocket in list(self.subscribers):
            if (
                websocket.client_state == WebSocketState.DISCONNECTED  # noqa: PLR1714
                or websocket.application_state == WebSocketState.DISCONNECTED
            ):
                # Already known to be disconnected — mark for lazy cleanup
                disconnected.append(websocket)
            else:
                try:
                    await websocket.send_text(evt.json())
                except Exception:
                    # Send failed (half-closed TCP, reset, etc.) — mark for
                    # cleanup so the next broadcast won't try this socket again.
                    disconnected.append(websocket)
                    logger.warning(
                        "Failed to send to client %s on pool %s, marking for cleanup",
                        websocket.client.host if websocket.client else "?",
                        ",".join(path),
                        exc_info=True,
                    )

        # Remove bad connections *after* the iteration is complete
        for websocket in disconnected:
            self.subscribers.discard(websocket)
            logger.info(
                "Removed client %s from pool %s",
                websocket.client.host if websocket.client else "?",
                ",".join(path),
            )

        # Send the message further down the tree
        if len(path) > 0 and path[0] in self.children:
            await self.children[path[0]].send(path[1:], evt)


class WebsocketManager:
    """Websocket manager."""

    def __init__(self) -> None:
        """Initialize."""
        self.tree = SubscriptionTree()

    def connect(self, pool: tuple[str, ...], websocket: WebSocket) -> None:
        """Connect a websocket."""
        self.tree.add(pool, websocket)
        logger.info(
            "Client %s is now listening on pool %s",
            websocket.client.host if websocket.client else "?",
            ",".join(pool),
        )

    def disconnect(self, pool: tuple[str, ...], websocket: WebSocket) -> None:
        """Disconnect a websocket."""
        self.tree.remove(pool, websocket)
        logger.info(
            "Client %s has stopped listening on pool %s",
            websocket.client.host if websocket.client else "?",
            ",".join(pool),
        )

    async def send(self, pool: tuple[str, ...], evt: Event) -> None:
        """Send a message to all websockets in a pool."""
        await self.tree.send(pool, evt)


websocket_manager = WebsocketManager()
