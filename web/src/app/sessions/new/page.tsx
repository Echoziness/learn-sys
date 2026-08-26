import { ProfileForm } from "@/components/student/profile-form";
import { PageHeader } from "@/components/shared/page-header";

export default function NewSessionPage() {
  return (
    <div className="space-y-8">
      <PageHeader
        title="新建导学会话"
        description="输入学习者画像 → 学情诊断 → 多智能体协同教学 → 生成个性化学习资源包。可先用演示画像快速体验。"
      />
      <div className="mx-auto max-w-2xl">
        <ProfileForm />
      </div>
    </div>
  );
}
