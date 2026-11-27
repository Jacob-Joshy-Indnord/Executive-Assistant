"use client";

/** People are named, not addressed (BR-12): a name must match exactly one person. */

import { useCallback } from "react";
import { ApiError } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import type { PersonMatch } from "@/lib/assistant/types";

export function usePeople() {
  const { client } = useAssistant();
  return useCallback(
    async (name: string, options: { peopleOnly?: boolean } = {}): Promise<PersonMatch> => {
      const wanted = name.trim();
      if (!wanted) throw new ApiError(422, "name_needed", "Name a person.");
      const found = (await client.people(wanted)).people.filter(
        (p) => !options.peopleOnly || p.kind === "person",
      );
      if (found.length === 0) throw new ApiError(422, "no_one", `No one in the organisation is called ${wanted}.`);
      if (found.length > 1) throw new ApiError(422, "many", `More than one person is called ${wanted}.`);
      return found[0]!;
    },
    [client],
  );
}
