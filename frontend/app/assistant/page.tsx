"use client";

import { AssistantPage } from "@/components/assistant/AssistantPage";
import { DEMO_BASE_PATH, demoClient } from "@/standins/client";

export default function Page() {
  return <AssistantPage client={demoClient()} basePath={DEMO_BASE_PATH} />;
}
