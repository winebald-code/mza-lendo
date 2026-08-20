/** Bacaan — Tailwind configuration.
 *  Token source of truth: the AXIOM design system (Fog ground, Signal orange,
 *  Space Grotesk display + Inter body, 24px capsule controls, 16px surfaces).
 */
module.exports = {
  content: ['./templates/**/*.html', './static/js/**/*.js', './app.py'],
  theme: {
    extend: {
      colors: {
        fog: '#F2F4F6',
        surface: '#FFFFFF',
        ink: '#07090B',
        divider: '#D7DCE2',
        signal: {
          50: '#FFF3EA',
          100: '#FFE4D1',
          200: '#FFC29A',
          300: '#FF9A57',
          400: '#FF7A24',
          500: '#FF6A00',
          600: '#DB5A00',
          700: '#B04900',
          800: '#7A3300',
          900: '#3D1A00',
        },
        slate: {
          100: '#FFFFFF',
          200: '#F2F4F6',
          300: '#D7DCE2',
          400: '#8B939E',
          500: '#5C6570',
          600: '#3A4250',
          700: '#1C222A',
          800: '#0F1216',
          900: '#07090B',
        },
        ok: '#1F8A5B',
        warn: '#D97706',
        bad: '#D62828',
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
