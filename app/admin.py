"""
app/admin.py
-------------
Enregistrement des modèles dans l'admin Django.
"""

from django.contrib import admin

from app.models import (
    Titre, PrixJournalier, Fondamentaux,
    ScoreSentiment, Article, Signal,
    AlerteConfig, Alerte, ProfilInvestisseur, ApiQuota,
)


@admin.register(Titre)
class TitreAdmin(admin.ModelAdmin):
    list_display = ('ticker', 'nom_court', 'place', 'secteur', 'statut', 'eligible_pea', 'lot', 'actif')
    list_filter = ('statut', 'eligible_pea', 'lot', 'actif', 'place')
    search_fields = ('ticker', 'nom', 'isin')
    ordering = ('ticker',)


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
