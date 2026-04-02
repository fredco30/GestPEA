"""
services/patterns.py
--------------------
Étape 31 — Détection algorithmique de patterns graphiques.

Analyse les séries OHLC (PrixJournalier) pour détecter :
  - Double creux / Double sommet
  - Tête-épaules / Tête-épaules inversée
  - Triangle ascendant / descendant / symétrique
  - Canal ascendant / descendant
  - Drapeau / Fanion

Chaque pattern détecté est sauvegardé dans PatternDetecte avec
les points clés (JSON) pour annotation sur Lightweight Charts.

Requiert au minimum 60 bougies de données.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from app.models import Titre, PrixJournalier, PatternDetecte
from app.services.scoring_llm import _get_client

logger = logging.getLogger(__name__)

MODEL_PATTERN = "mistral-small-latest"
MIN_BOUGIES = 60


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def detecter_patterns(ticker):
    """
    Détecte les patterns graphiques pour un titre.

    Returns:
        int: nombre de patterns détectés et sauvegardés.
    """
    try:
        titre = Titre.objects.get(ticker=ticker, actif=True)
    except Titre.DoesNotExist:
        return 0

    bougies = list(
        PrixJournalier.objects.filter(titre=titre)
        .order_by('date')
        .values('date', 'ouverture', 'haut', 'bas', 'cloture', 'volume')
    )

    if len(bougies) < MIN_BOUGIES:
        logger.info("[Patterns] %s : %d bougies insuffisantes (min %d)",
                     ticker, len(bougies), MIN_BOUGIES)
        return 0

    dates = [b['date'] for b in bougies]
    closes = np.array([float(b['cloture']) for b in bougies])
    highs = np.array([float(b['haut']) for b in bougies])
    lows = np.array([float(b['bas']) for b in bougies])
    volumes = np.array([float(b['volume']) for b in bougies])

    # Trouver les extrema locaux
    maxima = _find_local_maxima(highs, window=5)
    minima = _find_local_minima(lows, window=5)

    patterns = []

    # Détection sur la fenêtre récente (dernières 120 bougies max)
    window_start = max(0, len(closes) - 120)

    # Double bottom / Double top
    patterns.extend(_detect_double_bottom(dates, lows, closes, minima, window_start))
    patterns.extend(_detect_double_top(dates, highs, closes, maxima, window_start))

    # Tête-épaules
    patterns.extend(_detect_head_shoulders(dates, highs, closes, maxima, window_start))
    patterns.extend(_detect_inv_head_shoulders(dates, lows, closes, minima, window_start))

    # Triangles
    patterns.extend(_detect_triangles(dates, highs, lows, closes, maxima, minima, window_start))

    # Canaux
    patterns.extend(_detect_channels(dates, highs, lows, closes, window_start))

    # Drapeaux
    patterns.extend(_detect_flags(dates, closes, volumes, window_start))

    # Sauvegarder les patterns détectés
    nb = _sauvegarder_patterns(titre, patterns)
    if nb > 0:
        logger.info("[Patterns] %s : %d patterns détectés", ticker, nb)

    return nb


# ---------------------------------------------------------------------------
# Détection des extrema locaux
# ---------------------------------------------------------------------------

def _find_local_maxima(data, window=5):
    """Retourne les indices des maxima locaux."""
    maxima = []
    for i in range(window, len(data) - window):
        if data[i] == max(data[i - window:i + window + 1]):
            maxima.append(i)
    return maxima


def _find_local_minima(data, window=5):
    """Retourne les indices des minima locaux."""
    minima = []
    for i in range(window, len(data) - window):
        if data[i] == min(data[i - window:i + window + 1]):
            minima.append(i)
    return minima


# ---------------------------------------------------------------------------
# Double bottom (Double creux)
# ---------------------------------------------------------------------------

def _detect_double_bottom(dates, lows, closes, minima, ws):
    """
    Détecte un double creux : 2 creux proches en prix, séparés de 10-50 bougies,
    avec une ligne de cou cassée à la hausse.
    """
    patterns = []
    recent_min = [m for m in minima if m >= ws]

    for i in range(len(recent_min)):
        for j in range(i + 1, len(recent_min)):
            idx1, idx2 = recent_min[i], recent_min[j]
            ecart = idx2 - idx1

            if ecart < 10 or ecart > 50:
                continue

            prix1, prix2 = lows[idx1], lows[idx2]

            # Les 2 creux doivent être à ±3%
            if abs(prix1 - prix2) / min(prix1, prix2) > 0.03:
                continue

            # Ligne de cou = plus haut entre les 2 creux
            neckline = max(closes[idx1:idx2 + 1])

            # Vérifier la cassure : cours actuel > neckline
            cours_actuel = closes[-1]
            if cours_actuel <= neckline:
                statut = 'en_formation'
            else:
                statut = 'confirme'

            support = min(prix1, prix2)
            hauteur = neckline - support
            objectif = neckline + hauteur

            patterns.append({
                'type_pattern': 'double_bottom',
                'statut': statut,
                'direction': 'haussier',
                'date_debut': dates[idx1],
                'date_fin': dates[idx2] if statut == 'confirme' else None,
                'prix_support': round(support, 4),
                'prix_resistance': round(neckline, 4),
                'prix_objectif': round(objectif, 4),
                'prix_invalidation': round(support * 0.97, 4),
                'points_cles': [
                    {'time': str(dates[idx1]), 'value': round(prix1, 2), 'label': 'Creux 1'},
                    {'time': str(dates[idx2]), 'value': round(prix2, 2), 'label': 'Creux 2'},
                ],
            })
            break  # Un seul double bottom par paire

    return patterns[:1]  # Max 1


# ---------------------------------------------------------------------------
# Double top (Double sommet)
# ---------------------------------------------------------------------------

def _detect_double_top(dates, highs, closes, maxima, ws):
    """Détecte un double sommet (miroir du double bottom)."""
    patterns = []
    recent_max = [m for m in maxima if m >= ws]

    for i in range(len(recent_max)):
        for j in range(i + 1, len(recent_max)):
            idx1, idx2 = recent_max[i], recent_max[j]
            ecart = idx2 - idx1

            if ecart < 10 or ecart > 50:
                continue

            prix1, prix2 = highs[idx1], highs[idx2]
            if abs(prix1 - prix2) / max(prix1, prix2) > 0.03:
                continue

            neckline = min(closes[idx1:idx2 + 1])
            cours_actuel = closes[-1]

            statut = 'confirme' if cours_actuel < neckline else 'en_formation'

            resistance = max(prix1, prix2)
            hauteur = resistance - neckline
            objectif = neckline - hauteur

            patterns.append({
                'type_pattern': 'double_top',
                'statut': statut,
                'direction': 'baissier',
                'date_debut': dates[idx1],
                'date_fin': dates[idx2] if statut == 'confirme' else None,
                'prix_support': round(neckline, 4),
                'prix_resistance': round(resistance, 4),
                'prix_objectif': round(max(objectif, 0), 4),
                'prix_invalidation': round(resistance * 1.03, 4),
                'points_cles': [
                    {'time': str(dates[idx1]), 'value': round(prix1, 2), 'label': 'Sommet 1'},
                    {'time': str(dates[idx2]), 'value': round(prix2, 2), 'label': 'Sommet 2'},
                ],
            })
            break

    return patterns[:1]


# ---------------------------------------------------------------------------
# Tête-épaules
# ---------------------------------------------------------------------------

def _detect_head_shoulders(dates, highs, closes, maxima, ws):
    """
    Détecte tête-épaules : 3 sommets consécutifs où le médian est le plus haut,
    les 2 épaules sont à ±5% l'une de l'autre.
    """
    patterns = []
    recent_max = [m for m in maxima if m >= ws]

    for i in range(len(recent_max) - 2):
        idx_g, idx_t, idx_d = recent_max[i], recent_max[i + 1], recent_max[i + 2]

        # Tête doit être le plus haut
        h_g, h_t, h_d = highs[idx_g], highs[idx_t], highs[idx_d]
        if h_t <= h_g or h_t <= h_d:
            continue

        # Épaules à ±5%
        if abs(h_g - h_d) / min(h_g, h_d) > 0.05:
            continue

        # Ligne de cou (creux entre épaule gauche et tête, et entre tête et épaule droite)
        creux_g = min(closes[idx_g:idx_t + 1])
        creux_d = min(closes[idx_t:idx_d + 1])
        neckline = (creux_g + creux_d) / 2

        cours_actuel = closes[-1]
        statut = 'confirme' if cours_actuel < neckline else 'en_formation'

        hauteur = h_t - neckline
        objectif = neckline - hauteur

        patterns.append({
            'type_pattern': 'head_shoulders',
            'statut': statut,
            'direction': 'baissier',
            'date_debut': dates[idx_g],
            'date_fin': dates[idx_d] if statut == 'confirme' else None,
            'prix_support': round(neckline, 4),
            'prix_resistance': round(h_t, 4),
            'prix_objectif': round(max(objectif, 0), 4),
            'prix_invalidation': round(h_t * 1.02, 4),
            'points_cles': [
                {'time': str(dates[idx_g]), 'value': round(h_g, 2), 'label': 'Épaule G.'},
                {'time': str(dates[idx_t]), 'value': round(h_t, 2), 'label': 'Tête'},
                {'time': str(dates[idx_d]), 'value': round(h_d, 2), 'label': 'Épaule D.'},
            ],
        })
        break

    return patterns[:1]


def _detect_inv_head_shoulders(dates, lows, closes, minima, ws):
    """Tête-épaules inversée (miroir haussier)."""
    patterns = []
    recent_min = [m for m in minima if m >= ws]

    for i in range(len(recent_min) - 2):
        idx_g, idx_t, idx_d = recent_min[i], recent_min[i + 1], recent_min[i + 2]

        l_g, l_t, l_d = lows[idx_g], lows[idx_t], lows[idx_d]
        if l_t >= l_g or l_t >= l_d:
            continue

        if abs(l_g - l_d) / max(l_g, l_d) > 0.05:
            continue

        pic_g = max(closes[idx_g:idx_t + 1])
        pic_d = max(closes[idx_t:idx_d + 1])
        neckline = (pic_g + pic_d) / 2

        cours_actuel = closes[-1]
        statut = 'confirme' if cours_actuel > neckline else 'en_formation'

        hauteur = neckline - l_t
        objectif = neckline + hauteur

        patterns.append({
            'type_pattern': 'inv_head_shoulders',
            'statut': statut,
            'direction': 'haussier',
            'date_debut': dates[idx_g],
            'date_fin': dates[idx_d] if statut == 'confirme' else None,
            'prix_support': round(l_t, 4),
            'prix_resistance': round(neckline, 4),
            'prix_objectif': round(objectif, 4),
            'prix_invalidation': round(l_t * 0.98, 4),
            'points_cles': [
                {'time': str(dates[idx_g]), 'value': round(l_g, 2), 'label': 'Épaule G.'},
                {'time': str(dates[idx_t]), 'value': round(l_t, 2), 'label': 'Tête'},
                {'time': str(dates[idx_d]), 'value': round(l_d, 2), 'label': 'Épaule D.'},
            ],
        })
        break

    return patterns[:1]


# ---------------------------------------------------------------------------
# Triangles
# ---------------------------------------------------------------------------

def _detect_triangles(dates, highs, lows, closes, maxima, minima, ws):
    """
    Détecte triangles : convergence des hauts et des bas sur les 40 dernières bougies.
    - Ascendant : bas montants + hauts plats
    - Descendant : hauts descendants + bas plats
    - Symétrique : hauts descendants + bas montants
    """
    patterns = []
    n = len(closes)
    if n < 40:
        return patterns

    # Prendre les 4+ derniers extrema récents
    recent_max = [m for m in maxima if m >= n - 60]
    recent_min = [m for m in minima if m >= n - 60]

    if len(recent_max) < 3 or len(recent_min) < 3:
        return patterns

    # Tendance des hauts (linreg sur les 3 derniers maxima)
    h_vals = [highs[i] for i in recent_max[-3:]]
    h_trend = (h_vals[-1] - h_vals[0]) / max(h_vals[0], 0.01)

    # Tendance des bas
    l_vals = [lows[i] for i in recent_min[-3:]]
    l_trend = (l_vals[-1] - l_vals[0]) / max(l_vals[0], 0.01)

    type_pattern = None
    direction = 'neutre'

    # Triangle ascendant : bas montants (>1%), hauts plats (<1%)
    if l_trend > 0.01 and abs(h_trend) < 0.01:
        type_pattern = 'triangle_asc'
        direction = 'haussier'
    # Triangle descendant : hauts descendants (<-1%), bas plats (<1%)
    elif h_trend < -0.01 and abs(l_trend) < 0.01:
        type_pattern = 'triangle_desc'
        direction = 'baissier'
    # Triangle symétrique : hauts descendants, bas montants, convergence
    elif h_trend < -0.005 and l_trend > 0.005:
        type_pattern = 'triangle_sym'
        direction = 'neutre'

    if type_pattern:
        support = min(l_vals)
        resistance = max(h_vals)
        hauteur = resistance - support

        patterns.append({
            'type_pattern': type_pattern,
            'statut': 'en_formation',
            'direction': direction,
            'date_debut': dates[recent_min[-3]],
            'date_fin': None,
            'prix_support': round(l_vals[-1], 4),
            'prix_resistance': round(h_vals[-1], 4),
            'prix_objectif': round(h_vals[-1] + hauteur * 0.5, 4) if direction == 'haussier'
                else round(l_vals[-1] - hauteur * 0.5, 4) if direction == 'baissier'
                else None,
            'prix_invalidation': None,
            'points_cles': [
                {'time': str(dates[recent_max[-3]]), 'value': round(h_vals[0], 2), 'label': 'Haut 1'},
                {'time': str(dates[recent_min[-3]]), 'value': round(l_vals[0], 2), 'label': 'Bas 1'},
                {'time': str(dates[recent_max[-1]]), 'value': round(h_vals[-1], 2), 'label': 'Haut 3'},
                {'time': str(dates[recent_min[-1]]), 'value': round(l_vals[-1], 2), 'label': 'Bas 3'},
            ],
        })

    return patterns


# ---------------------------------------------------------------------------
# Canaux
# ---------------------------------------------------------------------------

def _detect_channels(dates, highs, lows, closes, ws):
    """
    Détecte un canal ascendant ou descendant sur les 40 dernières bougies.
    Utilise la régression linéaire sur les hauts et les bas.
    """
    patterns = []
    n = len(closes)
    window = min(40, n - ws)
    if window < 20:
        return patterns

    start = n - window
    x = np.arange(window)
    h = highs[start:]
    l = lows[start:]

    # Régression linéaire
    slope_h = np.polyfit(x, h, 1)[0]
    slope_l = np.polyfit(x, l, 1)[0]

    # Les deux pentes doivent être de même signe et proches
    if slope_h == 0 or slope_l == 0:
        return patterns

    # Ratio des pentes : doivent être parallèles (ratio entre 0.5 et 2.0)
    ratio = slope_h / slope_l if slope_l != 0 else float('inf')
    if not (0.5 < ratio < 2.0):
        return patterns

    # Canal significatif : pente > 0.1% par bougie
    avg_slope = (slope_h + slope_l) / 2
    pente_pct = avg_slope / closes[start] * 100

    if abs(pente_pct) < 0.05:
        return patterns

    type_pattern = 'channel_asc' if avg_slope > 0 else 'channel_desc'
    direction = 'haussier' if avg_slope > 0 else 'baissier'

    support = l[-1]
    resistance = h[-1]

    patterns.append({
        'type_pattern': type_pattern,
        'statut': 'en_formation',
        'direction': direction,
        'date_debut': dates[start],
        'date_fin': None,
        'prix_support': round(float(support), 4),
        'prix_resistance': round(float(resistance), 4),
        'prix_objectif': None,
        'prix_invalidation': None,
        'points_cles': [
            {'time': str(dates[start]), 'value': round(float(l[0]), 2), 'label': 'Bas canal'},
            {'time': str(dates[start]), 'value': round(float(h[0]), 2), 'label': 'Haut canal'},
            {'time': str(dates[-1]), 'value': round(float(l[-1]), 2), 'label': 'Support'},
            {'time': str(dates[-1]), 'value': round(float(h[-1]), 2), 'label': 'Résistance'},
        ],
    })

    return patterns


# ---------------------------------------------------------------------------
# Drapeaux (flags)
# ---------------------------------------------------------------------------

def _detect_flags(dates, closes, volumes, ws):
    """
    Détecte un drapeau : impulsion forte (>5% en 5 bougies) suivie
    d'une consolidation dans un range serré.
    """
    patterns = []
    n = len(closes)
    if n < 30:
        return patterns

    # Chercher une impulsion récente (dans les 30 dernières bougies)
    for i in range(max(ws, n - 30), n - 10):
        # Impulsion sur 5 bougies
        variation = (closes[i + 5] - closes[i]) / closes[i] * 100
        if abs(variation) < 5:
            continue

        # Consolidation après l'impulsion (5-15 bougies)
        consol_start = i + 5
        consol_end = min(consol_start + 15, n)
        consol = closes[consol_start:consol_end]

        if len(consol) < 5:
            continue

        range_consol = (max(consol) - min(consol)) / min(consol) * 100

        # Le range de consolidation doit être < 3%
        if range_consol > 3:
            continue

        direction = 'haussier' if variation > 0 else 'baissier'

        patterns.append({
            'type_pattern': 'flag',
            'statut': 'en_formation',
            'direction': direction,
            'date_debut': dates[i],
            'date_fin': None,
            'prix_support': round(float(min(consol)), 4),
            'prix_resistance': round(float(max(consol)), 4),
            'prix_objectif': round(float(closes[consol_start] + (closes[i + 5] - closes[i])), 4) if direction == 'haussier'
                else round(float(closes[consol_start] - (closes[i] - closes[i + 5])), 4),
            'prix_invalidation': round(float(min(consol) * 0.97), 4) if direction == 'haussier'
                else round(float(max(consol) * 1.03), 4),
            'points_cles': [
                {'time': str(dates[i]), 'value': round(float(closes[i]), 2), 'label': 'Début impulsion'},
                {'time': str(dates[i + 5]), 'value': round(float(closes[i + 5]), 2), 'label': 'Fin impulsion'},
                {'time': str(dates[min(consol_end - 1, n - 1)]), 'value': round(float(consol[-1]), 2), 'label': 'Consolidation'},
            ],
        })
        break

    return patterns


# ---------------------------------------------------------------------------
# Sauvegarde + description IA
# ---------------------------------------------------------------------------

def _sauvegarder_patterns(titre, patterns):
    """
    Sauvegarde les patterns détectés. Anti-doublon : (titre, type, date_debut ±5j).
    Génère une description IA en français débutant.
    """
    nb = 0

    for p in patterns:
        # Anti-doublon
        doublon = PatternDetecte.objects.filter(
            titre=titre,
            type_pattern=p['type_pattern'],
            date_debut__range=(
                p['date_debut'] - timedelta(days=5),
                p['date_debut'] + timedelta(days=5),
            ),
        ).exists()

        if doublon:
            continue

        # Générer la description
        description = _generer_description(p, titre.ticker, titre.nom)

        PatternDetecte.objects.create(
            titre=titre,
            type_pattern=p['type_pattern'],
            statut=p['statut'],
            direction=p['direction'],
            date_debut=p['date_debut'],
            date_fin=p.get('date_fin'),
            prix_support=p.get('prix_support'),
            prix_resistance=p.get('prix_resistance'),
            prix_objectif=p.get('prix_objectif'),
            prix_invalidation=p.get('prix_invalidation'),
            points_cles=p.get('points_cles', []),
            description=description,
        )
        nb += 1

    return nb


def _generer_description(pattern_data, ticker, nom):
    """Génère une description en français débutant via Mistral small."""
    type_labels = {
        'double_bottom': 'double creux (le prix a touché deux fois un plancher)',
        'double_top': 'double sommet (le prix a buté deux fois sur un plafond)',
        'head_shoulders': 'tête-épaules (figure de retournement à la baisse)',
        'inv_head_shoulders': 'tête-épaules inversée (figure de retournement à la hausse)',
        'triangle_asc': 'triangle ascendant (les creux montent vers une résistance)',
        'triangle_desc': 'triangle descendant (les sommets baissent vers un support)',
        'triangle_sym': 'triangle symétrique (le prix se resserre entre support et résistance)',
        'channel_asc': 'canal ascendant (le prix monte entre deux lignes parallèles)',
        'channel_desc': 'canal descendant (le prix baisse entre deux lignes parallèles)',
        'flag': 'drapeau (forte hausse/baisse suivie d\'une pause)',
        'pennant': 'fanion (pause triangulaire après un mouvement fort)',
    }

    type_label = type_labels.get(pattern_data['type_pattern'], pattern_data['type_pattern'])
    support = pattern_data.get('prix_support', 'N/D')
    resistance = pattern_data.get('prix_resistance', 'N/D')
    objectif = pattern_data.get('prix_objectif', 'N/D')
    statut = 'confirmé' if pattern_data['statut'] == 'confirme' else 'en formation'

    try:
        client = _get_client()
        response = client.chat.complete(
            model=MODEL_PATTERN,
            max_tokens=200,
            messages=[
                {"role": "system", "content": "Tu expliques simplement les figures graphiques boursières à un débutant complet. 2-3 phrases maximum, en français, avec des niveaux de prix en euros. Pas de conseil d'investissement."},
                {"role": "user", "content": (
                    f"Explique cette figure sur {nom} ({ticker}) : {type_label}. "
                    f"Statut : {statut}. "
                    f"Zone plancher : {support} €, zone plafond : {resistance} €"
                    f"{f', objectif : {objectif} €' if objectif and objectif != 'N/D' else ''}."
                )},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("[Patterns] Erreur description IA %s : %s", ticker, e)
        return f"Figure de type {type_label} détectée ({statut}). Zone plancher : {support} €, zone plafond : {resistance} €."
