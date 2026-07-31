export const json = (value: unknown) => JSON.stringify(value, null, 2)

export const date = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : 'Not recorded'

export const metric = (value?: number | null, suffix = '') =>
  value === null || value === undefined ? 'Not recorded' : `${value.toLocaleString()}${suffix}`

export const money = (value?: number | null) =>
  value === null || value === undefined ? 'Not recorded' : `$${value.toFixed(4)}`

export const duration = (value?: number | null) =>
  value === null || value === undefined
    ? 'Not recorded'
    : value < 1000
      ? `${value} ms`
      : `${(value / 1000).toFixed(1)} s`

export const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}

export const text = (value: unknown, fallback = 'Not recorded') =>
  typeof value === 'string' && value ? value : fallback
