"use client";

import { SettingsPage } from "@/components/assistant/SettingsPage";
import { DEMO_BASE_PATH, demoClient } from "@/standins/client";

export default function Page() {
  return <SettingsPage client={demoClient()} basePath={DEMO_BASE_PATH} />;
}
