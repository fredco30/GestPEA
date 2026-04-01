"""
app/admin.py
-------------
Enregistrement des modèles dans l'admin Django.
Auto-remplissage IA : seul le ticker est requis à l'ajout d'un titre.
"""

import logging

from django.contrib import admin

from app.models import (
    Titre, PrixJournalier, Fondamentaux,
    ScoreSentiment, Article, Signal,
    AlerteConfig, Alerte, ProfilInvestisseur, ApiQuota,
)

logger = logging.getLogger(__name__)


@admin.register(Titre)
class TitreAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'nom_court', 'place', 'secteur', 'statut', 'eligible_pea', 'lot', 'actif')
    list_filter = ('statut', 'eligible_pea', 'lot', 'actif', 'place')
    search_fields = ('ticker', 'nom', 'isin')
    ordering = ('ticker',)

    # Seul le ticker est requis dans le formulaire admin
    add_fieldsets = ('ticker',)

    def get_fields(self, request, obj=None):
        """Affiche tous les champs en édition, mais seulement le ticker à l'ajout."""
        if obj is None:
            # Mode ajout : seulement ticker + statut optionnel
            return ['ticker', 'statut', 'nb_actions', 'prix_revient_moyen', 'notes']
        return super().get_fields(request, obj)

    def save_model(self, request, obj, form, change):
        """
        À la création, auto-remplit les métadonnées via EODHD/FMP
        et crée une AlerteConfig avec seuils adaptés au secteur.
        """
        if not change:
            # Normaliser le ticker
            obj.ticker = obj.ticker.upper().strip()

            # Auto-remplissage IA
            from app.services.auto_fill import auto_remplir_titre, seuils_alerte_pour_secteur

            metadata = auto_remplir_titre(obj.ticker)
            for champ, valeur in metadata.items():
                if not getattr(obj, champ, None):
                    setattr(obj, champ, valeur)

            # Assigner le lot A/B en alternance
            nb_a = Titre.objects.filter(lot='A', actif=True).count()
            nb_b = Titre.objects.filter(lot='B', actif=True).count()
            obj.lot = 'A' if nb_a <= nb_b else 'B'

            super().save_model(request, obj, form, change)

            # Créer AlerteConfig avec seuils adaptés au secteur
            seuils = seuils_alerte_pour_secteur(obj.secteur)
            AlerteConfig.objects.get_or_create(
                titre=obj,
                defaults={
                    'score_min_declenchement': seuils['score_min'],
                    'seuil_drawdown': seuils['seuil_drawdown'],
                    'alertes_renforcement': True,
                    'ignorer_court_terme': True,
                }
            )

            # Lancer l'import historique OHLC
            from app.tasks import import_historique_task
            import_historique_task.delay(obj.ticker)

            auto_filled = list(metadata.keys())
            logger.info(
                "[Admin] Titre ajouté : %s — auto-fill: %s",
                obj.ticker, ', '.join(auto_filled)
            )
            self.message_user(
                request,
                f"✅ {obj.ticker} ajouté — {len(auto_filled)} champs auto-remplis "
                f"({', '.join(auto_filled)}). Import historique lancé."
            )
        else:
            super().save_model(request, obj, form, change)


@admin.register(PrixJournalier)
class PrixJournalierAdmin(admin.ModelAdmin):
    list_display = ('titre', 'date', 'cloture', 'volume', 'rsi_14')
    list_filter = ('titre',)
    date_hierarchy = 'date'
    ordering = ('-date',)


@admin.register(Fondamentaux)
class FondamentauxAdmin(admin.ModelAdmin):
    list_display = ('titre', 'date_maj', 'per', 'roe', 'rendement_dividende', 'score_qualite')
    list_filter = ('source',)
    ordering = ('-date_maj',)


@admin.register(ScoreSentiment)
class ScoreSentimentAdmin(admin.ModelAdmin):
    list_display = ('titre', 'date', 'source', 'score', 'nb_articles')
    list_filter = ('source',)
    date_hierarchy = 'date'
    ordering = ('-date',)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ('titre', 'titre_art', 'source', 'date_pub', 'score_sentiment')
    list_filter = ('source',)
    search_fields = ('titre_art',)
    ordering = ('-date_pub',)


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = ('titre', 'date', 'type_signal', 'direction', 'actif')
    list_filter = ('type_signal', 'direction', 'actif')
    ordering = ('-date',)


@admin.register(AlerteConfig)
class AlerteConfigAdmin(admin.ModelAdmin):
    list_display = ('titre', 'actif', 'score_min_declenchement', 'notif_email', 'notif_telegram')
    list_filter = ('actif',)


@admin.register(Alerte)
class AlerteAdmin(admin.ModelAdmin):
    list_display = ('titre', 'date_detection', 'score_confluence', 'niveau', 'statut')
    list_filter = ('niveau', 'statut')
    date_hierarchy = 'date_detection'
    ordering = ('-date_detection',)


@admin.register(ProfilInvestisseur)
class ProfilInvestisseurAdmin(admin.ModelAdmin):
    list_display = ('enveloppe', 'style', 'tolerance_risque', 'poids_fondamentaux', 'poids_technique')


@admin.register(ApiQuota)
class ApiQuotaAdmin(admin.ModelAdmin):
    list_display = ('api', 'date', 'nb_requetes')
    list_filter = ('api',)
    date_hierarchy = 'date'
    ordering = ('-date',)
