/**
 * frontend/src/components/GraphiqueTechnique.jsx
 * -----------------------------------------------
 * Graphique technique complet avec Lightweight Charts (TradingView).
 * Affiche : chandeliers OHLC, volumes, MM20/50, Bollinger, RSI, MACD.
 *
 * Installation :
 *   npm install lightweight-charts
 */

import React, { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, CrosshairMode, LineStyle } from 'lightweight-charts'

// Couleurs du thème
const THEME = {
  haussier:   '#1D9E75',  // vert teal
  baissier:   '#E24B4A',  // rouge
  neutre:     '#888780',
  mm20:       '#378ADD',  // bleu
  mm50:       '#7F77DD',  // violet
  mm200:      '#888780',  // gris
  bollSup:    '#EF9F27',  // amber
  bollMid:    '#EF9F27',
  bollInf:    '#EF9F27',
  macdLine:   '#378ADD',
  macdSignal: '#E24B4A',
  macdHist:   '#1D9E75',
  rsi:        '#7F77DD',
  rsiSurvente:'#E24B4A',
  rsiSurachat:'#1D9E75',
  volume:     'rgba(136, 135, 128, 0.3)',
  bg:         '#ffffff',
  bgSombre:   '#1a1a1a',
  grille:     'rgba(0,0,0,0.05)',
  texte:      '#888780',
  bordure:    'rgba(0,0,0,0.1)',
}

const PERIODES = ['1S', '1M', '3M', '6M', '1A', '3A', 'MAX']

const INDICATEURS_DEFAUT = {
  mm20: true, mm50: true, mm200: false,
  bollinger: false, rsi: false, macd: false,
}

