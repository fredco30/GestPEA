from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
import decimal


# ---------------------------------------------------------------------------
# RÉFÉRENTIEL TITRES
# ---------------------------------------------------------------------------

class Titre(models.Model):
    """
    Un titre financier éligible (ou non) au PEA.
    Chaque titre appartient à un lot (A ou B) pour la rotation Celery.
    """

    LOT_CHOICES = [('A', 'Lot A — lundi/mercredi'), ('B', 'Lot B — mardi/jeudi')]

    STATUT_CHOICES = [
        ('portefeuille', 'En portefeuille'),
        ('surveillance', 'En surveillance'),
        ('archive',      'Archivé'),
    ]

    # Identification
    ticker       = models.CharField(max_length=20, unique=True, help_text="Ex: MC.PA, AI.PA, TTE.PA")
    isin         = models.CharField(max_length=12, blank=True)
    nom          = models.CharField(max_length=120, blank=True, help_text="Auto-rempli via EODHD si vide")
    nom_court    = models.CharField(max_length=20, blank=True, help_text="Ex: LVMH, Air Liquide")

    # Place boursière
    place        = models.CharField(max_length=20, blank=True, help_text="Ex: EPA, AMS, XETRA")
    pays         = models.CharField(max_length=3, blank=True, help_text="Code ISO-3 : FRA, DEU, NLD… (auto-rempli)")
    secteur      = models.CharField(max_length=80, blank=True)
    sous_secteur = models.CharField(max_length=80, blank=True)

    # Éligibilité PEA (mise à jour par le screener mensuel)
    eligible_pea          = models.BooleanField(default=False)
    date_verif_eligibilite = models.DateField(null=True, blank=True)

    # Statut dans l'app
    statut       = models.CharField(max_length=20, choices=STATUT_CHOICES, default='surveillance')
    lot          = models.CharField(max_length=1, choices=LOT_CHOICES, default='A',
                                    help_text="Lot de rotation pour les appels API fondamentaux")

    # Portefeuille PEA
    nb_actions         = models.PositiveIntegerField(default=0)
    prix_revient_moyen = models.DecimalField(max_digits=10, decimal_places=4,
                                             null=True, blank=True)
    date_premier_achat = models.DateField(null=True, blank=True)

    # Métadonnées
    actif        = models.BooleanField(default=True)
    date_ajout   = models.DateTimeField(auto_now_add=True)
    date_maj     = models.DateTimeField(auto_now=True)
    notes        = models.TextField(blank=True)

    class Meta:
        ordering = ['nom']
        verbose_name = 'Titre'
        indexes = [
            models.Index(fields=['statut']),
            models.Index(fields=['lot']),
            models.Index(fields=['eligible_pea']),
        ]

    def __str__(self):
        return f"{self.ticker} — {self.nom_court or self.nom}"

    @property
    def valeur_position(self):
        """Valeur actuelle de la position (nb actions × dernier cours)."""
        dernier = self.prix_journaliers.order_by('-date').first()
        if dernier and self.nb_actions:
            return self.nb_actions * dernier.cloture
        return None

    @property
    def plus_moins_value(self):
        """PV/MV latente en euros."""
        vp = self.valeur_position
        if vp and self.prix_revient_moyen and self.nb_actions:
            return vp - (self.nb_actions * self.prix_revient_moyen)
        return None


# ---------------------------------------------------------------------------
# PRIX JOURNALIERS (OHLCV)
# ---------------------------------------------------------------------------

class PrixJournalier(models.Model):
    """
    Bougie journalière OHLCV pour un titre.
    Alimentée chaque soir par la tâche Celery fetch_cours_eod (18h30).
    L'historique complet est importé en bulk au premier lancement.
    """

    titre    = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                  related_name='prix_journaliers')
    date     = models.DateField()

    ouverture = models.DecimalField(max_digits=12, decimal_places=4)
    haut      = models.DecimalField(max_digits=12, decimal_places=4)
    bas       = models.DecimalField(max_digits=12, decimal_places=4)
    cloture   = models.DecimalField(max_digits=12, decimal_places=4)
    volume    = models.BigIntegerField(default=0)

    # Indicateurs techniques pré-calculés (remplis par calculate_indicators)
    rsi_14       = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    macd         = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    macd_signal  = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    macd_hist    = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    mm_20        = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    mm_50        = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    mm_200       = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    boll_sup     = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    boll_mid     = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    boll_inf     = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    volume_ratio = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                        help_text="Volume / moyenne volume 20j")

    date_calcul_indicateurs = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('titre', 'date')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['titre', 'date']),
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"{self.titre.ticker} {self.date} — clôture {self.cloture}"

    @property
    def variation_pct(self):
        """Variation % entre ouverture et clôture du jour."""
        if self.ouverture and self.ouverture != 0:
            return round(float((self.cloture - self.ouverture) / self.ouverture * 100), 2)
        return None


