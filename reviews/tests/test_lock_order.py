import threading

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from reviews import outcomes
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeRequest,
    MergeRequestState,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
)

THREAD_TIMEOUT_SECONDS = 20


class LockOrderTest(TransactionTestCase):
    def test_inv_6_done_and_a_writer_holding_the_area_review_do_not_deadlock(
        self,
    ) -> None:
        """INV-6: Given a writer that holds an area review lock and then writes
        its session, when the reviewer presses Done, then both finish with no
        database error.
        """
        # given
        author = Person.objects.create(gitlab_user_id=1, name="Author")
        reviewer = Person.objects.create(gitlab_user_id=2, name="Reviewer")
        merge_request = MergeRequest.objects.create(
            state=MergeRequestState.IN_REVIEW,
            title="Lock order",
            iid=1,
            project_id=7,
            author=author,
        )
        area_review = AreaReview.objects.create(
            merge_request=merge_request,
            state=AreaReviewState.BOOKED,
            reviewer=reviewer,
            booked_day=timezone.localdate(),
        )
        session = ReviewSession.objects.create(
            area_review=area_review,
            reviewer=reviewer,
            day=timezone.localdate(),
            slot_minutes=30,
        )
        holding = threading.Event()
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                with transaction.atomic():
                    AreaReview.objects.select_for_update().get(pk=area_review.pk)
                    holding.set()
                    done_started.wait(timeout=5)
                    threading.Event().wait(0.5)
                    ReviewSession.objects.filter(pk=session.pk).update(
                        outcome=ReviewSessionOutcome.SUPERSEDED
                    )
            except BaseException as error:  # pylint: disable=broad-exception-caught
                errors.append(error)
            finally:
                close_old_connections()

        def press_done() -> None:
            try:
                holding.wait(timeout=5)
                done_started.set()
                outcomes.mark_done(reviewer, session.pk, "reviewer")
            except ReviewSession.DoesNotExist:
                pass  # the writer's outcome came first: no deadlock either
            except BaseException as error:  # pylint: disable=broad-exception-caught
                errors.append(error)
            finally:
                close_old_connections()

        done_started = threading.Event()
        threads = [threading.Thread(target=writer), threading.Thread(target=press_done)]

        # when
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT_SECONDS)

        # then
        self.assertEqual(errors, [])
