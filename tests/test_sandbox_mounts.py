"""How the sandbox container gets its work dir — the two mount strategies.

There are two, and the difference is not cosmetic. On the host the work dir is a
temp directory bind-mounted by path. Under docker-compose the worker is itself a
container talking to the *host* daemon through a mounted socket, so any path it
names is resolved against the host filesystem, where its own /tmp does not exist.
A bind mount there silently produces an empty /work: no manifest, no results, and
every single run comes back as an infra error rather than a verdict.

Neither failure shows up in a unit test that mocks `subprocess.run`, and the
containerised path cannot be exercised at all without a daemon and a compose
project. So the argv itself is the thing under test.
"""
from __future__ import annotations

import backend.sandbox as sandbox
from backend import bank


def _problem():
    return bank.get("count-evens")


def test_on_the_host_the_work_dir_is_bind_mounted_by_path(monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_VOLUME", "")
    cmd = sandbox.docker_cmd(_problem(), "/tmp/cp_tutor_abc")

    assert cmd[-1] == sandbox.IMAGE, "the image is last; the entrypoint is untouched"
    mount = cmd[cmd.index("-v") + 1]
    assert mount.endswith(":/work")
    assert "cp_tutor_abc" in mount
    assert "--entrypoint" not in cmd


def test_in_a_container_the_shared_volume_is_named_instead_of_a_path(monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_VOLUME", "nlcp2g_sandboxwork")
    cmd = sandbox.docker_cmd(_problem(), "/sandboxwork/cp_tutor_abc")

    assert "-v" in cmd and cmd[cmd.index("-v") + 1] == "nlcp2g_sandboxwork:/workroot"
    # no host path anywhere: that is the whole point
    assert not any(a.startswith("/sandboxwork/") and ":" in a for a in cmd)
    # the image's ENTRYPOINT hardcodes /work, so it has to be overridden to aim
    # runner.py at this job's subdirectory of the volume
    assert cmd[cmd.index("--entrypoint") + 1] == "python3"
    assert cmd[-2:] == ["/runner.py", "/workroot/cp_tutor_abc"]


def test_the_containment_flags_are_present_either_way(monkeypatch):
    """The mount strategy must never quietly cost us the jail."""
    for volume in ("", "nlcp2g_sandboxwork"):
        monkeypatch.setattr(sandbox, "SANDBOX_VOLUME", volume)
        cmd = sandbox.docker_cmd(_problem(), "/w/cp_tutor_abc")
        assert "--network=none" in cmd
        assert "--cpus=1" in cmd
        assert "--pids-limit=64" in cmd
        assert any(a.startswith("--memory=") for a in cmd)
        assert "--rm" in cmd
