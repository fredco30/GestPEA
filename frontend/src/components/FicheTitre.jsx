/**
 * frontend/src/components/FicheTitre.jsx
 * ----------------------------------------
 * Fiche complète d'un titre du portefeuille PEA.
 * Affiche : métriques clés, graphique technique, sentiment, news, alertes.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { useTitre } from '../hooks/useTitre'
import { analyserTitre, updateTitre, getDocuments, uploadDocument, deleteDocument } from '../api/client'
import GraphiqueTechnique from './GraphiqueTechnique'
import { BadgeSentiment, CarteSignaux, FeedArticles, CarteAlertes } from './utilitaires'

export default function FicheTitre({ ticker }) {
  const { titre, ohlc, periode, loading, loadingOhlc, changerPeriode, rafraichir } = useTitre(ticker)
  const [analyseEnCours, setAnalyseEnCours] = useState(false)
  const [analyseResultat, setAnalyseResultat] = useState(null)
  const [docsRefreshKey, setDocsRefreshKey] = useState(0)  // eslint-disable-line

  const lancerAnalyse = async () => {
    setAnalyseEnCours(true)
    setAnalyseResultat(null)
    try {
      const result = await analyserTitre(ticker)
      setAnalyseResultat(result)
      rafraichir()
    } catch (e) {
      setAnalyseResultat({ erreur: e.message })
    } finally {
      setAnalyseEnCours(false)
    }
  }

  if (loading) return <Squelette />
  if (!titre)  return <div style={{ color: 'var(--color-text-tertiary)', padding: 24 }}>Titre introuvable.</div>

  const dernier         = titre.prix_historique?.[titre.prix_historique.length - 1]
  const sentimentGlobal = titre.sentiments_30j?.[titre.sentiments_30j.length - 1]
  const fond            = titre.fondamentaux

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

      {/* ---- En-tête compact 2 lignes ---- */}
      <EnTeteCompact
        titre={titre}
        ticker={ticker}
        dernier={dernier}
        sentimentGlobal={sentimentGlobal}
        analyseEnCours={analyseEnCours}
        onAnalyse={lancerAnalyse}
        onRefresh={rafraichir}
        onDocUploaded={() => setDocsRefreshKey(k => k + 1)}
      />


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
        <CarteSignaux
          signaux={titre.signaux_actifs}
          sentiments30j={titre.sentiments_30j}
          fondamentaux={fond}
          ticker={ticker}
        />
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
// En-tête compact — 2 lignes
// ---------------------------------------------------------------------------

