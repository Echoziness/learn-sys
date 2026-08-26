import type { NextConfig } from "next";

// output: "standalone" 仅容器部署需要（Dockerfile  COPY --from standalone）。
// 本地部署用默认 output 即可——Windows 下 standalone 构建会因 symlink EPERM 失败。
const nextConfig: NextConfig = {};

export default nextConfig;
