from reviews.models import AreaReview, MergeRequest


def serialize_area_review(area_review: AreaReview) -> dict[str, object]:
    """Return one area review with its areas, reviewer, booked day and reason."""
    reviewer = area_review.reviewer
    booked_day = area_review.booked_day
    return {
        "id": area_review.pk,
        "state": area_review.state,
        "areas": [area.name for area in area_review.areas.all()],
        "reviewer": reviewer.name if reviewer else None,
        "booked_day": booked_day.isoformat() if booked_day else None,
        "reason": area_review.reason,
    }


def serialize_merge_request(merge_request: MergeRequest) -> dict[str, object]:
    """Return the MR state and its area reviews. Use prefetched area reviews."""
    return {
        "id": merge_request.pk,
        "project_id": merge_request.project_id,
        "iid": merge_request.iid,
        "state": merge_request.state,
        "area_reviews": [
            serialize_area_review(area_review)
            for area_review in merge_request.area_reviews.all()
        ],
    }
