from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem, ListActivity, ListActivityType


class BulkListAddViewTests(TestCase):
    """Test bulk additions to editable manual lists."""

    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="bulk-list-owner",
            password="test-password",
        )
        self.collaborator = user_model.objects.create_user(
            username="bulk-list-collaborator",
            password="test-password",
        )
        self.outsider = user_model.objects.create_user(
            username="bulk-list-outsider",
            password="test-password",
        )
        self.custom_list = CustomList.objects.create(
            name="Bulk List",
            owner=self.owner,
        )
        self.custom_list.collaborators.add(self.collaborator)
        self.smart_list = CustomList.objects.create(
            name="Smart List",
            owner=self.owner,
            is_smart=True,
        )
        self.items = [
            Item.objects.create(
                media_id=f"bulk-list-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Bulk List Movie {index}",
            )
            for index in range(2)
        ]

    def _post(self, user, custom_list, item_ids):
        self.client.force_login(user)
        # A plain dict with a list value, not a QueryDict: the test client's
        # multipart encoder iterates data.items(), and QueryDict.items()
        # yields only the last value per key, so every id but the last would
        # be dropped before the request was sent.
        payload = {
            "item_ids": [str(item_id) for item_id in item_ids],
            "custom_list_id": str(custom_list.id),
        }
        return self.client.post(reverse("bulk_list_add"), payload)

    def test_owner_adds_items_once_and_records_activity_and_order(self):
        response = self._post(
            self.owner,
            self.custom_list,
            [item.id for item in self.items],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"], 2)
        list_items = list(
            CustomListItem.objects.filter(custom_list=self.custom_list).order_by(
                "list_item_id",
            ),
        )
        self.assertEqual([entry.item_id for entry in list_items], [item.id for item in self.items])
        self.assertEqual(
            ListActivity.objects.filter(
                custom_list=self.custom_list,
                activity_type=ListActivityType.ITEM_ADDED,
            ).count(),
            2,
        )

        response = self._post(
            self.owner,
            self.custom_list,
            [item.id for item in self.items],
        )
        self.assertEqual(response.json()["added"], 0)
        self.assertEqual(response.json()["already_present"], 2)

    def test_collaborator_can_add_but_outsider_and_smart_list_cannot(self):
        response = self._post(self.collaborator, self.custom_list, [self.items[0].id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            CustomListItem.objects.filter(
                custom_list=self.custom_list,
                item=self.items[0],
                added_by=self.collaborator,
            ).count(),
            1,
        )

        response = self._post(self.outsider, self.custom_list, [self.items[1].id])
        self.assertEqual(response.status_code, 404)

        response = self._post(self.owner, self.smart_list, [self.items[1].id])
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            CustomListItem.objects.filter(
                custom_list=self.smart_list,
                item=self.items[1],
            ).exists(),
        )
