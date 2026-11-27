/** Story 6.1: the shared parts of section 13.3, used with the keyboard only (WCAG 2.1 AA). */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  Avatar,
  Badge,
  Btn,
  ColourName,
  Dialog,
  Empty,
  Field,
  Input,
  Kpi,
  List,
  ListRow,
  Panel,
  SecHead,
  Toggle,
} from "@/components/assistant/parts";

describe("Btn", () => {
  it("is 32px high, or 28px when small, with 6px corners", () => {
    render(
      <>
        <Btn>Save</Btn>
        <Btn size="sm">Edit</Btn>
      </>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toHaveClass("h-8", "rounded-control");
    expect(screen.getByRole("button", { name: "Edit" })).toHaveClass("h-7");
  });

  it("becomes a link with href and never submits a form by accident", () => {
    render(
      <>
        <Btn href="/assistant/settings">Open settings</Btn>
        <Btn variant="primary">Send</Btn>
      </>,
    );
    expect(screen.getByRole("link", { name: "Open settings" })).toHaveAttribute("href", "/assistant/settings");
    expect(screen.getByRole("button", { name: "Send" })).toHaveAttribute("type", "button");
  });
});

describe("Panel", () => {
  it("has 10px corners and 18px padding", () => {
    render(<Panel aria-label="Part">x</Panel>);
    expect(screen.getByText("x")).toHaveClass("rounded-panel", "p-panel");
  });
});

describe("Toggle", () => {
  it("is a switch that says On or Off in words and works with the keyboard", async () => {
    const user = userEvent.setup();
    function Host() {
      const [on, setOn] = useState(false);
      return <Toggle checked={on} onChange={setOn} label="Pause the assistant" />;
    }
    render(<Host />);
    const toggle = screen.getByRole("switch", { name: /Pause the assistant/ });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveTextContent("Off");
    await user.tab();
    expect(toggle).toHaveFocus();
    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveTextContent("On");
  });

  it("does nothing when disabled", async () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Locked" disabled />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Field", () => {
  it("labels its control and reads out its error", () => {
    render(
      <Field label="Reason" htmlFor="reason" error="Give a reason." errorId="reason-error">
        <Input id="reason" aria-describedby="reason-error" aria-invalid />
      </Field>,
    );
    expect(screen.getByLabelText("Reason")).toHaveAccessibleDescription("Give a reason.");
    expect(screen.getByRole("alert")).toHaveTextContent("Give a reason.");
  });
});

describe("Badge, Avatar, ColourName, Kpi, Empty, SecHead", () => {
  it("never tells anything by colour alone", () => {
    render(
      <>
        <Badge variant="success" dot>
          Done
        </Badge>
        <ColourName colour="amber" />
        <Avatar identity="Legal lead" />
        <Avatar identity="Executive Assistant" variant="bot" />
      </>,
    );
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("Amber")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Legal lead" })).toHaveTextContent("LL");
    expect(screen.getByRole("img", { name: "Executive Assistant" })).toBeInTheDocument();
  });

  it("shows a heading with its line and action, a number and one short empty line", () => {
    render(
      <>
        <SecHead title="Done for you" description="6 this week" action={<Btn>Open</Btn>} />
        <Kpi label="Open" value={7} delta="2 due this week" />
        <Empty title="Nothing waits for you." />
      </>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Done for you" })).toBeInTheDocument();
    expect(screen.getByText("6 this week")).toBeInTheDocument();
    expect(screen.getByText("2 due this week")).toBeInTheDocument();
    expect(screen.getByText("Nothing waits for you.")).toBeInTheDocument();
  });
});

describe("List and ListRow", () => {
  it("opens a row with a button that says whether it is open", async () => {
    const onClick = vi.fn();
    render(
      <List label="Items">
        <ListRow title="Finish the review" sub="Legal lead" onClick={onClick} expanded={false} right={<Badge>Open</Badge>} />
      </List>,
    );
    const open = screen.getByRole("button", { name: /Finish the review/ });
    expect(open).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(open);
    expect(onClick).toHaveBeenCalled();
    expect(screen.getByRole("list", { name: "Items" })).toBeInTheDocument();
  });
});

describe("Dialog", () => {
  function Host() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <Btn onClick={() => setOpen(true)}>Change level</Btn>
        {open && (
          <Dialog
            title="Carry out without approval?"
            description="It will act without asking you."
            onClose={() => setOpen(false)}
            footer={
              <>
                <Btn onClick={() => setOpen(false)}>Cancel</Btn>
                <Btn variant="primary" onClick={() => setOpen(false)}>
                  Confirm
                </Btn>
              </>
            }
          />
        )}
      </>
    );
  }

  it("takes focus, keeps Tab inside, closes with Escape and returns focus", async () => {
    const user = userEvent.setup();
    render(<Host />);
    const opener = screen.getByRole("button", { name: "Change level" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Carry out without approval?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("It will act without asking you.");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.tab();
    await user.tab();
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("returns focus after its own buttons close it", async () => {
    const user = userEvent.setup();
    render(<Host />);
    await user.click(screen.getByRole("button", { name: "Change level" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(screen.getByRole("button", { name: "Change level" })).toHaveFocus();
  });
});
