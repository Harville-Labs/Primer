# 0001. Keep light and dark reading themes

- Status: Accepted
- Date: 2026-10-07

## Context

Harville Labs' visual standard uses one dark palette across its web properties.
Primer is a reading application where people spend long sessions with documents
and cited answers. Its existing interface offers light, dark, and system modes.
Removing the light mode would change that established reading preference.

Sivir UI supplies Primer's components. Its current theme contract exposes
semantic tokens for colors, type, density, motion, and elevation, so both
reading modes can share the same component behavior.

## Decision

Primer retains light and dark reading themes, with a system preference option.
It departs from the HL dark-only palette and typography. Define Primer's theme
through Sivir's semantic tokens in `apps/web/src/app.css`, and use Sivir
components for common controls.

## Alternatives considered

- **Adopt HL's dark-only palette:** It would remove Primer's established light
  reading mode and impose a different visual context on long-form reading.
- **Fork or restyle individual Sivir components:** It would duplicate component
  behavior and make library updates harder to adopt.

## Consequences

Primer's colors and typography differ from HL's other properties. New UI must
work in both modes, including focus, contrast, and system mode changes. Shared
Sivir tokens remain the single place to adjust component styling.
