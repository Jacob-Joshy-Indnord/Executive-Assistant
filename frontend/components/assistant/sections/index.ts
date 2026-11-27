/** The parts of the Assistant page in the order of section 13.1. Part 2 registers more. */

import { ApprovalsSection } from "./ApprovalsSection";
import { AttentionSection } from "./AttentionSection";
import { ChatSection } from "./ChatSection";
import { DoneSection } from "./DoneSection";
import { ItemsSection } from "./ItemsSection";
import { createSectionRegistry } from "./registry";
import { StatusSection } from "./StatusSection";

export const SECTIONS = createSectionRegistry();

SECTIONS.register({ key: "approval", title: "Needs your approval", order: 10, Component: ApprovalsSection });
SECTIONS.register({ key: "attention", title: "Needs your attention", order: 20, Component: AttentionSection });
SECTIONS.register({ key: "status", title: "Project status", order: 30, Component: StatusSection });
SECTIONS.register({ key: "items", title: "Action items", order: 40, Component: ItemsSection });
SECTIONS.register({ key: "done", title: "Done for you", order: 50, Component: DoneSection });
SECTIONS.register({ key: "chat", title: "Message your assistant", order: 60, Component: ChatSection });
