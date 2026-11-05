import Link from "next/link";

type Props = {
  number: string;
  label: string;
  title: string;
  description: string;
  href: string;
};
export function JourneyCard({
  number,
  label,
  title,
  description,
  href,
}: Props) {
  return (
    <Link className="journey-card" href={href}>
      <div className="card-top">
        <span>
          {number} / {label}
        </span>
        <span aria-hidden="true">↗</span>
      </div>
      <h2>{title}</h2>
      <p>{description}</p>
      <span className="card-link">
        Explore this journey <span aria-hidden="true">→</span>
      </span>
    </Link>
  );
}
