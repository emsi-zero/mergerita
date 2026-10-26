from unittest import mock

from django.test import TestCase, override_settings

from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from reviews import area_resolver
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
)


class AreaResolverTest(TestCase):
    def setUp(self) -> None:
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.merge_request = MergeRequest.objects.create(
            project_id=7,
            iid=3,
            author=author,
            title="MR",
            state=MergeRequestState.AWAITING_BOOKING,
        )
        self.api = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=500
        )
        self.web = CodeArea.objects.create(
            name="web", path_patterns=["web/*"], size_limit_lines=500
        )
        gateway = FakeGitLabGateway()
        gateway.changes[(7, 3)] = Changes(
            files=["api/a.py", "web/b.js"], changed_lines=9
        )
        patcher = mock.patch.object(
            area_resolver, "get_gitlab_gateway", return_value=gateway
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def own(self, gitlab_user_id: int, *areas: CodeArea) -> None:
        person, _ = Person.objects.get_or_create(
            gitlab_user_id=gitlab_user_id, defaults={"name": f"P{gitlab_user_id}"}
        )
        for area in areas:
            Ownership.objects.create(person=person, area=area, role=OwnerRole.PRIMARY)

    @override_settings(RMO_COVERAGE_POLICY="strict")
    def test_strict_policy_creates_one_review_per_area(self) -> None:
        """RMO-9: Given the strict policy and an MR touching two areas, when
        areas are resolved, then there is one area review per touched area."""
        # given
        self.own(21, self.api, self.web)

        # when
        area_resolver.resolve_areas({"merge_request_id": self.merge_request.pk})

        # then
        reviews = AreaReview.objects.prefetch_related("areas")
        self.assertEqual(reviews.count(), 2)
        self.assertEqual(
            sorted(review.areas.get().name for review in reviews), ["api", "web"]
        )

    @override_settings(RMO_COVERAGE_POLICY="combined")
    def test_combined_policy_needs_one_owner_of_all_areas(self) -> None:
        """RMO-10: Given the combined policy, when one person who is not the author owns
        every touched area, then one review covers all areas; otherwise it falls back to strict.
        """
        for name, owners, expected_reviews in (
            ("one_owner_of_all", ((21, (0, 1)),), 1),
            ("owners_split", ((21, (0,)), (22, (1,))), 2),
            ("only_owner_is_the_author", ((11, (0, 1)),), 2),
        ):
            with self.subTest(name=name):
                # given
                areas = (self.api, self.web)
                AreaReview.objects.all().delete()
                MergeRequest.objects.update(state=MergeRequestState.AWAITING_BOOKING)
                Ownership.objects.all().delete()
                for gitlab_user_id, indexes in owners:
                    self.own(gitlab_user_id, *(areas[i] for i in indexes))

                # when
                area_resolver.resolve_areas({"merge_request_id": self.merge_request.pk})

                # then
                self.assertEqual(AreaReview.objects.count(), expected_reviews)
                self.assertEqual(
                    AreaReview.objects.first().areas.count(), 2 // expected_reviews
                )
