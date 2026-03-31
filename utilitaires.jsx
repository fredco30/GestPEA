/**
 * frontend/src/components/utilitaires.jsx
 * -----------------------------------------
 * Composants utilitaires partagés dans tout le dashboard.
 * Exporter chacun individuellement depuis leur propre fichier en prod,
 * ici regroupés pour lisibilité.
 */

import React, { useState, useEffect } from 'react'
import { getAlertes, getTitres, updateStatutAlerte, getQuota } from '../api/client'

// ---------------------------------------------------------------------------
// BadgeSentiment
// ---------------------------------------------------------------------------
export function BadgeSentiment({ score, label }) {
  const s = Number(score)
  let bg, color
  if (s >= 0.2)       { bg = 'var(--color-background-success)'; color = 'var(--color-text-success)' }
  else if (s >= -0.2) { bg = 'var(--color-background-warning)'; color = 'var(--color-text-warning)' }
  else                { bg = 'var(--color-background-danger)';  color = 'var(--color-text-danger)'  }

  return (
    <span style={{ fontSize: 11, padding: '3px 10px', borderRadius: 20, fontWeight: 500, background: bg, color }}>
      Sentiment {label || (s >= 0.2 ? 'haussier' : s >= -0.2 ? 'neutre' : 'baissier')}
    </span>
  )
}

