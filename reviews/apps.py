from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    name = "reviews"

    def ready(self) -> None:
        # pylint: disable=import-outside-toplevel
        from integrations import outbox
        from reviews import ai_gate, area_resolver, coverage, outbox_handlers

        outbox.register("check_ai_review", ai_gate.check_ai_review)
        outbox.register("check_coverage", coverage.check_coverage)
        outbox.register("resolve_areas", area_resolver.resolve_areas)
        outbox.register("set_reviewer", outbox_handlers.set_reviewer)
        outbox.register("notify", outbox_handlers.notify)
