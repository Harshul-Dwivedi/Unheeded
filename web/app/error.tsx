"use client"; // error boundaries must be client components

export default function ErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <main className="wrap main">
      <section className="sec" style={{ maxWidth: 820 }}>
        <div className="k">Something went wrong</div>
        <h1 className="h1" style={{ fontSize: 36 }}>This page could not load.</h1>
        <p style={{ color: "var(--sub)" }}>The API or the graph may be down (make up, make api). {error.message}</p>
        <button type="button" className="b1" onClick={() => retry()} style={{ marginTop: 12 }}>Try again</button>
      </section>
    </main>
  );
}
