import decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_titre_tradingagents"),
    ]

    operations = [
        migrations.AddField(
            model_name="titre",
            name="score_documents",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Impact global des documents : -1 (négatif) à +1 (positif)",
                max_digits=4,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(decimal.Decimal("-1")),
                    django.core.validators.MaxValueValidator(decimal.Decimal("1")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="analyse_documents_ia",
            field=models.TextField(
                blank=True,
                help_text="Synthèse IA de l'impact des documents uploadés",
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="date_score_documents",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
