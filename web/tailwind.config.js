/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#F4F3F1',
        foreground: '#1F1B18',
        card: {
          DEFAULT: '#FBFAF8',
          foreground: '#1F1B18',
        },
        primary: {
          DEFAULT: '#C8211B',
          hover: '#AD1B16',
          foreground: '#FFFFFF',
        },
        secondary: {
          DEFAULT: '#F4F3F1',
          foreground: '#5E5650',
        },
        muted: {
          DEFAULT: '#F6F3EF',
          foreground: '#8E8379',
        },
        accent: {
          DEFAULT: '#F8ECEA',
          foreground: '#A52A25',
        },
        destructive: {
          DEFAULT: '#C4372D',
          foreground: '#FFFFFF',
        },
        success: {
          DEFAULT: '#18864B',
          bg: '#E9F9EF',
          border: '#CDEFD8',
        },
        warning: {
          DEFAULT: '#B8860B',
          bg: '#FFF8E1',
        },
        border: '#DDD8D3',
        input: '#DDD8D3',
        ring: '#C8211B',
      },
      borderRadius: {
        sm: '8px',
        DEFAULT: '12px',
        lg: '14px',
        xl: '16px',
      },
      maxWidth: {
        content: '1280px',
        'content-wide': '1500px',
        'content-narrow': '980px',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
}
