import { Sidebar } from "@/components/Sidebar";

export default function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { projectId: string };
}) {
  return (
    <div className="flex min-h-screen">
      <Sidebar projectId={params.projectId} />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
