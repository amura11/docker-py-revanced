"""Regression tests for RevancedConfig's app roster resolution."""

# unittest keeps this file aligned with the rest of the repository test suite.
# ruff: noqa: PT009

import os
from typing import Self
from unittest import TestCase
from unittest.mock import patch

from environs import Env

from src.config import RevancedConfig


class RevancedConfigAppsTests(TestCase):
    """Verify config.apps (this run's subset) stays distinct from config.all_apps (the full roster)."""

    def test_apps_and_all_apps_match_when_no_override_is_set(self: Self) -> None:
        """A normal run, with no PATCH_APPS_OVERRIDE, should use the same roster for both."""
        env_vars = {"PATCH_APPS": "youtube,reddit"}
        with patch.dict(os.environ, env_vars, clear=False):
            os.environ.pop("PATCH_APPS_OVERRIDE", None)
            config = RevancedConfig(Env())

        self.assertEqual(config.apps, ["reddit", "youtube"])
        self.assertEqual(config.all_apps, ["reddit", "youtube"])

    def test_override_narrows_apps_without_shrinking_the_full_roster(self: Self) -> None:
        """PATCH_APPS_OVERRIDE (a partial "needs updating" build) must not shrink all_apps.

        Regression test: scripts/prefered_apps.py used to overwrite PATCH_APPS itself for a partial
        build, permanently losing the full roster for anything (like the Obtainium export) that needs
        to know every configured app, not just the subset selected for a given run.
        """
        env_vars = {"PATCH_APPS": "youtube,reddit,twitter", "PATCH_APPS_OVERRIDE": "reddit"}
        with patch.dict(os.environ, env_vars, clear=False):
            config = RevancedConfig(Env())

        self.assertEqual(config.apps, ["reddit"])
        self.assertEqual(config.all_apps, ["reddit", "twitter", "youtube"])
