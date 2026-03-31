/**
 * frontend/src/pages/Dashboard.jsx
 * ----------------------------------
 * Page principale de l'application.
 * Gere la navigation entre les onglets Portefeuille et Surveillance,
 * et la selection du titre actif.
 */

import React, { useState, useEffect } from 'react'
import { getTitres, getDashboard } from '../api/client'
import FicheTitre from '../components/FicheTitre'
import { ListeSurveillance, PanneauAlertes, QuotaBadge } from '../components/utilitaires'

const ONGLETS_NAV = [
  { id: 'portefeuille', label: 'Mes actions' },
  { id: 'surveillance', label: 'Surveillance' },
  { id: 'alertes',      label: 'Alertes' },
]

export default function Dashboard() {
  const [onglet,          setOnglet]         = useState('portefeuille')
  const [tickerActif,     setTickerActif]    = useState(null)
  const [titresPf,        setTitresPf]       = useState([])
  const [dashboard,       setDashboard]      = useState(null)
  const [loading,         setLoading]        = useState(true)

  // Chargement initial
  useEffect(() => {
    const init = async () => {
      try {
        const [pf, dash] = await Promise.all([
          getTitres('portefeuille'),
          getDashboard(),
        ])
        setTitresPf(pf)
        setDashboard(dash)
        if (pf.length > 0) setTickerActif(pf[0].ticker)
      } catch (e) {
        console.error('[Dashboard] init:', e)
      } finally {
        setLoading(false)
      }
    }
    init()
  }, [])

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

        {/* Navigation */}
        <nav style={{ padding: '8px 0', flex: 1, overflowY: 'auto' }}>
          {ONGLETS_NAV.map(o => (
            <NavItem
              key={o.id}
              label={o.label}
              actif={onglet === o.id}
              onClick={() => setOnglet(o.id)}
              badge={o.id === 'alertes' && dashboard?.nb_alertes_nouvelles > 0
                ? dashboard.nb_alertes_nouvelles : null}
            />
          ))}

          {/* Separateur + titres du portefeuille */}
          {onglet === 'portefeuille' && titresPf.length > 0 && (
            <>
              <div style={{ padding: '10px 16px 4px', fontSize: 11, color: 'var(--color-text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Titres
              </div>
              {titresPf.map(t => (
                <NavTitre
                  key={t.ticker}
                  titre={t}
                  actif={tickerActif === t.ticker}
                  onClick={() => setTickerActif(t.ticker)}
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
        {onglet === 'portefeuille' && tickerActif && (
          <FicheTitre ticker={tickerActif} />
        )}
        {onglet === 'surveillance' && (
          <ListeSurveillance />
        )}
        {onglet === 'alertes' && (
          <PanneauAlertes />
        )}
      </main>

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

function NavTitre({ titre, actif, onClick }) {
  const sentiment = titre.sentiment_global
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        width: '100%', padding: '6px 16px',
        background: actif ? 'var(--color-background-secondary)' : 'transparent',
        border: 'none', cursor: 'pointer', textAlign: 'left',
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
