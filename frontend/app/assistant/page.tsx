"use client";

import { AssistantPage } from "@/components/assistant/AssistantPage";
import { APP_BASE_PATH, appClient } from "@/lib/assistant/app";

export default function Page() {
  return <AssistantPage client={appClient()} basePath={APP_BASE_PATH} />;
}
