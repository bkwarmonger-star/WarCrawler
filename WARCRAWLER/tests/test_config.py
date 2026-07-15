import pytest

from warcrawler.config import JobConfig, ThrottleConfig


def test_profile_aggressive_disables_robots():
    t = ThrottleConfig.from_profile("aggressive")
    assert t.respect_robots is False
    assert t.per_host_delay == 0.0


def test_explicit_key_overrides_profile():
    job = JobConfig.from_dict({
        "seeds": ["https://ex.com"],
        "throttle": {"profile": "aggressive", "respect_robots": True},
    })
    assert job.throttle.respect_robots is True   # explicit wins over profile


def test_unknown_key_raises():
    with pytest.raises(ValueError):
        JobConfig.from_dict({"seeds": ["https://ex.com"], "nonsense": 1})


def test_fleet_section_parsed():
    job = JobConfig.from_dict({
        "seeds": ["https://ex.com"],
        "fleet": {"shared": True, "lease": 120},
    })
    assert job.fleet.shared is True
    assert job.fleet.lease == 120


def test_effective_workers_defaults_to_global_concurrency():
    job = JobConfig.from_dict({"seeds": ["https://ex.com"],
                               "throttle": {"global_concurrency": 24}})
    assert job.effective_workers() == 24
    job.workers = 4
    assert job.effective_workers() == 4
