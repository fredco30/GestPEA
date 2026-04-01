"""
app/api/serializers.py
-----------------------
Serializers Django REST Framework pour tous les modèles du projet PEA.

Organisation :
  - Serializers légers (liste) : utilisés dans les vues tableau/liste
  - Serializers détaillés (detail) : utilisés dans les vues fiche complète
  - Serializers d'écriture : pour les POST/PATCH (création/modification)
"""

from rest_framework import serializers
from app.models import (
    Titre, PrixJournalier, Fondamentaux,
    ScoreSentiment, Article, Signal,
    AlerteConfig, Alerte, ProfilInvestisseur, ApiQuota,
    DocumentTitre,
)


# ---------------------------------------------------------------------------
# PRIX JOURNALIER
# ---------------------------------------------------------------------------

class PrixJournalierSerializer(serializers.ModelSerializer):
    """Bougie OHLCV + indicateurs — utilisé pour le graphique Lightweight Charts."""
    variation_pct = serializers.ReadOnlyField()

    class Meta:
        model  = PrixJournalier
        fields = [
            'date', 'ouverture', 'haut', 'bas', 'cloture', 'volume',
            'variation_pct',
            # Indicateurs techniques
            'rsi_14', 'macd', 'macd_signal', 'macd_hist',
            'mm_20', 'mm_50', 'mm_200',
            'boll_sup', 'boll_mid', 'boll_inf',
            'volume_ratio',
        ]


class PrixJournalierOHLCSerializer(serializers.ModelSerializer):
    """
    Format compact OHLCV pour Lightweight Charts.
    Lightweight Charts attend : {time, open, high, low, close, value (volume)}
    """
    time  = serializers.DateField(source='date')
    open  = serializers.DecimalField(source='ouverture', max_digits=12, decimal_places=4)
    high  = serializers.DecimalField(source='haut',      max_digits=12, decimal_places=4)
    low   = serializers.DecimalField(source='bas',       max_digits=12, decimal_places=4)
    close = serializers.DecimalField(source='cloture',   max_digits=12, decimal_places=4)
    value = serializers.IntegerField(source='volume')

    class Meta:
        model  = PrixJournalier
        fields = ['time', 'open', 'high', 'low', 'close', 'value']


# ---------------------------------------------------------------------------
# FONDAMENTAUX
# ---------------------------------------------------------------------------

class FondamentauxSerializer(serializers.ModelSerializer):
    score_qualite = serializers.ReadOnlyField()

    class Meta:
        model  = Fondamentaux
        fields = [
            'date_maj', 'source',
            # Valorisation
            'per', 'per_forward', 'peg', 'p_book', 'ev_ebitda', 'capitalisation',
            # Rentabilité
            'roe', 'roa', 'marge_nette', 'marge_operationnelle',
            # Bilan
            'dette_nette_ebitda', 'couverture_interets', 'cash_flow_libre',
            # Croissance
            'croissance_bpa_1an', 'croissance_bpa_3ans', 'croissance_ca_1an',
            # Dividende
            'rendement_dividende', 'dividende_par_action', 'payout_ratio',
            'date_ex_dividende', 'date_paiement',
            # Analystes
            'objectif_cours_moyen', 'nb_analystes', 'consensus',
            # Calculé
            'score_qualite',
            # Analyse IA
            'analyse_ia',
        ]


# ---------------------------------------------------------------------------
# SENTIMENT
# ---------------------------------------------------------------------------

class ScoreSentimentSerializer(serializers.ModelSerializer):
    label   = serializers.ReadOnlyField()
    couleur = serializers.ReadOnlyField()

    class Meta:
        model  = ScoreSentiment
        fields = [
            'date', 'source', 'score', 'label', 'couleur',
            'nb_articles', 'score_min', 'score_max',
            'variation_24h', 'resume_ia',
        ]


class ArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Article
        fields = [
            'id', 'date_pub', 'source', 'url',
            'titre_art', 'extrait', 'auteur',
            'score_sentiment', 'tags',
        ]


# ---------------------------------------------------------------------------
# SIGNAUX
# ---------------------------------------------------------------------------

