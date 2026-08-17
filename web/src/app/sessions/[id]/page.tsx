import { SessionWorkbench } from "@/components/student/session-workbench";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SessionWorkbench sessionId={id} />;
}
