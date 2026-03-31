"""
services/scoring_llm.py
------------------------
Service d'appel à Claude API (Anthropic) pour deux usages :

  1. scorer_articles(article_ids)
     → Analyse le sentiment de chaque article (-1 à +1)
     → Détecte les topics (résultats, dividende, acquisition, etc.)
     → Met à jour Article.score_sentiment + Article.tags
     → Agrège les scores pour mettre à jour ScoreSentiment du jour

  2. generer_texte_alerte(alerte_id)
     → Rédige le texte narratif d'une alerte en langage naturel
     → Intègre contexte technique + fondamental + sentiment
     → Respecte le profil PEA long terme (pas de conseil, orientation renforcement)
     → Met à jour Alerte.texte_ia + Alerte.fiabilite_historique

Modèle utilisé : claude-haiku-4-5-20251001 (rapide + économique pour le scoring en lot)
Coût estimé : ~0.001 € par article scoré, ~0.005 € par alerte rédigée

Dépendances :
  pip install anthropic
"""

import json
import logging
from datetime import date, timedelta
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTES
# ---------------------------------------------------------------------------

MODEL_SCORING  = "claude-haiku-4-5-20251001"   # rapide + pas cher pour le scoring en lot
MODEL_ALERTE   = "claude-sonnet-4-6"            # meilleure qualité rédactionnelle pour les alertes
MAX_TOKENS     = 800
BATCH_SIZE     = 5    # nb d'articles scorés par appel API (économie de tokens)

TOPICS_CONNUS = [
    "résultats trimestriels", "dividende", "acquisition", "fusion",
    "cession", "endettement", "guidance", "profit warning",
    "rachat d'actions", "introduction en bourse", "changement de direction",
    "litige", "réglementation", "expansion", "contrat majeur",
    "innovation", "restructuration", "notation crédit",
]


# ---------------------------------------------------------------------------
# CLIENT ANTHROPIC
# ---------------------------------------------------------------------------

def _get_client():
    """Retourne une instance du client Anthropic."""
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "La librairie 'anthropic' n'est pas installée. "
            "Exécuter : pip install anthropic"
        )

    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY manquant dans settings.py. "
            "Ajouter : ANTHROPIC_API_KEY = 'sk-ant-...'"
        )
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# 1. SCORING SENTIMENT DES ARTICLES
# ---------------------------------------------------------------------------

def scorer_articles(article_ids: list[int]) -> int:
    """
    Score le sentiment de chaque article via Claude API.
    Traite les articles par lots (BATCH_SIZE) pour économiser les tokens.

    Args:
        article_ids : liste d'IDs d'objets Article non encore scorés

    Retourne le nombre d'articles scorés avec succès.
    """
    from app.models import Article

    articles = Article.objects.filter(
        id__in=article_ids,
        score_sentiment__isnull=True,  # uniquement les non scorés
    ).select_related('titre')

    if not articles.exists():
        logger.info("[LLM] scorer_articles : aucun article à scorer.")
        return 0

    client  = _get_client()
    nb_ok   = 0

    # Traitement par batch
    articles_list = list(articles)
    for i in range(0, len(articles_list), BATCH_SIZE):
        batch = articles_list[i:i + BATCH_SIZE]
        nb_ok += _scorer_batch(client, batch)

    # Après scoring, recalculer les scores agrégés par titre et par jour
    tickers_touches = list({a.titre.ticker for a in articles_list})
    for ticker in tickers_touches:
        _agreger_sentiment_jour(ticker, date.today())

    logger.info(f"[LLM] scorer_articles : {nb_ok}/{len(articles_list)} articles scorés.")
    return nb_ok


