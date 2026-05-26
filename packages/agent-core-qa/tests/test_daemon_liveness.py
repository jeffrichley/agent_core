"""Scenario 1: test daemon liveness (precondition for all other scenarios).

The 'next failure class' Phase 2.6's PR named as Phase 2.7 territory — does
the daemon actually start after install. Catching this here as the autouse
precondition means every other scenario gets clean skip-with-reason instead
of cryptic connection errors when the daemon isn't up.
"""



def test_daemon_liveness(client):
    """The test daemon must be reachable at <daemon_url>.

    Preflight finding: the daemon's HTTPHost (Starlette+Uvicorn) has no
    dedicated liveness HTTP route — it only mounts MCP endpoints. Therefore
    health_check() uses a TCP-connect check on the bind port (see
    DaemonClient.health_check() docstring). A successful TCP connect means
    the daemon process is up and its HTTP server is accepting connections.

    daemon_liveness_required fixture (autouse, conftest.py) ALSO checks this
    so all other scenarios skip cleanly when the daemon is down. This
    explicit test makes the precondition discoverable as its own scenario.
    """
    response = client.health_check()
    assert response.status_code == 200, (
        f"daemon at {client.base_url} returned {response.status_code}; "
        f"body: {response.text[:200]}"
    )
