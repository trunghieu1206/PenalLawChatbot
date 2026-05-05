/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
      extend: {
          "colors": {
              "surface-container": "#edeeef",
              "outline": "#74777d",
              "surface-bright": "#f8f9fa",
              "secondary-fixed": "#ffdea5",
              "inverse-surface": "#2e3132",
              "primary-container": "#1a2b3c",
              "surface-container-highest": "#e1e3e4",
              "surface-variant": "#e1e3e4",
              "surface-container-high": "#e7e8e9",
              "primary": "#041627",
              "secondary": "#775a19",
              "surface": "#f8f9fa",
              "error-container": "#ffdad6",
              "primary-fixed-dim": "#b7c8de",
              "background": "#f8f9fa",
              "tertiary": "#001628",
              "primary-fixed": "#d2e4fb",
              "surface-dim": "#d9dadb",
              "on-tertiary-fixed": "#041d30",
              "on-secondary-fixed-variant": "#5d4201",
              "surface-container-low": "#f3f4f5",
              "tertiary-fixed": "#cee5ff",
              "inverse-on-surface": "#f0f1f2",
              "on-secondary-fixed": "#261900",
              "on-primary-fixed-variant": "#38485a",
              "on-tertiary-fixed-variant": "#33495e",
              "error": "#ba1a1a",
              "on-error-container": "#93000a",
              "secondary-fixed-dim": "#e9c176",
              "on-tertiary-container": "#7c93ab",
              "on-error": "#ffffff",
              "on-surface-variant": "#44474c",
              "on-primary-fixed": "#0b1d2d",
              "surface-tint": "#4f6073",
              "tertiary-container": "#142b3f",
              "on-primary-container": "#8192a7",
              "on-secondary-container": "#785a1a",
              "inverse-primary": "#b7c8de",
              "surface-container-lowest": "#ffffff",
              "tertiary-fixed-dim": "#b2c9e2",
              "on-background": "#191c1d",
              "on-secondary": "#ffffff",
              "outline-variant": "#c4c6cd",
              "on-tertiary": "#ffffff",
              "on-surface": "#191c1d",
              "secondary-container": "#fed488",
              "on-primary": "#ffffff"
          },
          "borderRadius": {
              "DEFAULT": "0.125rem",
              "lg": "0.25rem",
              "xl": "0.5rem",
              "full": "0.75rem"
          },
          "spacing": {
              "xl": "80px",
              "gutter": "24px",
              "base": "8px",
              "lg": "48px",
              "container-max": "1280px",
              "md": "24px",
              "sm": "12px",
              "xs": "4px"
          },
          "fontFamily": {
              "h1": ["ui-sans-serif", "system-ui", "sans-serif"],
              "body-md": ["ui-sans-serif", "system-ui", "sans-serif"],
              "label-sm": ["ui-sans-serif", "system-ui", "sans-serif"],
              "h2": ["ui-sans-serif", "system-ui", "sans-serif"],
              "h3": ["ui-sans-serif", "system-ui", "sans-serif"],
              "body-lg": ["ui-sans-serif", "system-ui", "sans-serif"]
          },
          "fontSize": {
              "h1": ["48px", { "lineHeight": "1.2", "letterSpacing": "-0.02em", "fontWeight": "600" }],
              "body-md": ["16px", { "lineHeight": "1.6", "fontWeight": "400" }],
              "label-sm": ["14px", { "lineHeight": "1", "letterSpacing": "0.05em", "fontWeight": "600" }],
              "h2": ["36px", { "lineHeight": "1.2", "fontWeight": "600" }],
              "h3": ["28px", { "lineHeight": "1.3", "fontWeight": "500" }],
              "body-lg": ["18px", { "lineHeight": "1.6", "fontWeight": "400" }]
          }
      }
  },
  plugins: [],
}
