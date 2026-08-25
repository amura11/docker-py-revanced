"""Regression tests for Obtainium export metadata."""

# Obtainium support is optional but user-facing, so these tests pin URL and update identity behavior.
# unittest keeps this file aligned with the rest of the repository test suite.
# ruff: noqa: PT009

import json
import re
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Self, cast
from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from environs import Env

from src.app import APP
from src.config import RevancedConfig
from src.utils import generate_obtainium_export, load_older_updates, save_patch_info


class _Env:
    """Small env double for only the config lookup used by Obtainium export."""

    def __init__(self: Self, github_repository: str) -> None:
        """Store the repository value so tests do not depend on real environment variables."""
        self.github_repository = github_repository

    def str(self: Self, key: str, default: str = "") -> str:
        """Return GitHub repository for export URL generation and defaults for unrelated keys."""
        if key == "GITHUB_REPOSITORY":
            return self.github_repository
        return default


def _app_with_patch_bundles(second_bundle_version: str) -> APP:
    """Build the minimum APP-shaped object needed to exercise output filename generation."""
    # APP initialization needs a full RevancedConfig, so allocate an instance and set only fields this method reads.
    app = APP.__new__(APP)
    app.app_name = "youtube"
    app.app_version = "20.47.62"
    app.patch_bundles = [
        {"file_name": "revanced.rvp", "version": "v1.0.0"},
        {"file_name": "extra.mpp", "version": second_bundle_version},
    ]
    # The method under test reads the private cache, so the test seeds it through __dict__ without lint noise.
    app.__dict__["_cached_output_file_name"] = ""
    return app


