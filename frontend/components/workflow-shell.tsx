import Link from "next/link";
export function WorkflowShell({
  label,
  title,
  description,
}: {
  label: string;
  title: string;
  description: string;
}) {
  return (
    <main className="workflow">
      <Link className="text-link" href="/">
        ← Back to Roamline
      </Link>
      <p className="eyebrow">{label} · Coming later</p>
      <h1>{title}</h1>
      <p className="subtitle">{description}</p>
      <div className="notice">
        <h2>This journey is still taking shape.</h2>
        <p>
          This workflow is not implemented yet. There are no recommendations or
          itineraries to generate at this stage.
        </p>
      </div>
    </main>
  );
}
