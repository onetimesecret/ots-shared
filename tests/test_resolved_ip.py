# tests/test_resolved_ip.py

"""Tests for the resolved-IP sidecar (multi-provider §5.1).

The sidecar persists private IPs assigned by providers that allocate them
automatically (DigitalOcean) so downstream consumers can resolve them even
though the marker is forbidden from carrying ``private_ip_*`` on such
providers. All paths use ``tmp_path`` — never a real ``.trust/`` — per the
repo testing rule.
"""

from __future__ import annotations

import pytest
import yaml

from ots_shared.resolved_ip import (
    SIDECAR_RELPATH,
    get_resolved_ip,
    read_sidecar,
    sidecar_path,
    write_resolved_ip,
)


class TestSidecarPath:
    def test_path_is_under_trust(self, tmp_path):
        p = sidecar_path(tmp_path)
        assert p == tmp_path / SIDECAR_RELPATH
        assert p.parts[-2:] == (".trust", "resolved-ips.yaml")


class TestReadSidecar:
    def test_missing_file_returns_empty(self, tmp_path):
        assert read_sidecar(tmp_path / "nope.yaml") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("", encoding="utf-8")
        assert read_sidecar(f) == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("::: not : valid : yaml :::", encoding="utf-8")
        assert read_sidecar(f) == {}

    def test_non_mapping_top_level_returns_empty(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("- a\n- b\n", encoding="utf-8")
        assert read_sidecar(f) == {}

    def test_reads_mapping(self, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("hosts:\n  eu-db-01:\n    private_ip: 10.0.0.3\n", encoding="utf-8")
        assert read_sidecar(f) == {"hosts": {"eu-db-01": {"private_ip": "10.0.0.3"}}}


class TestWriteResolvedIp:
    def test_creates_trust_dir_and_file(self, tmp_path):
        path = write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        assert path == sidecar_path(tmp_path)
        assert path.is_file()
        assert path.parent.name == ".trust"

    def test_roundtrip(self, tmp_path):
        write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) == "10.0.0.3"

    def test_header_present_and_machine_warning(self, tmp_path):
        path = write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "Machine-written" in text
        assert "Do NOT edit" in text

    def test_preserves_other_hosts(self, tmp_path):
        write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        write_resolved_ip("eu-web-01", "10.0.0.4", marker_dir=tmp_path)
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) == "10.0.0.3"
        assert get_resolved_ip("eu-web-01", marker_dir=tmp_path) == "10.0.0.4"

    def test_overwrite_same_host(self, tmp_path):
        write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        write_resolved_ip("eu-db-01", "10.0.0.9", marker_dir=tmp_path)
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) == "10.0.0.9"

    def test_idempotent_content(self, tmp_path):
        p1 = write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        first = p1.read_text(encoding="utf-8")
        p2 = write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        assert p2.read_text(encoding="utf-8") == first

    def test_written_yaml_is_parseable(self, tmp_path):
        path = write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data == {"hosts": {"eu-db-01": {"private_ip": "10.0.0.3"}}}

    def test_empty_hostname_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            write_resolved_ip("", "10.0.0.3", marker_dir=tmp_path)

    def test_empty_ip_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            write_resolved_ip("eu-db-01", "", marker_dir=tmp_path)

    def test_survives_preexisting_garbage_file(self, tmp_path):
        # A corrupt sidecar is treated as empty, then overwritten cleanly.
        sp = sidecar_path(tmp_path)
        sp.parent.mkdir(parents=True)
        sp.write_text("not: [valid", encoding="utf-8")
        write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) == "10.0.0.3"


class TestGetResolvedIp:
    def test_none_marker_dir_returns_none(self):
        assert get_resolved_ip("eu-db-01", marker_dir=None) is None

    def test_missing_sidecar_returns_none(self, tmp_path):
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) is None

    def test_unknown_hostname_returns_none(self, tmp_path):
        write_resolved_ip("eu-db-01", "10.0.0.3", marker_dir=tmp_path)
        assert get_resolved_ip("eu-web-99", marker_dir=tmp_path) is None

    def test_entry_without_ip_returns_none(self, tmp_path):
        sp = sidecar_path(tmp_path)
        sp.parent.mkdir(parents=True)
        sp.write_text("hosts:\n  eu-db-01: {}\n", encoding="utf-8")
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) is None

    def test_non_mapping_hosts_returns_none(self, tmp_path):
        sp = sidecar_path(tmp_path)
        sp.parent.mkdir(parents=True)
        sp.write_text("hosts: oops\n", encoding="utf-8")
        assert get_resolved_ip("eu-db-01", marker_dir=tmp_path) is None
