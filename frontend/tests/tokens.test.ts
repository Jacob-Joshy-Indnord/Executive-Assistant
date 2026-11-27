/** Story 6.1: colours and text sizes only from section 13.4, readable in light and dark. */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import tailwindConfig from "@/tailwind.config";
import { BRAND_COLOURS, TEXT_SIZES, tokenCss, type BrandColour } from "@/lib/assistant/tokens";

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

function mix(fg: string, bg: string, alpha: number): string {
  const hex = (s: string, i: number) => parseInt(s.slice(i, i + 2), 16);
  return (
    "#" +
    [1, 3, 5]
      .map((i) => Math.round(hex(fg, i) * alpha + hex(bg, i) * (1 - alpha)).toString(16).padStart(2, "0"))
      .join("")
  );
}

const THEMES = [
  ["light", 0],
  ["dark", 1],
] as const;
const c = (name: BrandColour, theme: 0 | 1) => BRAND_COLOURS[name][theme];

describe("section 13.4 tokens", () => {
  it("has every colour of the table with its light and dark value", () => {
    expect(BRAND_COLOURS.bg).toEqual(["#F7F8FA", "#111320"]);
    expect(BRAND_COLOURS["zone-act-ink"]).toEqual(["#A8203E", "#FF9AA3"]);
    expect(Object.keys(BRAND_COLOURS)).toHaveLength(19);
  });

  it("gives Tailwind brand colours that accept an opacity, dark by class", () => {
    const colours = (tailwindConfig.theme?.extend?.colors as Record<string, Record<string, string>>).brand!;
    expect(colours.panel).toBe("rgb(var(--brand-panel) / <alpha-value>)");
    expect(colours["ink-muted"]).toBe("rgb(var(--brand-ink-muted) / <alpha-value>)");
    expect(tailwindConfig.darkMode).toBe("class");
  });

  it("has the five text sizes", () => {
    expect(Object.keys(TEXT_SIZES)).toEqual(["ui-xs", "ui-sm", "ui", "ui-md", "ui-title"]);
    expect(TEXT_SIZES.ui[0]).toBe("13px");
    expect(TEXT_SIZES["ui-title"][1].fontWeight).toBe("600");
  });

  it("puts the same values in the page's stylesheet", () => {
    const css = readFileSync("app/globals.css", "utf8");
    expect(css).toContain(tokenCss());
  });
});

describe.each(THEMES)("readable in the %s theme (WCAG 2.1 AA)", (_name, t) => {
  it.each(["ink", "ink-muted", "accent", "zone-good-ink", "zone-watch-ink", "zone-act-ink"] as const)(
    "%s text reads at 4.5:1 on the page and on panels",
    (token) => {
      expect(contrast(c(token, t), c("bg", t))).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c(token, t), c("panel", t))).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("reads text on the main button and on chips at 4.5:1", () => {
    expect(contrast(c("primary-fg", t), c("primary", t))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(c("ink", t), c("accent-soft-strong", t))).toBeGreaterThanOrEqual(4.5);
  });

  // Status fills and dots always come with words, so they are never the only cue.
  it("shows field edges and focus rings at 3:1", () => {
    expect(contrast(mix(c("ink-muted", t), c("panel", t), 0.8), c("panel", t))).toBeGreaterThanOrEqual(3);
    expect(contrast(c("accent", t), c("panel", t))).toBeGreaterThanOrEqual(3);
    expect(contrast(c("accent", t), c("bg", t))).toBeGreaterThanOrEqual(3);
  });
});
