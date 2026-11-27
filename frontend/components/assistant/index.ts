/**
 * What Flow takes from the module's front end: each page as one component, given its base
 * path and API client, and the count for the top bar.
 */

export { AssistantPage } from "./AssistantPage";
export { RequestsForYouPage } from "./RequestsForYouPage";
export { SettingsPage } from "./SettingsPage";
export { OrganisationSettingsPage } from "./OrganisationSettingsPage";
export { WaitingCount, useWaitingCount, waitingCount } from "./WaitingCount";
export { SECTIONS } from "./sections";
export type { SectionDefinition } from "./sections/registry";
export { createAssistantClient, type AssistantClient } from "@/lib/assistant/client";
