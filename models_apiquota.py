"""
À AJOUTER dans app/models.py — en bas du fichier existant
----------------------------------------------------------
Modèle de suivi du quota API journalier.
"""

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
