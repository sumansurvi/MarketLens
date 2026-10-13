import { Suspense } from "react";

import { Workspace } from "@/components/workspace/workspace";

// Kept so existing links keep working: the run opens inside the single-page workspace.
export default function ResearchRunPage({ params }: PageProps<"/research/[runId]">) {
  return (
    <Suspense fallback={<div className="h-dvh bg-plane" />}>
      {params.then(({ runId }) => (
        <Workspace initial={{ run: runId }} />
      ))}
    </Suspense>
  );
}