class ObtainiumExportTests(TestCase):
    """Verify Obtainium export data changes when app or patch metadata changes."""

    def setUp(self: Self) -> None:
        """Stub the changelogs-branch git sync so tests never touch the network."""
        # generate_obtainium_export always tries to sync existing pages via a real `git fetch` first;
        # tests run outside any real changelogs branch, so that sync is irrelevant noise here.
        patcher = patch("src.utils._sync_published_obtainium_sources")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_output_file_name_includes_all_patch_bundle_versions(self: Self) -> None:
        """Patch-only updates in any bundle should change the release asset link Obtainium hashes."""
        first_name = _app_with_patch_bundles("v2.0.0").get_output_file_name()
        second_name = _app_with_patch_bundles("v3.0.0").get_output_file_name()

        self.assertIn("PatchVersionv1.0.0.v2.0.0", first_name)
        self.assertIn("PatchVersionv1.0.0.v3.0.0", second_name)
        self.assertNotEqual(first_name, second_name)

    def test_output_file_name_collapses_repeated_dots(self: Self) -> None:
        """Generated release asset names should match GitHub's uploaded asset names."""
        app = _app_with_patch_bundles("v2.0.0")
        app.app_version = "50.1.1..5001014"

        self.assertIn("Version50.1.1.5001014", app.get_output_file_name())

    def test_generate_obtainium_export_encodes_url_and_slugifies_html_name(self: Self) -> None:
        """Generated HTML should be safe to serve and should link to the exact encoded release asset."""
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            # This config mirrors the runtime fields used by generate_obtainium_export without booting Env.
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="release tag",
                    obtainium_site_export=False,
                    all_apps=["YouTube Music"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "YouTube Music": {
                    "app_version": "1<2",
                    "output_file_name": "My APK #1.apk",
                    "patched_this_run": True,
                },
            }

            generate_obtainium_export(updates_info, config)
            html_path = Path(temp_dir, "obtainium_sources", "youtube.music.html")
            html_content = html_path.read_text(encoding="utf_8")

        self.assertIn(
            "https://github.com/owner/repo/releases/download/release%20tag/My%20APK%20%231.apk",
            html_content,
        )
        self.assertIn("1&lt;2", html_content)

    def test_generate_obtainium_export_site_export_builds_deep_link(self: Self) -> None:
        """Site export should add a package-scoped obtainium://app/ deep link and an index page."""
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="latest",
                    obtainium_site_export=True,
                    obtainium_version_extraction_regex=r"Version([\w.]+)-PatchVersion[v]?([\w.]+)-PatchSet",
                    obtainium_version_match_group="$1+$2",
                    all_apps=["YouTube"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "YouTube": {
                    "app_version": "20.47.62",
                    "patches_versions": ["v1.0.0"],
                    "output_file_name": "ReYouTube-Version20.47.62-PatchVersionv1.0.0-PatchSetabc123-output.apk",
                    "app_dump": {"package_name": "app.revanced.android.youtube"},
                    "patched_this_run": True,
                },
            }

            generate_obtainium_export(updates_info, config)
            index_content = Path(temp_dir, "index.html").read_text(encoding="utf_8")

        self.assertIn('<span class="app-name">YouTube</span>', index_content)
        self.assertIn('<code class="package-name">app.revanced.android.youtube</code>', index_content)
        self.assertIn('<span class="meta-chip">App 20.47.62</span>', index_content)
        self.assertIn('<span class="meta-chip">Patch v1.0.0</span>', index_content)
        self.assertIn(
            'class="source-link" href="https://raw.githubusercontent.com/owner/repo/changelogs/'
            'obtainium_sources/youtube.html"',
            index_content,
        )
        self.assertIn("https://github.com/ImranR98/Obtainium", index_content)
        deep_link_match = re.search(r'href="(obtainium://app/[^"]+)"', index_content)
        self.assertIsNotNone(deep_link_match)
        payload = json.loads(unquote(deep_link_match.group(1).removeprefix("obtainium://app/")))  # type: ignore[union-attr]

        self.assertEqual(payload["id"], "app.revanced.android.youtube")
        self.assertEqual(payload["overrideSource"], "HTML")
        self.assertEqual(
            payload["url"],
            "https://raw.githubusercontent.com/owner/repo/changelogs/obtainium_sources/youtube.html",
        )

        additional_settings = json.loads(payload["additionalSettings"])
        self.assertEqual(additional_settings["matchGroupToUse"], "$1+$2")
        self.assertTrue(additional_settings["versionDetection"])

    def test_generate_obtainium_export_site_export_skips_app_without_package_name(self: Self) -> None:
        """An app_dump missing package_name should skip its deep link but not crash the export."""
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="latest",
                    obtainium_site_export=True,
                    obtainium_version_extraction_regex="",
                    obtainium_version_match_group="",
                    all_apps=["YouTube"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "YouTube": {
                    "app_version": "20.47.62",
                    "output_file_name": "youtube-output.apk",
                    "app_dump": {},
                    "patched_this_run": True,
                },
            }

            generate_obtainium_export(updates_info, config)
            index_path = Path(temp_dir, "index.html")

        self.assertFalse(index_path.exists())

    def test_save_patch_info_records_the_release_tag_the_app_was_built_under(self: Self) -> None:
        """Each app's persisted metadata should pin the tag its asset was actually uploaded under."""
        app = _app_with_patch_bundles("v2.0.0")
        app.resource = {"cli": {"version": "1.0.0"}}
        config = cast("RevancedConfig", SimpleNamespace(obtainium_github_tag="Build-1"))

        updates_info = save_patch_info(app, {}, config)

        self.assertEqual(updates_info["youtube"]["obtainium_github_tag"], "Build-1")

    def test_save_patch_info_marks_the_app_as_patched_this_run(self: Self) -> None:
        """save_patch_info is the single source of truth for "was this app actually rebuilt now"."""
        app = _app_with_patch_bundles("v2.0.0")
        app.resource = {"cli": {"version": "1.0.0"}}
        config = cast("RevancedConfig", SimpleNamespace(obtainium_github_tag="Build-1"))

        updates_info = save_patch_info(app, {}, config)

        self.assertTrue(updates_info["youtube"]["patched_this_run"])

    def test_load_older_updates_resets_patched_this_run_for_every_entry(self: Self) -> None:
        """Historical entries must never carry a stale True from whatever run last patched them.

        Regression test: without a reset, an app patched in an earlier run (and thus persisted with
        patched_this_run=True back then) would look "patched this run" forever after, even on runs
        that never touch it again - which would make generate_obtainium_export keep regenerating it.
        """
        stale_payload = {
            "AppA": {"output_file_name": "AppA-output.apk", "patched_this_run": True},
            "AppB": {"output_file_name": "AppB-output.apk", "patched_this_run": False},
        }
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = json.dumps(stale_payload).encode("utf_8")

        with patch("src.utils.urllib.request.urlopen", return_value=mock_response):
            updates_info = load_older_updates(cast("Env", _Env("owner/repo")))

        self.assertFalse(updates_info["AppA"]["patched_this_run"])
        self.assertFalse(updates_info["AppB"]["patched_this_run"])

    def test_generate_obtainium_export_uses_each_apps_own_persisted_tag(self: Self) -> None:
        """An app not rebuilt this run must keep linking to the release its own asset lives under.

        Regression test for the bug where every app's HTML was rewritten to the current run's tag on
        every run, even for apps whose asset was never uploaded to that release.
        """
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="Build-2",
                    obtainium_site_export=False,
                    all_apps=["AppA", "AppB", "AppC"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                # Not rebuilt this run; its asset only exists under its own older release.
                "AppA": {
                    "output_file_name": "AppA-output.apk",
                    "obtainium_github_tag": "Build-1",
                    "patched_this_run": False,
                },
                # Rebuilt this run, matching the live config tag.
                "AppB": {
                    "output_file_name": "AppB-output.apk",
                    "obtainium_github_tag": "Build-2",
                    "patched_this_run": True,
                },
                # Predates this field; only the live config tag is available for it.
                "AppC": {
                    "output_file_name": "AppC-output.apk",
                    "patched_this_run": False,
                },
            }

            generate_obtainium_export(updates_info, config)
            app_a_html = Path(temp_dir, "obtainium_sources", "appa.html").read_text(encoding="utf_8")
            app_b_html = Path(temp_dir, "obtainium_sources", "appb.html").read_text(encoding="utf_8")
            app_c_html = Path(temp_dir, "obtainium_sources", "appc.html").read_text(encoding="utf_8")

        self.assertIn("/releases/download/Build-1/AppA-output.apk", app_a_html)
        self.assertIn("/releases/download/Build-2/AppB-output.apk", app_b_html)
        self.assertIn("/releases/download/Build-2/AppC-output.apk", app_c_html)

    def test_generate_obtainium_export_keeps_existing_page_for_apps_not_patched_this_run(self: Self) -> None:
        """An app not patched this run must keep its real existing page untouched, not regenerated.

        The changelogs-branch sync (mocked out in setUp) is what would normally put this file on disk
        before generation runs; this test seeds it directly to stand in for that sync having happened.
        """
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="Build-2",
                    obtainium_site_export=False,
                    all_apps=["AppA", "AppB"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "AppA": {
                    "output_file_name": "AppA-output.apk",
                    "obtainium_github_tag": "Build-1",
                    "patched_this_run": False,
                },
                "AppB": {
                    "output_file_name": "AppB-output.apk",
                    "obtainium_github_tag": "Build-2",
                    "patched_this_run": True,
                },
            }

            sources_path = Path(temp_dir, "obtainium_sources")
            sources_path.mkdir()
            sources_path.joinpath("appa.html").write_text("<html>previously published AppA page</html>")

            generate_obtainium_export(updates_info, config)

            app_a_html = sources_path.joinpath("appa.html").read_text(encoding="utf_8")
            app_b_html = sources_path.joinpath("appb.html").read_text(encoding="utf_8")

        self.assertEqual(app_a_html, "<html>previously published AppA page</html>")
        self.assertIn("/releases/download/Build-2/AppB-output.apk", app_b_html)

    def test_generate_obtainium_export_bootstraps_missing_page_for_unpatched_app(self: Self) -> None:
        """An unpatched app with no existing page yet should still get one instead of being dropped."""
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="Build-2",
                    obtainium_site_export=False,
                    all_apps=["AppA"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "AppA": {
                    "output_file_name": "AppA-output.apk",
                    "obtainium_github_tag": "Build-1",
                    "patched_this_run": False,
                },
            }

            generate_obtainium_export(updates_info, config)

            app_a_html = Path(temp_dir, "obtainium_sources", "appa.html").read_text(encoding="utf_8")

        self.assertIn("/releases/download/Build-1/AppA-output.apk", app_a_html)

    def test_generate_obtainium_export_ignores_apps_not_in_current_roster(self: Self) -> None:
        """An app absent from config.all_apps must be skipped even if updates_info still has an entry.

        Regression test: updates_info can carry stale or foreign entries (e.g. from a JSON blob that
        outlived the branch it came from). Only apps in this project's configured roster should get a
        page or a site card - and that roster must be config.all_apps (the full PATCH_APPS roster), not
        config.apps, which can be narrowed to a "needs updating" subset for a given partial run.
        """
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="Build-1",
                    obtainium_site_export=True,
                    obtainium_version_extraction_regex="",
                    obtainium_version_match_group="",
                    all_apps=["AppB"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "GhostApp": {
                    "output_file_name": "GhostApp-output.apk",
                    "app_dump": {"package_name": "com.example.ghost"},
                    "patched_this_run": False,
                },
                "AppB": {
                    "output_file_name": "AppB-output.apk",
                    "app_dump": {"package_name": "com.example.appb"},
                    "patched_this_run": True,
                },
            }

            generate_obtainium_export(updates_info, config)

            sources_path = Path(temp_dir, "obtainium_sources")
            ghost_page_exists = sources_path.joinpath("ghostapp.html").exists()
            app_b_page_exists = sources_path.joinpath("appb.html").exists()
            index_content = Path(temp_dir, "index.html").read_text(encoding="utf_8")

        self.assertFalse(ghost_page_exists)
        self.assertTrue(app_b_page_exists)
        self.assertNotIn("GhostApp", index_content)
        self.assertIn("AppB", index_content)

    def test_generate_obtainium_export_includes_full_roster_even_on_a_narrowed_partial_run(self: Self) -> None:
        """The index must list every configured app, not just this run's narrowed "needs updating" subset.

        Regression test for the auto-release workflow: PATCH_APPS_OVERRIDE narrows config.apps to only
        the apps needing an update for a given run, but the Obtainium index should still cover the full
        roster (config.all_apps), including apps that simply didn't need rebuilding this time.
        """
        with TemporaryDirectory() as temp_dir, chdir(temp_dir):
            config = cast(
                "RevancedConfig",
                SimpleNamespace(
                    obtainium_export=True,
                    obtainium_github_tag="Build-2",
                    obtainium_site_export=True,
                    obtainium_version_extraction_regex="",
                    obtainium_version_match_group="",
                    # Only AppB needed updating this run, but the full roster is both apps.
                    apps=["AppB"],
                    all_apps=["AppA", "AppB"],
                    env=_Env("owner/repo"),
                ),
            )
            updates_info = {
                "AppA": {
                    "output_file_name": "AppA-output.apk",
                    "obtainium_github_tag": "Build-1",
                    "app_dump": {"package_name": "com.example.appa"},
                    "patched_this_run": False,
                },
                "AppB": {
                    "output_file_name": "AppB-output.apk",
                    "obtainium_github_tag": "Build-2",
                    "app_dump": {"package_name": "com.example.appb"},
                    "patched_this_run": True,
                },
            }

            generate_obtainium_export(updates_info, config)
            index_content = Path(temp_dir, "index.html").read_text(encoding="utf_8")

        self.assertIn("AppA", index_content)
        self.assertIn("AppB", index_content)

    def test_default_version_extraction_regex_handles_optional_patch_prefix(self: Self) -> None:
        """The shipped default regex must match patch bundle versions with or without a leading 'v'."""
        config = RevancedConfig(Env())
        regex = config.obtainium_version_extraction_regex

        with_v = re.search(regex, "ReYouTube-Version20.47.62-PatchVersionv1.0.0-PatchSetabc123-output.apk")
        without_v = re.search(regex, "ReYouTube-Version20.47.62-PatchVersion1.0.0-PatchSetabc123-output.apk")

        self.assertIsNotNone(with_v)
        self.assertIsNotNone(without_v)
        self.assertEqual(with_v.groups(), ("20.47.62", "1.0.0"))  # type: ignore[union-attr]
        self.assertEqual(without_v.groups(), ("20.47.62", "1.0.0"))  # type: ignore[union-attr]