def _scorer_batch(client, articles: list) -> int:
    """
    Appelle Claude pour scorer un lot d'articles en un seul appel API.
    Retourne le nombre d'articles scorés avec succès.
    """
    from django.utils import timezone

    # Construire le prompt avec tous les articles du batch
    articles_json = []
    for i, art in enumerate(articles):
        articles_json.append({
            "id": i,
            "ticker": art.titre.ticker,
            "titre": art.titre_art,
            "extrait": art.extrait[:500] if art.extrait else "",
            "source": art.source,
        })

    prompt_user = f"""Analyse le sentiment de chaque article financier ci-dessous concernant les actions boursières indiquées.

Articles à analyser :
{json.dumps(articles_json, ensure_ascii=False, indent=2)}

Topics à détecter parmi : {', '.join(TOPICS_CONNUS)}

Pour chaque article, fournis UNIQUEMENT un JSON valide avec ce format exact :
[
  {{
    "id": 0,
    "score": 0.72,
    "tags": ["résultats trimestriels", "guidance"]
  }},
  ...
]

Règles de scoring :
- score de -1.0 (très négatif) à +1.0 (très positif)
- 0.0 = neutre ou non pertinent pour le cours
- Évalue l'impact PROBABLE sur le cours de l'action, pas le sentiment général
- tags : liste vide [] si aucun topic reconnu
- Réponds UNIQUEMENT avec le JSON, sans texte avant ou après"""

    try:
        response = client.messages.create(
            model=MODEL_SCORING,
            max_tokens=MAX_TOKENS,
            system=(
                "Tu es un analyste financier expert en analyse de sentiment sur les marchés boursiers européens. "
                "Tu réponds toujours en JSON valide uniquement, sans markdown, sans explication."
            ),
            messages=[{"role": "user", "content": prompt_user}],
        )

        contenu = response.content[0].text.strip()

        # Nettoyer si le modèle a quand même ajouté des backticks
        if contenu.startswith("```"):
            contenu = contenu.split("```")[1]
            if contenu.startswith("json"):
                contenu = contenu[4:]
        contenu = contenu.strip()

        resultats = json.loads(contenu)

        nb_ok = 0
        now = timezone.now()

        for res in resultats:
            idx   = res.get("id")
            score = res.get("score")
            tags  = res.get("tags", [])

            if idx is None or score is None:
                continue

            try:
                art = articles[idx]
            except IndexError:
                continue

            # Validation du score
            try:
                score = max(-1.0, min(1.0, float(score)))
            except (TypeError, ValueError):
                score = 0.0

            art.score_sentiment = score
            art.tags            = tags if isinstance(tags, list) else []
            art.date_scoring    = now
            art.save(update_fields=['score_sentiment', 'tags', 'date_scoring'])
            nb_ok += 1

        return nb_ok

    except json.JSONDecodeError as e:
        logger.error(f"[LLM] _scorer_batch : JSON invalide reçu — {e}")
        return 0
    except Exception as e:
        logger.error(f"[LLM] _scorer_batch : erreur API — {e}", exc_info=True)
        return 0


def _agreger_sentiment_jour(ticker: str, jour: date) -> None:
    """
    Calcule et sauvegarde les scores de sentiment agrégés (presse, social, global)
    pour un ticker à une date donnée, à partir des articles scorés.
    """
    from app.models import Article, ScoreSentiment, Titre

    try:
        titre = Titre.objects.get(ticker=ticker)
    except Titre.DoesNotExist:
        return

    # Fenêtre : articles des 3 derniers jours (pour lisser)
    depuis = jour - timedelta(days=2)

    articles = Article.objects.filter(
        titre=titre,
        date_pub__date__gte=depuis,
        date_pub__date__lte=jour,
        score_sentiment__isnull=False,
    )

    if not articles.exists():
        return

    # Séparer presse et social
    sources_presse = ['newsapi', 'eodhd']
    sources_social = ['reddit', 'stocktwits']

    def score_moyen(qs):
        scores = list(qs.values_list('score_sentiment', flat=True))
        if not scores:
            return None, 0
        moyenne = sum(float(s) for s in scores) / len(scores)
        return round(moyenne, 3), len(scores)

    score_presse, nb_presse = score_moyen(
        articles.filter(source__in=sources_presse)
    )
    score_social, nb_social = score_moyen(
        articles.filter(source__in=sources_social)
    )

    # Score global pondéré (presse pèse plus pour profil PEA)
    if score_presse is not None and score_social is not None:
        score_global = round(0.65 * score_presse + 0.35 * score_social, 3)
    elif score_presse is not None:
        score_global = score_presse
    elif score_social is not None:
        score_global = score_social
    else:
        return

    # Score j-1 pour calculer la variation
    hier = jour - timedelta(days=1)
    score_hier = ScoreSentiment.objects.filter(
        titre=titre, date=hier, source='global'
    ).first()

    variation = None
    if score_hier and score_hier.score is not None:
        variation = round(score_global - float(score_hier.score), 3)

    # Sauvegarder les 3 scores
    for source, score, nb in [
        ('presse', score_presse, nb_presse),
        ('social', score_social, nb_social),
        ('global', score_global, nb_presse + nb_social),
    ]:
        if score is None:
            continue
        ScoreSentiment.objects.update_or_create(
            titre=titre,
            date=jour,
            source=source,
            defaults={
                'score':        score,
                'nb_articles':  nb,
                'variation_24h': variation if source == 'global' else None,
            }
        )

    logger.info(
        f"[LLM] Sentiment {ticker} {jour} — "
        f"presse: {score_presse}, social: {score_social}, global: {score_global}"
    )


