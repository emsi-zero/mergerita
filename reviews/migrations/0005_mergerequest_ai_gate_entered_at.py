import django.utils.timezone
from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Now


def backfill_from_opened_at(apps, schema_editor) -> None:
    apps.get_model("reviews", "MergeRequest").objects.update(
        ai_gate_entered_at=F("opened_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0004_person_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="mergerequest",
            name="ai_gate_entered_at",
            field=models.DateTimeField(
                db_default=Now(), default=django.utils.timezone.now
            ),
        ),
        migrations.RunPython(backfill_from_opened_at, migrations.RunPython.noop),
    ]
