import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from app.models import (
    CollectionEntry,
    CollectionField,
    CollectionFieldGroup,
    CollectionFieldType,
    Item,
    MediaTypes,
    Sources,
)


class CollectionFieldsSaveViewTest(TestCase):
    """Test the bulk custom-field schema save endpoint."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.other_user = get_user_model().objects.create_user(
            username="other", password="12345"
        )
        self.item = Item.objects.create(
            media_id="1234",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.client.login(**self.credentials)

    def _save(self, groups, item_id=None):
        return self.client.post(
            reverse("collection_fields_save"),
            data=json.dumps({"item_id": item_id, "groups": groups}),
            content_type="application/json",
        )

    def test_save_creates_group_and_field(self):
        """A payload with null ids creates new groups and fields."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "select",
                            "options": ["Mint", "Good"],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 200)
        group = CollectionFieldGroup.objects.get(user=self.user, name="Condition")
        field = CollectionField.objects.get(group=group, label="Grade")
        self.assertEqual(field.field_type, "select")
        self.assertEqual(field.options, ["Mint", "Good"])
        self.assertEqual(field.media_types, ["movie"])

    def test_save_rejects_blank_group_name(self):
        """A blank group name is rejected with 400."""
        response = self._save([{"id": None, "name": "  ", "fields": []}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CollectionFieldGroup.objects.filter(user=self.user).exists())

    def test_save_rejects_field_without_media_types(self):
        """A field with no media types is rejected with 400."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "text",
                            "options": [],
                            "media_types": [],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CollectionFieldGroup.objects.filter(user=self.user).exists())

    def test_save_rejects_invalid_field_type(self):
        """An unknown field_type is rejected with 400."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "not-a-type",
                            "options": [],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_invalid_media_type(self):
        """An unknown media type value is rejected with 400."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["not-a-media-type"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_malformed_json(self):
        """A malformed JSON body is rejected with 400."""
        response = self.client.post(
            reverse("collection_fields_save"),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_get(self):
        """GET requests are rejected."""
        response = self.client.get(reverse("collection_fields_save"))
        self.assertEqual(response.status_code, 405)

    def test_save_drops_options_for_non_select_type(self):
        """Options are discarded for a non-select field type."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "text",
                            "options": ["Mint", "Good"],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 200)
        field = CollectionField.objects.get(label="Grade")
        self.assertEqual(field.options, [])

    def test_save_reorders_groups_by_payload_index(self):
        """Group order follows payload array order; ids stay unchanged."""
        first = CollectionFieldGroup.objects.create(
            user=self.user, name="First", position=0
        )
        second = CollectionFieldGroup.objects.create(
            user=self.user, name="Second", position=1
        )
        response = self._save(
            [
                {"id": second.id, "name": "Second", "fields": []},
                {"id": first.id, "name": "First", "fields": []},
            ],
        )
        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(second.position, 0)
        self.assertEqual(first.position, 1)

    def test_save_reorders_fields_within_group(self):
        """Field order within a group follows payload array order."""
        group = CollectionFieldGroup.objects.create(user=self.user, name="Condition")
        first = CollectionField.objects.create(
            group=group, label="First", media_types=["movie"], position=0
        )
        second = CollectionField.objects.create(
            group=group, label="Second", media_types=["movie"], position=1
        )
        response = self._save(
            [
                {
                    "id": group.id,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": second.id,
                            "label": "Second",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["movie"],
                        },
                        {
                            "id": first.id,
                            "label": "First",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(second.position, 0)
        self.assertEqual(first.position, 1)

    def test_save_removes_group_absent_from_payload(self):
        """A group (and its fields) not present in the payload is deleted."""
        group = CollectionFieldGroup.objects.create(user=self.user, name="Condition")
        field = CollectionField.objects.create(
            group=group, label="Grade", media_types=["movie"]
        )
        response = self._save([])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CollectionFieldGroup.objects.filter(id=group.id).exists())
        self.assertFalse(CollectionField.objects.filter(id=field.id).exists())

    def test_save_with_empty_groups_clears_schema(self):
        """An empty groups list wipes only the requesting user's schema."""
        own_group = CollectionFieldGroup.objects.create(user=self.user, name="Mine")
        other_group = CollectionFieldGroup.objects.create(
            user=self.other_user, name="Other"
        )
        response = self._save([])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CollectionFieldGroup.objects.filter(id=own_group.id).exists())
        self.assertTrue(CollectionFieldGroup.objects.filter(id=other_group.id).exists())

    def test_save_rejects_other_users_group_id(self):
        """A payload naming another user's group id is rejected with 404."""
        other_group = CollectionFieldGroup.objects.create(
            user=self.other_user, name="Other"
        )
        response = self._save(
            [{"id": other_group.id, "name": "Hijacked", "fields": []}],
        )
        self.assertEqual(response.status_code, 404)
        other_group.refresh_from_db()
        self.assertEqual(other_group.name, "Other")

    def test_save_rejects_other_users_field_id(self):
        """A payload naming another user's field id is rejected with 404."""
        other_group = CollectionFieldGroup.objects.create(
            user=self.other_user, name="Other"
        )
        other_field = CollectionField.objects.create(
            group=other_group, label="Grade", media_types=["movie"]
        )
        own_group = CollectionFieldGroup.objects.create(user=self.user, name="Mine")
        response = self._save(
            [
                {
                    "id": own_group.id,
                    "name": "Mine",
                    "fields": [
                        {
                            "id": other_field.id,
                            "label": "Hijacked",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        self.assertEqual(response.status_code, 404)
        other_field.refresh_from_db()
        self.assertEqual(other_field.label, "Grade")

    def test_save_is_atomic_on_validation_error(self):
        """A validation error in a later group leaves earlier groups untouched."""
        response = self._save(
            [
                {"id": None, "name": "Valid Group", "fields": []},
                {"id": None, "name": "", "fields": []},
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            CollectionFieldGroup.objects.filter(
                user=self.user, name="Valid Group"
            ).exists()
        )

    def test_save_returns_schema_with_real_ids(self):
        """The response schema includes real database ids for new rows."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )
        payload = response.json()
        self.assertTrue(payload["success"])
        returned_field_id = payload["groups"][0]["fields"][0]["id"]
        self.assertIsInstance(returned_field_id, int)
        self.assertTrue(CollectionField.objects.filter(id=returned_field_id).exists())

    def test_save_preserves_field_ids_and_stored_values(self):
        """Renaming/reordering via save never changes an existing field's id."""
        group = CollectionFieldGroup.objects.create(user=self.user, name="Condition")
        field = CollectionField.objects.create(
            group=group, label="Grade", media_types=["movie"]
        )
        entry = CollectionEntry.objects.create(
            user=self.user,
            item=self.item,
            custom_field_values={str(field.id): "Mint"},
        )

        response = self._save(
            [
                {
                    "id": group.id,
                    "name": "Renamed Condition",
                    "fields": [
                        {
                            "id": field.id,
                            "label": "Renamed Grade",
                            "field_type": "text",
                            "options": [],
                            "media_types": ["movie"],
                        },
                    ],
                },
            ],
        )

        self.assertEqual(response.status_code, 200)
        field.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(field.label, "Renamed Grade")
        self.assertEqual(entry.custom_field_values.get(str(field.id)), "Mint")

    def test_save_renders_fragment_html_for_item(self):
        """When item_id is provided, the response includes a rendered fragment."""
        response = self._save(
            [
                {
                    "id": None,
                    "name": "Condition",
                    "fields": [
                        {
                            "id": None,
                            "label": "Grade",
                            "field_type": "text",
                            "options": [],
                            "media_types": [MediaTypes.MOVIE.value],
                        },
                    ],
                },
            ],
            item_id=self.item.id,
        )
        payload = response.json()
        self.assertIsNotNone(payload["html"])
        self.assertIn("Grade", payload["html"])


class CollectionModalCustomFieldsTest(TestCase):
    """Test that the Collection modal renders and saves custom field values."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="1234",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.group = CollectionFieldGroup.objects.create(
            user=self.user, name="Condition"
        )
        self.field = CollectionField.objects.create(
            group=self.group,
            label="Grade",
            field_type=CollectionFieldType.TEXT,
            media_types=[MediaTypes.MOVIE.value],
        )
        self.hidden_field = CollectionField.objects.create(
            group=self.group,
            label="Book only field",
            field_type=CollectionFieldType.TEXT,
            media_types=[MediaTypes.BOOK.value],
        )
        self.client.login(**self.credentials)

    def test_modal_shows_matching_field_and_hides_others(self):
        """Test the modal only renders fields scoped to the item's media type."""
        response = self.client.get(
            reverse(
                "collection_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": self.item.media_id,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="custom_field_{self.field.id}"')
        self.assertNotContains(response, f'name="custom_field_{self.hidden_field.id}"')

    def test_modal_bootstraps_schema_json(self):
        """The modal response bootstraps the save url and current schema."""
        response = self.client.get(
            reverse(
                "collection_modal",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": self.item.media_id,
                },
            )
        )
        self.assertContains(response, reverse("collection_fields_save"))
        self.assertContains(response, "Condition")

    def test_collection_add_saves_custom_field_value(self):
        """Test that a custom field value is saved on collection_add."""
        response = self.client.post(
            reverse("collection_add"),
            {"item_id": self.item.id, f"custom_field_{self.field.id}": "Mint"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        entry = self.item.collectionentry_set.get(user=self.user)
        self.assertEqual(entry.custom_field_values.get(str(self.field.id)), "Mint")

    def test_collection_update_does_not_clobber_out_of_scope_checkbox(self):
        """Updating a movie entry must not overwrite a book-only checkbox value."""
        checkbox_field = CollectionField.objects.create(
            group=self.group,
            label="Signed",
            field_type=CollectionFieldType.CHECKBOX,
            media_types=[MediaTypes.BOOK.value],
        )
        entry = CollectionEntry.objects.create(
            user=self.user,
            item=self.item,
            custom_field_values={str(checkbox_field.id): True},
        )
        response = self.client.post(
            reverse("collection_update", args=[entry.id]),
            {f"custom_field_{self.field.id}": "Good"},
        )
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertTrue(entry.custom_field_values.get(str(checkbox_field.id)))


class CollectionEntrySeasonCustomFieldsTest(TestCase):
    """Test that season/show collection submissions persist custom field values."""

    def setUp(self):
        """Create a user, a TV season, and two episodes under it."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client = Client()
        self.client.login(**self.credentials)

        self.tv_item = Item.objects.create(
            media_id="tv1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/tv.jpg",
        )
        self.season_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
            image="http://example.com/season1.jpg",
        )
        self.episode_one = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            image="http://example.com/episode1.jpg",
        )
        self.episode_two = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            image="http://example.com/episode2.jpg",
        )
        self.group = CollectionFieldGroup.objects.create(
            user=self.user, name="Condition"
        )
        self.field = CollectionField.objects.create(
            group=self.group,
            label="Grade",
            field_type=CollectionFieldType.TEXT,
            media_types=[MediaTypes.SEASON.value],
        )

    def test_collection_add_saves_custom_field_value_for_season(self):
        """Adding a season with a custom field value persists it on every episode."""
        response = self.client.post(
            reverse("collection_add"),
            {
                "item_id": self.season_item.id,
                f"custom_field_{self.field.id}": "Mint",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        entries = CollectionEntry.objects.filter(
            user=self.user, item__in=[self.episode_one, self.episode_two]
        )
        self.assertEqual(entries.count(), 2)
        for entry in entries:
            self.assertEqual(entry.custom_field_values.get(str(self.field.id)), "Mint")

    def test_collection_add_season_still_reports_created_counts(self):
        """The season/show add response still reports how many entries were made."""
        response = self.client.post(
            reverse("collection_add"),
            {"item_id": self.season_item.id},
            HTTP_HX_REQUEST="true",
        )
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn("2 episode(s)", payload["message"])
