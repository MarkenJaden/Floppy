from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from app.providers import googlebooks


class GoogleBooksProviderTests(SimpleTestCase):
    """Test Google Books request and metadata normalization behavior."""

    @override_settings(GOOGLE_BOOKS_API_KEY="google-key", PER_PAGE=24)
    @patch("app.providers.googlebooks.cache.set")
    @patch("app.providers.googlebooks.cache.get", return_value=None)
    @patch("app.providers.googlebooks.services.api_request")
    def test_search_sends_pagination_and_language_parameters(
        self,
        mock_request,
        mock_cache_get,
        mock_cache_set,
    ):
        mock_request.return_value = {
            "totalItems": 1,
            "items": [
                {
                    "id": "volume-1",
                    "volumeInfo": {
                        "title": "The Book",
                        "publishedDate": "2024-03-01",
                        "imageLinks": {
                            "thumbnail": "http://books.example/cover.jpg",
                        },
                    },
                },
            ],
        }

        result = googlebooks.search("the book", 3, language="fr")

        mock_cache_get.assert_called_once_with(
            "search_googlebooks_book_the book_fr_3",
        )
        mock_request.assert_called_once_with(
            "googlebooks",
            "GET",
            googlebooks.BASE_URL,
            params={
                "q": "the book",
                "startIndex": 48,
                "maxResults": 24,
                "printType": "books",
                "key": "google-key",
                "langRestrict": "fr",
            },
        )
        mock_cache_set.assert_called_once_with(
            "search_googlebooks_book_the book_fr_3",
            result,
        )
        self.assertEqual(result["total_results"], 1)

    @override_settings(GOOGLE_BOOKS_API_KEY="google-key")
    @patch("app.providers.googlebooks.cache.set")
    @patch("app.providers.googlebooks.cache.get", return_value=None)
    @patch("app.providers.googlebooks.services.api_request")
    def test_search_normalizes_results_and_skips_incomplete_volumes(
        self,
        mock_request,
        mock_cache_get,
        mock_cache_set,
    ):
        mock_request.return_value = {
            "totalItems": 2,
            "items": [
                {
                    "id": "volume-1",
                    "volumeInfo": {
                        "title": "The Book",
                        "publishedDate": "First published 1999",
                        "imageLinks": {
                            "smallThumbnail": "http://books.example/small.jpg",
                            "large": "http://books.example/large.jpg",
                        },
                    },
                },
                {"volumeInfo": {"title": "Missing ID"}},
            ],
        }

        result = googlebooks.search("book", 1)

        self.assertEqual(
            result["results"],
            [
                {
                    "media_id": "volume-1",
                    "source": "googlebooks",
                    "media_type": "book",
                    "title": "The Book",
                    "image": "https://books.example/large.jpg",
                    "year": 1999,
                },
            ],
        )

    @override_settings(GOOGLE_BOOKS_API_KEY="google-key")
    @patch("app.providers.googlebooks.cache.set")
    @patch("app.providers.googlebooks.cache.get", return_value=None)
    @patch("app.providers.googlebooks.services.api_request")
    def test_book_normalizes_volume_metadata(
        self, mock_request, mock_cache_get, mock_cache_set
    ):
        mock_request.return_value = {
            "id": "volume-1",
            "volumeInfo": {
                "title": "The Book",
                "authors": ["Author One", "Author Two"],
                "publisher": "Example Press",
                "publishedDate": "2020-02-03",
                "description": "<p>A <b>great</b> book.</p>",
                "pageCount": 320,
                "averageRating": 4.5,
                "ratingsCount": 12,
                "categories": ["Fiction"],
                "language": "en",
                "printType": "BOOK",
                "canonicalVolumeLink": "http://books.google.com/volume-1",
                "imageLinks": {
                    "thumbnail": "http://books.example/thumb.jpg",
                    "extraLarge": "http://books.example/large.jpg",
                },
                "industryIdentifiers": [
                    {"type": "ISBN_10", "identifier": "0123456789"},
                    {"type": "ISBN_13", "identifier": "9780123456789"},
                    {"type": "OTHER", "identifier": "ignore-me"},
                ],
            },
        }

        result = googlebooks.book("volume-1")

        mock_cache_get.assert_called_once_with("googlebooks_book_volume-1")
        mock_request.assert_called_once_with(
            "googlebooks",
            "GET",
            "https://www.googleapis.com/books/v1/volumes/volume-1",
            params={"key": "google-key"},
        )
        mock_cache_set.assert_called_once_with("googlebooks_book_volume-1", result)
        self.assertEqual(result["source_url"], "http://books.google.com/volume-1")
        self.assertEqual(result["max_progress"], 320)
        self.assertEqual(result["image"], "https://books.example/large.jpg")
        self.assertEqual(result["synopsis"], "A great book.")
        self.assertEqual(result["score"], 9.0)
        self.assertEqual(result["score_count"], 12)
        self.assertEqual(result["details"]["author"], ["Author One", "Author Two"])
        self.assertEqual(
            result["details"]["isbn"],
            ["0123456789", "9780123456789"],
        )
        self.assertEqual(result["details"]["format"], "Book")
        self.assertEqual(result["authors_full"], [])

    @override_settings(GOOGLE_BOOKS_API_KEY="google-key")
    @patch("app.providers.googlebooks.cache.set")
    @patch("app.providers.googlebooks.cache.get", return_value=None)
    @patch("app.providers.googlebooks.services.api_request")
    def test_book_handles_missing_optional_fields(
        self,
        mock_request,
        mock_cache_get,
        mock_cache_set,
    ):
        mock_request.return_value = {"volumeInfo": {}}

        result = googlebooks.book("volume-2")

        self.assertEqual(result["title"], "")
        self.assertEqual(result["image"], googlebooks.settings.IMG_NONE)
        self.assertEqual(result["synopsis"], "No synopsis available.")
        self.assertEqual(result["genres"], [])
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_count"], 0)
        self.assertIsNone(result["details"]["author"])
        self.assertEqual(result["details"]["isbn"], [])
