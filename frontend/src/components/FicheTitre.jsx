/**
 * frontend/src/components/FicheTitre.jsx
 * ----------------------------------------
 * Fiche complète d'un titre du portefeuille PEA.
 * Affiche : métriques clés, graphique technique, sentiment, news, alertes.
 */

import React, { useState } from 'react'
import { useTitre } from '../hooks/useTitre'
import { analyserTitre } from '../api/client'
import GraphiqueTechnique from './GraphiqueTechnique'
import { BadgeSentiment, CarteSignaux, FeedArticles, CarteAlertes } from './utilitaires'

export default function FicheTitre({ ticker }) {
  const { titre, ohlc, periode, loading, loadingOhlc, changerPeriode, rafraichir } = useTitre(ticker)
  const [analyseEnCours, setAnalyseEnCours] = useState(false)
  const [analyseResultat, setAnalyseResultat] = useState(null)

  const lancerAnalyse = async () => {
    setAnalyseEnCours(true)
    setAnalyseResultat(null)
    try {
      const result = await analyserTitre(ticker)
      setAnalyseResultat(result)
      // Rafraichir les donnees du titre apres analyse
      rafraichir()
    } catch (e) {
      setAnalyseResultat({ erreur: e.message })
    } finally {
      setAnalyseEnCours(false)
    }
  }

  if (loading) return <Squelette />
  if (!titre)  return <div style={{ color: 'var(--color-text-tertiary)', padding: 24 }}>Titre introuvable.</div>

  const dernier        = titre.prix_historique?.[titre.prix_historique.length - 1]
  const sentimentGlobal = titre.sentiments_30j?.[titre.sentiments_30j.length - 1]
  const fond           = titre.fondamentaux

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* ---- En-tête ---- */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18, fontWeight: 500, color: 'var(--color-text-primary)' }}>
              {titre.nom_court || titre.nom}
            </span>
            <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>
              {titre.ticker} · {titre.place} · {titre.secteur}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 6 }}>
            <span style={{ fontSize: 28, fontWeight: 500, color: 'var(--color-text-primary)' }}>
              {dernier ? `${Number(dernier.cloture).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €` : '—'}
            </span>
            {dernier?.variation_pct != null && (
              <span style={{
                fontSize: 14, fontWeight: 500,
                color: dernier.variation_pct >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)',
              }}>
                {dernier.variation_pct >= 0 ? '+' : ''}{dernier.variation_pct.toFixed(2)}%
              </span>
            )}
          </div>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={lancerAnalyse}
            disabled={analyseEnCours}
            style={{
              padding: '6px 14px', fontSize: 12, fontWeight: 500,
              background: analyseEnCours ? 'var(--color-background-secondary)' : 'var(--color-text-primary)',
              color: analyseEnCours ? 'var(--color-text-tertiary)' : 'var(--color-background-primary)',
              border: 'none', borderRadius: 'var(--border-radius-md)',
              cursor: analyseEnCours ? 'wait' : 'pointer',
            }}
          >
            {analyseEnCours ? 'Analyse en cours...' : 'Analyser IA'}
          </button>
          {sentimentGlobal && <BadgeSentiment score={Number(sentimentGlobal.score)} label={sentimentGlobal.label} />}
          <PositionBadge titre={titre} dernier={dernier} />
        </div>
      </div>

      {/* ---- Métriques clés ---- */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 8 }}>
        <MetriqueCard label="RSI (14)" valeur={dernier?.rsi_14 ? Number(dernier.rsi_14).toFixed(1) : '—'}
          couleur={getRsiCouleur(dernier?.rsi_14)} />
        <MetriqueCard label="MACD" valeur={dernier?.macd_hist != null
          ? `${Number(dernier.macd_hist) >= 0 ? '+' : ''}${Number(dernier.macd_hist).toFixed(2)}` : '—'}
          couleur={dernier?.macd_hist != null ? (Number(dernier.macd_hist) >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)') : undefined} />
        <MetriqueCard label="MM 50j vs prix" valeur={
          dernier?.mm_50 && dernier?.cloture
            ? `${((Number(dernier.cloture) - Number(dernier.mm_50)) / Number(dernier.mm_50) * 100).toFixed(1)}%`
            : '—'}
          couleur={getEcartMmCouleur(dernier)} />
        <MetriqueCard label="Sentiment" valeur={
          sentimentGlobal ? (Number(sentimentGlobal.score) >= 0 ? '+' : '') + Number(sentimentGlobal.score).toFixed(2) : '—'}
          couleur={sentimentGlobal?.couleur === 'success' ? 'var(--color-text-success)'
            : sentimentGlobal?.couleur === 'danger' ? 'var(--color-text-danger)'
            : 'var(--color-text-warning)'} />
      </div>

      {/* ---- Resultat analyse IA ---- */}
      {analyseResultat && !analyseResultat.erreur && (
        <div style={{
          background: 'var(--color-background-success)', border: '1px solid var(--color-text-success)',
          borderRadius: 'var(--border-radius-md)', padding: '10px 14px',
        }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-success)', marginBottom: 6 }}>
            Analyse terminee
          </div>
          {Object.entries(analyseResultat.etapes || {}).map(([etape, detail]) => (
            <div key={etape} style={{ fontSize: 11, color: 'var(--color-text-secondary)', padding: '2px 0' }}>
              <strong>{etape}</strong> : {typeof detail === 'object' ? `Score global ${detail.global?.toFixed(3) || '—'}` : detail}
            </div>
          ))}
        </div>
      )}
      {analyseResultat?.erreur && (
        <div style={{
          background: 'var(--color-background-danger)', border: '1px solid var(--color-text-danger)',
          borderRadius: 'var(--border-radius-md)', padding: '10px 14px',
          fontSize: 12, color: 'var(--color-text-danger)',
        }}>
          Erreur : {analyseResultat.erreur}
        </div>
      )}

      {/* ---- Graphique technique ---- */}
      {ohlc && (
        <GraphiqueTechnique
          ohlcData={ohlc}
          ticker={ticker}
          loadingOhlc={loadingOhlc}
          periode={periode}
          onChangePeriode={changerPeriode}
        />
      )}

      {/* ---- Grille inférieure : sentiment + news ---- */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 14 }}>

        {/* Signaux techniques + sentiment */}
        <CarteSignaux
          signaux={titre.signaux_actifs}
          sentiments30j={titre.sentiments_30j}
          fondamentaux={fond}
          ticker={ticker}
        />

        {/* Feed articles */}
        <FeedArticles articles={titre.articles_recents} />
      </div>

      {/* ---- Alertes récentes ---- */}
      {titre.alertes_recentes?.length > 0 && (
        <CarteAlertes alertes={titre.alertes_recentes} ticker={ticker} />
      )}

      {/* ---- Fondamentaux ---- */}
      {fond && <CarteFondamentaux fond={fond} />}

    </div>
  )
}

