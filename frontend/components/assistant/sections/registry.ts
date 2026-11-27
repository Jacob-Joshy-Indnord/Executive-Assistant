/**
 * The parts of the Assistant page, registered in one place (section 15, "Made to grow"):
 * each part names its place in the order of section 13.1, so part 2 adds its own parts
 * without changing these.
 */

import type { ComponentType } from "react";

export type SectionDefinition = {
  key: string;
  title: string;
  /** Its place on the page: lower comes first. */
  order: number;
  Component: ComponentType;
};

export type SectionRegistry = {
  register: (section: SectionDefinition) => void;
  all: () => SectionDefinition[];
};

export function createSectionRegistry(): SectionRegistry {
  const sections = new Map<string, SectionDefinition>();
  return {
    register(section) {
      if (sections.has(section.key)) throw new Error(`The section ${section.key} is registered twice.`);
      sections.set(section.key, section);
    },
    all() {
      return [...sections.values()].sort((a, b) => a.order - b.order);
    },
  };
}
