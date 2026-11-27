"use client";

import { OrganisationSettingsPage } from "@/components/assistant/OrganisationSettingsPage";
import { APP_BASE_PATH, appClient } from "@/lib/assistant/app";

export default function Page() {
  return <OrganisationSettingsPage client={appClient()} basePath={APP_BASE_PATH} />;
}
