from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    name = "reviews"

    def ready(self) -> None:
        # pylint: disable=import-outside-toplevel
        from integrations import outbox
        from reviews import area_resolver, outbox_handlers

        outbox.register("resolve_areas", area_resolver.resolve_areas)
        outbox.register("set_reviewer", outbox_handlers.set_reviewer)
        outbox.register("notify", outbox_handlers.notify)
