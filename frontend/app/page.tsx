import { JourneyCard } from "@/components/journey-card";

export default function Home() {
  return (
    <main>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Less searching. More looking forward.</p>
          <h1>
            Your next trip,
            <br />
            closer to <em>you.</em>
          </h1>
          <p className="subtitle">
            Travel discovery, shaped around your curiosity.
          </p>
          <p className="intro">
            For the places that spark your curiosity, and the days you want to
            make your own. Start with where you are in your journey.
          </p>
          <a className="text-link" href="#journeys">
            Find your starting point <span aria-hidden="true">↓</span>
          </a>
        </div>
        <div
          className="landscape"
          role="img"
          aria-label="Abstract illustration of a winding coastal path and mountains"
        >
          <div className="sun" />
          <div className="mountain far" />
          <div className="mountain near" />
          <div className="coast" />
          <div className="trail" />
          <span className="landscape-caption">Follow your own direction.</span>
          <span className="compass" aria-hidden="true">
            N<br />↑
          </span>
        </div>
      </section>
      <section id="journeys" className="journeys">
        <div className="section-heading">
          <p className="eyebrow">Two ways to begin</p>
          <p>Where are you starting from?</p>
        </div>
        <div className="cards">
          <JourneyCard
            number="01"
            label="Discover"
            title="Help me choose where to travel"
            description="I have dates and preferences, but I haven't chosen a destination yet."
            href="/discover"
          />
          <JourneyCard
            number="02"
            label="Plan"
            title="Find what to explore at my destination"
            description="I already know where I'm going. Help me explore what to see and experience."
            href="/plan"
          />
        </div>
        <p className="foundation-note">
          Start with your preferences, then explore explainable destination
          matches. Itinerary planning is still to come.
        </p>
      </section>
    </main>
  );
}
