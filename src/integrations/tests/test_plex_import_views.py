"""Tests for the Plex import view's multi-library selection (issue #1079)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from integrations.models import PlexAccount


class PlexImportMultiLibraryViewTests(TestCase):
    def setUp(self):
        self.credentials = {"username": "multiimportview", "password": "pw"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.account = PlexAccount.objects.create(
            user=self.user,
            plex_token="token",
            plex_username="multiimportview",
            sections_refreshed_at=timezone.now(),
        )

    @patch("integrations.views.tasks.import_plex.delay")
    def test_multiple_libraries_are_queued_as_a_list(self, mock_delay):
        self.client.post(
            reverse("import_plex"),
            {
                "mode": "new",
                "frequency": "once",
                "library": ["machine::1", "machine::2"],
            },
        )

        mock_delay.assert_called_once_with(
            library=["machine::1", "machine::2"],
            user_id=self.user.id,
            mode="new",
        )

    @patch("integrations.views.tasks.import_plex.delay")
    def test_no_library_selection_defaults_to_all(self, mock_delay):
        self.client.post(reverse("import_plex"), {"mode": "new", "frequency": "once"})

        mock_delay.assert_called_once_with(
            library=["all"],
            user_id=self.user.id,
            mode="new",
        )

    @patch("integrations.views.tasks.update_collection_metadata_from_plex.delay")
    def test_update_collection_mode_forwards_multiple_libraries(self, mock_delay):
        self.client.post(
            reverse("import_plex"),
            {
                "mode": "update_collection",
                "frequency": "once",
                "library": ["machine::1", "machine::2"],
            },
        )

        mock_delay.assert_called_once_with(
            library=["machine::1", "machine::2"],
            user_id=self.user.id,
        )

    def test_per_library_content_kind_is_persisted(self):
        self.client.post(
            reverse("import_plex"),
            {
                "mode": "new",
                "frequency": "once",
                "library": ["machine::1", "machine::2"],
                "library_content_kind": [
                    "machine::1::audiobook",
                    "machine::2::music",
                ],
            },
        )

        self.account.refresh_from_db()
        self.assertEqual(
            self.account.content_kind("machine", "1"),
            "audiobook",
        )
        self.assertEqual(
            self.account.content_kind("machine", "2"),
            "music",
        )
