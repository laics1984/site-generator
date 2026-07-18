/**
 * PORT of webtree-public/components/blocks/ContactFormBlock.vue — keep in
 * lockstep. The field-resolution rules (which fields appear, their labels,
 * placeholders and required flags) are ported exactly, because those decide
 * how tall and how full the form looks.
 *
 * The submit path is deliberately NOT ported: upstream posts to the published
 * site's contact endpoint, which doesn't exist for an unpushed preview. The
 * form renders in its idle state and submitting does nothing — see the
 * `onSubmit` note below.
 */
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import { getNodeClasses, getNodeContentRecord, getNodeStyles, getStringField } from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'

type FieldType = 'text' | 'email' | 'tel' | 'textarea'
type FieldKey = 'firstName' | 'lastName' | 'email' | 'phone' | 'company' | 'message'

interface RuntimeFieldDefinition {
  key: string
  label: string
  type: FieldType
  placeholder: string
  required: boolean
}

const DEFAULT_FIELD_ORDER: FieldKey[] = [
  'firstName',
  'lastName',
  'email',
  'phone',
  'company',
  'message',
]
const DEFAULT_FIELD_LABELS: Record<string, string> = {
  firstName: 'First name',
  lastName: 'Last name',
  email: 'Email',
  phone: 'Phone',
  company: 'Company',
  message: 'Project brief',
}
const DEFAULT_FIELD_PLACEHOLDERS: Record<string, string> = {
  firstName: 'Jane',
  lastName: 'Tan',
  email: 'jane@company.com',
  phone: '+60 12-345 6789',
  company: 'WebTree',
  message: 'Tell us what you need help building.',
}
const DEFAULT_FIELD_TYPES: Record<string, FieldType> = {
  firstName: 'text',
  lastName: 'text',
  email: 'email',
  phone: 'tel',
  company: 'text',
  message: 'textarea',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function normalizeFieldType(value: unknown, fallback: FieldType): FieldType {
  switch (value) {
    case 'email':
    case 'tel':
    case 'textarea':
    case 'text':
      return value
    default:
      return fallback
  }
}

function FieldIcon({ fieldKey }: { fieldKey: string }) {
  const common = {
    className: 'wt-contact-form__icon',
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
  if (fieldKey === 'email') {
    return (
      <svg {...common}>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="m3 7 9 6 9-6" />
      </svg>
    )
  }
  if (fieldKey === 'phone') {
    return (
      <svg {...common}>
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.37 1.9.72 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.35 1.85.59 2.81.72A2 2 0 0 1 22 16.92z" />
      </svg>
    )
  }
  if (fieldKey === 'message') {
    return (
      <svg {...common}>
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    )
  }
  if (fieldKey === 'company') {
    return (
      <svg {...common}>
        <path d="M3 21V7a2 2 0 0 1 2-2h6v16" />
        <path d="M11 21V3h8a2 2 0 0 1 2 2v16" />
        <path d="M9 9h0M9 13h0M9 17h0M15 9h0M15 13h0M15 17h0" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  )
}

export function ContactFormBlock({ node }: { node: PublicBlockNode }) {
  const content = getNodeContentRecord(node)
  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node) as CSSProperties
  const nodeDomId = getNodeDomId(node) || undefined
  const submitLabel = getStringField(node, 'submitLabel') || 'Send enquiry'
  const baseId = String(node?.id || 'contact-form')

  const fields: RuntimeFieldDefinition[] = (() => {
    const rawFields = isRecord(content?.fields) ? (content.fields as Record<string, unknown>) : {}

    return DEFAULT_FIELD_ORDER.map((key): RuntimeFieldDefinition | null => {
      const source = isRecord(rawFields[key]) ? (rawFields[key] as Record<string, unknown>) : {}

      const mode =
        source.mode === 'off' || source.mode === 'optional' || source.mode === 'required'
          ? source.mode
          : null

      let enabled: boolean
      let required: boolean

      if (mode) {
        enabled = mode !== 'off'
        required = mode === 'required'
      } else {
        const defaultEnabled = key !== 'company'
        const defaultRequired =
          key === 'firstName' || key === 'lastName' || key === 'email' || key === 'message'
        enabled = source.enabled === undefined ? defaultEnabled : source.enabled !== false
        required = source.required === undefined ? defaultRequired && enabled : source.required === true
      }

      if (!enabled) return null

      const label =
        typeof source.label === 'string' && source.label.trim()
          ? source.label.trim()
          : DEFAULT_FIELD_LABELS[key]

      const placeholder =
        typeof source.placeholder === 'string' && source.placeholder.trim()
          ? source.placeholder.trim()
          : DEFAULT_FIELD_PLACEHOLDERS[key]

      return {
        key,
        label,
        type: normalizeFieldType(source.type, DEFAULT_FIELD_TYPES[key]),
        placeholder,
        required,
      }
    }).filter((field): field is RuntimeFieldDefinition => Boolean(field))
  })()

  return (
    <section
      className={['wt-contact-form', nodeClasses].filter(Boolean).join(' ')}
      style={nodeStyles}
      data-wt-node-id={nodeDomId}
    >
      <div className="wt-contact-form__glow" aria-hidden="true" />
      <div className="wt-contact-form__shells">
        {/* No preview endpoint to submit to — the published site posts to its
            own contact API, which this unpushed site doesn't have yet. */}
        <form className="wt-contact-form__panel" noValidate onSubmit={(e) => e.preventDefault()}>
          {fields.map((field) => (
            <label key={field.key} className="wt-contact-form__field" htmlFor={`${baseId}-${field.key}`}>
              <span className="wt-contact-form__label">
                <FieldIcon fieldKey={field.key} />
                <span className="wt-contact-form__label-text">{field.label}</span>
                {field.required && <span className="wt-contact-form__required">Required</span>}
              </span>

              {field.type === 'textarea' ? (
                <textarea
                  id={`${baseId}-${field.key}`}
                  className="wt-contact-form__control wt-contact-form__textarea"
                  name={field.key}
                  placeholder={field.placeholder}
                  rows={5}
                  defaultValue=""
                />
              ) : (
                <input
                  id={`${baseId}-${field.key}`}
                  className="wt-contact-form__control"
                  type={field.type}
                  name={field.key}
                  placeholder={field.placeholder}
                  defaultValue=""
                />
              )}
            </label>
          ))}

          <button type="submit" className="wt-contact-form__submit">
            {submitLabel}
          </button>
        </form>
      </div>
    </section>
  )
}
