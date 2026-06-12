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
        """Remove a websocket from the subscription tree."""
        if len(path) == 0:
            self.subscribers.remove(websocket)
        elif path[0] in self.children:
            self.children[path[0]].remove(path[1:], websocket)

    async def send(self, path: tuple[str, ...], evt: Event) -> None:
        """Send a message to all websockets in this branch of the tree."""
        # Broadcast to all subscribers on this level. Iterate over a snapshot and
        # collect any dead sockets, then prune them afterwards. Mutating the set
        # while iterating it (or across an ``await``) would raise and abort the
        # whole broadcast, starving the remaining subscribers and the child trees.
        dead: set[WebSocket] = set()
        for websocket in list(self.subscribers):
            if (
                websocket.client_state == WebSocketState.DISCONNECTED  # noqa: PLR1714
                or websocket.application_state == WebSocketState.DISCONNECTED
            ):
                # A bad disconnection may have occurred
                dead.add(websocket)
                logger.info(
                    "Forcing disconnection of client %s on pool %s",
                    websocket.client.host if websocket.client else "?",
                    ",".join(path),
                )
            elif (
                websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            ):
                try:
                    await websocket.send_text(evt.json())
                except Exception:  # noqa: BLE001
                    # The socket reported as connected but the transport failed.
                    # Drop it so one broken client cannot break the broadcast.
                    dead.add(websocket)
                    logger.info(
                        "Dropping client %s on pool %s after a failed send",
                        websocket.client.host if websocket.client else "?",
                        ",".join(path),
                    )

        # Prune the dead sockets from this level's subscribers.
        self.subscribers.difference_update(dead)

        # Always continue down the tree, even if a subscriber on this level had to
        # be cleaned up, so resource-level listeners keep receiving events.
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
