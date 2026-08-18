from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_add_cloture_veille"),
    ]

    operations = [
        migrations.AddField(
            model_name="titre",
            name="devise",
            field=models.CharField(
                default="EUR",
                help_text="Devise de cotation : EUR, USD, GBP, GBX… (auto-remplie via EODHD)",
                max_length=4,
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="compte",
            field=models.CharField(
                choices=[("pea", "PEA"), ("cto", "Compte-titres (CTO)")],
                default="pea",
                help_text="Enveloppe de détention : PEA (UE/EEE) ou CTO (US, hors-PEA)",
                max_length=4,
            ),
        ),
        migrations.CreateModel(
            name="TauxChange",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "devise",
                    models.CharField(
                        help_text="Code devise source : USD, GBP, GBX…", max_length=4
                    ),
                ),
                ("date", models.DateField()),
                (
                    "taux_vers_eur",
                    models.DecimalField(
                        decimal_places=8,
                        help_text="1 unité de `devise` = X EUR",
                        max_digits=16,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        default="eodhd",
                        help_text="eodhd | fallback | manuel",
                        max_length=20,
                    ),
                ),
                ("date_maj", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Taux de change",
                "verbose_name_plural": "Taux de change",
                "ordering": ["-date"],
            },
        ),
        migrations.AddIndex(
            model_name="titre",
            index=models.Index(fields=["compte"], name="titre_compte_idx"),
        ),
        migrations.AddIndex(
            model_name="tauxchange",
            index=models.Index(
                fields=["devise", "date"], name="tauxchange_dev_date_idx"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="tauxchange",
            unique_together={("devise", "date")},
        ),
    ]