function EnTeteCompact({ titre, ticker, dernier, sentimentGlobal, analyseEnCours, onAnalyse, onRefresh, onDocUploaded }) {
  const [editPos, setEditPos]             = useState(false)
  const [nbActions, setNbActions]         = useState(titre.nb_actions || '')
  const [prixRevient, setPrixRevient]     = useState(titre.prix_revient_moyen || '')
  const [saving, setSaving]               = useState(false)
  const [showConviction, setShowConviction] = useState(false)
  const [showDocPanel, setShowDocPanel]   = useState(false)
  const [docs, setDocs]                   = useState([])
  const [typeDoc, setTypeDoc]             = useState('autre')
  const [uploading, setUploading]         = useState(false)

  const chargerDocs = useCallback(async () => {
    try { setDocs(await getDocuments(ticker)) } catch (e) { console.error(e) }
  }, [ticker])

  useEffect(() => { chargerDocs() }, [chargerDocs])

  const nb           = Number(titre.nb_actions) || 0
  const prm          = Number(titre.prix_revient_moyen) || 0
  const coursActuel  = dernier ? Number(dernier.cloture) : null
  const valeurPos    = nb && coursActuel ? nb * coursActuel : null
  const investi      = nb && prm ? nb * prm : null
  const pmv          = valeurPos && investi ? valeurPos - investi : null
  const pmvPct       = investi ? ((valeurPos - investi) / investi * 100) : null

  const handleSavePos = async () => {
    setSaving(true)
    try {
      await updateTitre(ticker, {
        nb_actions: Number(nbActions) || 0,
        prix_revient_moyen: Number(prixRevient) || null,
      })
      setEditPos(false)
      onRefresh()
    } catch (e) { console.error(e) } finally { setSaving(false) }
  }

  const handleUploadDoc = async (e) => {
    const fichier = e.target.files[0]
    if (!fichier) return
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('fichier', fichier)
      fd.append('nom', fichier.name)
      fd.append('type_doc', typeDoc)
      await uploadDocument(ticker, fd)
      setTypeDoc('autre')
      await chargerDocs()
      onDocUploaded()
    } catch (err) {
      alert('Erreur upload : ' + (err.message || 'inconnue'))
    } finally { setUploading(false) }
  }

  return (
    <div style={{
      background: 'var(--color-background-primary)',
      border: '0.5px solid var(--color-border-tertiary)',
      borderRadius: 'var(--border-radius-lg)',
      padding: '12px 16px',
    }}>

      {/* === LIGNE 1 : identité · cours · conviction · actions === */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>

        {/* Identité */}
        <div style={{ lineHeight: 1.25 }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)' }}>
            {titre.nom_court || titre.nom}
          </span>
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginLeft: 8 }}>
            {titre.ticker}
            {titre.place   ? ` · ${titre.place}`  : ''}
            {titre.secteur ? ` · ${titre.secteur}` : ''}
          </span>
        </div>

        <div style={{ width: 1, height: 28, background: 'var(--color-border-tertiary)', flexShrink: 0 }} />

        {/* Cours + variation */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 22, fontWeight: 600, color: 'var(--color-text-primary)' }}>
            {dernier
              ? `${Number(dernier.cloture).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} €`
              : '—'}
          </span>
          {dernier?.variation_pct != null && (
            <span style={{
              fontSize: 13, fontWeight: 500,
              color: dernier.variation_pct >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)',
            }}>
              {dernier.variation_pct >= 0 ? '+' : ''}{dernier.variation_pct.toFixed(2)}%
            </span>
          )}
        </div>

        {/* Mini jauge conviction (cliquable) */}
        {titre.score_conviction != null && (
          <button
            onClick={() => setShowConviction(v => !v)}
            title="Score de conviction IA — cliquer pour le détail"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          >
            <MiniGauge score={titre.score_conviction} />
          </button>
        )}

        <div style={{ flex: 1 }} />

        {/* Sentiment */}
        {sentimentGlobal && (
          <BadgeSentiment score={Number(sentimentGlobal.score)} label={sentimentGlobal.label} />
        )}

        {/* Docs */}
        <button
          onClick={() => setShowDocPanel(v => !v)}
          title="Documents du titre"
          style={{
            padding: '5px 10px', fontSize: 11, fontWeight: 500,
            background: 'var(--color-background-secondary)',
            border: `0.5px solid ${showDocPanel ? 'var(--color-text-tertiary)' : 'var(--color-border-tertiary)'}`,
            borderRadius: 'var(--border-radius-md)',
            cursor: 'pointer', color: 'var(--color-text-secondary)',
          }}
        >
          📎 Doc {docs.length > 0 && <span style={{ fontWeight: 700, color: 'var(--color-text-primary)' }}>({docs.length})</span>}
        </button>

        {/* Analyser IA */}
        <button
          onClick={onAnalyse}
          disabled={analyseEnCours}
          style={{
            padding: '5px 12px', fontSize: 12, fontWeight: 500,
            background: analyseEnCours ? 'var(--color-background-secondary)' : 'var(--color-text-primary)',
            color: analyseEnCours ? 'var(--color-text-tertiary)' : 'var(--color-background-primary)',
            border: 'none', borderRadius: 'var(--border-radius-md)',
            cursor: analyseEnCours ? 'wait' : 'pointer',
          }}
        >
          {analyseEnCours ? '⏳ Analyse...' : '✦ Analyser IA'}
        </button>
      </div>

      {/* === LIGNE 2 : pills indicateurs + position === */}
      <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>

        <PillMetrique
          label="RSI"
          valeur={dernier?.rsi_14 ? Number(dernier.rsi_14).toFixed(1) : '—'}
          couleur={getRsiCouleur(dernier?.rsi_14)}
        />
        <PillMetrique
          label="MACD"
          valeur={dernier?.macd_hist != null
            ? `${Number(dernier.macd_hist) >= 0 ? '+' : ''}${Number(dernier.macd_hist).toFixed(2)}`
            : '—'}
          couleur={dernier?.macd_hist != null
            ? (Number(dernier.macd_hist) >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)')
            : undefined}
        />
        <PillMetrique
          label="MM50 vs prix"
          valeur={dernier?.mm_50 && dernier?.cloture
            ? `${((Number(dernier.cloture) - Number(dernier.mm_50)) / Number(dernier.mm_50) * 100).toFixed(1)}%`
            : '—'}
          couleur={getEcartMmCouleur(dernier)}
        />
        <PillMetrique
          label="Sent."
          valeur={sentimentGlobal
            ? `${Number(sentimentGlobal.score) >= 0 ? '+' : ''}${Number(sentimentGlobal.score).toFixed(2)}`
            : '—'}
          couleur={sentimentGlobal?.couleur === 'success' ? 'var(--color-text-success)'
            : sentimentGlobal?.couleur === 'danger' ? 'var(--color-text-danger)'
            : 'var(--color-text-warning)'}
        />

        {/* Position portefeuille */}
        {titre.statut === 'portefeuille' && nb > 0 && !editPos && (
          <>
            <div style={{ width: 1, height: 18, background: 'var(--color-border-tertiary)', flexShrink: 0 }} />
            <PillMetrique label="Actions" valeur={nb.toLocaleString('fr-FR')} />
            <PillMetrique label="PRU" valeur={prm ? `${prm.toFixed(2)} €` : '—'} />
            <PillMetrique
              label="Valeur"
              valeur={valeurPos ? `${valeurPos.toLocaleString('fr-FR', { maximumFractionDigits: 0 })} €` : '—'}
            />
            {pmv != null && (
              <PillMetrique
                label="PV/MV"
                valeur={`${pmv >= 0 ? '+' : ''}${pmv.toLocaleString('fr-FR', { maximumFractionDigits: 0 })} € (${pmvPct >= 0 ? '+' : ''}${pmvPct.toFixed(1)}%)`}
                couleur={pmv >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)'}
              />
            )}
            <button
              onClick={() => { setEditPos(true); setNbActions(titre.nb_actions || ''); setPrixRevient(titre.prix_revient_moyen || '') }}
              title="Modifier ma position"
              style={{ fontSize: 12, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-tertiary)', padding: '2px 4px', lineHeight: 1 }}
            >
              ✏️
            </button>
          </>
        )}
        {titre.statut === 'portefeuille' && !nb && !editPos && (
          <>
            <div style={{ width: 1, height: 18, background: 'var(--color-border-tertiary)', flexShrink: 0 }} />
            <button
              onClick={() => setEditPos(true)}
              style={{
                fontSize: 11, padding: '3px 10px',
                background: 'none',
                border: '1px dashed var(--color-border-tertiary)',
                borderRadius: 'var(--border-radius-md)',
                cursor: 'pointer', color: 'var(--color-text-tertiary)',
              }}
            >
              + Saisir ma position
            </button>
          </>
        )}
      </div>

      {/* === Formulaire edition position === */}
      {editPos && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap',
          marginTop: 10, padding: '10px 12px',
          background: 'var(--color-background-secondary)',
          borderRadius: 'var(--border-radius-md)',
        }}>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 3 }}>Nb actions</label>
            <input
              type="number"
              value={nbActions}
              onChange={e => setNbActions(e.target.value)}
              style={{ width: 90, padding: '5px 8px', fontSize: 12, border: '1px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-sm)', background: 'var(--color-background-primary)', color: 'var(--color-text-primary)' }}
            />
          </div>
          <div>
            <label style={{ fontSize: 11, color: 'var(--color-text-tertiary)', display: 'block', marginBottom: 3 }}>Prix revient moyen</label>
            <input
              type="number"
              step="0.01"
              value={prixRevient}
              onChange={e => setPrixRevient(e.target.value)}
              style={{ width: 110, padding: '5px 8px', fontSize: 12, border: '1px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-sm)', background: 'var(--color-background-primary)', color: 'var(--color-text-primary)' }}
            />
          </div>
          <button
            onClick={handleSavePos}
            disabled={saving}
            style={{ padding: '5px 12px', fontSize: 11, fontWeight: 500, background: 'var(--color-text-success)', color: '#fff', border: 'none', borderRadius: 'var(--border-radius-sm)', cursor: 'pointer' }}
          >
            {saving ? '...' : 'Enregistrer'}
          </button>
          <button
            onClick={() => setEditPos(false)}
            style={{ padding: '5px 10px', fontSize: 11, background: 'none', border: '1px solid var(--color-border-tertiary)', borderRadius: 'var(--border-radius-sm)', cursor: 'pointer', color: 'var(--color-text-secondary)' }}
          >
            Annuler
          </button>
        </div>
      )}

      {/* === Panel documents (liste + upload) === */}
      {showDocPanel && (
        <div style={{ marginTop: 10, padding: '12px 14px', background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-md)' }}>

          {/* Liste des docs */}
          {docs.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginBottom: 10 }}>Aucun document ajouté.</div>
          )}
          {docs.map(doc => (
            <div key={doc.id} style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', padding: '6px 0', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
              <div>
                <a href={doc.url_fichier} target="_blank" rel="noreferrer"
                  style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-primary)', textDecoration: 'none' }}>
                  {doc.nom}
                </a>
                <span style={{ fontSize: 10, marginLeft: 8, padding: '1px 6px', borderRadius: 10, background: 'var(--color-background-primary)', color: 'var(--color-text-tertiary)' }}>
                  {doc.type_doc_display}
                </span>
                <span style={{ fontSize: 10, color: 'var(--color-text-tertiary)', marginLeft: 6 }}>
                  {new Date(doc.date_upload).toLocaleDateString('fr-FR')}
                </span>
                {doc.resume_ia && (
                  <div style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--color-text-secondary)', marginTop: 3, maxWidth: 420 }}>
                    {doc.resume_ia}
                  </div>
                )}
              </div>
              <button
                onClick={async () => { if (window.confirm(`Supprimer "${doc.nom}" ?`)) { await deleteDocument(ticker, doc.id); chargerDocs() } }}
                style={{ fontSize: 11, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-danger)', padding: '0 4px', flexShrink: 0 }}
              >✕</button>
            </div>
          ))}

          {/* Formulaire ajout */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
            <select value={typeDoc} onChange={e => setTypeDoc(e.target.value)}
              style={{ fontSize: 11, padding: '4px 8px', borderRadius: 'var(--border-radius-sm)', border: '1px solid var(--color-border-tertiary)', background: 'var(--color-background-primary)', color: 'var(--color-text-primary)' }}>
              {TYPE_DOC_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <input type="file" accept=".pdf,.docx,.xlsx,.xls,.png,.jpg,.jpeg,.txt,.csv"
              onChange={handleUploadDoc} disabled={uploading}
              style={{ fontSize: 11, color: 'var(--color-text-secondary)' }} />
            {uploading && <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>Analyse en cours...</span>}
          </div>
        </div>
      )}

      {/* === Détail conviction IA (dépliable via clic sur jauge) === */}
      {showConviction && titre.explication_conviction && (
        <div style={{
          marginTop: 10, padding: '10px 12px',
          background: 'var(--color-background-secondary)',
          borderRadius: 'var(--border-radius-md)',
          fontSize: 12, lineHeight: 1.6, color: 'var(--color-text-secondary)',
        }}>
          <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--color-text-tertiary)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Score de conviction IA
          </div>
          {titre.explication_conviction}
          {titre.date_calcul_conviction && (
            <div style={{ fontSize: 10, color: 'var(--color-text-tertiary)', marginTop: 4 }}>
              Mis à jour le {new Date(titre.date_calcul_conviction).toLocaleDateString('fr-FR')}
            </div>
          )}
        </div>
      )}

    </div>
  )
}