// ---------------------------------------------------------------------------
// Sous-composants
// ---------------------------------------------------------------------------

function MetriqueCard({ label, valeur, couleur }) {
  return (
    <div style={{ background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-md)', padding: '10px 12px' }}>
      <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 500, color: couleur || 'var(--color-text-primary)' }}>{valeur}</div>
    </div>
  )
}

function PositionBadge({ titre, dernier }) {
  if (!titre.nb_actions || titre.nb_actions === 0) return null
  const valeur = titre.valeur_position
  const pmv    = titre.plus_moins_value
  return (
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{titre.nb_actions} actions</div>
      {valeur && <div style={{ fontSize: 13, fontWeight: 500 }}>{Number(valeur).toLocaleString('fr-FR')} €</div>}
      {pmv != null && (
        <div style={{ fontSize: 12, color: Number(pmv) >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)' }}>
          {Number(pmv) >= 0 ? '+' : ''}{Number(pmv).toLocaleString('fr-FR')} €
        </div>
      )}
    </div>
  )
}

function CarteFondamentaux({ fond }) {
  const [ouvert, setOuvert] = useState(false)
  return (
    <div style={{ background: 'var(--color-background-primary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-lg)', padding: '12px 16px' }}>
      <button
        onClick={() => setOuvert(o => !o)}
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
      >
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>Fondamentaux</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {fond.score_qualite != null && (
            <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
              Score qualité : <strong>{fond.score_qualite}/10</strong>
            </span>
          )}
          <span style={{ fontSize: 12, color: 'var(--color-text-tertiary)' }}>{ouvert ? '▲' : '▼'}</span>
        </div>
      </button>

      {ouvert && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 8, marginTop: 12 }}>
          {[
            { label: 'PER', valeur: fond.per?.toFixed(1) },
            { label: 'PER Forward', valeur: fond.per_forward?.toFixed(1) },
            { label: 'ROE', valeur: fond.roe ? `${fond.roe.toFixed(1)}%` : null },
            { label: 'Dette/EBITDA', valeur: fond.dette_nette_ebitda?.toFixed(1) },
            { label: 'Marge nette', valeur: fond.marge_nette ? `${fond.marge_nette.toFixed(1)}%` : null },
            { label: 'Croiss. BPA 3A', valeur: fond.croissance_bpa_3ans ? `${fond.croissance_bpa_3ans.toFixed(1)}%` : null },
            { label: 'Dividende', valeur: fond.rendement_dividende ? `${fond.rendement_dividende.toFixed(2)}%` : null },
            { label: 'Consensus', valeur: fond.consensus },
          ].map(({ label, valeur }) => valeur && (
            <MetriqueCard key={label} label={label} valeur={valeur} />
          ))}
        </div>
      )}
    </div>
  )
}

// Helpers couleur
function getRsiCouleur(rsi) {
  if (rsi == null) return 'var(--color-text-primary)'
  if (rsi < 40) return 'var(--color-text-success)'
  if (rsi > 65) return 'var(--color-text-danger)'
  return 'var(--color-text-warning)'
}

function getEcartMmCouleur(dernier) {
  if (!dernier?.mm_50 || !dernier?.cloture) return 'var(--color-text-primary)'
  const ecart = (Number(dernier.cloture) - Number(dernier.mm_50)) / Number(dernier.mm_50) * 100
  return ecart > 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)'
}

function Squelette() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {[280, 80, 380, 200].map((h, i) => (
        <div key={i} style={{ height: h, background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-lg)', animation: 'pulse 1.5s ease-in-out infinite' }} />
      ))}
    </div>
  )
}
