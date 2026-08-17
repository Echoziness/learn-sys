import { ProfileForm } from "@/components/student/profile-form";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">新建导学会话</h1>
        <p className="text-sm text-muted-foreground">
          输入学习者画像 → 学情诊断 → 多智能体协同教学 → 生成个性化学习资源包
        </p>
      </div>
      <ProfileForm />
    </div>
  );
}
