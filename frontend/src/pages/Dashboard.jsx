/**
 * frontend/src/pages/Dashboard.jsx
 * ----------------------------------
 * Page principale de l'application.
 * Gere la navigation entre les onglets Portefeuille et Surveillance,
 * et la selection du titre actif.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { getTitres, getDashboard, createTitre, deleteTitre, updateTitre } from '../api/client'
import FicheTitre from '../components/FicheTitre'
import ChatIA from '../components/ChatIA'
import { ListeSurveillance, PanneauAlertes, QuotaBadge } from '../components/utilitaires'

const ONGLETS_NAV = [
  { id: 'portefeuille', label: 'Mes actions' },
  { id: 'surveillance', label: 'Surveillance' },
  { id: 'alertes',      label: 'Alertes' },
]

export default function Dashboard() {
  const [onglet,          setOnglet]         = useState('surveillance')
  const [tickerActif,     setTickerActif]    = useState(null)
  const [titresPf,        setTitresPf]       = useState([])
  const [titresSv,        setTitresSv]       = useState([])
  const [dashboard,       setDashboard]      = useState(null)
  const [loading,         setLoading]        = useState(true)

  // Chargement initial
  useEffect(() => {
    const init = async () => {
      try {
        const [pf, sv, dash] = await Promise.all([
          getTitres('portefeuille'),
          getTitres('surveillance'),
          getDashboard(),
        ])
        setTitresPf(pf)
        setTitresSv(sv)
        setDashboard(dash)
        // Sélectionner le premier titre disponible
        if (pf.length > 0) {
          setOnglet('portefeuille')
          setTickerActif(pf[0].ticker)
        } else if (sv.length > 0) {
          setOnglet('surveillance')
          setTickerActif(sv[0].ticker)
        }
      } catch (e) {
        console.error('[Dashboard] init:', e)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [])

  // Recharger les listes de titres
  const rechargerTitres = useCallback(async () => {
    try {
      const [pf, sv] = await Promise.all([
        getTitres('portefeuille'),
        getTitres('surveillance'),
      ])
      setTitresPf(pf)
      setTitresSv(sv)
    } catch (e) {
      console.error('[Dashboard] recharger:', e)
    }
  }, [])

  // Ajouter un titre
  const ajouterTitre = async (saisie, statut) => {
    try {
      const result = await createTitre({ ticker: saisie, statut })
      await rechargerTitres()
      setTickerActif(result.ticker)
      if (statut === 'portefeuille') setOnglet('portefeuille')
      else setOnglet('surveillance')
      return result
    } catch (e) {
      throw e
    }
  }

  // Supprimer un titre
  const supprimerTitre = async (ticker) => {
    try {
      await deleteTitre(ticker)
      await rechargerTitres()
      if (tickerActif === ticker) setTickerActif(null)
    } catch (e) {
      console.error('[Dashboard] supprimer:', e)
    }
  }

  // Changer le statut d'un titre
  const changerStatut = async (ticker, nouveauStatut) => {
    try {
      await updateTitre(ticker, { statut: nouveauStatut })
      await rechargerTitres()
    } catch (e) {
      console.error('[Dashboard] changerStatut:', e)
    }
  }

  if (loading) return <EcranChargement />

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--color-background-tertiary)' }}>

      {/* ---------------------------------------------------------------- */}
      {/* SIDEBAR                                                           */}
      {/* ---------------------------------------------------------------- */}
      <aside style={{
        width: 220, flexShrink: 0,
        background: 'var(--color-background-primary)',
        borderRight: '0.5px solid var(--color-border-tertiary)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Logo */}
        <div style={{ padding: '16px 16px 12px', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
          <div style={{ fontSize: 15, fontWeight: 500, color: 'var(--color-text-primary)' }}>
            PEA Dashboard
          </div>
          <div style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginTop: 2 }}>
            Gestion long terme
          </div>
        </div>

        {/* Stats rapides */}
        {dashboard && (
          <div style={{ padding: '12px 16px', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
            <StatLigne label="Valeur totale" valeur={
              dashboard.valeur_totale_portefeuille
                ? `${Number(dashboard.valeur_totale_portefeuille).toLocaleString('fr-FR')} \u20ac`
                : '\u2014'
            } />
            <StatLigne
              label="Variation aujourd'hui"
              valeur={dashboard.variation_jour_portefeuille
                ? `${Number(dashboard.variation_jour_portefeuille) >= 0 ? '+' : ''}${Number(dashboard.variation_jour_portefeuille).toLocaleString('fr-FR')} \u20ac`
                : '\u2014'
              }
              couleur={Number(dashboard.variation_jour_portefeuille) >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)'}
            />
            {dashboard.nb_alertes_nouvelles > 0 && (
              <div style={{
                marginTop: 8, padding: '4px 8px',
                background: 'var(--color-background-danger)',
                borderRadius: 'var(--border-radius-md)',
                fontSize: 11, color: 'var(--color-text-danger)', fontWeight: 500,
              }}>
                {dashboard.nb_alertes_nouvelles} alerte{dashboard.nb_alertes_nouvelles > 1 ? 's' : ''} nouvelle{dashboard.nb_alertes_nouvelles > 1 ? 's' : ''}
              </div>
            )}
          </div>
        )}

        {/* Formulaire ajout titre */}
        <FormulaireAjout onAjouter={ajouterTitre} />

        {/* Navigation */}
        <nav style={{ padding: '8px 0', flex: 1, overflowY: 'auto' }}>
          {ONGLETS_NAV.map(o => (
            <NavItem
              key={o.id}
              label={o.label}
              actif={onglet === o.id}
              onClick={() => {
                setOnglet(o.id)
                if (o.id === 'portefeuille' && titresPf.length > 0) setTickerActif(titresPf[0].ticker)
                if (o.id === 'surveillance' && titresSv.length > 0) setTickerActif(titresSv[0].ticker)
                if (o.id === 'alertes') setTickerActif(null)
              }}
              badge={o.id === 'alertes' && dashboard?.nb_alertes_nouvelles > 0
                ? dashboard.nb_alertes_nouvelles : null}
            />
          ))}

          {/* Separateur + titres selon l'onglet actif */}
          {onglet === 'portefeuille' && titresPf.length > 0 && (
            <>
              <div style={{ padding: '10px 16px 4px', fontSize: 11, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Portefeuille
              </div>
              {titresPf.map(t => (
                <NavTitre
                  key={t.ticker}
                  titre={t}
                  actif={tickerActif === t.ticker}
                  onClick={() => setTickerActif(t.ticker)}
                  onSupprimer={() => supprimerTitre(t.ticker)}
                  onChangerStatut={() => changerStatut(t.ticker, 'surveillance')}
                  labelStatut="vers Surveillance"
                />
              ))}
            </>
          )}
          {onglet === 'surveillance' && titresSv.length > 0 && (
            <>
              <div style={{ padding: '10px 16px 4px', fontSize: 11, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Surveillance
              </div>
              {titresSv.map(t => (
                <NavTitre
                  key={t.ticker}
                  titre={t}
                  actif={tickerActif === t.ticker}
                  onClick={() => setTickerActif(t.ticker)}
                  onSupprimer={() => supprimerTitre(t.ticker)}
                  onChangerStatut={() => changerStatut(t.ticker, 'portefeuille')}
                  labelStatut="vers Portefeuille"
                />
              ))}
            </>
          )}
        </nav>

        {/* Quota en bas de sidebar */}
        {dashboard?.quotas && (
          <div style={{ padding: '10px 12px', borderTop: '0.5px solid var(--color-border-tertiary)' }}>
            {dashboard.quotas.map(q => (
              <QuotaBadge key={q.api} quota={q} />
            ))}
          </div>
        )}
      </aside>

      {/* ---------------------------------------------------------------- */}
      {/* CONTENU PRINCIPAL                                                 */}
      {/* ---------------------------------------------------------------- */}
      <main style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
        {(onglet === 'portefeuille' || onglet === 'surveillance') && tickerActif && (
          <FicheTitre ticker={tickerActif} />
        )}
        {(onglet === 'portefeuille' || onglet === 'surveillance') && !tickerActif && (
          <div style={{ color: 'var(--color-text-tertiary)', fontSize: 14, textAlign: 'center', marginTop: 60 }}>
            Sélectionnez un titre dans la barre latérale
          </div>
        )}
        {onglet === 'alertes' && (
          <PanneauAlertes />
        )}
      </main>

      {/* Chat IA contextuel */}
      <ChatIA ticker={tickerActif} />

    </div>
  )
}

// ---------------------------------------------------------------------------
// Sous-composants de la sidebar
// ---------------------------------------------------------------------------

function NavItem({ label, actif, onClick, badge }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        width: '100%', padding: '8px 16px',
        background: actif ? 'var(--color-background-secondary)' : 'transparent',
        border: 'none', borderRadius: 0, cursor: 'pointer', textAlign: 'left',
        fontSize: 13, fontWeight: actif ? 500 : 400,
        color: actif ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
      }}
    >
      {label}
      {badge && (
        <span style={{
          background: 'var(--color-background-danger)',
          color: 'var(--color-text-danger)',
          fontSize: 10, fontWeight: 500,
          padding: '1px 6px', borderRadius: 20,
        }}>
          {badge}
        </span>
      )}
    </button>
  )
}

function NavTitre({ titre, actif, onClick, onSupprimer, onChangerStatut, labelStatut }) {
  const sentiment = titre.sentiment_global
  const [menuOuvert, setMenuOuvert] = useState(false)

  return (
    <div
      style={{
        display: 'flex', alignItems: 'center',
        background: actif ? 'var(--color-background-secondary)' : 'transparent',
        position: 'relative',
      }}
      onMouseLeave={() => setMenuOuvert(false)}
    >
      <button
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          flex: 1, padding: '6px 8px 6px 16px',
          background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {titre.nom_court || titre.ticker}
          </div>
          <div style={{ fontSize: 11, color: titre.variation_jour >= 0 ? 'var(--color-text-success)' : 'var(--color-text-danger)' }}>
            {titre.variation_jour != null
              ? `${titre.variation_jour >= 0 ? '+' : ''}${titre.variation_jour.toFixed(2)}%`
              : '\u2014'}
          </div>
        </div>
        {sentiment && (
          <div style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: sentiment.couleur === 'success' ? '#1D9E75' : sentiment.couleur === 'danger' ? '#E24B4A' : '#BA7517',
          }} />
        )}
      </button>

      {/* Bouton menu contextuel */}
      <button
        onClick={(e) => { e.stopPropagation(); setMenuOuvert(o => !o) }}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          padding: '4px 8px', fontSize: 14, color: 'var(--color-text-tertiary)',
          opacity: actif || menuOuvert ? 1 : 0,
          transition: 'opacity 0.15s',
        }}
      >
        &#8942;
      </button>

      {/* Menu contextuel */}
      {menuOuvert && (
        <div style={{
          position: 'absolute', right: 4, top: '100%', zIndex: 10,
          background: 'var(--color-background-primary)',
          border: '1px solid var(--color-border-tertiary)',
          borderRadius: 'var(--border-radius-md)',
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          minWidth: 150, overflow: 'hidden',
        }}>
          <button
            onClick={() => { onChangerStatut(); setMenuOuvert(false) }}
            style={{
              width: '100%', padding: '8px 12px', background: 'none', border: 'none',
              cursor: 'pointer', textAlign: 'left', fontSize: 12,
              color: 'var(--color-text-secondary)',
            }}
            onMouseEnter={e => e.target.style.background = 'var(--color-background-secondary)'}
            onMouseLeave={e => e.target.style.background = 'none'}
          >
            {labelStatut}
          </button>
          <button
            onClick={() => { if (window.confirm(`Supprimer ${titre.nom_court || titre.ticker} ?`)) { onSupprimer(); setMenuOuvert(false) } }}
            style={{
              width: '100%', padding: '8px 12px', background: 'none', border: 'none',
              cursor: 'pointer', textAlign: 'left', fontSize: 12,
              color: 'var(--color-text-danger)',
            }}
            onMouseEnter={e => e.target.style.background = 'var(--color-background-danger)'}
            onMouseLeave={e => e.target.style.background = 'none'}
          >
            Supprimer
          </button>
        </div>
      )}
    </div>
  )
}

