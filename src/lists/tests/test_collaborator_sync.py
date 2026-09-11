from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from lists.collaborator_sync import (
    get_collaborators_for_item,
    sync_episode_to_list_collaborators,
    sync_media_to_list_collaborators,
)
from lists.models import CustomList, CustomListItem


class CollaboratorSyncTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user1 = user_model.objects.create_user(username="user1", password="password")
        self.user2 = user_model.objects.create_user(username="user2", password="password")
        self.user3 = user_model.objects.create_user(username="user3", password="password")

        # Shared list between user1 and user2
        self.shared_list = CustomList.objects.create(
            name="Shared Watchlist",
            owner=self.user1,
        )
        self.shared_list.collaborators.add(self.user2)

        # Private list for user1
        self.private_list = CustomList.objects.create(
            name="Solo List",
            owner=self.user1,
        )

        # Create test items
        self.movie_item = Item.objects.create(
            title="Inception",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="27205",
        )
        self.solo_movie_item = Item.objects.create(
            title="Interstellar",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="157336",
        )

        # Add movie_item to shared_list
        CustomListItem.objects.create(custom_list=self.shared_list, item=self.movie_item)
        # Add solo_movie_item to private_list
        CustomListItem.objects.create(custom_list=self.private_list, item=self.solo_movie_item)

    def test_get_collaborators_for_item(self):
        collabs = get_collaborators_for_item(self.movie_item, self.user1)
        self.assertEqual(collabs, {self.user2})

        # From user2 perspective
        collabs2 = get_collaborators_for_item(self.movie_item, self.user2)
        self.assertEqual(collabs2, {self.user1})

        # Solo movie has no collaborators
        solo_collabs = get_collaborators_for_item(self.solo_movie_item, self.user1)
        self.assertEqual(solo_collabs, set())

        # user3 is not in any list
        user3_collabs = get_collaborators_for_item(self.movie_item, self.user3)
        self.assertEqual(user3_collabs, set())

    def test_movie_completion_syncs_to_collaborator(self):
        # user1 completes Inception
        now = timezone.now()
        media1 = Movie.objects.create(
            user=self.user1,
            item=self.movie_item,
            status=Status.COMPLETED.value,
            end_date=now,
        )
        sync_media_to_list_collaborators(media1, self.user1)

        # user2 should now have Inception completed
        user2_movie = Movie.objects.filter(user=self.user2, item=self.movie_item).first()
        self.assertIsNotNone(user2_movie)
        self.assertEqual(user2_movie.status, Status.COMPLETED.value)
        self.assertEqual(user2_movie.end_date, now)

        # user3 was not a collaborator, so no record
        self.assertFalse(Movie.objects.filter(user=self.user3, item=self.movie_item).exists())

    def test_movie_completion_from_collaborator_syncs_to_owner(self):
        # user2 (collaborator) completes Inception
        now = timezone.now()
        media2 = Movie.objects.create(
            user=self.user2,
            item=self.movie_item,
            status=Status.COMPLETED.value,
            end_date=now,
        )
        sync_media_to_list_collaborators(media2, self.user2)

        # user1 (owner) should now have Inception completed
        user1_movie = Movie.objects.filter(user=self.user1, item=self.movie_item).first()
        self.assertIsNotNone(user1_movie)
        self.assertEqual(user1_movie.status, Status.COMPLETED.value)

    def test_private_movie_completion_does_not_sync(self):
        # user1 completes solo movie
        media1 = Movie.objects.create(
            user=self.user1,
            item=self.solo_movie_item,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )
        sync_media_to_list_collaborators(media1, self.user1)

        # user2 must not have this movie
        self.assertFalse(Movie.objects.filter(user=self.user2, item=self.solo_movie_item).exists())

    def test_existing_record_preserves_score_and_notes(self):
        # user2 already had Inception In Progress with a personal score and note
        Movie.objects.create(
            user=self.user2,
            item=self.movie_item,
            status=Status.IN_PROGRESS.value,
            score=9.0,
            notes="Best movie ever!",
        )

        media1 = Movie.objects.create(
            user=self.user1,
            item=self.movie_item,
            status=Status.COMPLETED.value,
            score=7.0,
            notes="It was okay",
        )
        sync_media_to_list_collaborators(media1, self.user1)

        user2_movie = Movie.objects.get(user=self.user2, item=self.movie_item)
        self.assertEqual(user2_movie.status, Status.COMPLETED.value)
        self.assertEqual(user2_movie.score, 9.0)
        self.assertEqual(user2_movie.notes, "Best movie ever!")

    def test_non_completed_status_does_not_sync(self):
        # user1 sets Inception to In Progress
        media1 = Movie.objects.create(
            user=self.user1,
            item=self.movie_item,
            status=Status.IN_PROGRESS.value,
        )
        sync_media_to_list_collaborators(media1, self.user1)

        # user2 should NOT have a record created
        self.assertFalse(Movie.objects.filter(user=self.user2, item=self.movie_item).exists())

    @patch("app.models.tv.providers.services.get_media_metadata")
    def test_tv_show_completion_syncs_to_collaborator(self, mock_metadata):
        mock_metadata.return_value = {
            "title": "Breaking Bad",
            "details": {"seasons": 1},
            "related": {"seasons": []},
            "image": "",
        }
        tv_item = Item.objects.create(
            title="Breaking Bad",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
            media_id="1396",
        )
        CustomListItem.objects.create(custom_list=self.shared_list, item=tv_item)

        tv1 = TV.objects.create(
            user=self.user1,
            item=tv_item,
            status=Status.COMPLETED.value,
        )
        sync_media_to_list_collaborators(tv1, self.user1)

        user2_tv = TV.objects.filter(user=self.user2, item=tv_item).first()
        self.assertIsNotNone(user2_tv)
        self.assertEqual(user2_tv.status, Status.COMPLETED.value)

    @patch("app.models.tv.providers.services.get_media_metadata")
    def test_season_completion_syncs_to_collaborator(self, mock_metadata):
        mock_metadata.return_value = {
            "title": "Breaking Bad Season 1",
            "details": {"episodes": 1},
            "related": {"seasons": []},
            "image": "",
            "max_progress": 1,
        }
        season_item = Item.objects.create(
            title="Breaking Bad Season 1",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            media_id="1396",
            season_number=1,
        )
        CustomListItem.objects.create(custom_list=self.shared_list, item=season_item)

        tv1 = TV.objects.create(
            user=self.user1,
            item=Item.objects.create(
                title="Breaking Bad",
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id="1396",
            ),
            status=Status.IN_PROGRESS.value,
        )
        season1 = Season.objects.create(
            user=self.user1,
            item=season_item,
            related_tv=tv1,
            status=Status.COMPLETED.value,
        )
        sync_media_to_list_collaborators(season1, self.user1)

        user2_season = Season.objects.filter(user=self.user2, item=season_item).first()
        self.assertIsNotNone(user2_season)
        self.assertEqual(user2_season.status, Status.COMPLETED.value)

    @patch("app.fork_services_episode.create_episode_watch")
    @patch("app.fork_services_episode.resolve_or_create_season")
    def test_episode_watch_syncs_to_collaborator(self, mock_resolve_season, mock_create_watch):
        mock_season = Season(
            user=self.user2,
            item=Item(media_type=MediaTypes.SEASON.value, media_id="1396", source=Sources.TMDB.value, season_number=1),
        )
        mock_resolve_season.return_value = mock_season

        episode_item = Item.objects.create(
            title="Pilot",
            media_type=MediaTypes.EPISODE.value,
            source=Sources.TMDB.value,
            media_id="1396",
            season_number=1,
            episode_number=1,
        )
        CustomListItem.objects.create(custom_list=self.shared_list, item=episode_item)

        episode = Episode(
            item=episode_item,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )
        sync_episode_to_list_collaborators(episode, self.user1)

        mock_resolve_season.assert_called_once_with(
            user=self.user2,
            media_id="1396",
            source=Sources.TMDB.value,
            season_number=1,
            library_media_type="",
        )
        mock_create_watch.assert_called_once()

    @patch("app.models.media.providers.services.get_media_metadata")
    def test_media_save_endpoint_syncs_to_collaborator(self, mock_metadata):
        mock_metadata.return_value = {
            "title": "Inception",
            "image": "",
            "max_progress": 148,
        }
        self.client.force_login(self.user1)
        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "27205",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.COMPLETED.value,
            },
        )
        self.assertIn(response.status_code, (200, 302))

        user2_movie = Movie.objects.filter(user=self.user2, item=self.movie_item).first()
        self.assertIsNotNone(user2_movie)
        self.assertEqual(user2_movie.status, Status.COMPLETED.value)
