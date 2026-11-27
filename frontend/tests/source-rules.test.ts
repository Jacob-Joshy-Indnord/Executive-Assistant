/**
 * Story 6.1, checked over the source:
 * - controls come from the one shared-parts file, colours and text sizes only from section 13.4;
 * - nothing is loaded from another site (no fonts, scripts, styles or images);
 * - nothing is wider than a 375px screen.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function files(dir: string, ext = /\.(tsx?|css|mjs)$/): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path, ext) : ext.test(name) ? [path] : [];
  });
}

const SCREENS = [...files("app"), ...files("components")];
const SHARED_PARTS = join("components", "assistant", "parts.tsx");
const TAILWIND_PALETTE =
  /\b(?:bg|text|border|ring|outline|fill|stroke|divide|placeholder|from|to|via|decoration|shadow|accent|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|white|black)\b/;
const TAILWIND_TEXT_SIZE = /\btext-(?:xs|sm|base|lg|[2-9]?xl)\b/;
const ARBITRARY_COLOUR = /-\[(?:#|rgb|hsl)/;

describe("only shared parts and tokens", () => {
  it.each(SCREENS.filter((f) => f.endsWith(".tsx") && f !== SHARED_PARTS))(
    "%s builds its controls from the shared parts",
    (file) => {
      const source = readFileSync(file, "utf8");
      expect(source).not.toMatch(/<(button|input|select|textarea|dialog)\b/);
      expect(source).not.toMatch(/role="(dialog|switch)"/);
    },
  );

  it.each(SCREENS)("%s uses section 13.4 colours and text sizes only", (file) => {
    const source = readFileSync(file, "utf8");
    expect(source).not.toMatch(TAILWIND_PALETTE);
    expect(source).not.toMatch(TAILWIND_TEXT_SIZE);
    expect(source).not.toMatch(ARBITRARY_COLOUR);
    expect(source).not.toMatch(/style=\{\{[^}]*(color|background|fontSize)/);
  });
});

describe("nothing loaded from another site", () => {
  it.each([...SCREENS, ...files("lib"), "tailwind.config.ts", "next.config.mjs"])(
    "%s names no outside address",
    (file) => {
      const source = readFileSync(file, "utf8");
      expect(source).not.toMatch(/next\/font\/google/);
      expect(source).not.toMatch(/@import\s+url\(/);
      expect(source).not.toMatch(/(?:src|href)=["'{`]+(?:https?:)?\/\//);
      expect(source).not.toMatch(/url\(\s*["']?(?:https?:)?\/\//);
      expect(source).not.toMatch(/<(script|link)\b/);
    },
  );
});

describe("no sideways scrolling at 375px", () => {
  it.each(SCREENS.filter((f) => f.endsWith(".tsx")))("%s sets no width wider than the screen", (file) => {
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(/\b(?:min-)?w-\[(\d+)px\]/g)) {
      expect(Number(match[1])).toBeLessThanOrEqual(339);
    }
    expect(source).not.toMatch(/\b(?:min-)?w-(?:9[6-9]|[1-9]\d{2,})\b/);
    expect(source).not.toMatch(/whitespace-nowrap[^"]*\bw-full\b/);
  });
});