class SignalSerializer(serializers.ModelSerializer):
    type_signal_display = serializers.CharField(
        source='get_type_signal_display', read_only=True
    )

    class Meta:
        model  = Signal
        fields = [
            'id', 'date', 'type_signal', 'type_signal_display',
            'direction', 'valeur', 'description', 'actif',
        ]


# ---------------------------------------------------------------------------
# TITRE — plusieurs niveaux de détail
# ---------------------------------------------------------------------------

class TitreListSerializer(serializers.ModelSerializer):
    """
    Serializer léger pour la liste des titres (onglet surveillance / tableau).
    Inclut le dernier cours et le dernier score sentiment.
    """
    dernier_cours     = serializers.SerializerMethodField()
    variation_jour    = serializers.SerializerMethodField()
    sentiment_global  = serializers.SerializerMethodField()
    valeur_position   = serializers.ReadOnlyField()
    plus_moins_value  = serializers.ReadOnlyField()

    class Meta:
        model  = Titre
        fields = [
            'id', 'ticker', 'nom', 'nom_court', 'place', 'pays',
            'secteur', 'statut', 'eligible_pea',
            'nb_actions', 'prix_revient_moyen',
            'dernier_cours', 'variation_jour',
            'sentiment_global', 'valeur_position', 'plus_moins_value',
            'score_conviction',
        ]

    def get_dernier_cours(self, obj):
        p = obj.prix_journaliers.order_by('-date').first()
        if p:
            return {'date': str(p.date), 'cloture': float(p.cloture), 'rsi_14': float(p.rsi_14) if p.rsi_14 else None}
        return None

    def get_variation_jour(self, obj):
        p = obj.prix_journaliers.order_by('-date').first()
        return p.variation_pct if p else None

    def get_sentiment_global(self, obj):
        s = obj.scores_sentiment.filter(source='global').order_by('-date').first()
        if s:
            return {'score': float(s.score), 'label': s.label, 'couleur': s.couleur}
        return None


class TitreDetailSerializer(serializers.ModelSerializer):
    """
    Serializer complet pour la fiche d'un titre (onglet portefeuille).
    Contient les 90 dernières bougies, les fondamentaux, les scores sentiment sur 30j.
    """
    prix_historique  = serializers.SerializerMethodField()
    fondamentaux     = serializers.SerializerMethodField()
    sentiments_30j   = serializers.SerializerMethodField()
    signaux_actifs   = serializers.SerializerMethodField()
    alertes_recentes = serializers.SerializerMethodField()
    articles_recents = serializers.SerializerMethodField()
    valeur_position  = serializers.ReadOnlyField()
    plus_moins_value = serializers.ReadOnlyField()

    class Meta:
        model  = Titre
        fields = [
            'id', 'ticker', 'isin', 'nom', 'nom_court', 'place', 'pays',
            'secteur', 'sous_secteur', 'statut', 'eligible_pea', 'lot',
            'nb_actions', 'prix_revient_moyen', 'date_premier_achat',
            'valeur_position', 'plus_moins_value',
            'prix_historique', 'fondamentaux',
            'sentiments_30j', 'signaux_actifs',
            'alertes_recentes', 'articles_recents',
            'notes',
            'score_conviction', 'explication_conviction', 'date_calcul_conviction',
        ]

    def get_prix_historique(self, obj):
        """90 dernières bougies au format Lightweight Charts."""
        bougies = obj.prix_journaliers.order_by('-date')[:90]
        # Remettre dans l'ordre chronologique pour le graphique
        return PrixJournalierSerializer(
            reversed(list(bougies)), many=True
        ).data

    def get_fondamentaux(self, obj):
        f = obj.fondamentaux.order_by('-date_maj').first()
        return FondamentauxSerializer(f).data if f else None

    def get_sentiments_30j(self, obj):
        """Score sentiment global des 30 derniers jours (pour le graphique)."""
        from datetime import date, timedelta
        depuis = date.today() - timedelta(days=30)
        qs = obj.scores_sentiment.filter(
            source='global', date__gte=depuis
        ).order_by('date')
        return ScoreSentimentSerializer(qs, many=True).data

    def get_signaux_actifs(self, obj):
        """Signaux techniques actifs du jour."""
        from datetime import date
        qs = obj.signaux.filter(date=date.today(), actif=True)
        return SignalSerializer(qs, many=True).data

    def get_alertes_recentes(self, obj):
        """5 dernières alertes pour ce titre."""
        qs = obj.alertes.order_by('-date_detection')[:5]
        return AlerteListSerializer(qs, many=True).data

    def get_articles_recents(self, obj):
        """10 derniers articles scorés."""
        qs = obj.articles.filter(
            score_sentiment__isnull=False
        ).order_by('-date_pub')[:10]
        return ArticleSerializer(qs, many=True).data


