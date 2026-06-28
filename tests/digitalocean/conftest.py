# tests/digitalocean/conftest.py

"""Shared fixtures for ots_shared.digitalocean library tests.

These cover the pure-library layer — config, errors, regions, paginate, wait,
network_plan, droplet_defaults — and never speak to the DigitalOcean API. The
``pydo.Client`` is mocked via ``MagicMock`` where a client is needed; most tests
build their own mocks inline, so this file is intentionally minimal. Tests that
need the real azure-core exception types ``pytest.importorskip`` them, since pydo
is an optional extra.
"""
