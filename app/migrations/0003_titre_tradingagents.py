from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0002_titre_devise_compte_tauxchange"),
    ]

    operations = [
        migrations.AddField(
            model_name="titre",
            name="ta_note",
            field=models.CharField(
                blank=True,
                help_text="Note TradingAgents : Buy/Overweight/Hold/Underweight/Sell",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="ta_rapport",
            field=models.TextField(
                blank=True,
                help_text="Synthèse française de l'analyse multi-agents TradingAgents",
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="ta_statut",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "—"),
                    ("en_cours", "En cours"),
                    ("termine", "Terminé"),
                    ("erreur", "Erreur"),
                ],
                default="",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="titre",
            name="ta_date_analyse",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
