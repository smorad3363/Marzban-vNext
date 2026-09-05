---
title: vNext UI Design System
tags:
  - marzban-vnext
  - ui
  - design-system
status: implemented
---

# vNext UI Design System

## Direction

Modern premium operations console: compact, calm, readable, and low-motion. The interface uses neutral slate surfaces with blue operational accents. Gold remains an optional compatibility theme.

## Tokens

- Background: `#f4f7fb` light, `#0b1020` dark
- Surface: `#ffffff` light, `#111827` dark
- Nested surface: `#edf2f8` light, `#172033` dark
- Accent: `#2563eb` light, `#60a5fa` dark
- Panel radius: `16px`
- Control radius: `8px` to `10px`
- Control height: minimum `42px`; primary touch targets `44px`
- Fast motion: `140ms`; page motion: `220ms`

## Interaction Rules

- Focus is always visible with a two-pixel accent outline.
- Motion communicates state only and is disabled by `prefers-reduced-motion`.
- Destructive actions remain visually distinct and require confirmation or preview.
- Bulk actions operate only on explicitly checked user IDs and show a server-calculated preview.
- Owner-only routes are enforced by both API authorization and UI routing.

## Responsive and Direction

- Desktop: persistent `272px` navigation rail and dense operational content.
- Tablet: responsive content grids and collapsible detail regions.
- Mobile: stacked layout, collapsible navigation, `44px` controls, and no horizontal dependency for primary tasks.
- Persian uses RTL and Vazirmatn; English uses LTR and Fira Sans. Technical identifiers remain LTR.

## White Label

Owner settings control panel name, login title, optional description, logo, and favicon. Public branding is read before authentication. Static fallbacks use the neutral `Operations Console` identity and contain no legacy product credit.