# ---------------------------------------------------------------------------
# 2. GÉNÉRATION DU TEXTE D'ALERTE
# ---------------------------------------------------------------------------

def generer_texte_alerte(alerte_id: int) -> bool:
    """
    Génère le texte narratif d'une alerte via Claude API (modèle Sonnet).
    Intègre tous les éléments contextuels : technique, fondamental, sentiment.
    Respecte strictement le profil PEA long terme (pas de conseil).

    Met à jour :
      - Alerte.texte_ia
      - Alerte.fiabilite_historique
      - Alerte.nb_occurrences_passees

    Retourne True si succès.
    """
    from app.models import Alerte, Fondamentaux, ProfilInvestisseur, ScoreSentiment

    try:
        alerte = Alerte.objects.select_related('titre').get(pk=alerte_id)
    except Alerte.DoesNotExist:
        logger.error(f"[LLM] generer_texte_alerte : alerte {alerte_id} introuvable.")
        return False

    titre  = alerte.titre
    profil = ProfilInvestisseur.objects.first()

    # --- Collecter le contexte ---

    # Signaux ayant déclenché l'alerte
    signaux = list(alerte.signaux.all().values('type_signal', 'direction', 'valeur', 'description'))

    # Fondamentaux récents
    fond = Fondamentaux.objects.filter(titre=titre).order_by('-date_maj').first()
    fond_ctx = {}
    if fond:
        fond_ctx = {
            "per":                fond.per,
            "roe":                fond.roe,
            "rendement_dividende": fond.rendement_dividende,
            "dette_nette_ebitda": fond.dette_nette_ebitda,
            "croissance_bpa_3ans": fond.croissance_bpa_3ans,
            "marge_nette":        fond.marge_nette,
            "score_qualite":      fond.score_qualite,
            "consensus_analystes": fond.consensus,
            "objectif_cours":     fond.objectif_cours_moyen,
        }

    # Score sentiment actuel
    sent = ScoreSentiment.objects.filter(
        titre=titre, source='global'
    ).order_by('-date').first()
    sent_ctx = {
        "score": float(sent.score) if sent else None,
        "label": sent.label if sent else "Inconnu",
        "variation_24h": float(sent.variation_24h) if sent and sent.variation_24h else None,
    }

    # Historique des occurrences similaires (approximation)
    fiabilite, nb_occurrences = _calculer_fiabilite_historique(titre, signaux)
    alerte.fiabilite_historique    = fiabilite
    alerte.nb_occurrences_passees  = nb_occurrences
    alerte.save(update_fields=['fiabilite_historique', 'nb_occurrences_passees'])

    # Contexte profil investisseur
    profil_ctx = {
        "enveloppe":           "PEA",
        "horizon":             f"{profil.horizon_min_ans}–{profil.horizon_max_ans} ans" if profil else "7-15 ans",
        "style":               profil.style if profil else "croissance",
        "mode_accumulation":   profil.mode_accumulation if profil else True,
        "fiscalite_pleine":    profil.fiscalite_pleine if profil else True,
        "capacite_versement":  float(profil.capacite_versement_restante) if profil else None,
    }

    # --- Construire le prompt ---

    prompt_user = f"""Tu dois rédiger le texte d'une alerte boursière pour un investisseur particulier gérant son PEA en mode long terme (accumulation, pas de day trading).

TITRE : {titre.nom} ({titre.ticker}) — {titre.secteur}
DATE : {alerte.date_signal}
COURS AU SIGNAL : {alerte.cours_au_signal} €
SCORE DE CONFLUENCE : {alerte.score_confluence}/10
NIVEAU : {alerte.niveau}

SIGNAUX DÉTECTÉS :
{json.dumps(signaux, ensure_ascii=False, indent=2)}

FONDAMENTAUX :
{json.dumps({k: str(v) if v else 'N/D' for k, v in fond_ctx.items()}, ensure_ascii=False, indent=2)}

SENTIMENT ACTUEL :
{json.dumps(sent_ctx, ensure_ascii=False, indent=2)}

HISTORIQUE DES PATTERNS SIMILAIRES :
- Occurrences similaires sur ce titre : {nb_occurrences}
- Suivi d'une hausse dans les 10 jours suivants : {fiabilite}%

PROFIL INVESTISSEUR :
{json.dumps(profil_ctx, ensure_ascii=False, indent=2)}

INSTRUCTIONS DE RÉDACTION :
1. Commence par une ligne de titre : "NOM_TITRE · Type d'opportunité · Score X/10"
2. Liste les signaux détectés sous forme de tirets courts et précis
3. Rédige 2-3 phrases de contexte IA en langage naturel professionnel
4. Mentionne la fiabilité historique si > 0 occurrences
5. Si le titre est éligible PEA et que la capacité de versement est connue, mentionne-la
6. Termine TOUJOURS par cette phrase exacte sur une nouvelle ligne :
   "— Cette observation ne constitue pas un conseil d'investissement."

CONTRAINTES ABSOLUES :
- Ne jamais utiliser les mots "acheter", "vendre", "investir", "placer"
- Parler de "renforcement", "point d'entrée potentiel", "opportunité à étudier"
- Ton professionnel, factuel, sans exclamation
- Maximum 200 mots
- Langue : français"""

    try:
        client   = _get_client()
        response = client.messages.create(
            model=MODEL_ALERTE,
            max_tokens=600,
            system=(
                "Tu es un outil d'aide à la décision pour investisseurs particuliers. "
                "Tu fournis des observations factuelles et contextualisées sur les marchés financiers. "
                "Tu ne donnes jamais de conseils d'investissement. "
                "Tu rédiges en français, de manière concise et professionnelle."
            ),
            messages=[{"role": "user", "content": prompt_user}],
        )

        texte = response.content[0].text.strip()

        # S'assurer que le disclaimer est présent
        disclaimer = "— Cette observation ne constitue pas un conseil d'investissement."
        if disclaimer not in texte:
            texte += f"\n\n{disclaimer}"

        alerte.texte_ia = texte
        alerte.save(update_fields=['texte_ia'])

        logger.info(
            f"[LLM] Alerte {alerte_id} ({titre.ticker}) — texte généré "
            f"({len(texte)} caractères)"
        )
        return True

    except Exception as e:
        logger.error(f"[LLM] generer_texte_alerte {alerte_id} — erreur : {e}", exc_info=True)
        # Texte de fallback si l'API échoue
        alerte.texte_ia = (
            f"{titre.nom} ({titre.ticker}) · Score {alerte.score_confluence}/10\n\n"
            f"Confluence de {len(signaux)} signal(s) détectée le {alerte.date_signal}.\n"
            f"Cours au signal : {alerte.cours_au_signal} €\n\n"
            f"— Cette observation ne constitue pas un conseil d'investissement."
        )
        alerte.save(update_fields=['texte_ia'])
        return False


