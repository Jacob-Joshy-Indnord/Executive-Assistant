"use client";

import { OrganisationSettingsPage } from "@/components/assistant/OrganisationSettingsPage";
import { DEMO_BASE_PATH, demoClient } from "@/standins/client";

export default function Page() {
  return <OrganisationSettingsPage client={demoClient()} basePath={DEMO_BASE_PATH} />;
}
