"use client";

import { useCallback, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatDate, formatMoment } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { ReportView, StatementView, StatusLine } from "@/lib/assistant/types";
import { Badge, Block, Btn, ColourName, ErrorLine, List, ListRow } from "../parts";
import { useDisclosure, useOpenRows } from "../inPlace";
import { LoadFailed, Section } from "./Section";

const POINT_WORDS = {
  colour: "colour",
  reason: "reason",
  what_changed: "what changed",
  what_comes_next: "what comes next",
} as const;

/** A correction: who made it, when, and which points changed (BR-5). Never the wording
 * its owner did not confirm. */
function CorrectionDetail({ statementId }: { statementId: string }) {
  const { client } = useAssistant();
  const load = useCallback(() => client.statement(statementId), [client, statementId]);
  const { data, error } = useResource<StatementView>(load, { refresh: false });
  if (error) return <ErrorLine>{messageOf(error)}</ErrorLine>;
  if (!data?.correction) return null;
  const points = data.correction.points.map((p) => POINT_WORDS[p]);
  return (
    <p className="text-ui-sm text-brand-ink-muted">
      Corrected by {data.correction.by.display_name ?? "its owner"} on {formatMoment(data.correction.at)}.
      {points.length > 0 && ` Changed: ${points.join(", ")}.`}
    </p>
  );
}

/** One line: project, colour in words, reason, mark (section 13.1). */
export function StatusLineRow({
  line,
  open,
  onToggle,
  opener,
}: {
  line: StatusLine;
  open?: boolean;
  onToggle?: () => void;
  opener?: (element: HTMLElement | null) => void;
}) {
  return (
    <ListRow
      ref={opener}
      onClick={onToggle}
      expanded={onToggle ? open : undefined}
      title={
        <span>
          <span className="font-medium">{line.project_name}.</span>{" "}
          {line.colour && (
            <>
              <ColourName colour={line.colour} />.{" "}
            </>
          )}
          {line.reason}
        </span>
      }
      sub={
        <>
          {line.mark_words}
          {line.date && `, ${formatDate(line.date)}`}
          {line.changed_since_last && " · Changed since last report"}
        </>
      }
    >
      {open && (
        <div className="mt-2 flex flex-col gap-1 text-ui">
          {line.what_changed && (
            <p>
              <span className="text-brand-ink-muted">What changed: </span>
              {line.what_changed}
            </p>
          )}
          {line.what_comes_next && (
            <p>
              <span className="text-brand-ink-muted">What comes next: </span>
              {line.what_comes_next}
            </p>
          )}
          {line.source && (
            <p>
              <span className="text-brand-ink-muted">Source: </span>
              {line.source}
            </p>
          )}
          {line.mark === "corrected" && line.statement_id && <CorrectionDetail statementId={line.statement_id} />}
          {!line.what_changed && !line.what_comes_next && !line.source && line.mark === "no_confirmed_status" && (
            <p className="text-brand-ink-muted">No one has confirmed a status yet.</p>
          )}
        </div>
      )}
    </ListRow>
  );
}

function ReportRow({ report }: { report: ReportView }) {
  const disclosure = useDisclosure();
  return (
    <ListRow
      ref={disclosure.opener}
      onClick={disclosure.toggle}
      expanded={disclosure.open}
      title={`Report of ${formatMoment(report.created_at)}`}
      sub={
        report.changed_since_last.length
          ? `Changed since last report: ${report.changed_since_last.map((c) => c.project_name).join(", ")}`
          : `${report.lines.length} projects`
      }
    >
      {disclosure.open && (
        <List label="Report lines" className="mt-2 pl-3">
          {report.lines.map((line) => (
            <StatusLineRow key={line.project_id} line={line} />
          ))}
        </List>
      )}
    </ListRow>
  );
}

/** The reports made, read only while the part is open, and "Report now". */
function ReportsBlock({ onReported }: { onReported: () => Promise<void> }) {
  const { client } = useAssistant();
  const loadReports = useCallback(() => client.reports(), [client]);
  const reports = useResource(loadReports);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reportNow = async () => {
    setBusy(true);
    setError(null);
    try {
      await client.reportNow();
      await Promise.all([reports.reload(), onReported()]);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Block label="Reports">
      {reports.data?.reports.length ? (
        <List label="Reports made">
          {reports.data.reports.slice(0, 5).map((report) => (
            <ReportRow key={report.id} report={report} />
          ))}
        </List>
      ) : (
        <p className="text-ui text-brand-ink-muted">{reports.data ? "No reports yet." : "Reading reports."}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Btn size="sm" onClick={() => void reportNow()} disabled={busy}>
          Report now
        </Btn>
        {reports.data && reports.data.count > 0 && <Badge variant="outline">{reports.data.count} made</Badge>}
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
    </Block>
  );
}

/** Project status: confirmed words only (BR-4), opened in place. */
export function StatusSection() {
  const { client } = useAssistant();
  const loadStatus = useCallback(() => client.status(), [client]);
  const status = useResource(loadStatus);
  const disclosure = useDisclosure();
  const lines = useOpenRows();

  const data = status.data;
  const count = data?.lines.length ?? 0;
  const unconfirmed = data ? count - data.confirmed - data.awaiting_confirmation : 0;
  const summary = !data
    ? "Reading project status."
    : count === 0
      ? "You follow no projects yet."
      : [
          `${data.confirmed} confirmed`,
          data.awaiting_confirmation ? `${data.awaiting_confirmation} awaiting confirmation` : null,
          unconfirmed ? `${unconfirmed} with no confirmed status` : null,
        ]
          .filter(Boolean)
          .join(", ");

  return (
    <Section
      title="Project status"
      summary={summary}
      count={count}
      countLabel="projects followed"
      openable={{ open: disclosure.open, onToggle: disclosure.toggle, opener: disclosure.opener }}
    >
      {status.error && !data ? (
        <LoadFailed message={messageOf(status.error)} onRetry={() => void status.reload()} />
      ) : (
        <>
          {count > 0 && (
            <List label="Projects followed">
              {data!.lines.map((line) => (
                <StatusLineRow
                  key={line.project_id}
                  line={line}
                  open={lines.openId === line.project_id}
                  onToggle={() => lines.toggle(line.project_id)}
                  opener={lines.register(line.project_id)}
                />
              ))}
            </List>
          )}
          <ReportsBlock onReported={status.reload} />
        </>
      )}
    </Section>
  );
}
