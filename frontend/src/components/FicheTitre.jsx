/**
 * frontend/src/components/FicheTitre.jsx
 * ----------------------------------------
 * Fiche complète d'un titre du portefeuille PEA.
 * Affiche : métriques clés, graphique technique, sentiment, news, alertes.
 */

import React, { useState } from 'react'
import { useTitre } from '../hooks/useTitre'
import { analyserTitre, updateTitre } from '../api/client'
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

      {/* ---- Score de conviction IA ---- */}
      {titre.score_conviction != null && (
        <CarteConviction
          score={titre.score_conviction}
          explication={titre.explication_conviction}
          dateCalcul={titre.date_calcul_conviction}
        />
      )}

      {/* ---- Position portefeuille (éditable) ---- */}
      {titre.statut === 'portefeuille' && (
        <PanneauPosition titre={titre} ticker={ticker} onUpdate={rafraichir} />
      )}

      {/* ---- Resultat analyse IA ---- */}
      {analyseResultat && !analyseResultat.erreur && (
        <div style={{
          background: 'var(--color-background-success)', border: '1px solid var(--color-text-success)',
          borderRadius: 'var(--border-radius-md)', padding: '10px 14px',
        }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-success)', marginBottom: 6 }}>
            Analyse terminee
          </div>
          {Object.entries(analyseResultat.etapes || {}).map(([etape, detail]) => {
            let texte
            if (typeof detail === 'string') {
              texte = detail
            } else if (detail?.resume_ia) {
              texte = `Score global ${detail.global?.toFixed(3) || '—'}`
            } else if (typeof detail === 'object') {
              texte = Object.entries(detail).map(([k, v]) => `${k}: ${v}`).join(' · ')
            } else {
              texte = String(detail)
            }
            return (
              <div key={etape} style={{ fontSize: 11, color: 'var(--color-text-secondary)', padding: '2px 0' }}>
                <strong>{etape}</strong> : {texte}
              </div>
            )
          })}
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

function CarteConviction({ score, explication, dateCalcul }) {
  const couleur = score >= 70 ? 'success' : score >= 40 ? 'warning' : 'danger'
  const label = score >= 70 ? 'Conviction forte' : score >= 40 ? 'Conviction moderee' : 'Conviction faible'
  const pct = score / 100

  return (
    <div style={{
      background: 'var(--color-background-primary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {/* Jauge circulaire */}
        <div style={{ position: 'relative', width: 56, height: 56, flexShrink: 0 }}>
          <svg width="56" height="56" viewBox="0 0 56 56">
            <circle cx="28" cy="28" r="24" fill="none" stroke="var(--color-background-secondary)" strokeWidth="4" />
            <circle cx="28" cy="28" r="24" fill="none"
              stroke={`var(--color-text-${couleur})`} strokeWidth="4"
              strokeDasharray={`${pct * 150.8} 150.8`}
              strokeLinecap="round"
              transform="rotate(-90 28 28)" />
          </svg>
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 16, fontWeight: 600, color: `var(--color-text-${couleur})`,
          }}>
            {score}
          </div>
        </div>

        {/* Texte */}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>
              Score de conviction IA
            </span>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 12, fontWeight: 500,
              background: `var(--color-background-${couleur})`,
              color: `var(--color-text-${couleur})`,
            }}>
              {label}
            </span>
          </div>
          {explication && (
            <div style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--color-text-secondary)' }}>
              {explication}
            </div>
          )}
          {dateCalcul && (
            <div style={{ fontSize: 10, color: 'var(--color-text-tertiary)', marginTop: 4 }}>
              Mis a jour le {new Date(dateCalcul).toLocaleDateString('fr-FR')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

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

function PanneauPosition({ titre, ticker, onUpdate }) {
  const [edition, setEdition] = useState(false)
  const [nbActions, setNbActions] = useState(titre.nb_actions || '')
  const [prixRevient, setPrixRevient] = useState(titre.prix_revient_moyen || '')
  const [saving, setSaving] = useState(false)

  const dernier = titre.prix_historique?.[titre.prix_historique.length - 1]
  const coursActuel = dernier ? Number(dernier.cloture) : null
  const nb = Number(titre.nb_actions) || 0
  const prm = Number(titre.prix_revient_moyen) || 0

  const valeurPosition = nb && coursActuel ? nb * coursActuel : null
  const investiTotal = nb && prm ? nb * prm : null
  const pmv = valeurPosition && investiTotal ? valeurPosition - investiTotal : null
  const pmvPct = investiTotal ? ((valeurPosition - investiTotal) / investiTotal * 100) : null

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateTitre(ticker, {
        nb_actions: Number(nbActions) || 0,
        prix_revient_moyen: Number(prixRevient) || null,
      })
      setEdition(false)
      onUpdate()
    } catch (e) {
      console.error('Erreur sauvegarde position:', e)
    } finally {
      setSaving(false)
    }
  }

  if (!edition && !nb) {
    return (
      <button
        onClick={() => setEdition(true)}
        style={{
          width: '100%', padding: '10px 14px',
          background: 'var(--color-background-secondary)',
          border: '1px dashed var(--color-border-tertiary)',
          borderRadius: 'var(--border-radius-md)',
          cursor: 'pointer', fontSize: 12, color: 'var(--color-text-secondary)',
          textAlign: 'center',
        }}
      >
        + Renseigner ma position (nb actions, prix d'achat)
      </button>
    )
  }

  return (
    <div style={{
      background: 'var(--color-background-primary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)', padding: '12px 16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: edition ? 10 : 0 }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>Ma position</span>
        {!edition && (
          <button
            onClick={() => { setEdition(true); setNbActions(titre.nb_actions || ''); setPrixRevient(titre.prix_revient_moyen || '') }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 11, color: 'var(--color-text-tertiary)' }}
          >
            Modifier
          </button>
        )}
      </div>

      {edition ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 3 }}>Nb actions</label>
            <input
              type="number"
              value={nbActions}
              onChange={e => setNbActions(e.target.value)}
              style={{
                width: 100, padding: '6px 8px', fontSize: 12,
                border: '1px solid var(--color-border-tertiary)',
                borderRadius: 'var(--border-radius-sm)',
                background: 'var(--color-background-primary)',
                color: 'var(--color-text-primary)',
              }}
            />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 3 }}>Prix revient moyen</label>
            <input
              type="number"
              step="0.01"
              value={prixRevient}
              onChange={e => setPrixRevient(e.target.value)}
              style={{
                width: 120, padding: '6px 8px', fontSize: 12,
                border: '1px solid var(--color-border-tertiary)',
                borderRadius: 'var(--border-radius-sm)',
                background: 'var(--color-background-primary)',
                color: 'var(--color-text-primary)',
              }}
            />
          </div>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              padding: '6px 14px', fontSize: 11, fontWeight: 500,
              background: 'var(--color-text-success)', color: '#fff',
              border: 'none', borderRadius: 'var(--border-radius-sm)',
              cursor: 'pointer',
            }}
          >
            {saving ? '...' : 'Enregistrer'}
          </button>
          <button
            onClick={() => setEdition(false)}
            style={{
              padding: '6px 10px', fontSize: 11,
              background: 'none', border: '1px solid var(--color-border-tertiary)',
              borderRadius: 'var(--border-radius-sm)',
              cursor: 'pointer', color: 'var(--color-text-secondary)',
            }}
          >
            Annuler
          </button>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0,1fr))', gap: 8, marginTop: 8 }}>
          <MetriqueCard label="Nb actions" valeur={nb.toLocaleString('fr-FR')} />
          <MetriqueCard label="PRU" valeur={prm ? `${prm.toFixed(2)} \u20AC` : '\u2014'} />
          <MetriqueCard label="Valeur position" valeur={valeurPosition ? `${valeurPosition.toLocaleString('fr-FR', {minimumFractionDigits: 2})} \u20AC` : '\u2014'} />
          <MetriqueCard
            label="Plus/moins value"
            valeur={pmv != null ? `${pmv >= 0 ? '+' : ''}${pmv.toLocaleString('fr-FR', {minimumFractionDigits: 2})} \u20AC (${pmvPct >= 0 ? '+' : ''}${pmvPct.toFixed(1)}%)` : '\u2014'}
            couleur={pmv != null ? (pmv >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)') : undefined}
          />
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
