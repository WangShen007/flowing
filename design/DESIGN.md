# Feishu CLI Web - Notion Style Redesign

Design read: a focused Feishu operations workspace for individual operators, using a calm Notion document language while preserving every existing workflow and route.

## Mode and target

- Mode: `redesign` + `product-ui` + `design-dna`
- Output: production Vue code and reusable design documentation
- Selected reference: `/Users/a11/822-831/notion-style-showcase`
- Selected direction: `notion-workspace`
- Primary risk: dense chat controls, setup panels, and template forms must remain usable on narrow screens without visual drift.

## Design DNA

The canvas uses Notion's warm neutral `#f7f6f3`, with white document surfaces and `#37352f` text. Hierarchy comes from type weight, spacing, and subtle gray borders rather than decoration. Interactive rows use a quiet `#efedea` hover and `#e3e1db` active state. Blue is reserved for clear primary actions and links.

The interface uses the operating system font stack, 4-8px radii, one-pixel `#e5e7eb` borders, and only small ambient shadows for overlays and framed tools. All color transitions finish in 150ms. No gradients, glass effects, heavy shadows, translation, or scaling are used.

Repeated blocks expose a `⋮⋮` handle on hover and keyboard focus. This visual cue is applied to history rows, quick prompts, messages, scheduled tasks, plan commands, templates, fields, and versions without implying functional drag-and-drop.

## Layout decisions

- Chat: 252px warm sidebar, compact document header, centered 760px conversation column, and a stable bottom composer.
- Login: warm full-height canvas with a restrained 420px white form surface and product identity above the form.
- Templates: compact application header followed by a three-column database/editor layout; side panels remain flat and the editor is the main white document surface.
- Mobile: the chat sidebar becomes an off-canvas navigation panel; template columns stack in task order; all controls keep at least a 40px usable target.

## Components

Reusable visual patterns are CSS-token driven: `NotionButton`, `NotionInput`, `DocumentHeader`, `SidebarRow`, `BlockHandle`, `Popover`, `StatusCallout`, and `DatabaseRow`. Existing Vue component boundaries remain intact to reduce behavioral risk.

## Interaction states

All buttons, inputs, rows, and links have visible keyboard focus. Hover changes background only. Active states deepen the same neutral background. Disabled controls reduce opacity while retaining readable labels. Loading, empty, error, success, and warning states have text labels in addition to color.

## Anti-references

- No gradients, colored glows, backdrop blur, or floating glass surfaces.
- No oversized radii, pills, heavy shadows, hover movement, or scale effects.
- No nested decorative cards or marketing-style hero composition.
- No dense icon decoration; one Lucide line icon family is used for functional affordances.

## Asset decision

All interface visuals are code-native because this is an operational product with no semantic photography, illustration, or inspectable product media. The wordmark, block handles, status marks, and controls are rendered as accessible text, CSS, and Lucide icons.

