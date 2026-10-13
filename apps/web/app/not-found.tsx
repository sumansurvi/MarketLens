import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg py-16 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-ink-2">The page you requested does not exist.</p>
      <Link href="/" className="mt-4 inline-block text-sm font-medium text-accent hover:underline">
        Back to research
      </Link>
    </div>
  );
}
