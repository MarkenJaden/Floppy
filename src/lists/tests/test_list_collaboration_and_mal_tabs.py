from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Item, MediaTypes, Movie, Sources, Status
from lists.models import CustomList, CustomListItem


class ListCollaborationAndMALTabsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="owner", password="password")
        self.partner = user_model.objects.create_user(username="partner", password="password")
        self.stranger = user_model.objects.create_user(username="stranger", password="password")

        self.custom_list = CustomList.objects.create(
            name="Couple Watchlist",
            owner=self.owner,
        )
        self.custom_list.collaborators.add(self.partner)

        self.movie1 = Item.objects.create(
            title="Movie Alpha",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="101",
        )
        self.movie2 = Item.objects.create(
            title="Movie Beta",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="102",
        )
        self.movie3 = Item.objects.create(
            title="Movie Gamma",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="103",
        )

        self.item1 = CustomListItem.objects.create(custom_list=self.custom_list, item=self.movie1)
        self.item2 = CustomListItem.objects.create(custom_list=self.custom_list, item=self.movie2)
        self.item3 = CustomListItem.objects.create(custom_list=self.custom_list, item=self.movie3)

    def test_toggle_watched_requires_authentication(self):
        url = reverse("toggle_list_item_watched", args=[self.custom_list.id, self.item1.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

    def test_toggle_watched_forbidden_for_stranger(self):
        self.client.force_login(self.stranger)
        url = reverse("toggle_list_item_watched", args=[self.custom_list.id, self.item1.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_toggle_watched_syncs_between_owner_and_partner(self):
        self.client.force_login(self.owner)
        url = reverse("toggle_list_item_watched", args=[self.custom_list.id, self.item1.id])

        # 1. Check off (abhaken): mark as completed
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)

        # Owner has completed Movie Alpha
        owner_m1 = Movie.objects.filter(user=self.owner, item=self.movie1).first()
        self.assertIsNotNone(owner_m1)
        self.assertEqual(owner_m1.status, Status.COMPLETED.value)
        self.assertIsNotNone(owner_m1.end_date)

        # Partner ALSO has completed Movie Alpha via collaborator sync!
        partner_m1 = Movie.objects.filter(user=self.partner, item=self.movie1).first()
        self.assertIsNotNone(partner_m1)
        self.assertEqual(partner_m1.status, Status.COMPLETED.value)
        self.assertIsNotNone(partner_m1.end_date)

        # 2. Toggle off: uncomplete back to Planning
        response2 = self.client.post(url)
        self.assertEqual(response2.status_code, 200)

        owner_m1.refresh_from_db()
        self.assertEqual(owner_m1.status, Status.PLANNING.value)
        self.assertIsNone(owner_m1.end_date)

        partner_m1.refresh_from_db()
        self.assertEqual(partner_m1.status, Status.PLANNING.value)
        self.assertIsNone(partner_m1.end_date)

    def test_status_tabs_filtering_and_counts(self):
        # Setup:
        # movie1: Completed for owner
        # movie2: In Progress for owner
        # movie3: Untracked (counts towards Planning / Noch nicht begonnen)
        Movie.objects.create(
            user=self.owner,
            item=self.movie1,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )
        Movie.objects.create(
            user=self.owner,
            item=self.movie2,
            status=Status.IN_PROGRESS.value,
        )

        self.client.force_login(self.owner)
        detail_url = reverse("list_detail", args=[self.custom_list.public_reference])

        # All tab
        resp_all = self.client.get(detail_url, {"status_tab": "all"})
        self.assertEqual(resp_all.status_code, 200)
        counts = resp_all.context["status_counts"]
        self.assertEqual(counts["all"], 3)
        self.assertEqual(counts["completed"], 1)
        self.assertEqual(counts["in_progress"], 1)
        self.assertEqual(counts["planning"], 1)  # movie3 untracked
        self.assertEqual(len(resp_all.context["items"]), 3)

        # In progress tab
        resp_prog = self.client.get(detail_url, {"status_tab": "in_progress"})
        self.assertEqual(resp_prog.status_code, 200)
        items_prog = [item.title for item in resp_prog.context["items"]]
        self.assertEqual(items_prog, [self.movie2.title])

        # Completed tab
        resp_comp = self.client.get(detail_url, {"status_tab": "completed"})
        self.assertEqual(resp_comp.status_code, 200)
        items_comp = [item.title for item in resp_comp.context["items"]]
        self.assertEqual(items_comp, [self.movie1.title])

        # Planning tab
        resp_plan = self.client.get(detail_url, {"status_tab": "planning"})
        self.assertEqual(resp_plan.status_code, 200)
        items_plan = [item.title for item in resp_plan.context["items"]]
        self.assertEqual(items_plan, [self.movie3.title])

    def test_completed_placement_partitions_items_at_bottom(self):
        # movie1: completed, title "Movie Alpha"
        # movie2: planning, title "Movie Beta"
        Movie.objects.create(
            user=self.owner,
            item=self.movie1,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )

        self.client.force_login(self.owner)
        detail_url = reverse("list_detail", args=[self.custom_list.public_reference])

        # With normal placement and title sort asc: Alpha (completed), Beta, Gamma
        resp_normal = self.client.get(
            detail_url,
            {"sort": "title", "direction": "asc", "completed_placement": "normal"},
        )
        titles_normal = [item.title for item in resp_normal.context["items"]]
        self.assertEqual(titles_normal, ["Movie Alpha", "Movie Beta", "Movie Gamma"])

        # With bottom placement and title sort asc: Beta, Gamma, Alpha (completed at bottom)
        resp_bottom = self.client.get(
            detail_url,
            {"sort": "title", "direction": "asc", "completed_placement": "bottom"},
        )
        titles_bottom = [item.title for item in resp_bottom.context["items"]]
        self.assertEqual(titles_bottom, ["Movie Beta", "Movie Gamma", "Movie Alpha"])

        # With bottom placement and status sort desc: completed at bottom
        resp_status_bottom = self.client.get(
            detail_url,
            {"sort": "status", "direction": "desc", "completed_placement": "bottom"},
        )
        titles_status_bottom = [item.title for item in resp_status_bottom.context["items"]]
        self.assertEqual(titles_status_bottom[-1], "Movie Alpha")

    def test_shared_list_presentation_synchronization_for_all_collaborators(self):
        # Owner customizes list presentation
        self.client.force_login(self.owner)
        detail_url = reverse("list_detail", args=[self.custom_list.public_reference])

        resp = self.client.get(
            detail_url,
            {
                "sort": "title",
                "direction": "asc",
                "layout": "table",
                "completed_placement": "bottom",
                "status_tab": "in_progress",
            },
        )
        self.assertEqual(resp.status_code, 200)

        # Settings are persisted on custom_list model
        self.custom_list.refresh_from_db()
        self.assertEqual(self.custom_list.default_sort, "title")
        self.assertEqual(self.custom_list.default_sort_direction, "asc")
        self.assertEqual(self.custom_list.default_layout, "table")
        self.assertEqual(self.custom_list.completed_placement, "bottom")
        self.assertEqual(self.custom_list.status_tab, "in_progress")

        # Now partner visits the list without query parameters
        self.client.force_login(self.partner)
        partner_resp = self.client.get(detail_url)
        self.assertEqual(partner_resp.status_code, 200)

        # Partner sees the EXACT SAME view configuration configured by the owner
        self.assertEqual(partner_resp.context["current_sort"], "title")
        self.assertEqual(partner_resp.context["current_direction"], "asc")
        self.assertEqual(partner_resp.context["current_layout"], "table")
        self.assertEqual(partner_resp.context["completed_placement"], "bottom")
        self.assertEqual(partner_resp.context["current_status_tab"], "in_progress")