export default function GraphiqueTechnique({ ohlcData, ticker, loadingOhlc, periode, onChangePeriode }) {
  const containerRef   = useRef(null)
  const chartRef       = useRef(null)
  const seriesRef      = useRef({})
  const [indicateurs, setIndicateurs] = useState(INDICATEURS_DEFAUT)
  const [isDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)

  // Initialisation du chart
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background:  { color: isDark ? THEME.bgSombre : THEME.bg },
        textColor:   THEME.texte,
        fontFamily:  'Inter, system-ui, sans-serif',
        fontSize:    11,
      },
      grid: {
        vertLines:   { color: THEME.grille, style: LineStyle.Dotted },
        horzLines:   { color: THEME.grille, style: LineStyle.Dotted },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: THEME.bordure },
      timeScale: {
        borderColor:    THEME.bordure,
        timeVisible:    true,
        secondsVisible: false,
      },
      width:  containerRef.current.clientWidth,
      height: 340,
    })

    chartRef.current = chart

    // Redimensionnement responsive
    const observer = new ResizeObserver(entries => {
      if (entries[0]) {
        chart.applyOptions({ width: entries[0].contentRect.width })
      }
    })
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = {}
    }
  }, [isDark])

  // Mise à jour des données quand ohlcData change
  useEffect(() => {
    if (!chartRef.current || !ohlcData) return

    const chart  = chartRef.current
    const series = seriesRef.current
    const { ohlc, indicateurs: ind } = ohlcData

    // --- Chandeliers ---
    if (!series.candlestick) {
      series.candlestick = chart.addCandlestickSeries({
        upColor:          THEME.haussier,
        downColor:        THEME.baissier,
        borderUpColor:    THEME.haussier,
        borderDownColor:  THEME.baissier,
        wickUpColor:      THEME.haussier,
        wickDownColor:    THEME.baissier,
      })
    }
    series.candlestick.setData(ohlc)

    // --- Volume (histogramme en bas des chandeliers) ---
    if (!series.volume) {
      series.volume = chart.addHistogramSeries({
        color:       THEME.volume,
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        scaleMargins: { top: 0.85, bottom: 0 },
      })
    }
    series.volume.setData(
      ohlc.map(b => ({
        time:  b.time,
        value: b.value,
        color: b.close >= b.open
          ? 'rgba(29,158,117,0.3)'
          : 'rgba(226,75,74,0.3)',
      }))
    )

    // --- MM20 ---
    _updateLineSeries(chart, series, 'mm20',   ind.mm20,   THEME.mm20,   indicateurs.mm20)
    _updateLineSeries(chart, series, 'mm50',   ind.mm50,   THEME.mm50,   indicateurs.mm50)
    _updateLineSeries(chart, series, 'mm200',  ind.mm200,  THEME.mm200,  indicateurs.mm200)

    // --- Bollinger ---
    _updateLineSeries(chart, series, 'bollSup', ind.boll_sup, THEME.bollSup, indicateurs.bollinger, { lineStyle: LineStyle.Dashed, lineWidth: 1 })
    _updateLineSeries(chart, series, 'bollMid', ind.boll_mid, THEME.bollMid, indicateurs.bollinger, { lineStyle: LineStyle.Dotted,  lineWidth: 1 })
    _updateLineSeries(chart, series, 'bollInf', ind.boll_inf, THEME.bollInf, indicateurs.bollinger, { lineStyle: LineStyle.Dashed, lineWidth: 1 })

    chart.timeScale().fitContent()

  }, [ohlcData, indicateurs])

  // Basculer un indicateur
  const toggleIndicateur = useCallback((key) => {
    setIndicateurs(prev => {
      const next = { ...prev, [key]: !prev[key] }
      // Appliquer immédiatement la visibilité
      const series = seriesRef.current
      if (key === 'mm20'     && series.mm20)    series.mm20.applyOptions({ visible: next.mm20 })
      if (key === 'mm50'     && series.mm50)    series.mm50.applyOptions({ visible: next.mm50 })
      if (key === 'mm200'    && series.mm200)   series.mm200.applyOptions({ visible: next.mm200 })
      if (key === 'bollinger') {
        ['bollSup','bollMid','bollInf'].forEach(k => {
          if (series[k]) series[k].applyOptions({ visible: next.bollinger })
        })
      }
      return next
    })
  }, [])

  return (
    <div style={{ background: 'var(--color-background-primary)', borderRadius: 'var(--border-radius-lg)', border: '0.5px solid var(--color-border-tertiary)', padding: '14px 16px' }}>

      {/* Barre de contrôles */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>

        {/* Sélecteur de période */}
        <div style={{ display: 'flex', gap: 4 }}>
          {PERIODES.map(p => (
            <button
              key={p}
              onClick={() => onChangePeriode(p)}
              style={{
                padding: '3px 9px', fontSize: 12,
                borderRadius: 'var(--border-radius-md)',
                border: `0.5px solid ${p === periode ? 'var(--color-border-info)' : 'var(--color-border-tertiary)'}`,
                background: p === periode ? 'var(--color-background-info)' : 'transparent',
                color: p === periode ? 'var(--color-text-info)' : 'var(--color-text-secondary)',
                cursor: 'pointer', fontWeight: p === periode ? 500 : 400,
              }}
            >
              {p}
            </button>
          ))}
        </div>

        <div style={{ width: 1, height: 18, background: 'var(--color-border-tertiary)' }} />

        {/* Indicateurs */}
        {[
          { key: 'mm20',      label: 'MM20',      color: THEME.mm20  },
          { key: 'mm50',      label: 'MM50',      color: THEME.mm50  },
          { key: 'mm200',     label: 'MM200',     color: THEME.mm200 },
          { key: 'bollinger', label: 'Bollinger', color: THEME.bollSup },
        ].map(({ key, label, color }) => (
          <button
            key={key}
            onClick={() => toggleIndicateur(key)}
            style={{
              padding: '3px 10px', fontSize: 12,
              borderRadius: 'var(--border-radius-md)',
              border: `0.5px solid ${indicateurs[key] ? color : 'var(--color-border-tertiary)'}`,
              background: indicateurs[key] ? `${color}18` : 'transparent',
              color: indicateurs[key] ? color : 'var(--color-text-tertiary)',
              cursor: 'pointer', fontWeight: indicateurs[key] ? 500 : 400,
            }}
          >
            {label}
          </button>
        ))}

        {loadingOhlc && (
          <span style={{ fontSize: 11, color: 'var(--color-text-tertiary)', marginLeft: 'auto' }}>
            Chargement…
          </span>
        )}
      </div>

      {/* Conteneur du chart */}
      <div ref={containerRef} style={{ width: '100%', minHeight: 340 }} />

      {/* Légende */}
      <div style={{ display: 'flex', gap: 14, marginTop: 8, flexWrap: 'wrap' }}>
        {indicateurs.mm20 && <LegendItem color={THEME.mm20}   label="MM 20j" />}
        {indicateurs.mm50 && <LegendItem color={THEME.mm50}   label="MM 50j" />}
        {indicateurs.mm200 && <LegendItem color={THEME.mm200} label="MM 200j" />}
        {indicateurs.bollinger && <LegendItem color={THEME.bollSup} label="Bollinger" dashed />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _updateLineSeries(chart, series, key, data, color, visible, extraOptions = {}) {
  if (!data || data.length === 0) return
  if (!series[key]) {
    series[key] = chart.addLineSeries({
      color, lineWidth: 1.5, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
      visible,
      ...extraOptions,
    })
  }
  series[key].setData(data)
  series[key].applyOptions({ visible })
}

function LegendItem({ color, label, dashed }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--color-text-secondary)' }}>
      <span style={{
        width: 20, height: 2,
        background: dashed ? 'transparent' : color,
        borderTop: dashed ? `2px dashed ${color}` : 'none',
        display: 'inline-block',
      }} />
      {label}
    </span>
  )
}
