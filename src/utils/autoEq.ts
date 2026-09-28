// Picks EQ corrections from a track's measured spectrum, so the panel has a
// sensible answer for anyone who doesn't want to think about frequencies.
//
// It works by comparison, not by rule: the track is measured, then held against
// how much top end its style can carry before it reads as buzz. A style that is
// supposed to be bright is left alone at a level that would be cut on a jazz
// track. Nothing is cut unless the track actually measures over its target, so
// a clean generation comes back untouched.

import type { SpectrumBands } from './audioAnalysis';

export interface GenreTarget {
  /** Name shown in the UI. */
  name:  string;
  /** Ceiling for the 8-14 kHz share before it reads as buzz, 0-1. */
  air:   number;
  /** Ceiling for the 2.5-8 kHz share before it reads as bite, 0-1. */
  harsh: number;
}

// Targets are anchored on measurements of this app's own output: tracks that
// sound clean measured 3.1%, 4.0% and 9.5% air, and the one that audibly buzzes
// measured 17.1%. The ceilings sit just above the clean group, tilted by style —
// aggressive electronic music is meant to be bright, acoustic music isn't.
// They are ceilings, not averages of commercial records; nothing here was
// measured against real releases.
const TIERS: { target: GenreTarget; keywords: string[] }[] = [
  {
    target: { name: 'bright electronic', air: 0.11, harsh: 0.28 },
    keywords: [
      'phonk', 'trap', 'drill', 'hardstyle', 'hardcore', 'dubstep', 'hyperpop',
      'edm', 'techno', 'house', 'trance', 'rave', 'electronic', 'synthwave',
      'dnb', 'drum and bass', 'breakcore', 'metal', 'industrial',
    ],
  },
  {
    target: { name: 'soft acoustic', air: 0.07, harsh: 0.24 },
    keywords: [
      'jazz', 'lofi', 'lo-fi', 'ambient', 'classical', 'acoustic', 'orchestral',
      'piano', 'chill', 'folk', 'bossa', 'meditation', 'lullaby', 'choral',
      'string quartet', 'harp', 'nocturne',
    ],
  },
];

const DEFAULT_TARGET: GenreTarget = { name: 'general', air: 0.09, harsh: 0.26 };

/** Match the generation prompt against the style tiers. First hit wins. */
export function targetForPrompt(prompt: string | null | undefined): GenreTarget {
  const text = (prompt ?? '').toLowerCase();
  if (!text) return DEFAULT_TARGET;
  for (const tier of TIERS) {
    if (tier.keywords.some(k => text.includes(k))) return tier.target;
  }
  return DEFAULT_TARGET;
}

export interface AutoEq {
  eqMid:  number;   // dB, <= 0
  eqHigh: number;   // dB, <= 0
  /** Empty when the track needs nothing — the caller should offer RAW instead. */
  reason: string;
  target: GenreTarget;
}

// A band sitting X dB over its target gets cut by more than X. The excess is a
// share of the whole spectrum, so even an obviously buzzy track is only ~2 dB
// over; multiplying by 2.5 is what turns that into an audible correction. The
// factor is set so the measured drift phonk lands on -4.8 dB, which is where
// the hand-tuned DE-BUZZ preset ended up independently.
const PERCEPTUAL_FACTOR = 2.5;
const MAX_CUT_DB = 8;
const DEADZONE_DB = 1;   // below this, leave it alone rather than fiddle

function cutFor(measured: number, target: number): number {
  if (measured <= target) return 0;
  const excess = 10 * Math.log10(measured / target) * PERCEPTUAL_FACTOR;
  if (excess < DEADZONE_DB) return 0;
  // Round to the slider's own step so the UI and the graph agree exactly.
  return -Math.round(Math.min(excess, MAX_CUT_DB) * 2) / 2;
}

export function autoEqFor(bands: SpectrumBands, target: GenreTarget): AutoEq {
  const eqHigh = cutFor(bands.air, target.air);
  const eqMid  = cutFor(bands.harsh, target.harsh);

  const parts: string[] = [];
  if (eqHigh) parts.push(`8k+ at ${(bands.air * 100).toFixed(0)}% (${(target.air * 100).toFixed(0)}% suits ${target.name})`);
  if (eqMid)  parts.push(`3k at ${(bands.harsh * 100).toFixed(0)}% (${(target.harsh * 100).toFixed(0)}% suits ${target.name})`);

  return {
    eqMid,
    eqHigh,
    reason: parts.length ? `Measured ${parts.join(', ')}.` : '',
    target,
  };
}
