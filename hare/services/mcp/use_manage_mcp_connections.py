"""
Session-scoped MCP connection lifecycle (non-React equivalent of the TS hook).

Port of: src/services/mcp/useManageMCPConnections.ts

Manages the full lifecycle of MCP server connections:
- Loads server configurations from all scopes (user, project, local, etc.)
- Connects to all configured servers on init and on refresh
- Tracks per-server connection status (pending, connected, failed, needs_auth, disabled)
- Supports connect/disconnect/refresh operations
- Integrates with channel permission relay and channel allowlist
- Provides a subscriber pattern for reactive state updates
- Handles graceful shutdown and cleanup
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from hare.services.mcp.channel_allowlist import is_channels_enabled
from hare.services.mcp.channel_permissions import is_channel_permission_relay_enabled
from hare.services.mcp.client import McpClientPool, get_mcp_client_pool
from hare.services.mcp.config import load_mcp_servers
from hare.services.mcp.types import (
    ConnectionStatus,
    MCPServerConnection,
    McpServerConfig,
    McpStdioServerConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A subscriber callback receives the full state snapshot.
Subscriber = Callable[["ManageMcpConnectionsState"], None]

# Unsubscribe function — call to stop receiving updates.
Unsubscribe = Callable[[], None]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class ManageMcpConnectionsState:
    """Mutable snapshot of all MCP connections, channels, and permissions.

    Mirrors the return value of the TS useManageMCPConnections hook:
        { servers, permissionRelay, channelsEnabled }
    """

    servers: list[MCPServerConnection] = field(default_factory=list)
    permission_relay: bool = False
    channels_enabled: bool = False

    # Derived lookups (not serialised, populated after refresh)
    _by_name: dict[str, MCPServerConnection] = field(default_factory=dict, repr=False)

    def get_server(self, name: str) -> MCPServerConnection | None:
        """Look up a server by its config name (fast O(1) by-name lookup)."""
        return self._by_name.get(name)

    def connected_servers(self) -> list[MCPServerConnection]:
        """Return only servers whose status is 'connected'."""
        return [s for s in self.servers if s.status == "connected"]

    def failed_servers(self) -> list[MCPServerConnection]:
        """Return only servers whose status is 'failed'."""
        return [s for s in self.servers if s.status == "failed"]

    def _rebuild_index(self) -> None:
        """Rebuild the by-name index after the server list changes."""
        self._by_name = {s.name: s for s in self.servers}


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class ManageMcpConnections:
    """Owns subscribe/refresh for MCP connections.

    This is the non-React equivalent of the useManageMCPConnections TS hook.
    It holds the connection state, orchestrates connect/disconnect via the
    shared McpClientPool, and notifies subscribers on every state change.

    Lifecycle
    ---------
    1. Instantiate (reads channel/permission flags).
    2. Call ``await refresh()`` to load configs and connect.
    3. Subscribe for reactive updates.
    4. Call ``await disconnect_all()`` during shutdown.

    Usage::

        mgr = ManageMcpConnections()
        await mgr.refresh()
        unsub = mgr.subscribe(lambda state: print(state.connected_servers()))
        ...
        await mgr.disconnect_all()
    """

    def __init__(
        self,
        *,
        pool: McpClientPool | None = None,
        project_dir: str | None = None,
    ) -> None:
        self._state = ManageMcpConnectionsState()
        self._subscribers: list[Subscriber] = []
        self._pool = pool or get_mcp_client_pool()
        self._project_dir = project_dir
        self._refresh_lock = asyncio.Lock()

        # Feature-flag driven toggles (cached once at construction).
        self._state.channels_enabled = is_channels_enabled()
        self._state.permission_relay = is_channel_permission_relay_enabled()

        # Track whether refresh() has been called at least once.
        self._initialised = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_state(self) -> ManageMcpConnectionsState:
        """Return the current state snapshot (not a copy — read-only by convention)."""
        return self._state

    def subscribe(self, cb: Subscriber) -> Unsubscribe:
        """Register a callback that fires on every state change.

        Returns an unsubscribe function.  The callback is invoked synchronously
        inside ``_notify()`` — keep it fast.
        """
        self._subscribers.append(cb)
        # Push the current snapshot immediately (matching React useEffect init).
        try:
            cb(self._state)
        except Exception:
            logger.debug("MCP subscriber init callback raised", exc_info=True)

        def _unsub() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return _unsub

    async def refresh(self) -> ManageMcpConnectionsState:
        """Load (or reload) all MCP server configs and connect to every server.

        This is the primary entry-point.  It is safe to call multiple times
        (subsequent calls skip re-connecting already-connected servers unless
        their config changed).

        Returns the updated state snapshot.
        """
        async with self._refresh_lock:
            return await self._refresh_impl()

    async def connect_server(self, name: str, config: McpServerConfig) -> MCPServerConnection:
        """Connect (or reconnect) a single MCP server by name and config.

        Updates the internal state in-place and notifies subscribers.
        """
        logger.info("Connecting to MCP server %r (transport=%s)", name, getattr(config, "type", "stdio"))

        try:
            conn = await self._pool.connect(name, config)
        except Exception as exc:
            logger.error("Failed to connect to MCP server %r: %s", name, exc)
            conn = MCPServerConnection(
                name=name,
                config=config,
                status="failed",
                connected=False,
                error=str(exc),
            )

        # Merge or insert into the state list
        self._upsert_server(name, conn)
        self._notify()
        return conn

    async def disconnect_server(self, name: str) -> None:
        """Disconnect and remove a single MCP server from state.

        Does nothing if the server is not tracked.
        """
        logger.info("Disconnecting MCP server %r", name)
        await self._pool.disconnect(name)
        self._remove_server(name)
        self._notify()

    async def disconnect_all(self) -> None:
        """Disconnect every tracked MCP server and clear state.

        Should be called during session teardown / shutdown.
        """
        logger.info("Disconnecting all MCP servers (%d tracked)", len(self._state.servers))
        await self._pool.disconnect_all()
        self._state.servers.clear()
        self._state._rebuild_index()
        self._notify()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _refresh_impl(self) -> ManageMcpConnectionsState:
        """Core refresh logic (caller must hold _refresh_lock)."""
        logger.debug("Refreshing MCP connections (initialised=%s)", self._initialised)

        # 1. Reload configs from disk.
        try:
            loaded = load_mcp_servers(project_dir=self._project_dir)
        except Exception as exc:
            logger.error("Failed to load MCP server configs: %s", exc)
            return self._state

        # 2. Build a lookup of current state for diffing.
        current_by_name: dict[str, MCPServerConnection] = {
            s.name: s for s in self._state.servers
        }

        new_servers: list[MCPServerConnection] = []
        connect_tasks: list[asyncio.Task[MCPServerConnection]] = []

        for entry in loaded:
            if not entry.enabled:
                # Preserve disabled servers in state but skip connection.
                existing = current_by_name.get(entry.name)
                if existing is not None and existing.status == "disabled":
                    new_servers.append(existing)
                else:
                    new_servers.append(
                        MCPServerConnection(
                            name=entry.name,
                            config=entry.config,
                            scope=entry.scope,
                            enabled=False,
                            status="disabled",
                        )
                    )
                continue

            existing = current_by_name.get(entry.name)
            if existing is not None and existing.status == "connected":
                # Already connected — keep it unless config changed.
                if self._config_equals(existing.config, entry.config):
                    new_servers.append(existing)
                    continue
                # Config changed — disconnect first.
                await self._pool.disconnect(entry.name)

            # Mark as pending and schedule connection.
            new_servers.append(
                MCPServerConnection(
                    name=entry.name,
                    config=entry.config,
                    scope=entry.scope,
                    status="pending",
                )
            )
            connect_tasks.append(
                asyncio.create_task(self._connect_one(entry.name, entry.config))
            )

        # 3. Update state with pending entries so subscribers see progress.
        self._state.servers = new_servers
        self._state._rebuild_index()
        self._notify()

        # 4. Run all connections concurrently.
        if connect_tasks:
            results = await asyncio.gather(*connect_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    # The task itself raised — already handled in _connect_one,
                    # but guard against unhandled cancellation / system errors.
                    logger.error(
                        "Unhandled error connecting MCP server: %s", result
                    )

        self._initialised = True
        return self._state

    async def _connect_one(
        self, name: str, config: McpServerConfig
    ) -> MCPServerConnection:
        """Connect a single server and update its entry in the state list."""
        try:
            conn = await self._pool.connect(name, config)
        except Exception as exc:
            logger.error("Failed to connect to MCP server %r: %s", name, exc)
            conn = MCPServerConnection(
                name=name,
                config=config,
                status="failed",
                connected=False,
                error=str(exc),
            )

        self._upsert_server(name, conn)
        self._notify()
        return conn

    # ------------------------------------------------------------------
    # State mutation helpers
    # ------------------------------------------------------------------

    def _upsert_server(self, name: str, conn: MCPServerConnection) -> None:
        """Insert or replace a server entry in the state list."""
        for i, s in enumerate(self._state.servers):
            if s.name == name:
                self._state.servers[i] = conn
                self._state._rebuild_index()
                return
        self._state.servers.append(conn)
        self._state._rebuild_index()

    def _remove_server(self, name: str) -> None:
        """Remove a server entry from the state list."""
        self._state.servers = [s for s in self._state.servers if s.name != name]
        self._state._rebuild_index()

    def _notify(self) -> None:
        """Push the current state snapshot to every subscriber.

        Errors in subscriber callbacks are caught and logged so one bad
        subscriber does not prevent others from receiving updates.
        """
        for cb in self._subscribers:
            try:
                cb(self._state)
            except Exception:
                logger.debug("MCP subscriber callback raised", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _config_equals(a: McpServerConfig, b: McpServerConfig) -> bool:
        """Compare two MCP server configs for equality (shallow field comparison).

        Returns True when the connection does NOT need to be recycled.
        """
        if type(a) is not type(b):
            return False
        # Compare common fields from the dataclass.
        for field_name in ("command", "args", "env", "url", "headers", "type"):
            va = getattr(a, field_name, None)
            vb = getattr(b, field_name, None)
            if va != vb:
                return False
        return True


# ---------------------------------------------------------------------------
# Singleton / factory
# ---------------------------------------------------------------------------

_default_manager: ManageMcpConnections | None = None


def get_manage_mcp_connections(
    *, project_dir: str | None = None
) -> ManageMcpConnections:
    """Return (and cache) a session-scoped ManageMcpConnections instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ManageMcpConnections(project_dir=project_dir)
    return _default_manager


def reset_manage_mcp_connections() -> None:
    """Reset the cached singleton (used in tests and session restarts)."""
    global _default_manager
    _default_manager = None
