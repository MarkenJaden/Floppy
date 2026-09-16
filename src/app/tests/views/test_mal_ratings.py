from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources


class MalRatingDetailViewTests(TestCase):
    def setUp(self):
        credentials = {"username": "mal-test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_grouped_anime_renders_rating_and_link_with_tvdb_metadata(
        self, mock_metadata
    ):
        Item.objects.create(
            media_id="tv-1",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Grouped Anime",
            image="http://example.com/image.jpg",
            provider_external_ids={"mal_id": "52991"},
            mal_rating=8.78,
            mal_rating_count=1234,
        )
        mock_metadata.return_value = {
            "media_id": "tv-1",
            "title": "Grouped Anime",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "source_url": "https://www.thetvdb.com/series/tv-1",
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "tv-1",
                    "title": "grouped-anime",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "myanimelist-logo.svg")
        self.assertContains(response, "8.7")
        self.assertNotContains(response, "8.78")
        self.assertContains(response, "1,234 ratings")
        self.assertContains(response, "https://myanimelist.net/anime/52991")
        self.assertEqual(response.context["mal_score"]["rating_count"], 1234)

    @patch("app.providers.services.get_media_metadata")
    def test_anonymous_anime_detail_can_render_stored_rating(self, mock_metadata):
        Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="http://example.com/image.jpg",
            mal_rating=9.04,
            mal_rating_count=2000,
        )
        mock_metadata.return_value = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/52991",
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }
        self.client.logout()

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "myanimelist-logo.svg")
        self.assertContains(response, "9.0")
        self.assertContains(response, "2,000 ratings")