// ---------------------------------------------------------------------------
// CarteSignaux
// ---------------------------------------------------------------------------
export function CarteSignaux({ signaux = [], sentiments30j = [], fondamentaux, ticker }) {
  const sentimentActuel = sentiments30j[sentiments30j.length - 1]
  const sentimentPresse = sentiments30j.filter(s => s.source === 'presse').slice(-1)[0]
  const sentimentSocial = sentiments30j.filter(s => s.source === 'social').slice(-1)[0]

  return (
    <div style={{ background: 'var(--color-background-primary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-lg)', padding: '14px 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10, color: 'var(--color-text-primary)' }}>
        Sentiment & signaux IA
      </div>

      {/* Barres sentiment */}
      {[
        { label: 'Presse & analystes', score: sentimentPresse?.score },
        { label: 'Réseaux sociaux',    score: sentimentSocial?.score },
      ].map(({ label, score }) => score != null && (
        <div key={label} style={{ marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
            <span style={{ fontSize: 11, color: 'var(--color-text-secondary)' }}>{label}</span>
            <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
              {Number(score) >= 0 ? '+' : ''}{Number(score).toFixed(2)}
            </span>
          </div>
          <BarreSentiment score={Number(score)} />
        </div>
      ))}

      {/* Signaux techniques */}
      {signaux.length > 0 && (
        <div style={{ borderTop: '0.5px solid var(--color-border-tertiary)', paddingTop: 10, marginTop: 4 }}>
          {signaux.map(s => (
            <LigneSignal key={s.id} signal={s} />
          ))}
        </div>
      )}

      {signaux.length === 0 && (
        <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', textAlign: 'center', padding: '8px 0' }}>
          Aucun signal technique actif aujourd'hui
        </div>
      )}
    </div>
  )
}

function BarreSentiment({ score }) {
  const pct = Math.max(0, Math.min(100, (score + 1) / 2 * 100))
  const color = score >= 0.2 ? '#1D9E75' : score >= -0.2 ? '#BA7517' : '#E24B4A'
  return (
    <div style={{ background: 'var(--color-background-secondary)', borderRadius: 4, height: 7, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4 }} />
    </div>
  )
}

function LigneSignal({ signal }) {
  const couleur = signal.direction === 'haussier' ? 'var(--color-text-success)'
    : signal.direction === 'baissier' ? 'var(--color-text-danger)'
    : 'var(--color-text-secondary)'
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '0.5px solid var(--color-border-tertiary)', fontSize: 12 }}>
      <span style={{ color: 'var(--color-text-secondary)' }}>{signal.description || signal.type_signal_display}</span>
      <span style={{ color: couleur, fontWeight: 500, fontSize: 11, padding: '2px 8px', background: `${couleur}18`, borderRadius: 20 }}>
        {signal.direction}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FeedArticles
// ---------------------------------------------------------------------------
export function FeedArticles({ articles = [] }) {
  return (
    <div style={{ background: 'var(--color-background-primary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-lg)', padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>Actualités récentes</span>
        <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>analysées par IA</span>
      </div>

      {articles.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--color-text-tertiary)', textAlign: 'center', padding: '12px 0' }}>
          Aucun article collecté aujourd'hui
        </div>
      ) : (
        articles.slice(0, 6).map(art => (
          <ArticleItem key={art.id} article={art} />
        ))
      )}
    </div>
  )
}

function ArticleItem({ article }) {
  const score  = Number(article.score_sentiment)
  const couleur = score >= 0.2 ? '#1D9E75' : score >= -0.2 ? '#BA7517' : '#E24B4A'
  const bgBadge = score >= 0.2 ? 'var(--color-background-success)' : score >= -0.2 ? 'var(--color-background-warning)' : 'var(--color-background-danger)'
  const colorBadge = score >= 0.2 ? 'var(--color-text-success)' : score >= -0.2 ? 'var(--color-text-warning)' : 'var(--color-text-danger)'

  return (
    <div style={{ display: 'flex', gap: 8, padding: '7px 0', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
      <div style={{ width: 7, height: 7, borderRadius: '50%', background: couleur, marginTop: 4, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-primary)', textDecoration: 'none', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {article.titre_art}
        </a>
        <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 2, display: 'flex', gap: 8, alignItems: 'center' }}>
          <span>{article.source}</span>
          <span>·</span>
          <span>{new Date(article.date_pub).toLocaleDateString('fr-FR')}</span>
          <span style={{ marginLeft: 'auto', background: bgBadge, color: colorBadge, padding: '1px 6px', borderRadius: 20, fontWeight: 500 }}>
            {score >= 0 ? '+' : ''}{score.toFixed(2)}
          </span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CarteAlertes (dans la fiche titre)
// ---------------------------------------------------------------------------
export function CarteAlertes({ alertes = [], ticker }) {
  return (
    <div style={{ background: 'var(--color-background-primary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-lg)', padding: '14px 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10, color: 'var(--color-text-primary)' }}>
        Alertes récentes
      </div>
      {alertes.map(a => <AlerteItem key={a.id} alerte={a} />)}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PanneauAlertes (page alertes)
// ---------------------------------------------------------------------------
export function PanneauAlertes() {
  const [alertes,  setAlertes]  = useState([])
  const [loading,  setLoading]  = useState(true)
  const [filtre,   setFiltre]   = useState('nouvelle')

  useEffect(() => {
    setLoading(true)
    getAlertes({ statut: filtre === 'toutes' ? undefined : filtre, limit: 50 })
      .then(data => { setAlertes(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [filtre])

  const marquerVue = async (id) => {
    await updateStatutAlerte(id, { statut: 'vue' })
    setAlertes(prev => prev.map(a => a.id === id ? { ...a, statut: 'vue' } : a))
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {['nouvelle', 'vue', 'archivee', 'toutes'].map(f => (
          <button
            key={f}
            onClick={() => setFiltre(f)}
            style={{
              padding: '5px 14px', fontSize: 12, borderRadius: 'var(--border-radius-md)',
              border: `0.5px solid ${filtre === f ? 'var(--color-border-info)' : 'var(--color-border-tertiary)'}`,
              background: filtre === f ? 'var(--color-background-info)' : 'transparent',
              color: filtre === f ? 'var(--color-text-info)' : 'var(--color-text-secondary)',
              cursor: 'pointer',
            }}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }}>Chargement…</div>
      ) : alertes.length === 0 ? (
        <div style={{ color: 'var(--color-text-tertiary)', fontSize: 13, textAlign: 'center', padding: 40 }}>
          Aucune alerte {filtre !== 'toutes' ? filtre : ''}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {alertes.map(a => (
            <AlerteItem key={a.id} alerte={a} onMarquerVue={() => marquerVue(a.id)} />
          ))}
        </div>
      )}
    </div>
  )
}

function AlerteItem({ alerte, onMarquerVue }) {
  const [ouvert, setOuvert] = useState(false)
  const couleurNiveau = alerte.niveau === 'forte' ? 'var(--color-text-danger)'
    : alerte.niveau === 'moderee' ? 'var(--color-text-warning)'
    : 'var(--color-text-secondary)'
  const bgNiveau = alerte.niveau === 'forte' ? 'var(--color-background-danger)'
    : alerte.niveau === 'moderee' ? 'var(--color-background-warning)'
    : 'var(--color-background-secondary)'

  return (
    <div style={{
      background: 'var(--color-background-primary)',
      border: `0.5px solid ${alerte.statut === 'nouvelle' ? 'var(--color-border-warning)' : 'var(--color-border-tertiary)'}`,
      borderRadius: 'var(--border-radius-lg)', padding: '12px 16px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }} onClick={() => setOuvert(o => !o)}>
        <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>
          {alerte.nom_court || alerte.ticker}
        </span>
        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: bgNiveau, color: couleurNiveau, fontWeight: 500 }}>
          {alerte.niveau} · {alerte.score_confluence}/10
        </span>
        <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>
          {new Date(alerte.date_detection).toLocaleDateString('fr-FR')}
        </span>
        {alerte.statut === 'nouvelle' && onMarquerVue && (
          <button
            onClick={e => { e.stopPropagation(); onMarquerVue() }}
            style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 10px', borderRadius: 'var(--border-radius-md)', border: '0.5px solid var(--color-border-secondary)', background: 'transparent', cursor: 'pointer', color: 'var(--color-text-secondary)' }}
          >
            Marquer vue
          </button>
        )}
      </div>

      {ouvert && alerte.texte_ia && (
        <div style={{ marginTop: 10, padding: '10px 12px', background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-md)', fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.7, whiteSpace: 'pre-line' }}>
          {alerte.texte_ia}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// ListeSurveillance
// ---------------------------------------------------------------------------
export function ListeSurveillance() {
  const [titres,  setTitres]  = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getTitres('surveillance')
      .then(data => { setTitres(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ color: 'var(--color-text-tertiary)', fontSize: 13 }}>Chargement…</div>

  return (
    <div>
      <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 16, color: 'var(--color-text-primary)' }}>
        Titres en surveillance ({titres.length})
      </div>
      <div style={{ background: 'var(--color-background-primary)', border: '0.5px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-lg)', overflow: 'hidden' }}>
        {/* En-tête tableau */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 90px 90px 100px 90px', gap: 0, padding: '8px 16px', background: 'var(--color-background-secondary)', fontSize: 11, color: 'var(--color-text-tertiary)', fontWeight: 500 }}>
          <span>Titre</span>
          <span style={{ textAlign: 'right' }}>Cours</span>
          <span style={{ textAlign: 'right' }}>Variation</span>
          <span style={{ textAlign: 'right' }}>Sentiment</span>
          <span style={{ textAlign: 'right' }}>RSI</span>
        </div>
        {titres.map((t, i) => <LigneSurveillance key={t.ticker} titre={t} alterné={i % 2 === 1} />)}
        {titres.length === 0 && (
          <div style={{ padding: '20px 16px', textAlign: 'center', fontSize: 13, color: 'var(--color-text-tertiary)' }}>
            Aucun titre en surveillance. Ajouter des titres depuis la barre de recherche.
          </div>
        )}
      </div>
    </div>
  )
}

function LigneSurveillance({ titre, alterné }) {
  const dernier    = titre.dernier_cours
  const sentiment  = titre.sentiment_global
  const variation  = titre.variation_jour

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 90px 90px 100px 90px',
      padding: '10px 16px',
      background: alterné ? 'var(--color-background-secondary)' : 'transparent',
      borderBottom: '0.5px solid var(--color-border-tertiary)',
      alignItems: 'center',
    }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>{titre.nom_court || titre.ticker}</div>
        <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>{titre.ticker} · {titre.secteur}</div>
      </div>
      <div style={{ textAlign: 'right', fontSize: 13, fontWeight: 500 }}>
        {dernier ? `${Number(dernier.cloture).toFixed(2)} €` : '—'}
      </div>
      <div style={{ textAlign: 'right', fontSize: 12, color: variation >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)' }}>
        {variation != null ? `${variation >= 0 ? '+' : ''}${variation.toFixed(2)}%` : '—'}
      </div>
      <div style={{ textAlign: 'right' }}>
        {sentiment
          ? <BadgeSentiment score={Number(sentiment.score)} label={sentiment.label} />
          : <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>—</span>}
      </div>
      <div style={{ textAlign: 'right', fontSize: 12, color: getRsiColor(dernier?.rsi_14) }}>
        {dernier?.rsi_14 ? Number(dernier.rsi_14).toFixed(1) : '—'}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// QuotaBadge — état quota API dans la sidebar
// ---------------------------------------------------------------------------
export function QuotaBadge({ quota }) {
  const pct   = quota.pct_utilise
  const color = pct >= 90 ? '#E24B4A' : pct >= 70 ? '#BA7517' : '#1D9E75'
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
        <span style={{ fontSize: 10, color: 'var(--color-text-tertiary)', textTransform: 'uppercase' }}>{quota.api}</span>
        <span style={{ fontSize: 10, color }}>
          {quota.nb_requetes}/{quota.api === 'eodhd' ? 20 : quota.api === 'fmp' ? 250 : 100}
        </span>
      </div>
      <div style={{ height: 3, background: 'var(--color-background-secondary)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${Math.min(100, pct)}%`, height: '100%', background: color, borderRadius: 2 }} />
      </div>
    </div>
  )
}

// Helpers
function getRsiColor(rsi) {
  if (rsi == null) return 'var(--color-text-secondary)'
  if (rsi < 40)  return 'var(--color-text-success)'
  if (rsi > 65)  return 'var(--color-text-danger)'
  return 'var(--color-text-secondary)'
}
