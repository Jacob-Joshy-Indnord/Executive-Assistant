"use client";

/**
 * Naming an owner: a person, or a virtual employee with one of its assignments. The name
 * is resolved when the form is sent (BR-12), and the assignments are offered only when the
 * name is a virtual employee's.
 */

import { useCallback, useEffect, useId, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import type { AssignmentView, PersonMatch } from "@/lib/assistant/types";
import { Field, Input, Select } from "./parts";
import { usePeople } from "./people";

export type ChosenOwner = { owner_id: string; assignment_id?: string };

export function useOwnerChoice(initialName = "") {
  const { client } = useAssistant();
  const resolvePerson = usePeople();
  const [name, setName] = useState(initialName);
  const [virtual, setVirtual] = useState<PersonMatch | null>(null);
  const [assignments, setAssignments] = useState<AssignmentView[]>([]);
  const [assignmentId, setAssignmentId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVirtual(null);
    setAssignmentId("");
  }, [name]);

  /** The chosen owner, or null with the reason shown beside the field. */
  const resolve = useCallback(async (): Promise<ChosenOwner | null> => {
    setError(null);
    try {
      const person = await resolvePerson(name);
      if (person.kind !== "virtual_employee") return { owner_id: person.id };
      if (virtual?.id !== person.id) {
        const all = (await client.assignments()).assignments;
        setAssignments(all.filter((a) => a.virtual_employee_id === person.id));
        setVirtual(person);
        setError("Choose one of the virtual employee's assignments.");
        return null;
      }
      if (!assignmentId) {
        setError("Choose one of the virtual employee's assignments.");
        return null;
      }
      return { owner_id: person.id, assignment_id: assignmentId };
    } catch (failure) {
      setError(messageOf(failure));
      return null;
    }
  }, [assignmentId, client, name, resolvePerson, virtual]);

  return { name, setName, virtual, assignments, assignmentId, setAssignmentId, error, resolve };
}

export function OwnerFields({
  choice,
  label = "Owner",
}: {
  choice: ReturnType<typeof useOwnerChoice>;
  label?: string;
}) {
  const nameId = useId();
  const assignmentFieldId = useId();
  const errorId = useId();
  return (
    <>
      <Field
        label={label}
        help="A person or virtual employee, by name."
        htmlFor={nameId}
        error={choice.virtual ? null : choice.error}
        errorId={errorId}
      >
        <Input
          id={nameId}
          value={choice.name}
          onChange={(e) => choice.setName(e.target.value)}
          autoComplete="off"
          required
          aria-invalid={!choice.virtual && !!choice.error}
          aria-describedby={choice.error && !choice.virtual ? errorId : undefined}
        />
      </Field>
      {choice.virtual && (
        <Field label="Assignment" htmlFor={assignmentFieldId} error={choice.error} errorId={`${errorId}-a`}>
          <Select
            id={assignmentFieldId}
            value={choice.assignmentId}
            onChange={(e) => choice.setAssignmentId(e.target.value)}
            aria-invalid={!!choice.error}
            aria-describedby={choice.error ? `${errorId}-a` : undefined}
          >
            <option value="">Choose an assignment</option>
            {choice.assignments.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </Select>
        </Field>
      )}
    </>
  );
}
