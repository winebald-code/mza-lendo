/** Bacaan — Tailwind configuration.
 *  Token source of truth: the AXIOM design system (Fog ground, Signal orange,
 *  Space Grotesk display + Inter body, 24px capsule controls, 16px surfaces).
 */
module.exports = {
  content: ['./templates/**/*.html', './static/js/**/*.js', './app.py'],
  theme: {
    extend: {
      /* Every colour resolves through a CSS variable holding an "R G B" triple,
       * so one variable set swaps the whole marketing site between grounds and
       * every existing utility — bg-fog, text-slate-600, border-divider — comes
       * along without a single class changing in the markup. The <alpha-value>
       * placeholder is what keeps the slash syntax (bg-fog/40) working.
       * Values live in static/css/input.css under THEME GROUNDS. */
      colors: {
        fog: 'rgb(var(--c-fog) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        ink: 'rgb(var(--c-ink) / <alpha-value>)',
        divider: 'rgb(var(--c-divider) / <alpha-value>)',
        signal: {
          50: 'rgb(var(--c-signal-50) / <alpha-value>)',
          100: '#FFE4D1',
          200: '#FFC29A',
          300: '#FF9A57',
          400: '#FF7A24',
          500: '#FF6A00',
          600: 'rgb(var(--c-signal-600) / <alpha-value>)',
          700: 'rgb(var(--c-signal-700) / <alpha-value>)',
          800: '#7A3300',
          900: '#3D1A00',
        },
      /* 100-300 and 700-900 stay literal on purpose. The dark end is used as a
       * dark surface (bg-slate-900 bands, the footer) and the light end as text
       * ON those bands, so both must hold their value in either theme. Only the
       * middle of the scale — the body-text greys — flips. */
        slate: {
          100: '#FFFFFF',
          200: '#F2F4F6',
          300: '#D7DCE2',
          400: 'rgb(var(--c-slate-400) / <alpha-value>)',
          500: 'rgb(var(--c-slate-500) / <alpha-value>)',
          600: 'rgb(var(--c-slate-600) / <alpha-value>)',
          700: '#1C222A',
          800: '#0F1216',
          900: '#07090B',
        },
        /* Status colours lift a little on the dark ground; the light-mode
         * values are tuned for contrast against white and go flat on black. */
        ok: 'rgb(var(--c-ok) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        bad: 'rgb(var(--c-bad) / <alpha-value>)',
      },
      fontFamily: {
        display: ['"Space Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        control: '24px',
        surface: '16px',
        panel: '20px',
      },
      boxShadow: {
        s1: '0 1px 0 rgba(7,9,11,0.06)',
        s2: '0 12px 32px rgba(7,9,11,0.08)',
        s3: '0 28px 64px rgba(7,9,11,0.12)',
        rail: 'inset -1px 0 0 rgba(255,255,255,0.06)',
      },
      letterSpacing: {
        kicker: '0.12em',
        label: '0.08em',
      },
      maxWidth: { shell: '1400px', prose2: '68ch' },
      keyframes: {
        pulseIn: { '0%': { transform: 'scaleY(0)', opacity: '0' }, '100%': { transform: 'scaleY(1)', opacity: '1' } },
        riseIn: { '0%': { transform: 'translateY(8px)', opacity: '0' }, '100%': { transform: 'translateY(0)', opacity: '1' } },
        sweep: { '0%': { transform: 'translateX(-100%)' }, '100%': { transform: 'translateX(220%)' } },
        blink: { '0%,49%': { opacity: '1' }, '50%,100%': { opacity: '0' } },
      },
      animation: {
        pulseIn: 'pulseIn 420ms cubic-bezier(.2,.7,.3,1) both',
        riseIn: 'riseIn 320ms cubic-bezier(.2,.7,.3,1) both',
        sweep: 'sweep 2.4s linear infinite',
        blink: 'blink 1.1s steps(1) infinite',
      },
    },
  },
  plugins: [],
};