class TitreCreateSerializer(serializers.ModelSerializer):
    """
    Serializer pour l'ajout d'un nouveau titre.
    Seul le ticker est obligatoire — les autres champs sont auto-remplis
    par le service auto_fill via EODHD/FMP.
    """

    class Meta:
        model  = Titre
        fields = [
            'ticker', 'nom', 'nom_court', 'place', 'pays',
            'secteur', 'statut', 'nb_actions', 'prix_revient_moyen',
            'date_premier_achat', 'notes',
        ]
        extra_kwargs = {
            'nom':  {'required': False, 'default': ''},
            'pays': {'required': False, 'default': ''},
        }

    def validate_ticker(self, value):
        """Normalise le ticker en majuscules (la résolution ISIN/nom se fait dans la vue)."""
        return value.upper().strip()

    def create(self, validated_data):
        """
        À la création, assigne automatiquement le lot A ou B
        en alternance pour équilibrer la rotation.
        """
        nb_a = Titre.objects.filter(lot='A', actif=True).count()
        nb_b = Titre.objects.filter(lot='B', actif=True).count()
        validated_data['lot'] = 'A' if nb_a <= nb_b else 'B'
        return super().create(validated_data)


# ---------------------------------------------------------------------------
# ALERTES
# ---------------------------------------------------------------------------

class AlerteListSerializer(serializers.ModelSerializer):
    """Serializer léger pour la liste des alertes."""
    ticker      = serializers.CharField(source='titre.ticker', read_only=True)
    nom_court   = serializers.CharField(source='titre.nom_court', read_only=True)
    disclaimer  = serializers.ReadOnlyField()

    class Meta:
        model  = Alerte
        fields = [
            'id', 'ticker', 'nom_court',
            'date_detection', 'date_signal',
            'score_confluence', 'niveau', 'statut',
            'cours_au_signal', 'rsi_au_signal', 'sentiment_au_signal',
            'fiabilite_historique', 'nb_occurrences_passees',
            'texte_ia', 'disclaimer',
        ]


class AlerteDetailSerializer(serializers.ModelSerializer):
    """Serializer complet avec les signaux ayant déclenché l'alerte."""
    ticker     = serializers.CharField(source='titre.ticker', read_only=True)
    nom        = serializers.CharField(source='titre.nom', read_only=True)
    signaux    = SignalSerializer(many=True, read_only=True)
    disclaimer = serializers.ReadOnlyField()

    class Meta:
        model  = Alerte
        fields = [
            'id', 'ticker', 'nom',
            'date_detection', 'date_signal',
            'score_confluence', 'niveau', 'statut',
            'cours_au_signal', 'rsi_au_signal', 'sentiment_au_signal',
            'signaux',
            'texte_ia', 'disclaimer',
            'fiabilite_historique', 'nb_occurrences_passees',
            'note_utilisateur',
        ]


class AlerteStatutSerializer(serializers.ModelSerializer):
    """Pour PATCH statut + note uniquement."""
    class Meta:
        model  = Alerte
        fields = ['statut', 'note_utilisateur']


# ---------------------------------------------------------------------------
# ALERTE CONFIG
# ---------------------------------------------------------------------------

class AlerteConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model  = AlerteConfig
        fields = [
            'actif', 'score_min_declenchement',
            'notif_app', 'notif_email', 'notif_telegram', 'notif_webhook',
            'webhook_url',
            'ignorer_court_terme', 'alertes_renforcement', 'seuil_drawdown',
        ]


# ---------------------------------------------------------------------------
# PROFIL INVESTISSEUR
# ---------------------------------------------------------------------------

class ProfilInvestisseurSerializer(serializers.ModelSerializer):
    capacite_versement_restante = serializers.ReadOnlyField()
    fiscalite_pleine            = serializers.ReadOnlyField()
    tolerance_risque_display    = serializers.CharField(
        source='get_tolerance_risque_display', read_only=True
    )

    class Meta:
        model  = ProfilInvestisseur
        fields = [
            'enveloppe', 'plafond_versements', 'versements_effectues',
            'capacite_versement_restante',
            'date_ouverture', 'fiscalite_pleine',
            'horizon_min_ans', 'horizon_max_ans',
            'style', 'tolerance_risque', 'tolerance_risque_display',
            'poids_fondamentaux', 'poids_technique',
            'ignorer_signaux_court_terme', 'mode_accumulation',
            'seuil_drawdown_alerte', 'digest_hebdomadaire',
            'pays_eligibles_pea',
        ]

    def validate(self, data):
        pf = data.get('poids_fondamentaux', self.instance.poids_fondamentaux if self.instance else 60)
        pt = data.get('poids_technique',    self.instance.poids_technique    if self.instance else 40)
        if pf + pt != 100:
            raise serializers.ValidationError(
                "poids_fondamentaux + poids_technique doit être égal à 100."
            )
        return data


# ---------------------------------------------------------------------------
# QUOTA API
# ---------------------------------------------------------------------------

class ApiQuotaSerializer(serializers.ModelSerializer):
    restantes = serializers.ReadOnlyField()
    pct_utilise = serializers.SerializerMethodField()

    class Meta:
        model  = ApiQuota
        fields = ['api', 'date', 'nb_requetes', 'restantes', 'pct_utilise']

    def get_pct_utilise(self, obj):
        limites = {'eodhd': 20, 'fmp': 250, 'newsapi': 100}
        limite = limites.get(obj.api, 1)
        return round(obj.nb_requetes / limite * 100, 1)


# ---------------------------------------------------------------------------
# DASHBOARD — serializer agrégé pour la vue d'accueil
# ---------------------------------------------------------------------------

class DashboardSerializer(serializers.Serializer):
    """
    Données agrégées pour la page d'accueil du dashboard.
    Calculé dans la view, pas directement lié à un modèle.
    """
    # Portefeuille
    valeur_totale_portefeuille = serializers.DecimalField(
        max_digits=12, decimal_places=2, allow_null=True
    )
    variation_jour_portefeuille = serializers.DecimalField(
        max_digits=8, decimal_places=2, allow_null=True
    )
    nb_titres_portefeuille = serializers.IntegerField()
    nb_titres_surveillance = serializers.IntegerField()

    # Alertes
    nb_alertes_nouvelles  = serializers.IntegerField()
    nb_alertes_fortes     = serializers.IntegerField()
    derniere_alerte       = AlerteListSerializer(allow_null=True)

    # Sentiment global portefeuille
    sentiment_portefeuille_moyen = serializers.DecimalField(
        max_digits=4, decimal_places=3, allow_null=True
    )

    # Quota API du jour
    quotas = ApiQuotaSerializer(many=True)


# ---------------------------------------------------------------------------
# DOCUMENTS
# ---------------------------------------------------------------------------

class DocumentTitreSerializer(serializers.ModelSerializer):
    url_fichier   = serializers.SerializerMethodField()
    type_doc_display = serializers.CharField(source='get_type_doc_display', read_only=True)

    class Meta:
        model  = DocumentTitre
        fields = [
            'id', 'nom', 'type_doc', 'type_doc_display', 'taille',
            'resume_ia', 'date_upload', 'notes', 'url_fichier',
        ]

    def get_url_fichier(self, obj):
        if obj.fichier:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.fichier.url)
            return obj.fichier.url
        return None