# ---------------------------------------------------------------------------
# FONDAMENTAUX
# ---------------------------------------------------------------------------

class Fondamentaux(models.Model):
    """
    Données fondamentales d'un titre, rafraîchies 2x/semaine (rotation lots A/B).
    Source : EODHD + FMP en complément.
    """

    titre            = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                          related_name='fondamentaux')
    date_maj         = models.DateField()

    # Valorisation
    per              = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                           help_text="Price/Earnings Ratio")
    per_forward      = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    peg              = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    p_book           = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    ev_ebitda        = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    capitalisation   = models.BigIntegerField(null=True, blank=True, help_text="En euros")

    # Rentabilité
    roe              = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                           help_text="Return on Equity %")
    roa              = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    marge_nette      = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                           help_text="%")
    marge_operationnelle = models.DecimalField(max_digits=6, decimal_places=2,
                                                null=True, blank=True, help_text="%")

    # Solidité bilan
    dette_nette_ebitda = models.DecimalField(max_digits=6, decimal_places=2,
                                              null=True, blank=True,
                                              help_text="Ratio dette nette / EBITDA")
    couverture_interets = models.DecimalField(max_digits=8, decimal_places=2,
                                               null=True, blank=True)
    cash_flow_libre  = models.BigIntegerField(null=True, blank=True, help_text="FCF en euros")

    # Croissance
    croissance_bpa_1an  = models.DecimalField(max_digits=6, decimal_places=2,
                                               null=True, blank=True, help_text="%")
    croissance_bpa_3ans = models.DecimalField(max_digits=6, decimal_places=2,
                                               null=True, blank=True, help_text="% annualisé")
    croissance_ca_1an   = models.DecimalField(max_digits=6, decimal_places=2,
                                               null=True, blank=True, help_text="%")

    # Dividende
    rendement_dividende  = models.DecimalField(max_digits=5, decimal_places=2,
                                                null=True, blank=True, help_text="%")
    dividende_par_action = models.DecimalField(max_digits=8, decimal_places=4,
                                                null=True, blank=True)
    payout_ratio         = models.DecimalField(max_digits=5, decimal_places=2,
                                                null=True, blank=True, help_text="%")
    date_ex_dividende    = models.DateField(null=True, blank=True)
    date_paiement        = models.DateField(null=True, blank=True)

    # Analystes
    objectif_cours_moyen = models.DecimalField(max_digits=10, decimal_places=4,
                                                null=True, blank=True)
    nb_analystes         = models.PositiveSmallIntegerField(null=True, blank=True)
    consensus            = models.CharField(max_length=20, blank=True,
                                            help_text="Buy / Hold / Sell")

    # Source
    source = models.CharField(max_length=20, default='eodhd',
                               help_text="eodhd | fmp | manuel")

    class Meta:
        unique_together = ('titre', 'date_maj')
        ordering = ['-date_maj']
        get_latest_by = 'date_maj'

    def __str__(self):
        return f"Fondamentaux {self.titre.ticker} — {self.date_maj}"

    @property
    def score_qualite(self):
        """
        Score de qualité fondamentale simplifié sur 10.
        Utilisé dans le moteur de confluence (pondération 60%).
        """
        score = 0
        points = 0

        if self.roe is not None:
            points += 1
            if self.roe > 15:
                score += 2
            elif self.roe > 10:
                score += 1

        if self.dette_nette_ebitda is not None:
            points += 1
            if self.dette_nette_ebitda < 1.5:
                score += 2
            elif self.dette_nette_ebitda < 3:
                score += 1

        if self.croissance_bpa_3ans is not None:
            points += 1
            if self.croissance_bpa_3ans > 10:
                score += 2
            elif self.croissance_bpa_3ans > 5:
                score += 1

        if self.marge_nette is not None:
            points += 1
            if self.marge_nette > 15:
                score += 2
            elif self.marge_nette > 8:
                score += 1

        if self.rendement_dividende is not None and self.payout_ratio is not None:
            points += 1
            if 1.5 <= float(self.rendement_dividende) <= 6 and float(self.payout_ratio) < 75:
                score += 2
            elif self.rendement_dividende > 0:
                score += 1

        if points == 0:
            return None
        return round(score / (points * 2) * 10, 1)


