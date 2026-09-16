import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Game,
    ItemTag,
    Status,
)
from integrations.imports import (
    grouvee,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"

GAME_METADATA = {
    "1227": {"title": "Dragon Warrior I & II", "image": ""},
    "128167": {"title": "RoboCop: Rogue City", "image": ""},
    "5001": {"title": "Retro Backlog Game", "image": ""},
    "7001": {"title": "Custom Shelf Only Game", "image": ""},
    "8001": {"title": "Wish List Game", "image": ""},
}


def _fake_get_media_metadata(media_type, media_id, source):
    return GAME_METADATA[media_id]


class ImportGrouvee(TestCase):
    """Test importing media from a Grouvee JSON export."""

    def setUp(self):
        """Create user and import the mock Grouvee export."""
        self.metadata_patcher = patch(
            "app.providers.services.get_media_metadata",
            side_effect=_fake_get_media_metadata,
        )
        self.mock_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)

        self.credentials = {"username": "test", "password": "***"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        with Path(mock_path / "import_grouvee.json").open("rb") as file:
            self.imported_counts, self.warnings = grouvee.importer(
                file,
                self.user,
                "new",
            )

    def test_import_counts(self):
        """Games with an IGDB ID are imported; the unmatched one is skipped."""
        self.assertEqual(Game.objects.filter(user=self.user).count(), 5)

    def test_missing_igdb_id_warning(self):
        """A game without an igdb_id produces a warning and is not imported."""
        self.assertIsNotNone(self.warnings)
        self.assertIn("No IGDB Match Game", self.warnings)
        self.assertFalse(
            Game.objects.filter(user=self.user, item__media_id="9999").exists(),
        )

    def test_played_shelf_maps_to_completed(self):
        """A game only on the Played shelf is marked Completed."""
        game = Game.objects.get(user=self.user, item__media_id="1227")
        self.assertEqual(game.status, Status.COMPLETED.value)
        self.assertEqual(game.score, 6)
        self.assertEqual(game.progress, 60)

    def test_playing_shelf_maps_to_in_progress(self):
        """A game on the Playing shelf is marked In Progress."""
        game = Game.objects.get(user=self.user, item__media_id="128167")
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertIsNone(game.score)

    def test_backlog_shelf_ignores_custom_shelf(self):
        """A game on Backlog plus a custom shelf is marked Planning."""
        game = Game.objects.get(user=self.user, item__media_id="5001")
        self.assertEqual(game.status, Status.PLANNING.value)

    def test_dateless_playtime_excluded_from_progress(self):
        """Steam's dateless auto-tracked playtime isn't counted as progress."""
        game = Game.objects.get(user=self.user, item__media_id="5001")
        self.assertEqual(game.progress, 0)
        self.assertIsNone(game.start_date)
        self.assertIsNone(game.end_date)

    def test_overwrite_mode_replaces_existing(self):
        """Re-importing in overwrite mode replaces existing games."""
        with Path(mock_path / "import_grouvee.json").open("rb") as file:
            grouvee.importer(file, self.user, "overwrite")

        self.assertEqual(Game.objects.filter(user=self.user).count(), 5)

    def test_new_mode_skips_existing(self):
        """Re-importing in new mode does not duplicate existing games."""
        with Path(mock_path / "import_grouvee.json").open("rb") as file:
            imported_counts, _ = grouvee.importer(file, self.user, "new")

        self.assertNotIn("game", imported_counts)
        self.assertEqual(Game.objects.filter(user=self.user).count(), 5)

    def test_custom_shelf_only_maps_to_planning(self):
        """A game on only a custom shelf is Planning, not Completed (issue #764)."""
        game = Game.objects.get(user=self.user, item__media_id="7001")
        self.assertEqual(game.status, Status.PLANNING.value)
        self.assertIsNone(game.end_date)

    def test_notes_combine_session_notes_and_review(self):
        """Notes lead with per-session notes, then the review."""
        game = Game.objects.get(user=self.user, item__media_id="1227")
        self.assertEqual(
            game.notes,
            "Took a long break partway through.\n\nA charming double pack.",
        )

    def test_custom_shelf_creates_tag_not_status_shelf(self):
        """A custom shelf is tagged; the Backlog status shelf is not."""
        game = Game.objects.get(user=self.user, item__media_id="5001")
        tag_names = set(
            ItemTag.objects.filter(item=game.item).values_list(
                "tag__name",
                flat=True,
            ),
        )
        self.assertEqual(tag_names, {"2-Player Games"})

    def test_wish_list_creates_tag_and_no_status(self):
        """Wish List is tagged and still resolves to Planning."""
        game = Game.objects.get(user=self.user, item__media_id="8001")
        self.assertEqual(game.status, Status.PLANNING.value)
        self.assertTrue(
            ItemTag.objects.filter(
                item=game.item,
                tag__name="Wish List",
                tag__user=self.user,
            ).exists(),
        )

    def test_status_shelf_is_not_tagged(self):
        """Playing/Played/Backlog shelves don't produce redundant tags."""
        game = Game.objects.get(user=self.user, item__media_id="1227")
        self.assertFalse(ItemTag.objects.filter(item=game.item).exists())

    def test_zip_export_is_imported(self):
        """A zip archive containing the JSON export imports the same as raw JSON."""
        user = get_user_model().objects.create_user(username="zip", password="***")
        json_bytes = (mock_path / "import_grouvee.json").read_bytes()

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("grouvee_export.json", json_bytes)
            archive.writestr("collection.csv", "id,name\n")
        buffer.seek(0)

        imported_counts, _ = grouvee.importer(buffer, user, "new")
        self.assertEqual(imported_counts["game"], 5)


class ImportGrouveeTimeLimit(TestCase):
    """Test partial-import recovery when the Celery soft time limit trips."""

    def test_entries_processed_before_the_limit_are_still_saved(self):
        """A time limit mid-import saves prior entries instead of losing them."""
        user = get_user_model().objects.create_user(
            username="timelimit", password="***"
        )

        def side_effect(media_type, media_id, source):
            if media_id == "128167":
                raise SoftTimeLimitExceeded
            return GAME_METADATA[media_id]

        with (
            patch(
                "app.providers.services.get_media_metadata",
                side_effect=side_effect,
            ),
            Path(mock_path / "import_grouvee.json").open("rb") as file,
        ):
            _, warnings = grouvee.importer(file, user, "new")

        self.assertTrue(
            Game.objects.filter(user=user, item__media_id="1227").exists(),
        )
        self.assertFalse(
            Game.objects.filter(user=user, item__media_id="128167").exists(),
        )
        self.assertFalse(
            Game.objects.filter(user=user, item__media_id="5001").exists(),
        )
        self.assertIn("Run the import again", warnings)
