"use client";

import { useCallback, useId, useState } from "react";
import { messageOf } from "@/lib/assistant/client";
import { useAssistant } from "@/lib/assistant/context";
import { formatMoment } from "@/lib/assistant/format";
import { useResource } from "@/lib/assistant/refresh";
import type { ChatMessageView } from "@/lib/assistant/types";
import { Avatar, Btn, ErrorLine, List, ListRow, Textarea } from "../parts";
import { useOpenRows } from "../inPlace";
import { WorkBadge, WorkDetail } from "../WorkDetail";
import { LoadFailed, Section } from "./Section";

const SHOWN = 6;
const UNDER_WAY = new Set(["waiting", "running"]);

function MessageRow({
  message,
  open,
  onToggle,
  opener,
  onRetried,
}: {
  message: ChatMessageView;
  open: boolean;
  onToggle: () => void;
  opener: (element: HTMLElement | null) => void;
  onRetried: () => void;
}) {
  const mine = message.author === "manager";
  const work = message.work;
  return (
    <ListRow
      ref={work ? opener : undefined}
      onClick={work ? onToggle : undefined}
      expanded={work ? open : undefined}
      avatar={<Avatar identity={mine ? "You" : "Executive Assistant"} variant={mine ? "default" : "bot"} size={24} />}
      title={<span className="whitespace-pre-wrap">{message.text}</span>}
      sub={formatMoment(message.created_at)}
      right={work && <WorkBadge state={work.state} />}
    >
      {open && work && (
        <div className="mt-2">
          <WorkDetail workId={work.id} onRetried={onRetried} />
        </div>
      )}
    </ListRow>
  );
}

/** Message your assistant: each message becomes a piece of work, opened in place. */
export function ChatSection() {
  const { client } = useAssistant();
  const load = useCallback(() => client.chat(), [client]);
  const { data, error, reload } = useResource(load);
  const rows = useOpenRows();
  const [text, setText] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const fieldId = useId();
  const errorId = useId();

  const messages = data?.messages ?? [];
  const underWay = messages.filter((m) => m.work && UNDER_WAY.has(m.work.state)).length;

  const send = async () => {
    if (!text.trim()) return;
    setSending(true);
    setSendError(null);
    try {
      await client.sendChat(text);
      setText("");
      await reload();
    } catch (failure) {
      setSendError(messageOf(failure));
    } finally {
      setSending(false);
    }
  };

  return (
    <Section
      title="Message your assistant"
      summary={underWay ? `Working on ${underWay === 1 ? "one message" : `${underWay} messages`}.` : "Ask it to remind, follow up or draft."}
      count={underWay}
      countLabel="under way"
    >
      {error && !data ? (
        <LoadFailed message={messageOf(error)} onRetry={() => void reload()} />
      ) : (
        messages.length > 0 && (
          <List label="Messages">
            {messages.slice(-SHOWN).map((message) => (
              <MessageRow
                key={message.id}
                message={message}
                open={rows.openId === message.id}
                onToggle={() => rows.toggle(message.id)}
                opener={rows.register(message.id)}
                onRetried={() => void reload()}
              />
            ))}
          </List>
        )
      )}
      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <label htmlFor={fieldId} className="sr-only">
          Message your assistant
        </label>
        <Textarea
          id={fieldId}
          rows={2}
          value={text}
          maxLength={4000}
          placeholder="Message your assistant"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void send();
            }
          }}
          aria-describedby={sendError ? errorId : undefined}
          className="flex-1"
        />
        <Btn variant="primary" type="submit" disabled={sending || !text.trim()}>
          Send
        </Btn>
      </form>
      {sendError && <ErrorLine id={errorId}>{sendError}</ErrorLine>}
    </Section>
  );
}
