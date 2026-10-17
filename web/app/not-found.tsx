import Link from "next/link";

export default function NotFound() {
  return (
    <main className="wrap main">
      <section className="sec" style={{ maxWidth: 820 }}>
        <div className="k">Not in the record</div>
        <h1 className="h1" style={{ fontSize: 36 }}>Nothing in the NTSB data has that id.</h1>
        <p style={{ color: "var(--sub)" }}>Check the number: crashes look like ANC25MA018, recommendations like A-16-035, organisations like FAA.</p>
        <Link href="/" className="b1" style={{ marginTop: 12 }}>Ask the record</Link>
      </section>
    </main>
  );
}
