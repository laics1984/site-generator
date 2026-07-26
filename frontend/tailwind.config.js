/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      // Semantic tokens for the *tool* chrome only. The generated site inside
      // the preview iframe styles itself from `builder_styles` and never sees
      // these — see src/preview/lib/styles.ts.
      colors: {
        canvas: '#f6f7f9',
        surface: {
          DEFAULT: '#ffffff',
          sunken: '#f1f3f6',
        },
        line: {
          DEFAULT: '#e4e7ec',
          strong: '#cfd4dc',
        },
        ink: {
          DEFAULT: '#0f172a',
          soft: '#475569',
          muted: '#64748b',
          faint: '#94a3b8',
        },
        brand: {
          50: '#eff5ff',
          100: '#dbe8fe',
          200: '#bfd7fe',
          300: '#93bbfd',
          400: '#6096fa',
          500: '#3b76f6',
          600: '#2563eb',
          700: '#1d4fd7',
        },
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.05)',
        raised: '0 1px 2px 0 rgb(15 23 42 / 0.06), 0 4px 12px -4px rgb(15 23 42 / 0.10)',
        pop: '0 24px 48px -16px rgb(15 23 42 / 0.28)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-in-right': {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 150ms ease-out',
        'slide-in-right': 'slide-in-right 220ms cubic-bezier(0.32, 0.72, 0, 1)',
        'slide-up': 'slide-up 180ms ease-out',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
}