// ---------------------------------------------------------------------------
// Sous-composants utilitaires
// ---------------------------------------------------------------------------

function PillMetrique({ label, valeur, couleur }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 9px',
      background: 'var(--color-background-secondary)',
      borderRadius: 20,
      fontSize: 11,
      whiteSpace: 'nowrap',
    }}>
      <span style={{ color: 'var(--color-text-tertiary)' }}>{label}</span>
      <span style={{ fontWeight: 600, color: couleur || 'var(--color-text-primary)' }}>{valeur}</span>
    </div>
  )
}

function MiniGauge({ score }) {
  const couleur = score >= 70 ? 'success' : score >= 40 ? 'warning' : 'danger'
  const pct = score / 100
  // Circonférence d'un cercle r=14 : 2π×14 ≈ 87.96
  return (
    <div style={{ position: 'relative', width: 36, height: 36 }} title={`Conviction : ${score}/100`}>
      <svg width="36" height="36" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="14" fill="none" stroke="var(--color-background-secondary)" strokeWidth="3" />
        <circle
          cx="18" cy="18" r="14" fill="none"
          stroke={`var(--color-text-${couleur})`} strokeWidth="3"
          strokeDasharray={`${pct * 87.96} 87.96`}
          strokeLinecap="round"
          transform="rotate(-90 18 18)"
        />
      </svg>
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 10, fontWeight: 700, color: `var(--color-text-${couleur})`,
      }}>
        {score}
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

      {/* Analyse fondamentale IA */}
      {fond.analyse_ia && (
        <div style={{
          borderTop: '0.5px solid var(--color-border-tertiary)',
          paddingTop: 10, marginTop: ouvert ? 10 : 8,
        }}>
          <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--color-text-tertiary)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Analyse IA des fondamentaux
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--color-text-secondary)', whiteSpace: 'pre-wrap' }}>
            {fond.analyse_ia.replace(/\*\*/g, '').replace(/^#+\s.*/gm, '').trim()}
          </div>
        </div>
      )}
    </div>
  )
}

const TYPE_DOC_OPTIONS = [
  { value: 'rapport_annuel', label: 'Rapport annuel' },
  { value: 'etude_clinique', label: 'Etude clinique' },
  { value: 'news', label: 'Article / News' },
  { value: 'analyse', label: 'Analyse / Note' },
  { value: 'autre', label: 'Autre' },
]

// ---------------------------------------------------------------------------
// Helpers couleur
// ---------------------------------------------------------------------------

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
      {[120, 380, 200].map((h, i) => (
        <div key={i} style={{ height: h, background: 'var(--color-background-secondary)', borderRadius: 'var(--border-radius-lg)', animation: 'pulse 1.5s ease-in-out infinite' }} />
      ))}
    </div>
  )
}