# ---------------------------------------------------------------------------
# 3. CALCUL DE LA FIABILITÉ HISTORIQUE
# ---------------------------------------------------------------------------

def _calculer_fiabilite_historique(titre, signaux: list) -> tuple[Optional[float], int]:
    """
    Cherche dans l'historique des Alerte passées les patterns similaires
    et calcule le % de fois où une hausse a suivi dans les 10 jours.

    Retourne (fiabilite_pct, nb_occurrences).
    Fiabilité = None si moins de 3 occurrences (pas assez de données).
    """
    from app.models import Alerte, PrixJournalier

    types_signaux_actuels = {s['type_signal'] for s in signaux}

    alertes_passees = Alerte.objects.filter(
        titre=titre,
        statut__in=['vue', 'archivee'],
    ).exclude(cours_au_signal=0).order_by('-date_signal')[:20]

    if not alertes_passees.exists():
        return None, 0

    nb_occurrences = 0
    nb_hausse      = 0

    for alerte_passee in alertes_passees:
        # Vérifier si les types de signaux se recoupent suffisamment
        types_passes = {s.type_signal for s in alerte_passee.signaux.all()}
        intersection = types_signaux_actuels & types_passes

        if len(intersection) < max(1, len(types_signaux_actuels) // 2):
            continue

        nb_occurrences += 1

        # Vérifier si le cours a monté dans les 10 jours suivants
        date_alerte = alerte_passee.date_signal
        date_limite = date_alerte + timedelta(days=14)  # 14j = ~10 jours ouvrés

        cours_apres = PrixJournalier.objects.filter(
            titre=titre,
            date__gte=date_alerte + timedelta(days=1),
            date__lte=date_limite,
        ).order_by('date').values_list('cloture', flat=True)

        if not cours_apres:
            continue

        cours_max_apres = max(float(c) for c in cours_apres)
        cours_signal    = float(alerte_passee.cours_au_signal)

        if cours_max_apres > cours_signal * 1.02:  # hausse > 2%
            nb_hausse += 1

    if nb_occurrences < 3:
        return None, nb_occurrences

    fiabilite = round(nb_hausse / nb_occurrences * 100, 1)
    return fiabilite, nb_occurrences


# ---------------------------------------------------------------------------
# 4. RÉSUMÉ HEBDOMADAIRE (digest vendredi soir)
# ---------------------------------------------------------------------------

def generer_digest_hebdomadaire() -> str:
    """
    Génère un résumé hebdomadaire de toutes les alertes et du sentiment global.
    Appelé par une tâche Celery le vendredi soir.
    Retourne le texte du digest (envoyé par email / Telegram).
    """
    from app.models import Alerte, ScoreSentiment, Titre

    depuis = date.today() - timedelta(days=7)

    alertes_semaine = Alerte.objects.filter(
        date_signal__gte=depuis
    ).select_related('titre').order_by('-score_confluence')

    titres_pf = Titre.objects.filter(statut='portefeuille', actif=True)

    # Construire le contexte
    alertes_ctx = []
    for a in alertes_semaine[:10]:  # Top 10
        alertes_ctx.append({
            "ticker": a.titre.ticker,
            "nom":    a.titre.nom_court or a.titre.nom,
            "score":  float(a.score_confluence),
            "niveau": a.niveau,
            "date":   str(a.date_signal),
        })

    sentiments_ctx = []
    for titre in titres_pf:
        sent = ScoreSentiment.objects.filter(
            titre=titre, source='global', date__gte=depuis
        ).order_by('-date').first()
        if sent:
            sentiments_ctx.append({
                "ticker": titre.ticker,
                "nom":    titre.nom_court or titre.nom,
                "score":  float(sent.score),
                "label":  sent.label,
            })

    prompt_user = f"""Rédige un digest hebdomadaire concis pour un investisseur PEA long terme.

SEMAINE DU {depuis} AU {date.today()}

ALERTES DE LA SEMAINE :
{json.dumps(alertes_ctx, ensure_ascii=False, indent=2)}

SENTIMENT DE TON PORTEFEUILLE :
{json.dumps(sentiments_ctx, ensure_ascii=False, indent=2)}

FORMAT ATTENDU :
- Titre "Digest PEA — semaine du [date]"
- 2-3 phrases de synthèse macro
- Liste des titres à surveiller cette semaine
- 1 phrase de rappel sur la stratégie long terme
- Maximum 150 mots
- Toujours terminer par "— Ces observations ne constituent pas des conseils d'investissement."
- Langue : français"""

    try:
        client   = _get_client()
        response = client.messages.create(
            model=MODEL_ALERTE,
            max_tokens=400,
            system=(
                "Tu es un assistant d'aide à la gestion de portefeuille boursier. "
                "Tu rédiges des synthèses factuelles en français, sans conseils d'investissement."
            ),
            messages=[{"role": "user", "content": prompt_user}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error(f"[LLM] generer_digest_hebdomadaire — erreur : {e}", exc_info=True)
        return f"Digest PEA — semaine du {depuis}\n\nErreur de génération. Consulter le dashboard.\n\n— Ces observations ne constituent pas des conseils d'investissement."
