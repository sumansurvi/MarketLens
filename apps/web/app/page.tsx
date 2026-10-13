import { Suspense } from "react";

import { Workspace } from "@/components/workspace/workspace";

function param(value: string | string[] | undefined, max: number): string | null {
  return typeof value === "string" && value ? value.slice(0, max) : null;
}

export default function HomePage({ searchParams }: PageProps<"/">) {
  // ?run= and ?symbol= restore the workspace (refresh, back, shared links). Search
  // params are request-time data under Cache Components, so they are resolved inside Suspense.
  return (
    <Suspense fallback={<div className="h-dvh bg-plane" />}>
      {searchParams.then((p) => (
        <Workspace initial={{ run: param(p.run, 64), symbol: param(p.symbol, 16)?.toUpperCase() ?? null }} />
      ))}
    </Suspense>
  );
}
