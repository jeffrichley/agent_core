"""Public test helpers for downstream packages.

The router substrate ships a fake Connector so other inbound-notifications
connectors (and downstream packages) can exercise the router without
implementing a full classify() loop.
"""
from agent_core_inbound.testing.fake_connector import FakeConnector

__all__ = ["FakeConnector"]
