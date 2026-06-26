# tests/ssh/test_known_hosts.py

"""Unit tests for best-effort known_hosts pruning.

Subprocess seams (``_run_ssh_g``, ``_run_keygen_remove``) are patched by
name so nothing actually shells out — mirrors how pots.ssh.routing tests
stub ``_run_ssh_g``.
"""

from __future__ import annotations

from ots_shared.ssh import known_hosts as kh

# --- pure target-construction ---------------------------------------------


def test_target_is_bracketed_for_nonstandard_port():
    parsed = {"hostname": ("10.102.2.1",), "port": ("27161",)}
    assert kh._known_hosts_target(parsed) == "[10.102.2.1]:27161"


def test_target_is_bare_host_for_port_22():
    parsed = {"hostname": ("10.102.2.1",), "port": ("22",)}
    assert kh._known_hosts_target(parsed) == "10.102.2.1"


def test_target_defaults_to_bare_host_when_port_absent():
    assert kh._known_hosts_target({"hostname": ("db.internal",)}) == "db.internal"


def test_target_is_none_without_hostname():
    assert kh._known_hosts_target({"port": ("27161",)}) is None
    assert kh._known_hosts_target({"hostname": ("",)}) is None


# --- known_hosts file resolution ------------------------------------------


def test_files_keeps_existing_drops_missing_and_none(tmp_path):
    present = tmp_path / "known_hosts"
    present.write_text("[10.102.2.1]:27161 ssh-ed25519 AAAA\n")
    missing = tmp_path / "absent"
    parsed = {"userknownhostsfile": (f"{present} {missing} none",)}
    assert kh._known_hosts_files(parsed) == [present]


def test_files_empty_when_directive_absent():
    assert kh._known_hosts_files({}) == []


def test_files_empty_when_only_none():
    assert kh._known_hosts_files({"userknownhostsfile": ("none",)}) == []


# --- prune_known_hosts orchestration --------------------------------------


def test_prune_runs_keygen_with_resolved_target(tmp_path, monkeypatch):
    kh_file = tmp_path / "known_hosts"
    kh_file.write_text("[10.102.2.1]:27161 ssh-ed25519 AAAA\n")
    monkeypatch.setattr(
        kh,
        "_run_ssh_g",
        lambda hostname, ssh_config: {
            "hostname": ("10.102.2.1",),
            "port": ("27161",),
            "userknownhostsfile": (str(kh_file),),
        },
    )
    calls: list[tuple[str, str]] = []

    def _record(target, known_hosts):
        calls.append((target, str(known_hosts)))
        return True

    monkeypatch.setattr(kh, "_run_keygen_remove", _record)
    result = kh.prune_known_hosts("eu-db-01", ssh_config=None)
    assert result == [kh_file]
    assert calls == [("[10.102.2.1]:27161", str(kh_file))]


def test_prune_noop_when_ssh_g_unavailable(monkeypatch):
    monkeypatch.setattr(kh, "_run_ssh_g", lambda hostname, ssh_config: None)
    called = False

    def _fail(target, known_hosts):  # pragma: no cover - must not run
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(kh, "_run_keygen_remove", _fail)
    assert kh.prune_known_hosts("eu-db-01") == []
    assert called is False


def test_prune_noop_when_known_hosts_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kh,
        "_run_ssh_g",
        lambda hostname, ssh_config: {
            "hostname": ("10.102.2.1",),
            "port": ("27161",),
            "userknownhostsfile": (str(tmp_path / "absent"),),
        },
    )
    called = False

    def _fail(target, known_hosts):  # pragma: no cover - must not run
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(kh, "_run_keygen_remove", _fail)
    assert kh.prune_known_hosts("eu-db-01") == []
    assert called is False


def test_prune_stops_when_keygen_binary_absent(tmp_path, monkeypatch):
    kh_file = tmp_path / "known_hosts"
    kh_file.write_text("[10.102.2.1]:27161 ssh-ed25519 AAAA\n")
    monkeypatch.setattr(
        kh,
        "_run_ssh_g",
        lambda hostname, ssh_config: {
            "hostname": ("10.102.2.1",),
            "port": ("27161",),
            "userknownhostsfile": (str(kh_file),),
        },
    )
    # ssh-keygen unavailable -> seam returns False -> prune is a clean no-op.
    monkeypatch.setattr(kh, "_run_keygen_remove", lambda target, known_hosts: False)
    assert kh.prune_known_hosts("eu-db-01") == []
