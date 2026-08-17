import { OrchestrationView } from "@/components/orchestration/orchestration-view";

export default async function OrchestrationPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ mode?: string }>;
}) {
  const { id } = await params;
  const { mode } = await searchParams;
  return <OrchestrationView sessionId={id} initialMode={mode === "replay" ? "replay" : "live"} />;
}
