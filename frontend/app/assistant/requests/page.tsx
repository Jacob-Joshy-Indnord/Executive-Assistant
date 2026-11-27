"use client";

import { RequestsForYouPage } from "@/components/assistant/RequestsForYouPage";
import { APP_BASE_PATH, appClient } from "@/lib/assistant/app";

export default function Page() {
  return <RequestsForYouPage client={appClient()} basePath={APP_BASE_PATH} />;
}
