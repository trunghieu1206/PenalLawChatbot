---
name: Lex Precision
colors:
  surface: '#f8f9fa'
  surface-dim: '#d9dadb'
  surface-bright: '#f8f9fa'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f4f5'
  surface-container: '#edeeef'
  surface-container-high: '#e7e8e9'
  surface-container-highest: '#e1e3e4'
  on-surface: '#191c1d'
  on-surface-variant: '#44474c'
  inverse-surface: '#2e3132'
  inverse-on-surface: '#f0f1f2'
  outline: '#74777d'
  outline-variant: '#c4c6cd'
  surface-tint: '#4f6073'
  primary: '#041627'
  on-primary: '#ffffff'
  primary-container: '#1a2b3c'
  on-primary-container: '#8192a7'
  inverse-primary: '#b7c8de'
  secondary: '#775a19'
  on-secondary: '#ffffff'
  secondary-container: '#fed488'
  on-secondary-container: '#785a1a'
  tertiary: '#001628'
  on-tertiary: '#ffffff'
  tertiary-container: '#142b3f'
  on-tertiary-container: '#7c93ab'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d2e4fb'
  primary-fixed-dim: '#b7c8de'
  on-primary-fixed: '#0b1d2d'
  on-primary-fixed-variant: '#38485a'
  secondary-fixed: '#ffdea5'
  secondary-fixed-dim: '#e9c176'
  on-secondary-fixed: '#261900'
  on-secondary-fixed-variant: '#5d4201'
  tertiary-fixed: '#cee5ff'
  tertiary-fixed-dim: '#b2c9e2'
  on-tertiary-fixed: '#041d30'
  on-tertiary-fixed-variant: '#33495e'
  background: '#f8f9fa'
  on-background: '#191c1d'
  surface-variant: '#e1e3e4'
typography:
  h1:
    fontFamily: Newsreader
    fontSize: 48px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: -0.02em
  h2:
    fontFamily: Newsreader
    fontSize: 36px
    fontWeight: '600'
    lineHeight: '1.2'
  h3:
    fontFamily: Newsreader
    fontSize: 28px
    fontWeight: '500'
    lineHeight: '1.3'
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.6'
  label-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: '1'
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 8px
  xs: 4px
  sm: 12px
  md: 24px
  lg: 48px
  xl: 80px
  container-max: 1280px
  gutter: 24px
---

## Brand & Style

This design system is engineered to project unwavering authority and modern efficiency for the legal sector. It balances the gravitas of traditional law with the agility of contemporary technology. The aesthetic leans heavily into **Minimalism** and **Corporate Modernism**, utilizing expansive whitespace to ensure that complex legal data remains legible and digestible.

The target audience—attorneys, legal clerks, and corporate stakeholders—requires a UI that feels organized, precise, and meticulous. Every element is designed to minimize cognitive load, replacing visual noise with structured hierarchy and intentional "breathing room." The emotional response should be one of security, clarity, and institutional trust.

## Colors

The palette is anchored by **Deep Navy Blue (#1A2B3C)**, representing stability and intellectual depth. This is contrasted against **Legal White (#F8F9FA)**, which serves as the primary canvas to prevent eye fatigue during long reading sessions.

**Refined Gold (#C5A059)** is used sparingly as a "prestige accent" for high-priority actions or indicators of excellence. **Slate Blue (#4A6076)** provides a functional secondary layer for interactive states and utility elements. Neutral tones are strictly cool-skewed to maintain a crisp, clinical professionalism.

## Typography

The typography strategy employs a classic "Serif for Structure, Sans for Utility" approach. **Newsreader** is utilized for headings to evoke the heritage of printed legal documents and judicial authority. Its literary qualities provide a sophisticated rhythm to the page.

**Inter** is the workhorse for all body copy, forms, and data grids. Its high x-height and neutral character ensure maximum legibility even in dense, multi-clause paragraphs. Small labels use increased letter-spacing and uppercase styling to denote metadata and categorical information clearly.

## Layout & Spacing

This design system adopts a **Fixed Grid** model for desktop views to ensure that information occupies predictable, authoritative positions on the screen. A 12-column system is used with generous 24px gutters.

The spacing rhythm is strictly based on an 8px baseline. To handle dense legal information, the layout prioritizes vertical "chunking"—using 48px to 80px of whitespace between major content sections to prevent the UI from feeling claustrophobic. Margins within components (like cards) should be generous to signify that every piece of information has the room to be considered carefully.

## Elevation & Depth

Depth is conveyed through **low-contrast outlines** and **ambient shadows**. The design system avoids high-elevation floating effects to stay grounded and serious. 

1.  **Level 0 (Base):** The Legal White surface.
2.  **Level 1 (Cards/Sections):** A 1px border in `#E2E8F0` with a very soft, diffused shadow (0px 4px 20px rgba(26, 43, 60, 0.05)).
3.  **Level 2 (Modals/Popovers):** A slightly more defined shadow (0px 12px 32px rgba(26, 43, 60, 0.12)) to indicate temporary focus.

Surface-container tiers are used to differentiate "navigation" zones from "content" zones without relying on heavy color fills.

## Shapes

The shape language is conservative and disciplined. A **Soft (Level 1)** roundedness is applied to buttons and input fields (0.25rem), providing just enough modernity to feel current without losing the "sharpness" associated with legal precision. 

Larger containers like cards may use `rounded-lg` (0.5rem) to slightly soften the overall interface, but circles and organic shapes are strictly avoided. All icons should follow a consistent line-weight, matching the stroke of the body typography.

## Components

### Buttons
Primary buttons are solid Deep Navy (#1A2B3C) with white text, signifying finality and action. Secondary buttons use a Slate Blue outline. The "Gold" accent is reserved for "Premium" actions or status indicators (e.g., "Certified" or "Verified").

### Input Fields
Inputs use a crisp 1px border. Focus states transition the border to Deep Navy with a subtle 2px outer glow. Labels are always positioned above the field in a bold, smaller-scale Inter font for clarity.

### Cards & Document Lists
Cards are the primary container for legal matters. They feature a thin border and no background fill on Level 0 surfaces. Document lists should include clear "Type" icons (PDF, Docx) and use alternating row highlights in a very faint grey for scanability.

### Specialized Components
*   **Status Badges:** Use a "Verdict" style—pill-shaped with low-saturation background tints (e.g., pale green for "Executed," pale gold for "Pending Review").
*   **Breadcrumbs:** Essential for navigating deep legal folder structures; always visible and rendered in Slate Blue.
*   **Clause Highlighter:** A specific UI pattern for the document viewer that uses a soft gold background fill to indicate selected or flagged legal text.