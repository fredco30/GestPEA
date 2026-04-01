"""
services/scoring_llm.py
------------------------
Service d'appel à Mistral AI pour deux usages :

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

Modèle utilisé : mistral-small-latest (rapide + économique pour le scoring en lot)

Dépendances :
  pip install mistralai
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

MODEL_SCORING  = "mistral-small-latest"   # rapide + économique pour le scoring en lot
MODEL_ALERTE   = "mistral-large-latest"   # meilleure qualité rédactionnelle pour les alertes
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
# CLIENT MISTRAL
# ---------------------------------------------------------------------------

def _get_client():
    """Retourne une instance du client Mistral."""
    try:
        from mistralai import Mistral
    except ImportError:
        try:
            from mistralai.client import Mistral
        except ImportError:
            raise ImportError(
                "La librairie 'mistralai' n'est pas installée. "
                "Exécuter : pip install mistralai"
            )

    api_key = getattr(settings, 'MISTRAL_API_KEY', '')
    if not api_key:
        raise ValueError(
            "MISTRAL_API_KEY manquant dans settings.py. "
            "Ajouter : MISTRAL_API_KEY = '...'"
        )
    return Mistral(api_key=api_key)


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
        response = client.chat.complete(
            model=MODEL_SCORING,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": (
                    "Tu es un analyste financier expert en analyse de sentiment sur les marchés boursiers européens. "
                    "Tu réponds toujours en JSON valide uniquement, sans markdown, sans explication."
                )},
                {"role": "user", "content": prompt_user},
            ],
        )

        contenu = response.choices[0].message.content.strip()

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
    sources_presse = ['newsapi', 'eodhd', 'google_news', 'boursorama', 'zonebourse']
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
# 1b. SENTIMENT TECHNIQUE + MIXTE + RAPPORT IA
# ---------------------------------------------------------------------------

def calculer_sentiment_technique(ticker: str) -> dict | None:
    """
    Calcule un score de sentiment technique (-1 à +1) basé sur les indicateurs
    de la dernière bougie : RSI, MACD, position vs MM50/MM200, Bollinger.

    Retourne un dict avec score, details et resume ou None si pas de données.
    """
    from app.models import PrixJournalier, Titre

    try:
        titre = Titre.objects.get(ticker=ticker)
    except Titre.DoesNotExist:
        return None

    bougie = titre.prix_journaliers.order_by('-date').first()
    if not bougie:
        return None

    signaux = []
    score_total = 0.0
    nb_signaux = 0

    # --- RSI ---
    if bougie.rsi_14 is not None:
        rsi = float(bougie.rsi_14)
        nb_signaux += 1
        if rsi < 30:
            s = 0.8
            signaux.append(f"RSI({rsi:.1f}) en forte survente → signal haussier")
        elif rsi < 40:
            s = 0.4
            signaux.append(f"RSI({rsi:.1f}) en survente modérée → légèrement haussier")
        elif rsi > 70:
            s = -0.8
            signaux.append(f"RSI({rsi:.1f}) en fort surachat → signal baissier")
        elif rsi > 60:
            s = -0.3
            signaux.append(f"RSI({rsi:.1f}) en surachat modéré → légèrement baissier")
        else:
            s = 0.0
            signaux.append(f"RSI({rsi:.1f}) en zone neutre")
        score_total += s

    # --- MACD ---
    if bougie.macd_hist is not None:
        hist = float(bougie.macd_hist)
        nb_signaux += 1
        if hist > 0.05:
            s = 0.5
            signaux.append(f"MACD histogramme positif ({hist:+.4f}) → momentum haussier")
        elif hist < -0.05:
            s = -0.5
            signaux.append(f"MACD histogramme négatif ({hist:+.4f}) → momentum baissier")
        else:
            s = 0.0
            signaux.append(f"MACD neutre ({hist:+.4f})")
        score_total += s

    # --- Position vs MM50 ---
    if bougie.mm_50 is not None and bougie.cloture:
        cloture = float(bougie.cloture)
        mm50 = float(bougie.mm_50)
        ecart_pct = ((cloture - mm50) / mm50) * 100
        nb_signaux += 1
        if ecart_pct > 5:
            s = 0.4
            signaux.append(f"Cours {ecart_pct:+.1f}% au-dessus de la MM50 → tendance haussière")
        elif ecart_pct < -5:
            s = -0.4
            signaux.append(f"Cours {ecart_pct:+.1f}% en dessous de la MM50 → tendance baissière")
        else:
            s = ecart_pct / 12.5  # normaliser entre -0.4 et +0.4
            signaux.append(f"Cours {ecart_pct:+.1f}% vs MM50 → proche de la moyenne")
        score_total += s

    # --- Position vs MM200 (tendance long terme) ---
    if bougie.mm_200 is not None and bougie.cloture:
        cloture = float(bougie.cloture)
        mm200 = float(bougie.mm_200)
        ecart_pct = ((cloture - mm200) / mm200) * 100
        nb_signaux += 1
        if ecart_pct > 0:
            s = 0.3
            signaux.append(f"Au-dessus de la MM200 ({ecart_pct:+.1f}%) → tendance LT haussière")
        else:
            s = -0.3
            signaux.append(f"En dessous de la MM200 ({ecart_pct:+.1f}%) → tendance LT baissière")
        score_total += s

    # --- Bollinger ---
    if bougie.boll_inf is not None and bougie.boll_sup is not None and bougie.cloture:
        cloture = float(bougie.cloture)
        boll_inf = float(bougie.boll_inf)
        boll_sup = float(bougie.boll_sup)
        boll_range = boll_sup - boll_inf
        if boll_range > 0:
            position = (cloture - boll_inf) / boll_range  # 0 = bande basse, 1 = bande haute
            nb_signaux += 1
            if position < 0.15:
                s = 0.5
                signaux.append(f"Proche bande Bollinger basse ({position:.0%}) → rebond probable")
            elif position > 0.85:
                s = -0.5
                signaux.append(f"Proche bande Bollinger haute ({position:.0%}) → correction probable")
            else:
                s = 0.0
                signaux.append(f"Position Bollinger médiane ({position:.0%})")
            score_total += s

    if nb_signaux == 0:
        return None

    score_final = max(-1.0, min(1.0, round(score_total / nb_signaux, 3)))

    return {
        'score': score_final,
        'signaux': signaux,
        'nb_signaux': nb_signaux,
        'date': str(bougie.date),
    }


def generer_sentiment_mixte(ticker: str) -> dict | None:
    """
    Génère un sentiment mixte (technique + presse) avec un rapport IA écrit.
    Met à jour les ScoreSentiment en base avec resume_ia.

    Retourne le dict de résultat ou None.
    """
    from app.models import Article, ScoreSentiment, Titre
    from datetime import date as dt_date

    try:
        titre = Titre.objects.get(ticker=ticker)
    except Titre.DoesNotExist:
        return None

    aujourd_hui = dt_date.today()

    # --- 1. Sentiment technique ---
    tech = calculer_sentiment_technique(ticker)
    score_tech = tech['score'] if tech else 0.0
    signaux_tech = tech['signaux'] if tech else []

    # --- 2. Sentiment presse (déjà en base) ---
    score_presse_obj = ScoreSentiment.objects.filter(
        titre=titre, date=aujourd_hui, source='presse'
    ).first()
    score_presse = float(score_presse_obj.score) if score_presse_obj else None

    # Articles récents pour le contexte IA
    articles_recents = list(
        Article.objects.filter(
            titre=titre,
            score_sentiment__isnull=False,
        ).order_by('-date_pub')[:5].values_list('titre_art', 'score_sentiment')
    )

    # --- 3. Score global mixte ---
    if score_presse is not None:
        # 40% technique + 60% presse (profil PEA long terme, presse pèse plus)
        score_global = round(0.4 * score_tech + 0.6 * score_presse, 3)
    else:
        # Pas d'articles → 100% technique
        score_global = score_tech

    # --- 4. Rapport IA ---
    resume_ia = _generer_rapport_sentiment(
        ticker=ticker,
        nom=titre.nom_court or titre.nom,
        score_tech=score_tech,
        signaux_tech=signaux_tech,
        score_presse=score_presse,
        articles_recents=articles_recents,
        score_global=score_global,
    )

    # --- 5. Sauvegarder en base ---
    # Score technique
    ScoreSentiment.objects.update_or_create(
        titre=titre, date=aujourd_hui, source='social',
        defaults={
            'score': score_tech,
            'nb_articles': 0,
            'resume_ia': f"Sentiment technique basé sur {len(signaux_tech)} indicateurs.",
        }
    )

    # Score global mixte avec rapport IA
    variation = None
    score_hier = ScoreSentiment.objects.filter(
        titre=titre, date=aujourd_hui - timedelta(days=1), source='global'
    ).first()
    if score_hier and score_hier.score is not None:
        variation = round(score_global - float(score_hier.score), 3)

    ScoreSentiment.objects.update_or_create(
        titre=titre, date=aujourd_hui, source='global',
        defaults={
            'score': score_global,
            'nb_articles': len(articles_recents),
            'variation_24h': variation,
            'resume_ia': resume_ia,
        }
    )

    logger.info(
        f"[LLM] Sentiment mixte {ticker} — tech={score_tech}, "
        f"presse={score_presse}, global={score_global}"
    )

    return {
        'ticker': ticker,
        'score_technique': score_tech,
        'score_presse': score_presse,
        'score_global': score_global,
        'resume_ia': resume_ia,
        'signaux': signaux_tech,
    }


def _generer_rapport_sentiment(ticker, nom, score_tech, signaux_tech,
                                score_presse, articles_recents, score_global):
    """Génère un rapport IA écrit sur le sentiment d'un titre."""
    try:
        client = _get_client()
    except (ImportError, ValueError) as e:
        logger.warning(f"[LLM] Rapport impossible : {e}")
        return _rapport_fallback(nom, score_tech, signaux_tech, score_presse, score_global)

    # Construire le contexte
    ctx_tech = "\n".join(f"  - {s}" for s in signaux_tech) if signaux_tech else "  Aucun indicateur disponible"

    ctx_articles = ""
    if articles_recents:
        ctx_articles = "\n".join(
            f"  - \"{titre}\" (score: {float(score):+.2f})"
            for titre, score in articles_recents
        )
    else:
        ctx_articles = "  Aucun article récent collecté"

    prompt = f"""Rédige un bref rapport de sentiment (3-5 phrases) pour l'action {nom} ({ticker}).

Données :
- Score technique : {score_tech:+.3f} (basé sur {len(signaux_tech)} indicateurs)
- Signaux techniques :
{ctx_tech}
- Score presse : {f"{score_presse:+.3f}" if score_presse is not None else "non disponible"}
- Articles récents :
{ctx_articles}
- Score global mixte : {score_global:+.3f}

Règles :
- Sois concis et factuel (3-5 phrases max)
- Mentionne les signaux techniques clés
- Si des articles existent, résume le sentiment presse
- Indique la tendance générale (haussière, baissière, neutre)
- TERMINE TOUJOURS par : "Cette observation ne constitue pas un conseil d'investissement."
- Rédige en français"""

    try:
        response = client.chat.complete(
            model=MODEL_ALERTE,
            max_tokens=400,
            messages=[
                {"role": "system", "content": (
                    "Tu es un analyste financier qui rédige des rapports de sentiment "
                    "concis et factuels pour des investisseurs PEA long terme."
                )},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[LLM] Rapport sentiment {ticker} : {e}")
        return _rapport_fallback(nom, score_tech, signaux_tech, score_presse, score_global)


def _rapport_fallback(nom, score_tech, signaux_tech, score_presse, score_global):
    """Rapport de secours sans LLM."""
    tendance = "haussière" if score_global > 0.15 else "baissière" if score_global < -0.15 else "neutre"
    lignes = [f"Analyse technique de {nom} : tendance {tendance} (score {score_global:+.3f})."]
    if signaux_tech:
        lignes.append(f"Principaux signaux : {signaux_tech[0]}.")
    if score_presse is not None:
        lignes.append(f"Le sentiment presse est {'positif' if score_presse > 0.1 else 'négatif' if score_presse < -0.1 else 'neutre'} ({score_presse:+.3f}).")
    lignes.append("Cette observation ne constitue pas un conseil d'investissement.")
    return " ".join(lignes)


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

    # --- Niveaux de prix techniques ---
    from app.models import PrixJournalier
    bougie = PrixJournalier.objects.filter(titre=titre).order_by('-date').first()
    niveaux_ctx = {}
    if bougie:
        niveaux_ctx = {
            "cours_actuel": str(bougie.cloture),
            "support_mm50": str(bougie.mm_50) if bougie.mm_50 else "N/D",
            "support_mm200": str(bougie.mm_200) if bougie.mm_200 else "N/D",
            "support_bollinger_bas": str(bougie.boll_inf) if bougie.boll_inf else "N/D",
            "resistance_bollinger_haut": str(bougie.boll_sup) if bougie.boll_sup else "N/D",
            "objectif_analystes": str(fond.objectif_cours_moyen) if fond and fond.objectif_cours_moyen else "N/D",
        }

    # --- Construire le prompt ---

    prompt_user = f"""Tu dois rédiger le texte d'une alerte boursière pour un investisseur DÉBUTANT gérant son PEA en mode long terme. Cette personne n'a AUCUNE connaissance technique — elle ne sait pas ce qu'est un RSI, un MACD ou des bandes de Bollinger.

TITRE : {titre.nom} ({titre.ticker}) — {titre.secteur}
DATE : {alerte.date_signal}
COURS AU SIGNAL : {alerte.cours_au_signal} €
SCORE DE CONFLUENCE : {alerte.score_confluence}/10
NIVEAU : {alerte.niveau}

SIGNAUX DÉTECTÉS :
{json.dumps(signaux, ensure_ascii=False, indent=2, default=str)}

NIVEAUX DE PRIX CLÉS :
{json.dumps(niveaux_ctx, ensure_ascii=False, indent=2)}

FONDAMENTAUX :
{json.dumps({k: str(v) if v is not None else 'N/D' for k, v in fond_ctx.items()}, ensure_ascii=False, indent=2)}

SENTIMENT ACTUEL :
{json.dumps(sent_ctx, ensure_ascii=False, indent=2)}

HISTORIQUE DES PATTERNS SIMILAIRES :
- Occurrences similaires sur ce titre : {nb_occurrences}
- Suivi d'une hausse dans les 10 jours suivants : {fiabilite}%

PROFIL INVESTISSEUR :
{json.dumps(profil_ctx, ensure_ascii=False, indent=2)}

INSTRUCTIONS DE RÉDACTION :
1. Commence par une ligne de titre : "NOM_TITRE · Type d'opportunité · Score X/10"
2. Explique la situation en langage SIMPLE (pas de jargon technique : pas de RSI, MACD, Bollinger, MM50)
3. Indique clairement les NIVEAUX DE PRIX EN EUROS :
   - "Zone de support autour de XX €" (niveau en dessous duquel le titre pourrait baisser davantage)
   - "Zone de résistance vers XX €" (niveau au-dessus duquel le titre aurait du mal à monter)
   - "Zone d'entrée potentielle entre XX € et XX €" si pertinent
   - "Objectif des analystes : XX €" si disponible
4. Rédige 2-3 phrases de contexte en langage naturel accessible
5. Mentionne la fiabilité historique si > 0 occurrences
6. Termine TOUJOURS par cette phrase exacte sur une nouvelle ligne :
   "— Cette observation ne constitue pas un conseil d'investissement."

CONTRAINTES ABSOLUES :
- Ne jamais utiliser les mots "acheter", "vendre", "investir", "placer"
- Parler de "renforcement", "point d'entrée potentiel", "opportunité à étudier"
- TOUJOURS donner des niveaux de prix concrets en euros
- Pas de jargon technique — traduire en langage courant
- Ton professionnel mais accessible, sans exclamation
- Maximum 250 mots
- Langue : français"""

    try:
        client   = _get_client()
        response = client.chat.complete(
            model=MODEL_ALERTE,
            max_tokens=600,
            messages=[
                {"role": "system", "content": (
                    "Tu es un outil d'aide à la décision pour investisseurs particuliers. "
                    "Tu fournis des observations factuelles et contextualisées sur les marchés financiers. "
                    "Tu ne donnes jamais de conseils d'investissement. "
                    "Tu rédiges en français, de manière concise et professionnelle."
                )},
                {"role": "user", "content": prompt_user},
            ],
        )

        texte = response.choices[0].message.content.strip()

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
        response = client.chat.complete(
            model=MODEL_ALERTE,
            max_tokens=400,
            messages=[
                {"role": "system", "content": (
                    "Tu es un assistant d'aide à la gestion de portefeuille boursier. "
                    "Tu rédiges des synthèses factuelles en français, sans conseils d'investissement."
                )},
                {"role": "user", "content": prompt_user},
            ],
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[LLM] generer_digest_hebdomadaire — erreur : {e}", exc_info=True)
        return f"Digest PEA — semaine du {depuis}\n\nErreur de génération. Consulter le dashboard.\n\n— Ces observations ne constituent pas des conseils d'investissement."