# ---------------------------------------------------------------------------
# SENTIMENT
# ---------------------------------------------------------------------------

class ScoreSentiment(models.Model):
    """
    Score de sentiment calculé par le LLM (Claude API) pour un titre,
    sur une fenêtre de temps donnée.
    Alimenté chaque soir par fetch_news + scoring IA.
    """

    SOURCE_CHOICES = [
        ('presse',   'Presse financière'),
        ('social',   'Réseaux sociaux'),
        ('global',   'Score global pondéré'),
    ]

    titre     = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                   related_name='scores_sentiment')
    date      = models.DateField()
    source    = models.CharField(max_length=10, choices=SOURCE_CHOICES)

    # Score de -1 (très négatif) à +1 (très positif)
    score     = models.DecimalField(
        max_digits=4, decimal_places=3,
        validators=[MinValueValidator(decimal.Decimal('-1.000')),
                    MaxValueValidator(decimal.Decimal('1.000'))]
    )

    # Métadonnées du calcul
    nb_articles       = models.PositiveSmallIntegerField(default=0)
    score_min         = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    score_max         = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    variation_24h     = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True,
                                             help_text="Variation du score vs j-1")
    resume_ia         = models.TextField(blank=True,
                                          help_text="Résumé IA des thèmes principaux détectés")
    date_calcul       = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('titre', 'date', 'source')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['titre', 'date']),
            models.Index(fields=['date', 'source']),
        ]

    def __str__(self):
        return f"Sentiment {self.titre.ticker} {self.date} [{self.source}] = {self.score}"

    @property
    def label(self):
        s = float(self.score)
        if s >= 0.6:   return 'Très positif'
        if s >= 0.2:   return 'Positif'
        if s >= -0.2:  return 'Neutre'
        if s >= -0.6:  return 'Négatif'
        return 'Très négatif'

    @property
    def couleur(self):
        s = float(self.score)
        if s >= 0.2:   return 'success'
        if s >= -0.2:  return 'warning'
        return 'danger'


class Article(models.Model):
    """
    Article ou post individuel collecté et scoré par le LLM.
    """

    SOURCE_CHOICES = [
        ('newsapi',    'NewsAPI'),
        ('eodhd',      'EODHD News'),
        ('reddit',     'Reddit'),
        ('stocktwits', 'StockTwits'),
    ]

    titre      = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                    related_name='articles')
    date_pub   = models.DateTimeField()
    source     = models.CharField(max_length=15, choices=SOURCE_CHOICES)
    url        = models.URLField(max_length=500, blank=True)
    titre_art  = models.CharField(max_length=300)
    extrait    = models.TextField(blank=True)
    auteur     = models.CharField(max_length=100, blank=True)

    # Score LLM
    score_sentiment = models.DecimalField(
        max_digits=4, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(decimal.Decimal('-1.000')),
                    MaxValueValidator(decimal.Decimal('1.000'))]
    )
    tags            = models.JSONField(default=list, blank=True,
                                        help_text="Topics détectés : résultats, fusion, dividende…")
    date_scoring    = models.DateTimeField(null=True, blank=True)

    date_collecte = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_pub']
        indexes = [
            models.Index(fields=['titre', 'date_pub']),
            models.Index(fields=['source', 'date_pub']),
        ]

    def __str__(self):
        return f"[{self.source}] {self.titre_art[:60]}"


# ---------------------------------------------------------------------------
# SIGNAUX TECHNIQUES
# ---------------------------------------------------------------------------

class Signal(models.Model):
    """
    Signal technique ou sentiment détecté sur un titre à une date donnée.
    Alimenté après le calcul des indicateurs sur PrixJournalier.
    """

    TYPE_CHOICES = [
        ('rsi_survente',    'RSI zone survente (<40)'),
        ('rsi_surachat',    'RSI zone surachat (>65)'),
        ('macd_haussier',   'MACD croisement haussier'),
        ('macd_baissier',   'MACD croisement baissier'),
        ('mm_golden_cross', 'Golden cross MM20/MM50'),
        ('mm_death_cross',  'Death cross MM20/MM50'),
        ('boll_inf',        'Prix proche bande Bollinger basse'),
        ('boll_sup',        'Prix proche bande Bollinger haute'),
        ('volume_spike',    'Pic de volume anormal'),
        ('sentiment_hausse','Sentiment en forte hausse'),
        ('sentiment_baisse','Sentiment en forte baisse'),
        ('fondamental',     'Signal fondamental'),
    ]

    DIRECTION_CHOICES = [
        ('haussier', 'Haussier'),
        ('baissier', 'Baissier'),
        ('neutre',   'Neutre'),
    ]

    titre      = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                    related_name='signaux')
    date       = models.DateField()
    type_signal = models.CharField(max_length=30, choices=TYPE_CHOICES)
    direction  = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    valeur     = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True,
                                      help_text="Valeur de l'indicateur au moment du signal")
    description = models.CharField(max_length=200, blank=True)
    actif      = models.BooleanField(default=True,
                                      help_text="False une fois intégré dans une alerte")
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'titre']
        indexes = [
            models.Index(fields=['titre', 'date']),
            models.Index(fields=['actif', 'date']),
        ]

    def __str__(self):
        return f"Signal {self.type_signal} — {self.titre.ticker} {self.date}"


