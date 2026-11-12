# Ruixen UI components

Vendored copies (`magnetic-tabs.tsx`: one line adjusted for the React 19 types; `notification-inbox-popover.tsx`: a `labels` prop so the texts can be translated) of components from [Ruixen UI](https://www.ruixen.com) (MIT, © 2025 Ruixen UI,
see `LICENSE.md` here), installed from their shadcn registry (`https://ruixen.com/r/<name>.json`):

`spark-chart`, `magnetic-tabs`, `invert-tabs`, `notification-inbox-popover`, `clean-tag-input`,
`color-picker-input`, `better-time-picker`, `sliding-pagination`.

They are used through small adapters in `components/cms/premium.tsx`, which set the props Mas needs
(no sound, Mas colours) and keep these files untouched so they can be refreshed from the registry.
