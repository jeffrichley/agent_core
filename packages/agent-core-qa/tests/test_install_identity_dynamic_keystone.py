"""Scenario 3: install identity dynamic keystone.

Phase 3.5's `test_install_code_path_identity_between_prod_and_test`
enforces install-code-path identity at the UNIT level (mocked subprocess
captures). This scenario is the DYNAMIC version: invoke the real install
command against a clean sandbox home; assert it completes; assert the
venv contains the expected agent_core wheels.

Phase 2.6 bug-cadence: static unit caught Bugs 1-2; dynamic install
caught Bug 3. This scenario is the standing dynamic check for release
v0.3.0 onward — if it ever fails, the install path regressed.

NOTE: This scenario does its own daemon install. It does NOT require
the precondition test daemon to be running (the autouse liveness
fixture is bypassed via the test-name check). It uses its own sandbox
home so it never touches the running test daemon's state.

PLAN DEVIATION (documented):
The plan specifies `--instance test` which requires the Phase 3.5 three-
instance branch to be merged first (adds Instance.TEST to instance.py).
The qa-runner-tester branch currently has only prod/dev. The `--instance
test` argument is correct per spec and will work once phase35 merges into
the v0.3.0 release sequence (step: #120 → #121 → #119 → qa-runner).
In the interim, calling `--instance test` will fail with ValueError on the
install subprocess — that's the expected failure mode on this branch until
phase35 lands. The `AGENT_CORE_HOME` sandbox env var correctly isolates
the install regardless of which instance flag is passed (home_for() returns
Path(AGENT_CORE_HOME) directly when that env var is set, bypassing instance
mapping). Written per plan spec; no functional change needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

EXPECTED_PACKAGES = [
    "agent_core",
    "agent_core_briefs",
    "agent_core_busproxy",
    "agent_core_channel",
    "agent_core_credentials",
    "agent_core_discord",
    "agent_core_hatchery",
    "agent_core_notify",
    "agent_core_voice",
    "agent_core_webcam",
    "qwen_tts",
]


@pytest.mark.slow
def test_install_identity_dynamic_keystone():
    """Invoke `agent-core daemon install --instance test --release v0.3.0`
    against a clean sandbox; assert install completes; assert venv has
    all expected wheels.

    Sandbox home is `/tmp/qa-{uuid}/` so each invocation is isolated;
    cleanup happens on test teardown via try/finally.

    Phase 3.5 prerequisite: `--instance test` requires Instance.TEST to be
    present in instance.py (added in feat/phase35-three-instance-test).
    This test will fail with subprocess ValueError until that branch merges.
    That's expected — this test documents the post-merge validation contract.

    AGENT_CORE_HOME bypass: home_for() returns Path(AGENT_CORE_HOME) directly
    when the env var is set, so the sandbox isolation holds regardless of the
    instance flag once the ValueError is resolved.
    """
    sandbox = Path("/tmp") / f"qa-{uuid.uuid4().hex[:8]}"
    sandbox.mkdir(parents=True, exist_ok=True)

    try:
        env = os.environ.copy()
        env["AGENT_CORE_HOME"] = str(sandbox)

        # Invoke the real install command.
        # NOTE: pin the release tag the runbook intends to validate.
        # For local pre-release validation, use a built-locally tag
        # like "vlocal" (see runbook).
        release_tag = os.environ.get("AGENT_CORE_QA_RELEASE_TAG", "v0.3.0")
        result = subprocess.run(
            [
                "agent-core",
                "daemon",
                "install",
                "--instance",
                "test",
                "--release",
                release_tag,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min — torch download is large
        )
        assert result.returncode == 0, (
            f"install failed (exit {result.returncode}):\n"
            f"STDOUT: {result.stdout[-2000:]}\n"
            f"STDERR: {result.stderr[-2000:]}"
        )

        # Assert install stamp is correct.
        stamp_path = sandbox / ".daemon-install-stamp.json"
        assert stamp_path.exists(), f"install stamp not written at {stamp_path}"

        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        # The stamp must record the release tag either under `release_tag`
        # (Phase 3.5 install.py field) or `installed_version` (fallback).
        assert stamp.get("release_tag") == release_tag or stamp.get(
            "installed_version"
        ) == release_tag, (
            f"install stamp shows wrong version — expected {release_tag!r}; stamp: {stamp}"
        )

        # Assert ALL expected wheels are present at site-packages
        # (per spec-review clarification: partial install must fail).
        venv = sandbox / ".venv"
        if sys.platform == "win32":
            site_packages = venv / "Lib" / "site-packages"
        else:
            # /lib/python*/site-packages — glob to handle 3.12 / 3.13 etc.
            candidates = list((venv / "lib").glob("python*/site-packages"))
            assert candidates, f"no site-packages found under {venv}/lib/"
            site_packages = candidates[0]

        for package_name in EXPECTED_PACKAGES:
            init_py = site_packages / package_name / "__init__.py"
            assert init_py.exists(), (
                f"expected wheel-installed package {package_name!r} not found at "
                f"{init_py}; install was partial"
            )

    finally:
        # Tear down sandbox; never reuse across runs.
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)