# ---------------------------------------------------------------------------
# CONFLUENCE & ALERTES
# ---------------------------------------------------------------------------

class AlerteConfig(models.Model):
    """
    Configuration des seuils d'alerte définis par l'utilisateur pour un titre.
    """

    titre                  = models.OneToOneField(Titre, on_delete=models.CASCADE,
                                                   related_name='alerte_config')
    actif                  = models.BooleanField(default=True)
    score_min_declenchement = models.DecimalField(
        max_digits=3, decimal_places=1, default=5.0,
        help_text="Score de confluence minimum pour déclencher une alerte (0-10)"
    )

    # Canaux
    notif_app      = models.BooleanField(default=True)
    notif_email    = models.BooleanField(default=False)
    notif_telegram = models.BooleanField(default=False)
    notif_webhook  = models.BooleanField(default=False)
    webhook_url    = models.URLField(blank=True)

    # Filtres spécifiques
    ignorer_court_terme     = models.BooleanField(default=True,
                                                    help_text="Ignorer signaux < 1 semaine")
    alertes_renforcement    = models.BooleanField(default=True,
                                                   help_text="Signaux d'entrée uniquement (profil PEA)")
    seuil_drawdown          = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Alerte si baisse > X% depuis le plus haut récent"
    )

    date_maj = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Config alertes — {self.titre.ticker}"


class Alerte(models.Model):
    """
    Alerte déclenchée par le moteur de confluence.
    Générée automatiquement par la tâche Celery run_confluence_engine.
    """

    NIVEAU_CHOICES = [
        ('forte',    'Forte (score 8-10)'),
        ('moderee',  'Modérée (score 5-7)'),
        ('surveillance', 'Surveillance (score 3-4)'),
    ]

    STATUT_CHOICES = [
        ('nouvelle', 'Nouvelle'),
        ('vue',      'Vue'),
        ('archivee', 'Archivée'),
    ]

    titre          = models.ForeignKey(Titre, on_delete=models.CASCADE,
                                        related_name='alertes')
    date_detection = models.DateTimeField(auto_now_add=True)
    date_signal    = models.DateField(help_text="Date des données ayant déclenché l'alerte")

    # Score et niveau
    score_confluence = models.DecimalField(
        max_digits=4, decimal_places=1,
        validators=[MinValueValidator(decimal.Decimal('0')),
                    MaxValueValidator(decimal.Decimal('10'))]
    )
    niveau         = models.CharField(max_length=15, choices=NIVEAU_CHOICES)

    # Signaux ayant contribué (clés étrangères vers Signal)
    signaux        = models.ManyToManyField(Signal, blank=True,
                                             related_name='alertes')

    # Contexte marché au moment de l'alerte (snapshot)
    cours_au_signal      = models.DecimalField(max_digits=12, decimal_places=4)
    rsi_au_signal        = models.DecimalField(max_digits=5, decimal_places=2,
                                                null=True, blank=True)
    sentiment_au_signal  = models.DecimalField(max_digits=4, decimal_places=3,
                                                null=True, blank=True)

    # Texte généré par le LLM
    texte_ia             = models.TextField(help_text="Observation rédigée par le LLM")
    fiabilite_historique = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="% de fois où ce pattern a été suivi d'une hausse sur ce titre"
    )
    nb_occurrences_passees = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Nombre de fois où ce pattern s'est présenté sur ce titre"
    )

    # Suivi
    statut         = models.CharField(max_length=15, choices=STATUT_CHOICES, default='nouvelle')
    note_utilisateur = models.TextField(blank=True)

    class Meta:
        ordering = ['-date_detection']
        indexes = [
            models.Index(fields=['titre', 'date_detection']),
            models.Index(fields=['statut']),
            models.Index(fields=['niveau']),
        ]

    def __str__(self):
        return f"Alerte {self.niveau} — {self.titre.ticker} {self.date_signal} (score {self.score_confluence})"

    @property
    def disclaimer(self):
        return "Cette observation ne constitue pas un conseil d'investissement."


