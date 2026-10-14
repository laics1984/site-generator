/**
 * The brand brief, read defensively off the opaque `DetectedBrand` the recipe
 * returns (backend: planner.DetectedBrand, grounded by
 * services/brand_brief.ground_brief).
 *
 * `DetectedBrand` stays opaque — the picker passes it back to
 * /generate/with-pages verbatim — so this is a read-only VIEW of it for the
 * "What we understood" card. An older payload, or one with nothing worth
 * showing, yields null rather than an empty card.
 */

import type { DetectedBrand } from '@/lib/types'

/** Mirrors models/brand.PERSONALITY_AXES — each axis named by its two poles. */
export const PERSONALITY_AXES = [
  ['playful_sophisticated', 'Playful', 'Sophisticated'],
  ['traditional_contemporary', 'Traditional', 'Contemporary'],
  ['minimal_expressive', 'Minimal', 'Expressive'],
  ['calm_energetic', 'Calm', 'Energetic'],
  ['organic_structured', 'Organic', 'Structured'],
  ['premium_accessible', 'Premium', 'Accessible'],
] as const

export type PersonalityAxis = (typeof PERSONALITY_AXES)[number][0]

export interface AxisReading {
  /** -2 (first pole) … +2 (second pole). */
  value: number
  /** Verbatim source text the reading rests on; null = an inference. */
  evidence: string | null
}

export interface BrandBrief {
  mood: string | null
  moodBasis: 'personality' | 'detected' | 'industry' | null
  audience: string | null
  positioning: string | null
  differentiators: string[]
  unknowns: string[]
  personality: Partial<Record<PersonalityAxis, AxisReading>>
}

const str = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(str).filter((s): s is string => s !== null) : []

function reading(value: unknown): AxisReading | null {
  if (!value || typeof value !== 'object') return null
  const raw = (value as Record<string, unknown>).value
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return {
    value: Math.max(-2, Math.min(2, Math.round(raw))),
    evidence: str((value as Record<string, unknown>).evidence),
  }
}

export function parseBrandBrief(detected: DetectedBrand | null | undefined): BrandBrief | null {
  if (!detected) return null
  const personality: BrandBrief['personality'] = {}
  const rawPersonality = detected.personality
  if (rawPersonality && typeof rawPersonality === 'object') {
    for (const [axis] of PERSONALITY_AXES) {
      const r = reading((rawPersonality as Record<string, unknown>)[axis])
      if (r) personality[axis] = r
    }
  }
  const basis = detected.mood_basis
  const brief: BrandBrief = {
    mood: str(detected.brand_mood),
    moodBasis: basis === 'personality' || basis === 'detected' || basis === 'industry' ? basis : null,
    audience: str(detected.audience),
    positioning: str(detected.positioning),
    differentiators: strings(detected.differentiators),
    unknowns: strings(detected.unknowns),
    personality,
  }
  const hasContent =
    brief.audience ||
    brief.positioning ||
    brief.differentiators.length > 0 ||
    brief.unknowns.length > 0 ||
    Object.keys(personality).length > 0
  return hasContent ? brief : null
}