function FormulaireAjout({ onAjouter }) {
  const [ouvert, setOuvert]     = useState(false)
  const [saisie, setSaisie]     = useState('')
  const [statut, setStatut]     = useState('surveillance')
  const [chargement, setChargement] = useState(false)
  const [erreur, setErreur]     = useState('')
  const [succes, setSucces]     = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!saisie.trim()) return

    setChargement(true)
    setErreur('')
    setSucces('')
    try {
      const result = await onAjouter(saisie.trim(), statut)
      setSucces(`${result.nom_court || result.ticker} ajouté`)
      setSaisie('')
      setTimeout(() => { setSucces(''); setOuvert(false) }, 2000)
    } catch (e) {
      setErreur(e.data?.ticker?.[0] || e.message || 'Erreur')
    } finally {
      setChargement(false)
    }
  }

  if (!ouvert) {
    return (
      <div style={{ padding: '8px 12px', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
        <button
          onClick={() => setOuvert(true)}
          style={{
            width: '100%', padding: '7px 10px',
            background: 'var(--color-background-secondary)',
            border: '1px dashed var(--color-border-tertiary)',
            borderRadius: 'var(--border-radius-md)',
            cursor: 'pointer', fontSize: 12, color: 'var(--color-text-secondary)',
          }}
        >
          + Ajouter un titre
        </button>
      </div>
    )
  }

  return (
    <div style={{ padding: '10px 12px', borderBottom: '0.5px solid var(--color-border-tertiary)' }}>
      <form onSubmit={handleSubmit}>
        <input
          type="text"
          value={saisie}
          onChange={e => setSaisie(e.target.value)}
          placeholder="Ticker, ISIN ou nom..."
          autoFocus
          style={{
            width: '100%', padding: '7px 10px', fontSize: 12,
            border: '1px solid var(--color-border-tertiary)',
            borderRadius: 'var(--border-radius-md)',
            background: 'var(--color-background-primary)',
            color: 'var(--color-text-primary)',
            outline: 'none', boxSizing: 'border-box',
          }}
        />

        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          {['surveillance', 'portefeuille'].map(s => (
            <button
              key={s}
              type="button"
              onClick={() => setStatut(s)}
              style={{
                flex: 1, padding: '4px 6px', fontSize: 10,
                border: '1px solid var(--color-border-tertiary)',
                borderRadius: 'var(--border-radius-sm)',
                background: statut === s ? 'var(--color-text-primary)' : 'var(--color-background-secondary)',
                color: statut === s ? 'var(--color-background-primary)' : 'var(--color-text-secondary)',
                cursor: 'pointer', fontWeight: statut === s ? 500 : 400,
              }}
            >
              {s === 'surveillance' ? 'Surveillance' : 'Portefeuille'}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          <button
            type="submit"
            disabled={chargement || !saisie.trim()}
            style={{
              flex: 1, padding: '6px', fontSize: 11, fontWeight: 500,
              background: 'var(--color-text-success)', color: '#fff',
              border: 'none', borderRadius: 'var(--border-radius-sm)',
              cursor: chargement ? 'wait' : 'pointer',
              opacity: chargement || !saisie.trim() ? 0.5 : 1,
            }}
          >
            {chargement ? 'Ajout...' : 'Ajouter'}
          </button>
          <button
            type="button"
            onClick={() => { setOuvert(false); setSaisie(''); setErreur(''); setSucces('') }}
            style={{
              padding: '6px 10px', fontSize: 11,
              background: 'var(--color-background-secondary)',
              border: '1px solid var(--color-border-tertiary)',
              borderRadius: 'var(--border-radius-sm)',
              cursor: 'pointer', color: 'var(--color-text-secondary)',
            }}
          >
            Annuler
          </button>
        </div>

        {erreur && <div style={{ marginTop: 4, fontSize: 11, color: 'var(--color-text-danger)' }}>{erreur}</div>}
        {succes && <div style={{ marginTop: 4, fontSize: 11, color: 'var(--color-text-success)' }}>{succes}</div>}
      </form>
    </div>
  )
}

function StatLigne({ label, valeur, couleur }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '3px 0' }}>
      <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)' }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 500, color: couleur || 'var(--color-text-primary)' }}>{valeur}</span>
    </div>
  )
}

function EcranChargement() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--color-text-tertiary)', fontSize: 14 }}>
      Chargement du portefeuille...
    </div>
  )
}