# ---------------------------------------------------------------------------
# PROFIL INVESTISSEUR
# ---------------------------------------------------------------------------

class ProfilInvestisseur(models.Model):
    """
    Paramètres du profil PEA — un seul enregistrement pour usage personnel.
    Conditionne le filtrage des alertes et le langage du LLM.
    """

    ENVELOPPE_CHOICES = [
        ('pea',     'PEA classique'),
        ('pea_pme', 'PEA-PME'),
        ('cto',     'Compte-titres ordinaire'),
    ]

    STYLE_CHOICES = [
        ('croissance',  'Croissance'),
        ('valeur',      'Valeur'),
        ('dividendes',  'Dividendes'),
        ('mixte',       'Mixte'),
    ]

    RISQUE_CHOICES = [
        (1, 'Prudente'),
        (2, 'Modérée-faible'),
        (3, 'Modérée'),
        (4, 'Modérée-élevée'),
        (5, 'Dynamique'),
    ]

    # Enveloppe
    enveloppe              = models.CharField(max_length=10, choices=ENVELOPPE_CHOICES,
                                               default='pea')
    plafond_versements     = models.DecimalField(max_digits=10, decimal_places=2,
                                                  default=150000.00)
    versements_effectues   = models.DecimalField(max_digits=10, decimal_places=2,
                                                  default=0.00)
    date_ouverture         = models.DateField(null=True, blank=True)

    # Style
    horizon_min_ans        = models.PositiveSmallIntegerField(default=7)
    horizon_max_ans        = models.PositiveSmallIntegerField(default=15)
    style                  = models.CharField(max_length=15, choices=STYLE_CHOICES,
                                               default='croissance')
    tolerance_risque       = models.PositiveSmallIntegerField(
        choices=RISQUE_CHOICES, default=3,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )

    # Pondération du scoring (total doit faire 100)
    poids_fondamentaux     = models.PositiveSmallIntegerField(
        default=60, help_text="% du score de confluence attribué aux fondamentaux"
    )
    poids_technique        = models.PositiveSmallIntegerField(
        default=40, help_text="% du score de confluence attribué à l'analyse technique"
    )

    # Filtres alertes
    ignorer_signaux_court_terme = models.BooleanField(
        default=True, help_text="Ignorer signaux < 1 semaine"
    )
    mode_accumulation      = models.BooleanField(
        default=True, help_text="Alertes de renforcement uniquement — pas de suggestions de vente"
    )
    seuil_drawdown_alerte  = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Alerte si baisse > X% pour réévaluer la thèse"
    )
    digest_hebdomadaire    = models.BooleanField(
        default=True, help_text="Résumé hebdomadaire le vendredi soir"
    )

    # Pays éligibles PEA (liste JSON) — mise à jour par le screener mensuel
    pays_eligibles_pea     = models.JSONField(
        default=list,
        help_text="Codes ISO-3 des pays dont les actions sont éligibles PEA"
    )

    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Profil investisseur'

    def __str__(self):
        return f"Profil PEA — ouvert {self.date_ouverture}"

    @property
    def capacite_versement_restante(self):
        return self.plafond_versements - self.versements_effectues

    @property
    def fiscalite_pleine(self):
        """True si le PEA a plus de 5 ans (exonération IR)."""
        if not self.date_ouverture:
            return False
        from dateutil.relativedelta import relativedelta
        return timezone.now().date() >= self.date_ouverture + relativedelta(years=5)

    @property
    def poids_valides(self):
        return self.poids_fondamentaux + self.poids_technique == 100



# ---------------------------------------------------------------------------
# QUOTA API
# ---------------------------------------------------------------------------

class ApiQuota(models.Model):
    """
    Compteur de requêtes API par jour.
    Utilisé par EODHDClient pour respecter le quota gratuit (20 req/jour).
    Un enregistrement par API par jour.
    """
    api          = models.CharField(max_length=20, help_text="eodhd | fmp | newsapi")
    date         = models.DateField()
    nb_requetes  = models.PositiveSmallIntegerField(default=0)
    date_maj     = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('api', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"Quota {self.api} {self.date} : {self.nb_requetes} req"

    @property
    def restantes(self):
        limites = {'eodhd': 20, 'fmp': 250, 'newsapi': 100}
        return max(0, limites.get(self.api, 0) - self.nb_requetes)
