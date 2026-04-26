# tests/hcloud/conftest.py

"""Shared fixtures for ots_shared.hcloud library tests.

These tests cover the pure-library layer — config, errors, network_plan,
zones, server_defaults — and never speak to the Hetzner API. Most tests
build their own MagicMocks inline, so this file is intentionally minimal.
"""
