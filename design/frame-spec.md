# Frame Specification

## Chat - desktop (1440 x 900)

- Sidebar: fixed 252px, `#f7f6f3`, right border, full viewport height.
- Main header: 48px, white, bottom border, breadcrumb at left and connection state at right.
- Conversation: centered, maximum width 760px, 40px top breathing room.
- Composer: centered, maximum width 800px, white bordered surface above a white footer band.
- Popovers: anchored above the composer, maximum width constrained to the viewport.

## Chat - tablet (768 x 1024)

- Sidebar stays hidden until invoked from the 44px header menu button.
- Conversation and composer use 24px side gutters.
- Popovers use the available viewport width and scroll internally.

## Chat - mobile (390 x 844)

- Sidebar width is `min(88vw, 320px)` and overlays the document.
- Header labels truncate rather than wrap into controls.
- Conversation uses 16px gutters; message blocks and quick prompts are single-column.
- Skill controls scroll horizontally; composer remains usable above the viewport edge.

## Login

- Full `100dvh` warm canvas.
- Form surface: 420px maximum width, white, 1px border, 8px radius, subtle shadow.
- Identity, title, supporting copy, fields, error, action, and demo credentials follow a single vertical reading order.

## Templates - desktop

- 52px white application header.
- Body grid: 288px template database, minmax 0 editor, 256px history.
- Side panels have border separation; the editor is white and scrolls naturally.
- Repeated template/version/field rows reveal a left block handle.

## Templates - narrow

- Below 1080px: template database, editor, and history stack.
- Below 640px: header/actions, form grids, field rows, and AI draft controls become one column.
- No horizontal page overflow at 390px.

