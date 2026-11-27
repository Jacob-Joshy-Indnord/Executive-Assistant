"use client";

import { RequestsForYouPage } from "@/components/assistant/RequestsForYouPage";
import { DEMO_BASE_PATH, demoClient } from "@/standins/client";

export default function Page() {
  return <RequestsForYouPage client={demoClient()} basePath={DEMO_BASE_PATH} />;
}
