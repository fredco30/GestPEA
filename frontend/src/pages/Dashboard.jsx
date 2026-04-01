/**
 * frontend/src/pages/Dashboard.jsx
 * ----------------------------------
 * Page principale — sidebar glassmorphism + contenu principal.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { getTitres, getDashboard, createTitre, deleteTitre, updateTitre } from '../api/client'
import FicheTitre from '../components/FicheTitre'
import ChatIA from '../components/ChatIA'
import { ListeSurveillance, PanneauAlertes, QuotaBadge } from '../components/utilitaires'

// ---------------------------------------------------------------------------
// Couleurs du thème sidebar
// ---------------------------------------------------------------------------

const SB = {
  bg: 'linear-gradient(180deg, #0a1628 0%, #0d2040 40%, #0e2a52 100%)',
  glass: 'rgba(16, 36, 68, 0.55)',
  glassBorder: 'rgba(0, 212, 255, 0.12)',
  cyan: '#00d4ff',
  cyanDim: 'rgba(0, 212, 255, 0.5)',
  cyanGlow: 'rgba(0, 212, 255, 0.15)',
  textPrimary: '#e8edf4',
  textSecondary: 'rgba(200, 215, 235, 0.65)',
  textTertiary: 'rgba(150, 170, 200, 0.45)',
  border: 'rgba(0, 212, 255, 0.08)',
  hoverBg: 'rgba(0, 212, 255, 0.06)',
  activeBg: 'rgba(0, 212, 255, 0.10)',
  danger: '#ff4d6a',
  success: '#22d1a0',
  warning: '#f0a030',
}

// ---------------------------------------------------------------------------
// Icônes SVG inline (outline, 18x18)
// ---------------------------------------------------------------------------

const ICONS = {
  dashboard: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      <polyline points="9 22 9 12 15 12 15 22" />
    </svg>
  ),
  portfolio: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
    </svg>
  ),
  surveillance: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  alertes: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 01-3.46 0" />
    </svg>
  ),
  axo: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <line x1="9" y1="9" x2="9.01" y2="9" />
      <line x1="15" y1="9" x2="15.01" y2="9" />
    </svg>
  ),
  performance: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  news: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z" />
      <path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z" />
    </svg>
  ),
  plus: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  ),
}

// ---------------------------------------------------------------------------
// Onglets navigation
// ---------------------------------------------------------------------------

const ONGLETS_NAV = [
  { id: 'portefeuille', label: 'Mes actions',   icon: ICONS.portfolio },
  { id: 'surveillance', label: 'Surveillance',  icon: ICONS.surveillance },
  { id: 'alertes',      label: 'Alertes',       icon: ICONS.alertes },
]

// ---------------------------------------------------------------------------
// Dashboard principal
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const [onglet,      setOnglet]      = useState('surveillance')
  const [tickerActif, setTickerActif] = useState(null)
  const [titresPf,    setTitresPf]    = useState([])
  const [titresSv,    setTitresSv]    = useState([])
  const [dashboard,   setDashboard]   = useState(null)
  const [loading,     setLoading]     = useState(true)

  useEffect(() => {
    const init = async () => {
      try {
        const [pf, sv, dash] = await Promise.all([
          getTitres('portefeuille'), getTitres('surveillance'), getDashboard(),
        ])
        setTitresPf(pf); setTitresSv(sv); setDashboard(dash)
        if (pf.length > 0) { setOnglet('portefeuille'); setTickerActif(pf[0].ticker) }
        else if (sv.length > 0) { setOnglet('surveillance'); setTickerActif(sv[0].ticker) }
      } catch (e) { console.error('[Dashboard] init:', e) }
      finally { setLoading(false) }
    }
    init()
  }, [])

  const rechargerTitres = useCallback(async () => {
    try {
      const [pf, sv] = await Promise.all([getTitres('portefeuille'), getTitres('surveillance')])
      setTitresPf(pf); setTitresSv(sv)
    } catch (e) { console.error('[Dashboard] recharger:', e) }
  }, [])

  const ajouterTitre = async (saisie, statut) => {
    const result = await createTitre({ ticker: saisie, statut })
    await rechargerTitres()
    setTickerActif(result.ticker)
    if (statut === 'portefeuille') setOnglet('portefeuille')
    else setOnglet('surveillance')
    return result
  }

  const supprimerTitre = async (ticker) => {
    await deleteTitre(ticker)
    await rechargerTitres()
    if (tickerActif === ticker) setTickerActif(null)
  }

  const changerStatut = async (ticker, nouveauStatut) => {
    await updateTitre(ticker, { statut: nouveauStatut })
    await rechargerTitres()
  }

  if (loading) return <EcranChargement />

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--color-background-tertiary)' }}>

      {/* ================================================================ */}
      {/* SIDEBAR GLASSMORPHISM                                            */}
      {/* ================================================================ */}
      <aside style={{
        width: 290, flexShrink: 0,
        background: SB.bg,
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        position: 'relative',
      }}>
        {/* Overlay glassmorphism */}
        <div style={{
          position: 'absolute', inset: 0,
          background: SB.glass,
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          borderRight: `1px solid ${SB.glassBorder}`,
          pointerEvents: 'none',
        }} />

        {/* Glow accent en haut */}
        <div style={{
          position: 'absolute', top: -60, left: '50%', transform: 'translateX(-50%)',
          width: 200, height: 200,
          background: 'radial-gradient(circle, rgba(0,212,255,0.12) 0%, transparent 70%)',
          pointerEvents: 'none',
        }} />

        {/* --- Contenu sidebar (z-index au-dessus du glass) --- */}
        <div style={{ position: 'relative', zIndex: 1, display: 'flex', flexDirection: 'column', height: '100%' }}>

          {/* === BRANDING avec bannière === */}
          <div style={{
            position: 'relative',
            height: 110,
            borderBottom: `1px solid ${SB.border}`,
            overflow: 'hidden',
          }}>
            {/* Bannière en fond, recadrée sur la mascotte */}
            <img
              src="/banniereGestPEA.png"
              alt=""
              style={{
                position: 'absolute', top: '50%', left: '50%',
                transform: 'translate(-50%, -50%)',
                width: '130%', minHeight: '100%',
                objectFit: 'cover', objectPosition: 'center center',
                opacity: 0.55,
              }}
            />
            {/* Dégradé sombre en bas pour lisibilité */}
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0, height: '60%',
              background: 'linear-gradient(to top, #0a1628 10%, transparent 100%)',
            }} />
            {/* Texte par-dessus */}
            <div style={{
              position: 'absolute', bottom: 12, left: 16, right: 16,
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <img
                src="/chatbot.png"
                alt="GestPEA"
                style={{ width: 42, height: 42, borderRadius: '50%', objectFit: 'cover', boxShadow: `0 0 16px ${SB.cyanGlow}`, border: `2px solid ${SB.glassBorder}` }}
              />
              <div>
                <div style={{
                  fontSize: 20, fontWeight: 700, letterSpacing: '0.02em',
                  background: 'linear-gradient(135deg, #00d4ff 0%, #7B61FF 100%)',
                  WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}>
                  GestPEA
                </div>
                <div style={{ fontSize: 10, color: SB.textTertiary, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                  IA · Portefeuille PEA
                </div>
              </div>
            </div>
          </div>

          {/* === STATS RAPIDES === */}
          {dashboard && (
            <div style={{ padding: '12px 16px', borderBottom: `1px solid ${SB.border}` }}>
              <StatLigne label="Valeur totale" valeur={
                dashboard.valeur_totale_portefeuille
                  ? `${Number(dashboard.valeur_totale_portefeuille).toLocaleString('fr-FR')} \u20ac`
                  : '\u2014'
              } />
              <StatLigne
                label="Variation jour"
                valeur={dashboard.variation_jour_portefeuille
                  ? `${Number(dashboard.variation_jour_portefeuille) >= 0 ? '+' : ''}${Number(dashboard.variation_jour_portefeuille).toLocaleString('fr-FR')} \u20ac`
                  : '\u2014'
                }
                couleur={Number(dashboard.variation_jour_portefeuille) >= 0 ? SB.success : SB.danger}
              />
            </div>
          )}

          {/* === FORMULAIRE AJOUT === */}
          <FormulaireAjout onAjouter={ajouterTitre} />

          {/* === NAVIGATION PRINCIPALE === */}
          <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>

            {ONGLETS_NAV.map(o => (
              <SidebarNavItem
                key={o.id}
                icon={o.icon}
                label={o.label}
                actif={onglet === o.id}
                badge={o.id === 'alertes' && dashboard?.nb_alertes_nouvelles > 0
                  ? dashboard.nb_alertes_nouvelles : null}
                onClick={() => {
                  setOnglet(o.id)
                  if (o.id === 'portefeuille' && titresPf.length > 0) setTickerActif(titresPf[0].ticker)
                  if (o.id === 'surveillance' && titresSv.length > 0) setTickerActif(titresSv[0].ticker)
                  if (o.id === 'alertes') setTickerActif(null)
                }}
              />
            ))}

            {/* Séparateur section IA */}
            <div style={{
              padding: '14px 16px 6px',
              fontSize: 9, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase',
              color: SB.cyan, opacity: 0.6,
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <span style={{ width: 16, height: 1, background: SB.cyan, opacity: 0.3 }} />
              Insights IA
              <span style={{ flex: 1, height: 1, background: SB.cyan, opacity: 0.3 }} />
            </div>

            <SidebarNavItem icon={ICONS.axo} label="Alertes Axo" actif={false} onClick={() => setOnglet('alertes')} />
            <SidebarNavItem icon={ICONS.performance} label="Performance PEA" actif={false} onClick={() => {}} />
            <SidebarNavItem icon={ICONS.news} label="Actualités" actif={false} onClick={() => {}} />

            {/* === TITRES (selon onglet) === */}
            {onglet === 'portefeuille' && titresPf.length > 0 && (
              <>
                <SectionLabel label="Portefeuille" />
                {titresPf.map(t => (
                  <NavTitre key={t.ticker} titre={t} actif={tickerActif === t.ticker}
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
                <SectionLabel label="Surveillance" />
                {titresSv.map(t => (
                  <NavTitre key={t.ticker} titre={t} actif={tickerActif === t.ticker}
                    onClick={() => setTickerActif(t.ticker)}
                    onSupprimer={() => supprimerTitre(t.ticker)}
                    onChangerStatut={() => changerStatut(t.ticker, 'portefeuille')}
                    labelStatut="vers Portefeuille"
                  />
                ))}
              </>
            )}
          </nav>

          {/* === QUOTA EN BAS === */}
          {dashboard?.quotas && (
            <div style={{ padding: '10px 14px', borderTop: `1px solid ${SB.border}` }}>
              {dashboard.quotas.map(q => (
                <div key={q.api} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                  <span style={{ fontSize: 10, color: SB.textTertiary, textTransform: 'uppercase' }}>{q.api}</span>
                  <span style={{ fontSize: 10, color: SB.textSecondary }}>{q.utilisees}/{q.limite}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>

      {/* ================================================================ */}
      {/* CONTENU PRINCIPAL                                                */}
      {/* ================================================================ */}
      <main style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
        {(onglet === 'portefeuille' || onglet === 'surveillance') && tickerActif && (
          <FicheTitre ticker={tickerActif} />
        )}
        {(onglet === 'portefeuille' || onglet === 'surveillance') && !tickerActif && (
          <div style={{ color: 'var(--color-text-tertiary)', fontSize: 14, textAlign: 'center', marginTop: 60 }}>
            Sélectionnez un titre dans la barre latérale
          </div>
        )}
        {onglet === 'alertes' && <PanneauAlertes />}
      </main>

      <ChatIA ticker={tickerActif} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Composants sidebar
// ---------------------------------------------------------------------------

function SidebarNavItem({ icon, label, actif, onClick, badge }) {
  const [hover, setHover] = useState(false)

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        width: '100%', padding: '9px 16px',
        background: actif ? SB.activeBg : hover ? SB.hoverBg : 'transparent',
        border: 'none', borderRadius: 0, cursor: 'pointer', textAlign: 'left',
        position: 'relative',
        transition: 'background 0.2s',
      }}
    >
      {/* Barre active cyan à gauche */}
      {actif && (
        <div style={{
          position: 'absolute', left: 0, top: '15%', bottom: '15%', width: 3,
          borderRadius: '0 3px 3px 0',
          background: SB.cyan,
          boxShadow: `0 0 8px ${SB.cyanDim}`,
        }} />
      )}

      {/* Glow sur hover */}
      {hover && !actif && (
        <div style={{
          position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)',
          width: 28, height: 28, borderRadius: '50%',
          background: `radial-gradient(circle, ${SB.cyanGlow} 0%, transparent 70%)`,
          pointerEvents: 'none',
        }} />
      )}

      <span style={{ color: actif ? SB.cyan : SB.textSecondary, flexShrink: 0, transition: 'color 0.2s' }}>
        {icon}
      </span>
      <span style={{
        fontSize: 14, fontWeight: actif ? 600 : 400,
        color: actif ? SB.textPrimary : SB.textSecondary,
        transition: 'color 0.2s',
      }}>
        {label}
      </span>

      {badge && (
        <span style={{
          marginLeft: 'auto',
          background: SB.cyan,
          color: '#0a1628',
          fontSize: 10, fontWeight: 700,
          padding: '1px 6px', borderRadius: 10,
          boxShadow: `0 0 6px ${SB.cyanDim}`,
        }}>
          {badge}
        </span>
      )}
    </button>
  )
}

function SectionLabel({ label }) {
  return (
    <div style={{
      padding: '12px 16px 4px',
      fontSize: 9, fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase',
      color: SB.textTertiary,
    }}>
      {label}
    </div>
  )
}

function NavTitre({ titre, actif, onClick, onSupprimer, onChangerStatut, labelStatut }) {
  const sentiment = titre.sentiment_global
  const [menuOuvert, setMenuOuvert] = useState(false)
  const [hover, setHover] = useState(false)

  return (
    <div
      style={{
        display: 'flex', alignItems: 'center',
        background: actif ? SB.activeBg : hover ? SB.hoverBg : 'transparent',
        position: 'relative',
        transition: 'background 0.2s',
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => { setHover(false); setMenuOuvert(false) }}
    >
      {actif && (
        <div style={{
          position: 'absolute', left: 0, top: '15%', bottom: '15%', width: 3,
          borderRadius: '0 3px 3px 0', background: SB.cyan, boxShadow: `0 0 8px ${SB.cyanDim}`,
        }} />
      )}

      <button
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          flex: 1, padding: '7px 8px 7px 16px',
          background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 13, fontWeight: 500,
            color: actif ? SB.textPrimary : SB.textSecondary,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {titre.nom_court || titre.ticker}
          </div>
          <div style={{
            fontSize: 12,
            color: titre.variation_jour >= 0 ? SB.success : SB.danger,
          }}>
            {titre.variation_jour != null
              ? `${titre.variation_jour >= 0 ? '+' : ''}${titre.variation_jour.toFixed(2)}%`
              : '\u2014'}
          </div>
        </div>

        {titre.score_conviction != null && (
          <span style={{
            fontSize: 9, fontWeight: 700, padding: '2px 5px', borderRadius: 4, flexShrink: 0,
            background: titre.score_conviction >= 70 ? 'rgba(34,209,160,0.15)'
              : titre.score_conviction >= 40 ? 'rgba(240,160,48,0.15)' : 'rgba(255,77,106,0.15)',
            color: titre.score_conviction >= 70 ? SB.success
              : titre.score_conviction >= 40 ? SB.warning : SB.danger,
          }}>
            {titre.score_conviction}
          </span>
        )}

        {sentiment && (
          <div style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: sentiment.couleur === 'success' ? SB.success
              : sentiment.couleur === 'danger' ? SB.danger : SB.warning,
            boxShadow: `0 0 4px ${sentiment.couleur === 'success' ? SB.success : sentiment.couleur === 'danger' ? SB.danger : SB.warning}`,
          }} />
        )}
      </button>

      <button
        onClick={(e) => { e.stopPropagation(); setMenuOuvert(o => !o) }}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          padding: '4px 8px', fontSize: 14, color: SB.textTertiary,
          opacity: actif || hover ? 1 : 0, transition: 'opacity 0.15s',
        }}
      >
        &#8942;
      </button>

      {menuOuvert && (
        <div style={{
          position: 'absolute', right: 4, top: '100%', zIndex: 10,
          background: '#0d1e38',
          border: `1px solid ${SB.glassBorder}`,
          borderRadius: 8,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          minWidth: 150, overflow: 'hidden',
          backdropFilter: 'blur(12px)',
        }}>
          <button
            onClick={() => { onChangerStatut(); setMenuOuvert(false) }}
            style={{
              width: '100%', padding: '8px 12px', background: 'none', border: 'none',
              cursor: 'pointer', textAlign: 'left', fontSize: 12, color: SB.textSecondary,
            }}
            onMouseEnter={e => e.target.style.background = SB.hoverBg}
            onMouseLeave={e => e.target.style.background = 'none'}
          >
            {labelStatut}
          </button>
          <button
            onClick={() => { if (window.confirm(`Supprimer ${titre.nom_court || titre.ticker} ?`)) { onSupprimer(); setMenuOuvert(false) } }}
            style={{
              width: '100%', padding: '8px 12px', background: 'none', border: 'none',
              cursor: 'pointer', textAlign: 'left', fontSize: 12, color: SB.danger,
            }}
            onMouseEnter={e => e.target.style.background = 'rgba(255,77,106,0.08)'}
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
    setChargement(true); setErreur(''); setSucces('')
    try {
      const result = await onAjouter(saisie.trim(), statut)
      setSucces(`${result.nom_court || result.ticker} ajouté`)
      setSaisie('')
      setTimeout(() => { setSucces(''); setOuvert(false) }, 2000)
    } catch (e) {
      setErreur(e.data?.ticker?.[0] || e.message || 'Erreur')
    } finally { setChargement(false) }
  }

  if (!ouvert) {
    return (
      <div style={{ padding: '10px 14px', borderBottom: `1px solid ${SB.border}` }}>
        <button
          onClick={() => setOuvert(true)}
          style={{
            width: '100%', padding: '8px 10px',
            background: 'transparent',
            border: `1px dashed ${SB.glassBorder}`,
            borderRadius: 8,
            cursor: 'pointer', fontSize: 12, color: SB.textSecondary,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            transition: 'border-color 0.2s, color 0.2s',
          }}
          onMouseEnter={e => { e.target.style.borderColor = SB.cyanDim; e.target.style.color = SB.cyan }}
          onMouseLeave={e => { e.target.style.borderColor = SB.glassBorder; e.target.style.color = SB.textSecondary }}
        >
          {ICONS.plus} Ajouter un titre
        </button>
      </div>
    )
  }

  return (
    <div style={{ padding: '10px 14px', borderBottom: `1px solid ${SB.border}` }}>
      <form onSubmit={handleSubmit}>
        <input
          type="text" value={saisie}
          onChange={e => setSaisie(e.target.value)}
          placeholder="Ticker, ISIN ou nom..."
          autoFocus
          style={{
            width: '100%', padding: '8px 10px', fontSize: 12,
            border: `1px solid ${SB.glassBorder}`, borderRadius: 8,
            background: 'rgba(0,0,0,0.25)', color: SB.textPrimary,
            outline: 'none', boxSizing: 'border-box',
          }}
        />
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          {['surveillance', 'portefeuille'].map(s => (
            <button key={s} type="button" onClick={() => setStatut(s)}
              style={{
                flex: 1, padding: '4px 6px', fontSize: 10,
                border: `1px solid ${statut === s ? SB.cyan : SB.glassBorder}`,
                borderRadius: 6,
                background: statut === s ? 'rgba(0,212,255,0.12)' : 'transparent',
                color: statut === s ? SB.cyan : SB.textSecondary,
                cursor: 'pointer', fontWeight: statut === s ? 600 : 400,
              }}
            >
              {s === 'surveillance' ? 'Surveillance' : 'Portefeuille'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
          <button type="submit" disabled={chargement || !saisie.trim()}
            style={{
              flex: 1, padding: '6px', fontSize: 11, fontWeight: 600,
              background: SB.cyan, color: '#0a1628',
              border: 'none', borderRadius: 6, cursor: chargement ? 'wait' : 'pointer',
              opacity: chargement || !saisie.trim() ? 0.4 : 1,
            }}>
            {chargement ? 'Ajout...' : 'Ajouter'}
          </button>
          <button type="button"
            onClick={() => { setOuvert(false); setSaisie(''); setErreur(''); setSucces('') }}
            style={{
              padding: '6px 10px', fontSize: 11,
              background: 'transparent', border: `1px solid ${SB.glassBorder}`,
              borderRadius: 6, cursor: 'pointer', color: SB.textSecondary,
            }}>
            Annuler
          </button>
        </div>
        {erreur && <div style={{ marginTop: 4, fontSize: 11, color: SB.danger }}>{erreur}</div>}
        {succes && <div style={{ marginTop: 4, fontSize: 11, color: SB.success }}>{succes}</div>}
      </form>
    </div>
  )
}

function StatLigne({ label, valeur, couleur }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '3px 0' }}>
      <span style={{ fontSize: 12, color: SB.textTertiary }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: couleur || SB.textPrimary }}>{valeur}</span>
    </div>
  )
}

function EcranChargement() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', color: SB.cyan, fontSize: 14,
      background: '#0a1628',
    }}>
      <img src="/chatbot.png" alt="" style={{ width: 48, height: 48, borderRadius: '50%', marginRight: 12, opacity: 0.7 }} />
      Chargement du portefeuille...
    </div>
  )
}
