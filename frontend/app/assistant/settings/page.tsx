"use client";

import { SettingsPage } from "@/components/assistant/SettingsPage";
import { APP_BASE_PATH, appClient } from "@/lib/assistant/app";

export default function Page() {
  return <SettingsPage client={appClient()} basePath={APP_BASE_PATH} />;
}
