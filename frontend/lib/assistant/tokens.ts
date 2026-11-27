/**
 * The colours and text sizes of section 13.4: the only ones a screen may use.
 * Each colour is given as hex for light and dark; `tailwind.config.ts` exposes them as
 * `brand-*` colours that accept an opacity, and `app/assistant/tokens.css` switches them
 * with a `dark` class on the page.
 */
export const BRAND_COLOURS = {
  bg: ["#F7F8FA", "#111320"],
  panel: ["#FFFFFF", "#181A2A"],
  ink: ["#13161B", "#ECEEF8"],
  "ink-muted": ["#5B6472", "#A0A3BF"],
  border: ["#E6E8EC", "#9099D7"],
  accent: ["#4F46E5", "#A8A4FF"],
  "accent-soft": ["#EDECFC", "#252646"],
  "accent-soft-strong": ["#DFDEFA", "#2F2F5A"],
  primary: ["#4F46E5", "#635DE0"],
  "primary-fg": ["#FFFFFF", "#FFFFFF"],
  success: ["#0F9D7A", "#4FD08C"],
  attention: ["#FF6B4A", "#FF6E68"],
  gold: ["#C47A00", "#E7A63B"],
  "zone-good": ["#0F9D7A", "#28A894"],
  "zone-good-ink": ["#0B7A5F", "#7FDCC7"],
  "zone-watch": ["#C47A00", "#C4851F"],
  "zone-watch-ink": ["#8F5900", "#F5C97A"],
  "zone-act": ["#D12A4E", "#E2404C"],
  "zone-act-ink": ["#A8203E", "#FF9AA3"],
} as const satisfies Record<string, readonly [string, string]>;

export type BrandColour = keyof typeof BRAND_COLOURS;

/** [size, line height, extra] for each text size of section 13.4. */
export const TEXT_SIZES = {
  "ui-xs": ["10.5px", { lineHeight: "14px", letterSpacing: "0.04em" }],
  "ui-sm": ["12px", { lineHeight: "16px" }],
  ui: ["13px", { lineHeight: "18px" }],
  "ui-md": ["13.5px", { lineHeight: "20px" }],
  "ui-title": ["18px", { lineHeight: "24px", fontWeight: "600" }],
} as const;

/** "#4F46E5" → "79 70 229", the form Tailwind's `<alpha-value>` needs. */
export function channels(hex: string): string {
  const value = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16)).join(" ");
}

/** The CSS custom properties for light (`:root`) and dark (`.dark`). */
export function tokenCss(): string {
  const entries = Object.entries(BRAND_COLOURS) as [BrandColour, readonly [string, string]][];
  const block = (index: 0 | 1) =>
    entries.map(([name, pair]) => `  --brand-${name}: ${channels(pair[index])};`).join("\n");
  return `:root {\n${block(0)}\n  color-scheme: light;\n}\n.dark {\n${block(1)}\n  color-scheme: dark;\n}\n`;
}
