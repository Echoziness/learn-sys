import { ReportView } from "@/components/report/report-view";

export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReportView sessionId={id} />;
}
