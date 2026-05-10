"""Tests for jobs.d/ conf.d-style merging in load_seed_jobs."""

from pathlib import Path

import pytest

from agent_core.endpoints.scheduler import SchedulerConfigError, load_seed_jobs

FIXTURES = Path(__file__).parent / "fixtures"


def test_jobs_d_happy_path_merges_main_and_fragment():
    jobs_yaml = FIXTURES / "jobs_d" / "jobs.yaml"
    jobs = load_seed_jobs(jobs_yaml)
    assert set(jobs.keys()) == {"main-job", "fragment-job"}


def test_jobs_d_collision_raises_loudly():
    jobs_yaml = FIXTURES / "jobs_d_collision" / "jobs.yaml"
    with pytest.raises(SchedulerConfigError, match="shared-name"):
        load_seed_jobs(jobs_yaml)


def test_jobs_d_malformed_fragment_raises_with_filename():
    jobs_yaml = FIXTURES / "jobs_d_malformed" / "jobs.yaml"
    with pytest.raises(SchedulerConfigError, match="bad.yaml"):
        load_seed_jobs(jobs_yaml)


def test_no_jobs_d_dir_is_silent_noop(tmp_path):
    jobs_yaml = tmp_path / "jobs.yaml"
    jobs_yaml.write_text(
        "solo-job:\n"
        "  trigger: cron\n"
        '  timezone: "America/New_York"\n'
        "  schedule: {hour: 0, minute: 0}\n"
        "  target: stub\n"
        "  envelope_kind: Event\n"
        "  payload: {type: Heartbeat, data: {}}\n"
    )
    jobs = load_seed_jobs(jobs_yaml)
    assert set(jobs.keys()) == {"solo-job"}
