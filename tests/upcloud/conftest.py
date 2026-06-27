# tests/upcloud/conftest.py

"""Shared fixtures for ots_shared.upcloud library tests.

These tests cover the pure-library layer — config, errors, zones, wait,
network_plan, server_defaults — and never speak to the UpCloud API. The
``CloudManager`` is mocked via ``MagicMock``; ``Config.get_client`` is
patched at the class level where a client is needed. Most tests build their
own mocks inline, so this file is intentionally minimal.
"""
